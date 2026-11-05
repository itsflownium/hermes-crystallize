"""Line up runs of the same job and work out which steps never really change.

How it works:
  1. Find the backbone: the tool calls that happen, in the same order, in every
     run (a longest-common-subsequence over each run's tool names).
  2. For each backbone step, compare its inputs across runs:
       fixed    identical every run
       fill-in  identical once dates, ids and temp paths are blanked out
       varies   same tool, same place, different inputs (freezable, but must
                pass test runs before anything relies on it)
       setup    loads context a script doesn't need (skills, todo lists)
       side     sends or changes something: always stays with the agent
  3. Estimate how much of each run could skip the AI.

Pure functions over ``history.Run`` objects, so it's easy to test.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

MIN_RUNS = 3

# Tools that only load context for the agent. A script doesn't need them.
SETUP_TOOLS = {"skill_view", "skills_list", "todo", "todo_write", "todo_read", "memory_view"}

# Tools that send or change something outside the run.
SIDE_EFFECT_TOOLS = {
    "send_message", "send_email", "post_message", "write_file", "patch", "file_edit",
    "delete_file", "cronjob_manage", "delegate_task",
}
_SIDE_EFFECT_NAME = re.compile(r"(send|post|delete|remove|create|update|publish|deploy|write|upload)",
                               re.IGNORECASE)

# Shell commands that change things. Writing into a scratch/temp file is fine.
_MUTATING_SHELL = re.compile(
    r"(\brm\s|\bmv\s|\bchmod\b|\bchown\b|\bgit\s+(push|commit|reset|checkout|merge)\b|"
    r"\bnpm\s+(publish|install)\b|\bpip\s+install\b|\bsudo\b|\bkill\b|"
    r"curl\b[^|;&]*\s-X\s*(POST|PUT|PATCH|DELETE)|curl\b[^|;&]*\s(-d|--data)\b)",
    re.IGNORECASE,
)

# Blanks, in the order we try them. Each is (kind, pattern, placeholder).
_FILL_INS: list[tuple[str, re.Pattern[str], str]] = [
    ("date", re.compile(
        r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"), "{date}"),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "{id}"),
    ("timestamp", re.compile(r"\b1\d{9}(?:\d{3})?\b"), "{timestamp}"),
    ("temp_path", re.compile(r"(?:/tmp|/var/folders|/private/tmp)/[\w./-]+"
                            r"|[A-Za-z]:(?:\\\\|\\|/)[^\"\s]*?\b(?:Temp|TEMP|tmp)(?:\\\\|\\|/)[\w.\\/-]+"),
     "{temp}"),
    ("hex_id", re.compile(r"\b[0-9a-f]{12,}\b", re.I), "{id}"),
]


@dataclass
class StepPlan:
    position: int
    tool_name: str
    kind: str                      # fixed | fill-in | varies | setup | side
    args: Any                      # from the most recent run
    fill_ins: list[str] = field(default_factory=list)
    distinct_inputs: int = 1
    note: str = ""
    result_lens: list[int] = field(default_factory=list)


@dataclass
class Analysis:
    job_id: Optional[str]
    job_name: Optional[str]
    runs_used: int
    ready: bool
    reason: str
    steps: list[StepPlan] = field(default_factory=list)
    extra_steps_per_run: float = 0.0     # calls outside the backbone, on average
    same_shape_share: float = 0.0        # runs whose tool sequence equals the backbone
    avg_tool_calls: Optional[float] = None
    avg_api_calls: Optional[float] = None
    avg_cost_usd: Optional[float] = None
    est_api_calls_after: Optional[float] = None
    est_cost_after_usd: Optional[float] = None
    agent_free: bool = False             # whole job could run with no AI at all

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- helpers

def _canon(args: Any) -> str:
    if isinstance(args, str):
        return " ".join(args.split())
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(args)


def _blank(text: str) -> tuple[str, list[str]]:
    kinds: list[str] = []
    for kind, pattern, placeholder in _FILL_INS:
        new = pattern.sub(placeholder, text)
        if new != text:
            kinds.append(kind)
            text = new
    return text, kinds


def _lcs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Index pairs of a longest common subsequence of ``a`` and ``b``."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _backbone(sequences: list[list[str]]) -> list[str]:
    common = list(sequences[0])
    for seq in sequences[1:]:
        common = [common[i] for i, _ in _lcs(common, seq)]
    return common


def _is_side_effect(tool_name: str, args: Any) -> bool:
    if tool_name in SIDE_EFFECT_TOOLS:
        return True
    if tool_name in ("terminal", "shell", "execute_code"):
        return bool(_MUTATING_SHELL.search(_canon(args)))
    if tool_name.startswith("mcp_") or "__" in tool_name:
        return bool(_SIDE_EFFECT_NAME.search(tool_name))
    return False


def _avg(values: list[Optional[float]]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


# ---------------------------------------------------------------- main entry

def analyze_runs(runs: list[Any]) -> Analysis:
    """Analyze usable runs of one job (``history.Run`` objects, oldest first)."""
    runs = [r for r in runs if getattr(r, "usable", True) and r.steps]
    job_id = runs[-1].job_id if runs else None
    job_name = runs[-1].job_name if runs else None
    base = Analysis(job_id=job_id, job_name=job_name, runs_used=len(runs), ready=False, reason="")
    base.avg_tool_calls = _avg([len(r.steps) for r in runs])
    base.avg_api_calls = _avg([r.api_calls for r in runs])
    base.avg_cost_usd = _avg([r.cost_usd for r in runs])

    if len(runs) < MIN_RUNS:
        base.reason = f"Needs at least {MIN_RUNS} finished scheduled runs, has {len(runs)}."
        return base

    sequences = [[s.tool_name for s in r.steps] for r in runs]
    backbone = _backbone(sequences)
    if not backbone:
        base.reason = "The runs have no tool calls in common, so there's nothing stable to save."
        return base

    # Map each run's steps onto the backbone positions.
    per_position: list[list[Any]] = [[] for _ in backbone]
    extra = 0
    for run, seq in zip(runs, sequences):
        matched = _lcs(backbone, seq)
        for b_idx, s_idx in matched:
            per_position[b_idx].append(run.steps[s_idx])
        extra += len(seq) - len(matched)

    plans: list[StepPlan] = []
    for pos, (tool, steps) in enumerate(zip(backbone, per_position)):
        latest = steps[-1].args
        raw = [_canon(s.args) for s in steps]
        distinct = len(set(raw))
        plan = StepPlan(position=pos, tool_name=tool, kind="fixed", args=latest, distinct_inputs=distinct,
                        result_lens=[s.result_len for s in steps if s.result_len is not None])

        if any(_is_side_effect(tool, s.args) for s in steps):
            plan.kind, plan.note = "side", "Sends or changes something, so the agent keeps this step."
        elif tool in SETUP_TOOLS:
            plan.kind, plan.note = "setup", "Loads context for the agent; a script can skip it."
        elif distinct == 1:
            plan.kind = "fixed"
        else:
            blanked = [_blank(r) for r in raw]
            if len({b for b, _ in blanked}) == 1:
                plan.kind = "fill-in"
                plan.fill_ins = sorted({k for _, kinds in blanked for k in kinds})
            else:
                plan.kind = "varies"
                plan.note = (f"{distinct} different inputs across {len(steps)} runs. "
                             "Freezing the latest version is allowed only after it passes test runs.")
        plans.append(plan)

    base.steps = plans
    base.extra_steps_per_run = extra / len(runs)
    base.same_shape_share = sum(1 for s in sequences if s == backbone) / len(runs)

    # Rough estimate until test runs measure it: the agent still makes one call to
    # write the answer, plus one for each step it has to keep doing itself.
    agent_steps = sum(1 for p in plans if p.kind == "side") + round(base.extra_steps_per_run)
    base.est_api_calls_after = 1.0 + agent_steps
    if base.avg_api_calls and base.avg_cost_usd is not None:
        ratio = min(1.0, base.est_api_calls_after / base.avg_api_calls)
        base.est_cost_after_usd = base.avg_cost_usd * ratio

    scriptable = [p for p in plans if p.kind in ("fixed", "fill-in", "varies")]
    base.agent_free = (
        bool(scriptable) and agent_steps == 0
        and all(p.kind in ("fixed", "fill-in", "setup") for p in plans)
    )

    if not scriptable:
        base.reason = "Every common step needs the agent, so there's nothing to save yet."
    elif base.same_shape_share < 0.5:
        base.reason = ("Runs take different paths more than half the time. "
                       "Wait for more runs before saving a step list.")
    else:
        base.ready = True
        base.reason = f"{len(scriptable)} of {len(plans)} common steps can run without the AI."
    return base


def analyze_job(job: str) -> Analysis:
    from . import history

    return analyze_runs(history.load_runs(job, only_usable=True))
