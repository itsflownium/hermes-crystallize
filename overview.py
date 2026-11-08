"""What the Desktop panel shows: score, money, and the Spotted / Testing / Live lists."""

from __future__ import annotations

import statistics
import time
from typing import Any, Optional

from . import analyze, chat, history, lifecycle, prerun, starter_script, store, tuning

WEEK_S = 7 * 24 * 3600


def _runs_per_week(runs: list[history.Run]) -> Optional[float]:
    starts = sorted(r.started_ts for r in runs)
    gaps = [b - a for a, b in zip(starts, starts[1:]) if b > a]
    if not gaps:
        return None
    return WEEK_S / statistics.median(gaps)


def cron_state(job: Optional[dict[str, Any]], running: set[str]) -> dict[str, Any]:
    """What a cron job is doing right now, as Hermes has it. The panel shows only this."""
    if not job:
        return {"status": "finished", "gone": True}
    repeat = job.get("repeat") if isinstance(job.get("repeat"), dict) else {}
    if job.get("state") == "completed":
        status = "finished"
    elif job.get("id") in running:
        status = "running"
    elif job.get("state") == "paused" or job.get("enabled") is False:
        status = "paused"
    else:
        status = "scheduled"
    return {
        "status": status,
        "schedule": job.get("schedule_display"),
        "next_run_at": job.get("next_run_at"),
        "last_run_at": job.get("last_run_at"),
        "last_error": job.get("last_error") if job.get("last_status") == "error" else None,
        "runs_done": repeat.get("completed"),
        "runs_total": repeat.get("times"),
        "no_ai": bool(job.get("no_agent")),
        "settings": tuning.job_settings(job),
    }


def _schedule_runs_per_week(job: Optional[dict[str, Any]]) -> Optional[float]:
    """Runs a week from the job's own schedule, when it's a plain interval."""
    schedule = (job or {}).get("schedule")
    minutes = schedule.get("minutes") if isinstance(schedule, dict) else None
    return WEEK_S / (float(minutes) * 60) if minutes else None


def _capped_per_week(per_week: Optional[float], cron: dict[str, Any]) -> Optional[float]:
    """A job limited to a few more runs can't keep saving at its weekly rate."""
    if per_week is None or cron.get("status") == "finished":
        return None if per_week is None else 0.0
    total, done = cron.get("runs_total"), cron.get("runs_done") or 0
    return min(per_week, max(0, total - done)) if total else per_week


def _stats(runs: list[history.Run]) -> dict[str, Optional[float]]:
    # Median, not average: one odd run (a cache miss, a long answer) can't hide the trend.
    def mid(xs):
        xs = [float(x) for x in xs if x is not None]
        return statistics.median(xs) if xs else None
    return {
        "runs": len(runs),
        "cost_usd": mid([r.cost_usd for r in runs]),
        "seconds": mid([r.duration_s for r in runs]),
        "tool_calls": mid([len(r.steps) for r in runs]),
        "ai_calls": mid([r.api_calls for r in runs]),
        "skipped": sum(1 for r in runs if r.model == SKIPPED),
        "api_cost_usd": mid([r.api_cost_usd for r in runs]),
        "tokens": mid([r.total_tokens for r in runs]),
    }


SKIPPED = "(skipped: no change)"
NO_AI = "(no AI)"


def _skipped_runs(meta: dict[str, Any], job_name: str) -> list[history.Run]:
    """Runs the AI never took part in, so they cost nothing: the data hadn't
    changed, or the job runs with no AI."""
    return [history.Run(execution_id=f"free-{ts}", job_id=None, job_name=job_name, session_id=None,
                        scheduled=True, started_ts=ts, outcome="completed", cost_usd=0.0,
                        api_calls=0, api_cost_usd=0.0, input_tokens=0, output_tokens=0,
                        cache_read_tokens=0, cache_write_tokens=0, cost_provenance="no_llm", duration_s=None, model=label)
            for key, label in (("skips", SKIPPED), ("no_ai_runs", NO_AI))
            for ts in meta.get(key) or []]


def _live_runs(job_id: str, meta: dict[str, Any]) -> list[history.Run]:
    """Every run since Promote, including failed ones and ones that made no tool calls or were skipped."""
    promoted_at = meta.get("promoted_at") or 0
    ran = [r for r in history.runs_for_job(job_id)
           if r.scheduled and r.outcome not in ("running", "claimed") and r.started_ts >= promoted_at]
    return sorted(ran + _skipped_runs(meta, job_id), key=lambda r: r.started_ts)


_WINDOWS = (("24h", "24 hours", 86400), ("7d", "7 days", 7 * 86400), ("30d", "30 days", 30 * 86400), ("all", "All time", None))


def period_rows(runs: list[history.Run], baseline: Optional[float] = None,
                after: Optional[list[history.Run]] = None, now: Optional[float] = None) -> list[dict[str, Any]]:
    """Totals for each time range. A figure with nothing behind it stays None, so the
    panel leaves it out. Savings only count runs after Promote, in real money."""
    now = time.time() if now is None else now
    after_ids = {id(r) for r in after or []}
    rows = []
    for key, label, span in _WINDOWS:
        since = now - span if span else None
        inside = [r for r in runs if since is None or r.started_ts >= since]
        costs = [r.cost_usd for r in inside if r.cost_usd is not None]
        apis = [r.api_cost_usd for r in inside if r.api_cost_usd is not None]
        tokens = [r.total_tokens for r in inside if r.total_tokens]
        times = [r.duration_s for r in inside if r.duration_s]
        free = sum(1 for r in inside if r.model in (SKIPPED, NO_AI))
        saved = _saved_since([r for r in inside if id(r) in after_ids], baseline) if baseline is not None else None
        rows.append({
            "key": key, "label": label, "since": since, "runs": len(inside),
            "spent_usd": sum(costs) if costs else None,
            "api_cost_usd": sum(apis) if apis else None,
            "tokens": sum(tokens) if tokens else None,
            "seconds": statistics.median(times) if times else None,
            "free_runs": free or None,
            "saved_usd": saved if saved else None,
        })
    return rows


def visible_periods(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A longer range only appears if it adds runs. A job that has only run today has no
    "7 days" of data to show."""
    shown: list[dict[str, Any]] = []
    for row in rows:
        if row["runs"] > (shown[-1]["runs"] if shown else 0):
            shown.append(row)
    return shown


def total_periods(all_rows: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every job's ranges added together."""
    totals = []
    for i, (key, label, _) in enumerate(_WINDOWS):
        rows = [r[i] for r in all_rows]

        def add(field: str) -> Optional[float]:
            values = [x[field] for x in rows if x[field] is not None]
            return sum(values) if values else None
        totals.append({"key": key, "label": label, "since": rows[0]["since"] if rows else None,
                       "runs": sum(x["runs"] for x in rows), "spent_usd": add("spent_usd"),
                       "api_cost_usd": add("api_cost_usd"), "tokens": add("tokens"), "seconds": None,
                       "free_runs": add("free_runs"), "saved_usd": add("saved_usd")})
    return totals


def cost_parts(runs: list[history.Run], prices: Optional[dict[str, float]]) -> Optional[dict[str, float]]:
    """Where a job's money goes, as shares: prompt and data, thinking, the written answer."""
    if not prices or not runs:
        return None
    prompt = thinking = answer = 0.0
    for r in runs:
        prompt += ((r.input_tokens or 0) * prices["input"] + (r.cache_read_tokens or 0) * prices["cache_read"]
                   + (r.cache_write_tokens or 0) * prices["cache_write"])
        think = r.reasoning_tokens or 0
        thinking += think * prices["output"]
        answer += max(0, (r.output_tokens or 0) - think) * prices["output"]
    total = prompt + thinking + answer
    if total <= 0:
        return None
    return {"prompt": prompt / total, "thinking": thinking / total, "answer": answer / total}


def ladder(routine: Optional[dict[str, Any]], tune: Optional[dict[str, Any]], card: dict[str, Any], *,
           routine_possible: bool, can_tune: bool) -> list[dict[str, str]]:
    """Every way a job can save, biggest first, and where it stands on each.
    A state is "on", "trying", "available" or "n/a"."""
    status = (routine or {}).get("status")
    meta = (routine or {}).get("meta") or {}
    live = status == "live"
    acts = isinstance((routine or {}).get("steps"), dict) and routine["steps"].get("leaves_to_ai")
    tuning_state = (tune or {}).get("status")

    def from_tune(flag: bool) -> str:
        if flag and tuning_state == "kept":
            return "on"
        if flag and tuning_state == "trial":
            return "trying"
        return "available" if can_tune else "n/a"

    mode = meta.get("mode")
    no_ai = "on" if live and mode == "no_ai" else "available" if live and card.get("no_ai_fit") else "n/a"
    fetch = ("on" if live else "trying" if status == "testing" else "available" if routine_possible else "n/a")
    unchanged = "on" if live and not acts and card.get("when_unchanged") in ("quiet", "repeat") else "available" if routine_possible and not acts and not live else "n/a"
    new_only = from_tune(bool((tune or {}).get("new_only"))) if live and not acts and mode != "no_ai" else "n/a"
    t = tune or {}
    return [
        {"key": "no_ai", "label": "No AI at all", "state": no_ai},
        {"key": "unchanged", "label": "Nothing changed: no full AI run", "state": unchanged},
        {"key": "fetch", "label": "Fetch data before the AI starts", "state": fetch},
        {"key": "calls", "label": "Fewer AI calls", "state": from_tune(bool(t.get("hint")))},
        {"key": "clean", "label": "Cleaned tool results", "state": from_tune(bool(t.get("clean")))},
        {"key": "new", "label": "New items only", "state": new_only},
        {"key": "tools", "label": "Only the tools it uses", "state": from_tune(t.get("toolsets") is not None)},
    ]


def _before_after(before: list[history.Run], after: Optional[list[history.Run]]) -> dict[str, Any]:
    """Measured numbers: runs before Promote vs runs after it."""
    return {"before": _stats(before), "after": _stats(after) if after is not None else None}


def _timeline(before: list[history.Run], after: list[history.Run], limit: int = 48) -> list[dict[str, Any]]:
    rows = [(r, False) for r in before] + [(r, True) for r in after]
    return [{
        "cost_usd": r.cost_usd,
        "after": is_after,
        "skipped": r.model == SKIPPED,
        "no_ai": r.model == NO_AI,
        "ts": r.started_ts,
        "seconds": r.duration_s,
        "tool_calls": len(r.steps),
        "ai_calls": r.api_calls,
        "api_cost_usd": r.api_cost_usd,
        "included": r.included,
        "tokens": r.total_tokens,
    } for r, is_after in rows[-limit:]]


MIN_RUNS_FOR_RATE = 3   # one run can't show a weekly rate


def _settings_match(before: list[history.Run], after: list[history.Run]) -> list[history.Run]:
    """Runs that used the same model, provider and reasoning as the baseline. Nothing else
    can count as a saving, so thinking less never looks like money saved."""
    keys = {history.comparison_identity(r) for r in before}
    if len(keys) != 1 or None in keys:
        return []
    key = keys.pop()
    return [r for r in after if history.comparison_identity(r) == key]


def _baseline(runs: list[history.Run], field: str) -> Optional[float]:
    """The average normal run, quiet days included. Comparing a median of busy days with every
    later run would show a saving when nothing changed."""
    values = [getattr(r, field) for r in runs]
    return sum(values) / len(values) if values and all(v is not None for v in values) else None


def _saving_view(job_id: str, cutoff: Optional[float], after: list[history.Run], field: str) -> dict[str, Any]:
    """Baseline, comparable runs and weekly-rate inputs for one kind of cost."""
    before = [r for r in history.runs_for_job(job_id) if r.scheduled and r.finished and r.outcome in (None, "completed")
              and (not cutoff or r.started_ts < cutoff)]
    matched = _settings_match(before, after)
    known = [r for r in matched if getattr(r, field) is not None]
    baseline = _baseline(before, field) if matched else None
    average = sum(getattr(r, field) for r in known) / len(known) if known else None
    return {"baseline": baseline, "runs": known, "average": average,
            "excluded_settings_runs": len(after) - len(matched), "unknown_cost_runs": len(matched) - len(known),
            "rate_per_run": baseline - average if baseline is not None and average is not None and len(known) >= MIN_RUNS_FOR_RATE else None}


def _saved_since(after: list[history.Run], baseline: Optional[float]) -> float:
    """Money saved since Promote: what each run would have cost minus what it did."""
    if baseline is None:
        return 0.0
    return sum(history.net_saving(r, baseline, r.cost_usd) for r in after if r.cost_usd is not None)


def _plan_saved(after: list[history.Run], baseline: Optional[float]) -> float:
    """The same as _saved_since for runs included in a plan, at API prices."""
    if baseline is None:
        return 0.0
    return sum(history.net_saving(r, baseline, r.api_cost_usd) for r in after if r.api_cost_usd is not None)


def _chat_spent(session_id: Optional[str]) -> Optional[float]:
    if not session_id:
        return None
    info = history._session_costs([session_id]).get(session_id)
    if not info:
        return None
    actual = info.get("actual_cost_usd")
    return actual if actual is not None else info.get("estimated_cost_usd")


def routine_full(row: dict[str, Any]) -> dict[str, Any]:
    return store.get_routine(row["id"]) or row


def build(session_id: Optional[str] = None) -> dict[str, Any]:
    tuning.refresh()
    routines: dict[str, Any] = {}
    for r in store.list_routines():
        if r["source"] == "cron":
            routines.setdefault(r["source_ref"], r)
    jobs = {j["id"]: j for j in lifecycle.all_cron_jobs() if j.get("id")}
    tuning_by_id = {}
    for t in store.list_tuning():
        if t.get("job_id"):
            tuning_by_id.setdefault(t["job_id"], t)
    running = history.running_job_ids()
    spotted, testing, live, watching, finished = [], [], [], [], []
    all_rows: list[list[dict[str, Any]]] = []
    waiting_jobs = 0
    active_weights: list[float] = []
    saved_api = 0.0
    api_week = 0.0
    seen: set[str] = set()
    could_save_week = 0.0
    saved = 0.0
    # Score: how much of the repeatable work is already handled by routines,
    # weighted by what each job costs. Live counts fully, testing by progress.
    score_weight = 0.0
    score_done = 0.0

    for summary in history.job_summaries():
        job_id = summary["job_id"]
        job = jobs.get(job_id)
        name = (job or {}).get("name") or summary["job_name"] or job_id
        seen.add(job_id)
        cron = cron_state(job, running)
        active = cron["status"] != "finished"
        runs = history.runs_for_job(job_id, only_usable=True)
        a = analyze.analyze_runs(runs, analyze.job_prompt(job_id))
        projection = lifecycle.analysis_projection(a)
        per_week = _capped_per_week(_schedule_runs_per_week(job) or _runs_per_week(runs), cron)
        per_run_saving = None
        if a.avg_cost_usd is not None and projection["cost_after_usd"] is not None:
            per_run_saving = max(0.0, a.avg_cost_usd - projection["cost_after_usd"])

        fetch_steps = sum(1 for s in a.steps if s.kind in ("fixed", "fill-in", "varies", "frozen"))
        card = {
            "fetch_steps": fetch_steps,
            "cron": cron,
            "strategy": a.strategy,
            "job_id": job_id,
            "name": name,
            "runs": a.runs_used,
            "steps": len(a.steps),
            "scriptable_steps": sum(1 for s in a.steps if s.kind in ("fixed", "fill-in", "varies")),
            "cost_now": a.avg_cost_usd,
            "cost_after": projection["cost_after_usd"],
            "projection": projection,
            "agent_free": a.agent_free,
            "reason": a.reason,
            "skip_fit": starter_script.fit(job_id, runs),
            "skip_result": starter_script.since_attached(job_id, store.get_setting("native_cron:" + job_id) or {}),
        }

        routine = routines.get(job_id)
        meta = (routine or {}).get("meta") or {}
        if routine:
            card["fetch_steps"] = routine.get("steps") or card["fetch_steps"]  # list_routines gives the step count
        is_live = (routine or {}).get("status") == "live"
        promoted_at = meta.get("promoted_at") if is_live else None
        cutoff = (meta.get("first_promoted_at") or promoted_at) if is_live else None
        before = [r for r in runs if not cutoff or r.started_ts < cutoff]
        after = _live_runs(job_id, meta) if is_live else None
        card.update(_before_after(before, after))
        card["timeline"] = _timeline(before, after or [])
        tune = tuning_by_id.get(job_id)
        tuned = tuning.measured(job_id, tune) if tune and not is_live else None
        tuned_saving = 0.0
        tuned_runs = None
        if tuned:
            tuned_runs = tuned.pop("run_list")
            tuned_saving = tuned["saved"] or 0.0
            rate = tuned["before"] - tuned["after"]
            tuned["per_week"] = (rate * per_week if per_week is not None and tuned["metric"] in ("cost", "api cost")
                                 and tune["status"] != "reverted" and tuned["runs"] >= MIN_RUNS_FOR_RATE else None)
            card["tuned"] = tuned
            if tuned["metric"] == "api cost":
                saved_api += tuned_saving
                api_week += tuned["per_week"] or 0.0
            else:
                saved += tuned_saving
        promoted = (promoted_at or 0)
        earlier = [r for r in history.runs_for_job(job_id)
                   if r.scheduled and r.finished and not (is_live and r.started_ts >= promoted)]
        everything = earlier + (after or [])
        if tuned and tuned["metric"] == "cost":
            tuned_ids = {r.execution_id for r in tuned_runs}
            rows = period_rows(everything, tuned["before"], [r for r in everything if r.execution_id in tuned_ids])
        else:
            rows = period_rows(everything, (card["before"].get("cost_usd") or meta.get("baseline_cost")) if is_live else None, after)
        all_rows.append(rows)
        card["periods"] = visible_periods(rows)
        if is_live:
            card["mode"] = meta.get("mode") or "tool"
            card["when_unchanged"] = prerun.unchanged_action(routine_full(routine))
            fit, why = analyze.no_ai_fit(before)
            card["no_ai_fit"], card["no_ai_why"] = fit, why
        weight = a.avg_cost_usd or max([r.api_cost_usd or 0.0 for r in runs[-8:]] or [0.0]) or 1e-6
        if active:
            active_weights.append(weight)
        if active and tuned and not is_live:
            score_weight += weight
            score_done += weight * (1.0 if tune["status"] == "kept" else 0.5)
        elif active and not routine:
            score_weight += weight
        week_saving = per_run_saving * per_week if per_run_saving is not None and per_week is not None else None
        card["saving_per_week"] = week_saving
        card["runs_per_week"] = per_week
        if not active:  # nothing it can do now, so no promises and no buttons: only what it saved
            job_saved = 0.0
            plan_saved = 0.0
            if is_live:
                real_view = _saving_view(job_id, cutoff, after or [], "cost_usd")
                job_saved = _saved_since(real_view["runs"], real_view["baseline"])
                saved += job_saved
                list_view = _saving_view(job_id, cutoff, after or [], "api_cost_usd")
                plan_saved = _plan_saved(list_view["runs"], list_view["baseline"])
                saved_api += plan_saved
            if tuned and tuned["metric"] == "api cost":
                plan_saved += tuned_saving
            card["saving_per_week"] = 0.0
            real = job_saved + (tuned_saving if tuned and tuned["metric"] != "api cost" else 0.0)
            finished.append({**card, "saved_usd": real, "plan_saved_usd": plan_saved or None, "was_live": is_live or bool(tuned)})
        elif routine and routine["status"] == "live":
            score_weight += weight
            score_done += weight
            real_view = _saving_view(job_id, cutoff, after or [], "cost_usd")
            job_saved = _saved_since(real_view["runs"], real_view["baseline"])
            saved += job_saved
            card["excluded_settings_runs"] = real_view["excluded_settings_runs"]
            card["unknown_cost_runs"] = real_view["unknown_cost_runs"]
            list_view = _saving_view(job_id, cutoff, after or [], "api_cost_usd")
            if list_view["baseline"] is not None and list_view["average"] is not None:
                plan_saved = _plan_saved(list_view["runs"], list_view["baseline"])
                saved_api += plan_saved
                rate = (list_view["rate_per_run"] or 0.0) * (per_week or 0.0)
                api_week += rate
                card["plan"] = {"before": list_view["baseline"], "after": list_view["average"], "saved": plan_saved, "per_week": rate,
                                "included": any(r.included for r in after or [])}
            card["saving_per_week"] = (real_view["rate_per_run"] * per_week
                                       if real_view["rate_per_run"] is not None and per_week else None)
            free_share = (af or {}).get("skipped", 0) / af["runs"] if af and af.get("runs") else 0.0
            if af and af.get("runs", 0) >= 4 and free_share >= 0.5:
                card["schedule_hint"] = (f"Nothing changed on {af['skipped']} of its last {af['runs']} runs, "
                                         "so it could run less often.")
            live.append({**card, "routine_id": routine["id"], "saved_usd": job_saved,
                         "live_runs": meta.get("live_runs", 0), "failures": meta.get("failures", 0)})
        elif routine and routine["status"] == "testing":
            needed = meta.get("needed", 3) or 3
            score_weight += weight
            score_done += weight * 0.5 * min(1.0, meta.get("matches", 0) / needed)
            could_save_week += week_saving or 0.0
            tests = meta.get("tests") or []
            testing.append({**card, "routine_id": routine["id"], "matches": meta.get("matches", 0),
                            "needed": meta.get("needed", 3), "ready": bool(meta.get("ready")),
                            "last_test": tests[-1] if tests else None})
        elif a.ready:
            spotted.append(card)
            could_save_week += week_saving or 0.0
        else:
            watching.append({**card, "reason": f"Routine: {a.reason}"})

    for job_id, job in jobs.items():  # scheduled, but no runs recorded yet
        if job_id in seen or job.get("state") == "completed":
            continue
        cron = cron_state(job, running)
        if not cron["no_ai"]:
            waiting_jobs += 1
        watching.append({"job_id": job_id, "name": job.get("name") or job_id, "cron": cron, "reason": (
            "Runs with no AI, so there's nothing to save." if cron["no_ai"] else "Waiting for its first runs.")})

    # A job with no runs yet still counts: it has simply saved nothing so far.
    if waiting_jobs and active_weights:
        score_weight += waiting_jobs * statistics.median(active_weights)
    score = round(100 * score_done / score_weight) if score_weight > 0 else None

    quick = tuning.cards()
    tune_by_id = {c["job_id"]: c for c in quick}
    routine_rows = {job_id: routine_full(row) for job_id, row in routines.items()}
    for card in spotted + testing + live + watching:
        job_id = card["job_id"]
        card["tune"] = tune_by_id.get(job_id)
        job = jobs.get(job_id) or {}
        route = tuning.modelinfo.route(job)
        recent = [r for r in history.runs_for_job(job_id) if r.scheduled and r.finished][-8:]
        card["cost_parts"] = cost_parts(recent, tuning.modelinfo.token_prices(route.get("model"), route.get("provider")))
        card["ladder"] = ladder(routine_rows.get(job_id), tuning_by_id.get(job_id), card,
                                routine_possible=card in spotted, can_tune=bool((card["tune"] or {}).get("can_try")))
        # Capability facts reach every control, including a newly spotted job
        # with no saved routine. Missing SDK guards must never look executable.
        candidate = routine_rows.get(job_id) or {"source_ref": job.get("id"), "status": "spotted", "meta": {}}
        card["promotion_availability"] = lifecycle.promotion_availability(candidate)
    for card in finished:
        card["promotion_availability"] = {"try_available": False, "available": False,
            "automatic_replay_available": False, "reason": "This job has finished.", "modes": {}}

    live_week = sum(x.get("saving_per_week") or 0.0 for x in live)
    live_week += sum((c.get("tuned") or {}).get("per_week") or 0.0
                     for c in spotted + testing + watching if (c.get("tuned") or {}).get("metric") == "cost")
    # Historical counterfactuals can mix model/reasoning changes. Only receipt
    # cohorts with complete equivalent-settings evidence feed qualified totals.
    from . import receipts
    proof_jobs = {t.get("job_id") for t in store.list_tuning() if t.get("job_id")}
    proof_jobs.update(r.get("source_ref") for r in store.list_routines() if r.get("source") == "cron" and r.get("source_ref"))
    proofs = [receipts.receipt(job_id) for job_id in sorted(proof_jobs)]
    gross = [p["qualified_efficiency_savings_usd"] for p in proofs if p.get("qualified_efficiency_savings_usd") is not None]
    net = [p["qualified_net_payback_usd"] for p in proofs if p.get("qualified_net_payback_usd") is not None]
    return {
        "qualified_efficiency_savings_usd": sum(gross) if gross else None,
        "qualified_net_payback_usd": sum(net) if net else None,
        "qualified_jobs": len(gross),
        "efficiency_proof_scope": "Comparable recorded cohorts with the same model/provider/reasoning; structural quality checks do not establish semantic equivalence.",
        "live_saving_usd_per_week": live_week,
        "session_id": session_id,
        "chat_enabled": store.chat_enabled(session_id),
        "chat": chat.analyse(session_id) if store.chat_enabled(session_id) else None,
        "chat_spent_usd": _chat_spent(session_id),
        "score": score,
        "saved_usd": saved,
        "saved_api_usd": saved_api or None,
        "api_saving_usd_per_week": api_week or None,
        "could_save_usd_per_week": could_save_week if lifecycle._replay_available() else None,
        "spotted": spotted,
        "testing": testing,
        "live": live,
        "watching": watching,
        "finished": finished,
        "quick": quick,
        "trial_runs": tuning.TRIAL_RUNS,
        "lower_reasoning": tuning.lowers_reasoning(),
        "periods": visible_periods(total_periods(all_rows)) if all_rows else [],
    }
