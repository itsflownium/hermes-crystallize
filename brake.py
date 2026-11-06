"""Runaway Brake: stops a cron run that has gone far past what its job normally uses.

Every limit comes from the job's own history and is counted in tokens, so it works on any
model or plan, priced or not. It's off until you turn it on, and it leaves alone any job that
sends or changes things, because stopping one part-way could leave its work half done.
"""

from __future__ import annotations

import statistics
import threading
import time
from typing import Any, Optional

from . import analyze, history, store

SETTING = "runaway_brake"
MIN_RUNS = 5            # fewer finished runs than this can't show what normal is
RECENT_RUNS = 20        # a job's settings change, so only its recent runs count
OVER_NORMAL = 3         # the limit is at least this many times a normal run
OVER_LARGEST = 1.5      # and always well above the largest recent run

_lock = threading.Lock()
_used: dict[tuple[str, str], int] = {}
_limits: dict[tuple[str, str], Optional[dict[str, Any]]] = {}


def _key(session_id: str) -> tuple[str, str]:
    """A shared gateway can host the same session identifier in different profiles."""
    return str(history._hermes_home().resolve()), session_id


def enabled() -> bool:
    return bool(store.get_setting(SETTING, False))


def set_enabled(on: bool) -> dict[str, Any]:
    store.set_setting(SETTING, bool(on))
    return {"ok": True, "message": "Runaway Brake is on." if on else "Runaway Brake is off."}


def _tokens(run: history.Run) -> Optional[int]:
    parts = (run.input_tokens, run.output_tokens, run.cache_read_tokens, run.cache_write_tokens)
    return sum(p or 0 for p in parts) if any(p for p in parts) else None


def plan(job_name: str) -> dict[str, Any]:
    """This job's limit, or why it doesn't have one."""
    stopped = store.brake_stopped_sessions(job_name)  # a stopped run says nothing about normal
    runs = [r for r in history.load_runs(job_name) if r.scheduled and r.outcome in (None, "completed")
            and r.session_id not in stopped][-RECENT_RUNS:]
    if any(analyze.run_actions(r) for r in runs):
        return {"limit": None, "why": "It sends or changes things, so it's never stopped part-way."}
    sizes = [t for t in (_tokens(r) for r in runs) if t]
    if len(sizes) < MIN_RUNS:
        return {"limit": None, "why": f"It needs {MIN_RUNS} finished runs first ({len(sizes)} so far)."}
    normal = int(statistics.median(sizes))
    return {"limit": int(max(normal * OVER_NORMAL, max(sizes) * OVER_LARGEST)), "normal": normal, "why": None}


def _plan_for_run(job_name: str, session_id: str) -> Optional[dict[str, Any]]:
    """Worked out once per run, because this is checked before every tool."""
    key = _key(session_id)
    with _lock:
        if key in _limits:
            return _limits[key]
    found = plan(job_name)
    with _lock:
        _limits[key] = found
    return found


def note_usage(session_id: str, usage: Optional[dict[str, Any]]) -> None:
    """Add one AI call's tokens to this run's total."""
    if not session_id or not usage:
        return
    if not isinstance(usage, dict):
        return
    total = usage.get("total_tokens")
    if total is None:
        total = (usage.get("prompt_tokens") or usage.get("input_tokens") or 0) + (usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    try:
        total = max(0, int(total or 0))
    except (TypeError, ValueError, OverflowError):
        return
    key = _key(session_id)
    with _lock:
        # Never evict an active run to make room for another run. Session finalization
        # removes its own entry; reaching a process-wide count cannot reset a runaway.
        _used[key] = _used.get(key, 0) + total


def check(job_name: str, session_id: str) -> Optional[dict[str, str]]:
    """A block for the next tool if this run has passed its limit, otherwise None."""
    if not job_name or not session_id or not enabled():
        return None
    key = _key(session_id)
    with _lock:
        used = _used.get(key, 0)
    if not used:
        return None
    found = _plan_for_run(job_name, session_id)
    limit = found.get("limit") if found else None
    if not limit or used <= limit:
        return None
    store.add_brake_stop(session_id, job_name, used, limit, found["normal"])
    return {"action": "block", "message": (
        f"Crystallize Runaway Brake: this run has used {used:,} tokens. This job normally uses about "
        f"{found['normal']:,}, and its limit is {limit:,}. Don't call any more tools. Answer now with what you "
        "have, and say in your first line that the run was stopped early and what's missing.")}


def forget(session_id: str) -> None:
    key = _key(session_id)
    with _lock:
        _used.pop(key, None)
        _limits.pop(key, None)


def summary(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """For the panel: whether it's on, each job's limit, and the runs it stopped."""
    rows = []
    for job in jobs:
        name = job.get("name") or job.get("id")
        if not job.get("no_agent"):
            rows.append({"name": name, **plan(name)})
    return {"on": enabled(), "jobs": rows, "stops": store.list_brake_stops(), "checked_at": time.time()}
