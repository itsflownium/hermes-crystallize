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


def _saved_since(runs: list[history.Run], promoted_at: Optional[float],
                 baseline: Optional[float]) -> float:
    """Money saved by runs after promotion: baseline cost minus what each run cost."""
    if not promoted_at or baseline is None:
        return 0.0
    return sum(max(0.0, baseline - (r.cost_usd or 0.0)) for r in runs if r.started_ts >= promoted_at)


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
    # Score: how much of the repeatable work is already handled by routines,
    # weighted by what each job costs. Live counts fully, testing by progress.
    score_weight = 0.0
    score_done = 0.0

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
        meta = (routine or {}).get("meta") or {}
        weight = a.avg_cost_usd or 0.0
        week_saving = per_run_saving * per_week if per_run_saving is not None and per_week is not None else 0.0
        card["saving_per_week"] = week_saving
        card["runs_per_week"] = per_week
        if routine and routine["status"] == "live":
            score_weight += weight
            score_done += weight
            job_saved = _saved_since(runs, meta.get("promoted_at"), (meta.get("baseline_cost")
                                                                   or a.avg_cost_usd))
            saved += job_saved
            live.append({**card, "routine_id": routine["id"], "saved_usd": job_saved,
                         "live_runs": meta.get("live_runs", 0), "failures": meta.get("failures", 0)})
        elif routine and routine["status"] == "testing":
            needed = meta.get("needed", 3) or 3
            score_weight += weight
            score_done += weight * 0.5 * min(1.0, meta.get("matches", 0) / needed)
            could_save_week += week_saving
            tests = meta.get("tests") or []
            testing.append({**card, "routine_id": routine["id"], "matches": meta.get("matches", 0),
                            "needed": meta.get("needed", 3), "ready": bool(meta.get("ready")),
                            "last_test": tests[-1] if tests else None})
        elif a.ready:
            spotted.append(card)
            score_weight += weight
            could_save_week += week_saving
        else:
            watching.append(card)

    score = round(100 * score_done / score_weight) if score_weight > 0 else None

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
