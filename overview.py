"""What the Desktop panel shows: score, money, and the Spotted / Testing / Live lists."""

from __future__ import annotations

import statistics
from typing import Any, Optional

from . import analyze, chat, history, store

WEEK_S = 7 * 24 * 3600


def _runs_per_week(runs: list[history.Run]) -> Optional[float]:
    starts = sorted(r.started_ts for r in runs)
    gaps = [b - a for a, b in zip(starts, starts[1:]) if b > a]
    if not gaps:
        return None
    return WEEK_S / statistics.median(gaps)


def _chat_spent(session_id: Optional[str]) -> Optional[float]:
    if not session_id:
        return None
    info = history._session_costs([session_id]).get(session_id)
    if not info:
        return None
    return info.get("actual_cost_usd") or info.get("estimated_cost_usd")


def build(session_id: Optional[str] = None) -> dict[str, Any]:
    routines = {r["source_ref"]: r for r in store.list_routines() if r["source"] == "cron"}
    spotted, testing, live, watching = [], [], [], []
    could_save_week = 0.0
    saved = 0.0

    for summary in history.job_summaries():
        job_id = summary["job_id"]
        runs = history.load_runs(job_id, only_usable=True)
        a = analyze.analyze_runs(runs)
        per_week = _runs_per_week(runs)
        per_run_saving = None
        if a.avg_cost_usd is not None and a.est_cost_after_usd is not None:
            per_run_saving = max(0.0, a.avg_cost_usd - a.est_cost_after_usd)

        card = {
            "job_id": job_id,
            "name": summary["job_name"] or job_id,
            "runs": a.runs_used,
            "steps": len(a.steps),
            "scriptable_steps": sum(1 for s in a.steps if s.kind in ("fixed", "fill-in", "varies")),
            "cost_now": a.avg_cost_usd,
            "cost_after": a.est_cost_after_usd,
            "agent_free": a.agent_free,
            "reason": a.reason,
        }

        routine = routines.get(job_id)
        if routine and routine["status"] == "live":
            live.append({**card, "routine_id": routine["id"]})
        elif routine and routine["status"] == "testing":
            testing.append({**card, "routine_id": routine["id"], "matches": 0, "needed": 3})
        elif a.ready:
            spotted.append(card)
            if per_run_saving is not None and per_week is not None:
                could_save_week += per_run_saving * per_week
        else:
            watching.append(card)

    score = None
    if spotted or testing or live:
        total = saved + could_save_week
        score = round(100 * saved / total) if total > 0 else 0

    return {
        "session_id": session_id,
        "chat": chat.analyse(session_id),
        "chat_spent_usd": _chat_spent(session_id),
        "score": score,
        "saved_usd": saved,
        "could_save_usd_per_week": could_save_week,
        "spotted": spotted,
        "testing": testing,
        "live": live,
        "watching": watching,
    }
