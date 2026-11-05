"""Turn an analysis into a saved routine: a readable list of steps, each with a check.

A routine is stored as JSON in the routines table:

    {
      "version": 1,
      "job_id": "...", "job_name": "...",
      "steps": [
        {"tool": "terminal", "args": {...}, "kind": "fixed" | "fill-in" | "varies",
         "fill_ins": ["date", ...], "check": {"min_len": 40}}
      ]
    }

Setup steps (loading a skill, a todo list) are dropped: a script doesn't need them.
A job with any step that sends or changes something isn't turned into a routine.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import statistics
import tempfile
from typing import Any, Optional

from . import analyze

RUNNABLE_KINDS = ("fixed", "fill-in", "varies")


class NotCrystallizable(ValueError):
    """The analysis says this job can't be saved as a routine (yet)."""


def routine_id(job_id: str) -> str:
    return "cr_" + hashlib.sha1(job_id.encode()).hexdigest()[:10]


def build(a: analyze.Analysis) -> dict[str, Any]:
    if not a.ready:
        raise NotCrystallizable(a.reason)
    if any(p.kind == "side" for p in a.steps):
        raise NotCrystallizable("A step sends or changes something, so Hermes keeps doing this job itself.")

    steps = []
    for p in a.steps:
        if p.kind not in RUNNABLE_KINDS:
            continue
        lens = [n for n in p.result_lens if n is not None]
        # Pass if the output is at least a third of the smallest one we saw.
        min_len = max(1, int(min(lens) / 3)) if lens else 1
        steps.append({
            "tool": p.tool_name,
            "args": p.args,
            "kind": p.kind,
            "fill_ins": p.fill_ins,
            "check": {"min_len": min_len, "typical_len": int(statistics.median(lens)) if lens else None},
        })
    if not steps:
        raise NotCrystallizable("No steps can run without the AI.")

    return {
        "version": 1,
        "job_id": a.job_id,
        "job_name": a.job_name,
        "steps": steps,
        "baseline": {
            "cost_usd": a.avg_cost_usd,
            "api_calls": a.avg_api_calls,
            "tool_calls": a.avg_tool_calls,
            "runs": a.runs_used,
        },
    }


# ---------------------------------------------------------------- fill-ins

def _today_like(sample: str) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    if "T" in sample or " " in sample:
        return now.isoformat(timespec="seconds")
    return now.date().isoformat()


def fill(args: Any, fill_ins: list[str]) -> Any:
    """Replace values that change every run (dates, temp paths) with fresh ones."""
    if not fill_ins:
        return args
    text = json.dumps(args)
    fresh_tmp = tempfile.mkdtemp(prefix="crystallize-")
    for kind, pattern, _ in analyze._FILL_INS:
        if kind not in fill_ins:
            continue
        if kind == "date":
            text = pattern.sub(lambda m: _today_like(m.group(0)), text)
        elif kind == "timestamp":
            now = _dt.datetime.now().timestamp()
            text = pattern.sub(lambda m: str(int(now * 1000)) if len(m.group(0)) > 10 else str(int(now)), text)
        elif kind == "temp_path":
            # Keep the file name, move it into a fresh temp folder.
            def _fresh(m: re.Match) -> str:
                name = re.split(r"[\\\\/]+", m.group(0))[-1]
                return json.dumps(os.path.join(fresh_tmp, name))[1:-1]
            text = pattern.sub(_fresh, text)
        # uuid / hex ids: keep the latest value; they're usually stable references.
    return json.loads(text)


def describe(routine: dict[str, Any]) -> list[str]:
    """Plain-English lines for showing a routine before Promote."""
    out = []
    for i, s in enumerate(routine.get("steps", []), 1):
        args = s["args"]
        detail = args.get("command") or args.get("url") or args.get("query") if isinstance(args, dict) else None
        detail = re.sub(r"\s+", " ", str(detail or json.dumps(args)))[:120]
        blanks = f" (fresh {', '.join(s['fill_ins'])} each run)" if s.get("fill_ins") else ""
        out.append(f"{i}. {s['tool']}: {detail}{blanks}")
    return out


def baseline_cost(routine: dict[str, Any]) -> Optional[float]:
    return (routine.get("baseline") or {}).get("cost_usd")
