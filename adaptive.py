"""Conservative Cron presentation and measurement, without changing model capability.

Budgets are learned from complete observations. They choose a presentation, never
authorize dropping data. Tool definitions always come from the current host request.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import statistics
from typing import Any, Optional

MIN_OBSERVATIONS = 3
WINDOW = 24
_SUCCESS = {None, "ok", "success", "succeeded", "completed"}
logger = logging.getLogger(__name__)


def _spread(values: list[float]) -> float:
    middle = statistics.median(values)
    mad = statistics.median(abs(value - middle) for value in values)
    # The full range prevents a small, mostly identical sample hiding an outlier.
    return max(1.4826 * mad, (max(values) - min(values)) / 2)


def savings_evidence(before: list[float], trial: list[float]) -> dict[str, Any]:
    """A conservative uncertainty allowance derived from spread and sample counts.

    This is a descriptive decision rule, not a claimed statistical confidence
    interval. Even zero measured spread leaves a finite-sample allowance.
    """
    valid = lambda values: bool(values) and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                               and math.isfinite(v) and v >= 0 for v in values)
    result: dict[str, Any] = {"method": "observed-spread-and-count-v1", "baseline_count": len(before),
                              "trial_count": len(trial), "sufficient_samples": False,
                              "statistical_confidence_claimed": False}
    if not valid(before) or not valid(trial):
        return {**result, "why": "complete finite measurements are required"}
    old, new = statistics.median(before), statistics.median(trial)
    baseline_spread, trial_spread = _spread(before), _spread(trial)
    sample_allowance = old * (1 / (len(before) + 1) + 1 / (len(trial) + 1)) / 2
    spread_allowance = 2 * (baseline_spread / math.sqrt(len(before)) + trial_spread / math.sqrt(len(trial)))
    allowance = sample_allowance + spread_allowance
    enough = len(before) >= 2 and len(trial) >= 2
    return {**result, "sufficient_samples": enough, "baseline_spread": baseline_spread,
            "trial_spread": trial_spread, "sample_allowance": sample_allowance,
            "spread_allowance": spread_allowance, "required_drop": allowance,
            "observed_drop": old - new, "required_drop_fraction": allowance / old if old > 0 else None,
            "ok": bool(enough and old > 0 and old - new > allowance),
            "why": "saving exceeds observed variation and sample uncertainty" if enough and old - new > allowance
                   else "more comparable runs or a saving above observed variation are required"}


def _complete_text(step: Any) -> Optional[str]:
    data = step if isinstance(step, dict) else getattr(step, "__dict__", {})
    if data.get("status") not in _SUCCESS or data.get("truncated") or data.get("result_truncated"):
        return None
    raw = data.get("output", data.get("result", data.get("result_preview")))
    if not isinstance(raw, str):
        return None
    if "output" not in data and "result" not in data:
        length = data.get("result_len")
        if not isinstance(length, int) or length != len(raw):
            return None
    digest = data.get("result_sha256")
    if digest and digest != hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest():
        return None
    return raw


def presentation_budgets(runs: list[Any]) -> dict[str, Any]:
    """Per-tool advisory sizes from complete successful same-configuration runs."""
    from . import history

    runs = [run for run in runs if run.scheduled and run.finished and run.outcome in (None, "completed")][-WINDOW:]
    if len(runs) < MIN_OBSERVATIONS or not history.comparable_runs(runs, runs):
        return {"ready": False, "by_tool": {}, "why": "more complete comparable observations are required"}
    sizes: dict[str, list[int]] = {}
    for run in runs:
        if not run.steps:
            return {"ready": False, "by_tool": {}, "why": "an observation has no recorded tool evidence"}
        totals: dict[str, int] = {}
        for step in run.steps:
            text = _complete_text(step)
            if text is None:
                return {"ready": False, "by_tool": {}, "why": "an observation contains an incomplete result"}
            totals[step.tool_name] = totals.get(step.tool_name, 0) + len(text)
        for tool, size in totals.items():
            sizes.setdefault(tool, []).append(size)
    budgets = {}
    for tool, values in sizes.items():
        if len(values) < MIN_OBSERVATIONS:
            continue
        middle = statistics.median(values)
        uncertainty = _spread(values) + middle / math.sqrt(len(values))
        budgets[tool] = {"chars": math.ceil(max(values) + uncertainty), "observations": len(values),
                         "observed_max": max(values), "spread": _spread(values), "advisory_only": True}
    return {"ready": bool(budgets), "by_tool": budgets, "complete_results": True,
            "overflow": "complete original output", "sample_count": len(runs)}


def schema_plan(runs: list[Any]) -> dict[str, Any]:
    """Only a stable, complete workflow provides enough evidence to prune schemas."""
    budget = presentation_budgets(runs)
    if not budget.get("ready"):
        return {"ready": False, "why": budget["why"]}
    good = [run for run in runs if run.scheduled and run.finished and run.outcome in (None, "completed")][-WINDOW:]
    names = [{step.tool_name for step in run.steps} for run in good]
    if not names or any(value != names[0] for value in names):
        return {"ready": False, "why": "observed tool requirements vary between runs"}
    return {"ready": True, "observed_tools": sorted(names[0]), "sample_count": len(good)}


def _tool_name(schema: Any) -> Optional[str]:
    if not isinstance(schema, dict):
        return None
    body = schema.get("function", schema)
    return body.get("name") if isinstance(body, dict) and isinstance(body.get("name"), str) else None


def _declared_read(schema: dict[str, Any]) -> bool:
    """Unknown and mixed-action tools retain their complete host definitions."""
    from . import analyze

    body = schema.get("function", schema)
    annotations = schema.get("annotations", body.get("annotations", {}))
    return (isinstance(annotations, dict) and annotations.get("readOnlyHint") is True
            and annotations.get("destructiveHint") is not True
            and analyze.observation_safety_error(_tool_name(schema), {}) is None)


def prune_request(request: Any, job_ref: str, *, tuning: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Prune unused host-declared read schemas for an opted-in Cron campaign.

    Returns None for the host's original request on any uncertainty. Observed
    actions, unknown tools, discovery/recovery tools and every schema field remain.
    Any current tool error or unexpected tool call restores all original host defs.
    The caller must pass the host request, not a previously pruned cached request.
    """
    try:
        return _prune_request(request, job_ref, tuning=tuning)
    except Exception:  # noqa: BLE001 — uncertainty always restores current host defs
        logger.debug("crystallize: original tool definitions retained", exc_info=True)
        return None


def _prune_request(request: Any, job_ref: str, *, tuning: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    from . import history, lifecycle, store

    if not isinstance(request, dict) or not job_ref or not isinstance(request.get("tools"), list):
        return None
    found = lifecycle.cron_job(job_ref)
    if not found:
        return None
    tune = tuning if tuning is not None else lifecycle._tuning_for_job(found)
    if (not tune or tune.get("status") not in {"trial", "kept"} or not tune.get("schema_pruning")
            or tune.get("level") or tune.get("needs_restore")):
        return None
    saved = tune.get("config_snapshot")
    snapshot = lifecycle.configuration_fingerprint(found, store.routine_for_job(found["id"]), tune)
    if not saved or snapshot.get("fingerprint") != saved.get("fingerprint"):
        return None
    runs = history.load_runs(found["id"])
    # Trial observations cannot teach away a tool the unoptimized job used.
    boundary = tune.get("baseline_until", tune.get("first_started_at", tune.get("started_at", 0)))
    baseline = [run for run in runs if (run.session_started or run.started_ts) < boundary]
    plan = schema_plan(baseline)
    if not plan.get("ready"):
        return None
    observed = set(plan["observed_tools"])
    messages = request.get("messages")
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, dict):
            return None
        for call in message.get("tool_calls") or []:
            name = _tool_name(call)
            if not name or name not in observed:
                return None
        if message.get("role") == "tool":
            # A changed or failing source can require a different recovery workflow.
            content = message.get("content")
            if not isinstance(content, str):
                return None
            from . import runner
            if runner.step_failure(content) is not None:
                return None
    tools = request["tools"]
    names = [_tool_name(schema) for schema in tools]
    if any(name is None for name in names) or len(set(names)) != len(names) or not observed.issubset(names):
        return None
    recovery = {"tool_search", "tool_describe", "crystal_full_result", "skills_list", "skill_view"}
    if not {"tool_search", "tool_describe"}.intersection(names):
        return None  # the model needs a way to request a freshly restored definition
    choice = request.get("tool_choice")
    if isinstance(choice, dict) and _tool_name(choice) not in observed | recovery:
        return None
    for message in messages:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            if any(name not in observed | recovery and name in message["content"] for name in names):
                return None  # an explicit new requirement outranks historic observations
    keep = [schema for schema, name in zip(tools, names)
            if name in observed | recovery or not _declared_read(schema)]
    return {**request, "tools": keep} if len(keep) < len(tools) else None
