"""Make any AI cron job cheaper, on any cloud model, with or without a routine.

Cost is roughly prompt size x AI calls, plus reasoning. A trial changes all of it at once:
  - Instruction. A short line asks the AI to use only the tools the task needs, once each.
    It often cuts the number of AI calls, the biggest multiplier of all.
  - Tools. Every model pays to read every tool definition on every call. The job starts
    with only the toolsets its cheapest good run used.
  - Results. Tool results are cleaned before the AI reads them (see prerun.clean_result),
    so no later call pays to re-read markup and API link fields.
  - Reasoning. The weakest level the job's model takes, asked of Hermes (see modelinfo.py).
  - New items only. A live routine sends what changed plus its last answer to update.

  start    Apply the first stage, remembering what the job had before.
  refresh  After TRIAL_RUNS answers, compare with earlier runs. Kept if the job still does
           what it did, the answers hold up and it got cheaper. If the answers suffer, the
           next stage gets a turn: every tool the job has ever used, then more reasoning. If
           nothing works, or it wasn't cheaper, the old settings go back by themselves.
  undo     Put the old settings back from the terminal.

The comparison is a set of plain checks (see `evaluate`). It catches broken, empty or
much thinner answers and regular actions that stopped happening. It can't catch subtle
mistakes, or an action a job only takes now and then, so `hermes crystal tune <job> --status`
shows an answer from before and after to read for yourself.
"""

from __future__ import annotations

import logging
import re
import statistics
import time
from typing import Any, Optional

from . import analyze, history, lifecycle, modelinfo, store

logger = logging.getLogger(__name__)

TRIAL_RUNS = 2
MAX_TRIAL_RUNS = 6  # a job that had always answered gets this long to say something again
CLOSE_CALL = 0.15   # a result within this of "no saving" gets more runs before it's judged
EFFICIENCY_HINT = ("\n\nWork efficiently: use only the tools this task needs, once each. Don't load skills, "
                   "re-check results or research beyond what the task asks. Still do every action it asks "
                   "for, on every run. Then answer.")
MIN_BEFORE_RUNS = 2
BEFORE_WINDOW = 8
MIN_LENGTH_RATIO = 0.5
MIN_LINES_RATIO = 0.6
MIN_COST_DROP = 0.1  # below 10% cheaper isn't worth a lower level
_URL = re.compile(r"https?://")
_SILENT = "[SILENT]"
_FAILURE = "[CRON_FAILURE]"


def _mid(values: list[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def counted_runs(before: list[history.Run], trial: list[history.Run], answers: dict[str, str]) -> list[history.Run]:
    """The trial runs to judge. "Nothing new" can be a fair answer, so for a job that had
    always answered, those runs neither pass nor fail: it waits for real answers."""
    said = lambda r: answers.get(r.session_id or "", "").strip()  # noqa: E731
    if any(said(r) == _SILENT for r in before):
        return trial
    return [r for r in trial if said(r) != _SILENT]


def _measure(before: list[history.Run], trial: list[history.Run]) -> tuple[str, Optional[float], Optional[float]]:
    """What to compare: real cost if both sides have one. A subscription run costs $0 and some
    routes have no price, so those compare the cost at listed API prices. A model on no price
    list compares all the tokens it used, since prompt tokens are usually most of the saving,
    then time."""
    fields = (("cost", lambda r: r.cost_usd), ("api cost", lambda r: r.api_cost_usd),
              ("tokens", lambda r: r.total_tokens), ("seconds", lambda r: r.duration_s))
    for name, field in fields:
        old = _mid([field(r) for r in before if field(r)])
        new = _mid([field(r) for r in trial if field(r)])
        if old and new:
            return name, old, new
    return "nothing", None, None


def _amount(metric: Optional[str], value: Optional[float]) -> str:
    if value is None:
        return "?"
    return {"cost": f"${value:.4f}", "api cost": f"${value:.4f}", "tokens": f"{value:,.0f} tokens", "seconds": f"{value:.0f}s"}.get(metric or "", str(value))


def evaluate(before: list[history.Run], trial: list[history.Run],
             answers: dict[str, str]) -> dict[str, Any]:
    """Do the trial runs' answers hold up against earlier runs, and did the cost fall?"""
    def texts(runs):
        return [answers.get(r.session_id or "", "").strip() for r in runs]

    old, new = texts(before), texts(trial)
    metric, old_value, new_value = _measure(before, trial)
    result: dict[str, Any] = {
        "ok": False, "metric": metric, "before": old_value, "after": new_value,
        "reasoning_before": _mid([r.reasoning_tokens for r in before if r.reasoning_tokens is not None]),
        "reasoning_after": _mid([r.reasoning_tokens for r in trial if r.reasoning_tokens is not None]),
    }

    def fail(why: str, kind: str = "quality") -> dict[str, Any]:
        return {**result, "why": why, "kind": kind}

    if any(r.outcome not in (None, "completed") for r in trial):
        return fail("a run with these settings failed")
    if any(not t or t.startswith(_FAILURE) for t in new):
        return fail("a run with these settings gave no usable answer")
    lost = {t for t in analyze.regular_actions(before) if not all(t in analyze.run_actions(r) for r in trial)}
    if lost:
        return fail(f"it stopped doing something it normally does ({', '.join(sorted(lost))})")
    real_old = [t for t in old if t and t != _SILENT]
    real_new = [t for t in new if t != _SILENT]
    if real_old and real_new:
        if _mid([len(t) for t in real_new]) < MIN_LENGTH_RATIO * _mid([len(t) for t in real_old]):
            return fail("its answers got much shorter")
        if _mid([_lines(t) for t in real_new]) < MIN_LINES_RATIO * _mid([_lines(t) for t in real_old]):
            return fail("its answers lost items")
        linked = sum(1 for t in real_old if _URL.search(t))
        if linked * 2 > len(real_old) and not all(_URL.search(t) for t in real_new):
            return fail("its answers stopped including links")
    if old_value is None or new_value is None:
        return fail("there were no numbers to compare", "saving")
    if new_value > old_value * (1 - MIN_COST_DROP):
        return fail(f"it wasn't cheaper ({_amount(metric, new_value)} vs {_amount(metric, old_value)} a run)", "saving")
    return {**result, "ok": True, "why": "answers held up and it got cheaper"}


# ---------------------------------------------------------------- runs

def _finished_scheduled(name: str) -> list[history.Run]:
    # A run's session closes just before Hermes records its outcome. Until then it isn't
    # finished, and judging it would count a good run as failed.
    return [r for r in history.load_runs(name)
            if r.scheduled and r.finished and r.session_started is not None and r.outcome not in ("running", "claimed")]


def _split(name: str, started_at: float) -> tuple[list[history.Run], list[history.Run]]:
    runs = _finished_scheduled(name)
    before = [r for r in runs if r.session_started < started_at and r.outcome in (None, "completed")]
    trial = [r for r in runs if r.session_started >= started_at]
    if len(before) < BEFORE_WINDOW:  # older runs from Hermes's own records
        found = lifecycle.cron_job(name)
        known = {r.session_id for r in runs}
        older = [r for r in history.ledger_runs((found or {}).get("id"))
                 if r.session_id not in known and r.session_started and r.session_started < started_at]
        before = sorted(older + before, key=lambda r: r.session_started)
    return before[-BEFORE_WINDOW:], trial


def _job_or_error(job: str) -> dict[str, Any]:
    found = lifecycle.cron_job(job)
    if not found:
        raise lifecycle.LifecycleError(f"No cron job called {job!r}.")
    return found


# ---------------------------------------------------------------- actions

def status(name: str) -> Optional[dict[str, Any]]:
    return store.get_tuning(name)


def _score(run: history.Run) -> float:
    return run.cost_usd or run.api_cost_usd or float(run.total_tokens or 0)


def _tool_choices(found: dict[str, Any], name: str) -> tuple[Optional[list[str]], Optional[list[str]]]:
    """(narrow, wide) toolsets: what the job's cheapest good run used, and all it has ever used.
    The cheapest run proves those tools were enough. None means leave the tools alone."""
    runs = [r for r in history.load_runs(name) if r.scheduled and r.steps]
    if not runs:
        return None, None
    used = {s.tool_name for r in runs for s in r.steps}
    fixed: set[str] = set(analyze.regular_actions(runs))
    routine = store.routine_for_job(found["id"])
    for step in ((routine or {}).get("steps") or {}).get("steps", []) if routine else []:
        fixed.add(step["tool"])  # needed for the full task if the routine fails
    good = [r for r in runs if r.outcome in (None, "completed") and r.finished]
    answers = history.final_answers([r.session_id for r in good if r.session_id])
    good = [r for r in good if answers.get(r.session_id or "", "").strip()]
    cheapest = {s.tool_name for s in min(good, key=_score).steps} if good else used
    wide = modelinfo.toolsets_for(used | fixed)
    narrow = modelinfo.toolsets_for(cheapest | fixed)
    if wide is None:
        return None, None
    attached = found.get("skills") or found.get("skill")
    if narrow is not None and not attached and "skills" in narrow:
        narrow = [t for t in narrow if t != "skills"]  # browsing skills costs calls; it's restored if missed
    current = found.get("enabled_toolsets")
    if current is not None:
        if set(wide) - set(current):
            return None, None  # it used something its own list no longer allows: leave that alone
        if len(wide) >= len(current) and (narrow is None or len(narrow) >= len(current)):
            return None, None
    return (narrow if narrow is not None else wide), wide


def plan_stages(levels: list[str], narrow: Optional[list[str]], wide: Optional[list[str]]) -> list[dict[str, Any]]:
    """The settings to try in order, cheapest first, each one giving a little more back."""
    first = levels[0] if levels else None
    stages = [{"level": first, "toolsets": narrow}]
    if narrow != wide:
        stages.append({"level": first, "toolsets": wide})
    stages += [{"level": level, "toolsets": wide} for level in levels[1:]]
    return stages


def _apply_stage(job_id: str, stage: dict[str, Any]) -> None:
    if stage.get("level"):
        lifecycle._edit_job(job_id, "--reasoning-effort", stage["level"])
    if stage.get("toolsets") is not None:
        lifecycle.set_toolsets(job_id, stage["toolsets"])


def _live_routine(job_id: str) -> Optional[dict[str, Any]]:
    routine = store.routine_for_job(job_id)
    return routine if routine and routine["status"] == "live" else None


def _set_new_only(job_id: str, on: bool) -> bool:
    """Send a live routine's new lines only. A job that acts on its data needs all of it."""
    routine = _live_routine(job_id)
    steps = (routine or {}).get("steps")
    if not routine or (isinstance(steps, dict) and steps.get("leaves_to_ai")) or (routine.get("meta") or {}).get("mode") == "no_ai":
        return False
    meta = dict(routine.get("meta") or {})
    meta["new_only"] = on
    store.update_routine(routine["id"], meta=meta)
    return True


def start(job: str, level: Optional[str] = None) -> dict[str, Any]:
    """Start a trial. With a level, reasoning tries only that one."""
    found = _job_or_error(job)
    name = found.get("name") or found["id"]
    if found.get("no_agent"):
        raise lifecycle.LifecycleError(f"{name} runs with no AI, so it's already free.")
    if found.get("state") == "completed":
        raise lifecycle.LifecycleError(f"{name} has finished.")
    current = store.get_tuning(name)
    upgrade = bool(current and current["status"] == "kept" and not current.get("clean"))
    if current and current["status"] in ("trial", "kept") and not upgrade:
        raise lifecycle.LifecycleError(f"{name} already has cheaper settings ({status_text(current)}).")
    now = time.time()
    before, _ = _split(name, now)
    if len(before) < MIN_BEFORE_RUNS:
        raise lifecycle.LifecycleError(
            f"{name} needs {MIN_BEFORE_RUNS} finished runs to compare with first (has {len(before)}).")
    levels = [level] if level else modelinfo.levels_to_try(found)
    if levels and found.get("reasoning_effort") == levels[0]:
        levels = levels[1:]
    narrow, wide = _tool_choices(found, name)
    stages = plan_stages(levels, narrow, wide)
    if upgrade:  # kept settings from before these savings existed: keep them unless narrower is known
        stages = [{"level": stage["level"] or current.get("level"),
                   "toolsets": stage["toolsets"] if stage["toolsets"] is not None else current.get("toolsets")}
                  for stage in stages]
    _apply_stage(found["id"], stages[0])
    prompt = found.get("prompt") or ""
    if EFFICIENCY_HINT not in prompt:
        lifecycle._edit_job(found["id"], "--prompt", prompt + EFFICIENCY_HINT)
    data = {"job_name": name, "job_id": found["id"], "status": "trial", **stages[0], "stages": stages[1:],
            "previous": found.get("reasoning_effort"), "started_at": now, "first_started_at": now,
            "previous_toolsets": found.get("enabled_toolsets"), "clean": True, "hint": True,
            "new_only": _set_new_only(found["id"], True)}
    if upgrade:
        # Judged against its kept settings, and put back to them if it doesn't help.
        data.update({"baseline_from": current.get("first_started_at") or current["started_at"],
                     "first_started_at": current.get("first_started_at") or current["started_at"]})
    store.save_tuning(name, data)
    return data


def start_all() -> dict[str, Any]:
    """Start a trial on every job that can have one. One job's problem doesn't stop the rest."""
    started, skipped = [], {}
    for job in _all_jobs():
        name = job.get("name") or job.get("id")
        try:
            start(name)
            started.append(name)
        except lifecycle.LifecycleError as exc:
            skipped[name] = str(exc)
    return {"started": started, "skipped": skipped}


def cleans_results(name: str) -> bool:
    t = store.get_tuning(name)
    return bool(t and t.get("clean") and t["status"] in ("trial", "kept"))


def _restore(t: dict[str, Any]) -> None:
    found = lifecycle.cron_job(t["job_name"])
    if not found:  # a job that has finished for good has nothing to restore
        return
    if t.get("level"):
        lifecycle._edit_job(found["id"], "--reasoning-effort", t.get("previous") or "")
    if t.get("toolsets") is not None:
        lifecycle.set_toolsets(found["id"], t.get("previous_toolsets"))
    if t.get("hint") and EFFICIENCY_HINT in (found.get("prompt") or ""):
        lifecycle._edit_job(found["id"], "--prompt", found["prompt"].replace(EFFICIENCY_HINT, ""))
    if t.get("new_only"):
        _set_new_only(found["id"], False)


def undo(job: str) -> dict[str, Any]:
    found = lifecycle.cron_job(job)
    name = (found or {}).get("name") or job
    t = store.get_tuning(name)
    if not t or t["status"] not in ("trial", "kept"):
        raise lifecycle.LifecycleError(f"{name} has no cheaper settings to undo.")
    _restore(t)
    t.update({"status": "undone", "finished_at": time.time()})
    store.save_tuning(name, t)
    return t


def refresh() -> None:
    """Decide any trial that has enough runs. Never raises: the panel calls this on every load."""
    try:
        for t in store.list_tuning():
            if t["status"] == "trial":
                _decide(t)
    except Exception:  # noqa: BLE001
        logger.warning("crystallize: checking reasoning trials failed", exc_info=True)


def _is_close(result: dict[str, Any]) -> bool:
    old, new = result.get("before"), result.get("after")
    return result.get("kind") == "saving" and bool(old and new) and new <= old * (1 + CLOSE_CALL)


def _decide(t: dict[str, Any]) -> None:
    before, trial = _split(t["job_name"], t["started_at"])
    if t.get("baseline_from"):
        before = [r for r in before if (r.session_started or 0) >= t["baseline_from"]]
    answers = history.final_answers([r.session_id for r in before + trial if r.session_id])
    counted = counted_runs(before, trial, answers)
    alive = lifecycle.cron_job(t["job_name"]) is not None and lifecycle.cron_job(t["job_name"]).get("state") != "completed"
    if len(counted) >= TRIAL_RUNS:
        result = evaluate(before, counted, answers)
        if _is_close(result) and alive and len(counted) < MAX_TRIAL_RUNS:
            return  # one odd run can decide a close call, so it gets more runs
    elif len(trial) >= MAX_TRIAL_RUNS:
        result = {"ok": False, "why": f"it said nothing to report on {len(trial) - len(counted)} runs in a row"}
    elif not alive:
        result = {"ok": False, "why": "the job finished before there were enough runs to compare"}
    else:
        return
    # Weak answers may recover with more tools or reasoning. "Not cheaper" won't.
    if not result["ok"] and result.get("kind") == "quality" and t.get("stages") and alive:
        stage, *rest = t["stages"]
        try:
            _apply_stage(t["job_id"], stage)
        except lifecycle.LifecycleError:
            logger.warning("crystallize: couldn't change %s", t["job_name"], exc_info=True)
            return
        t.update({**stage, "stages": rest, "started_at": time.time(), "last_setback": result["why"]})
        store.save_tuning(t["job_name"], t)
        return
    if not result["ok"]:
        try:
            _restore(t)
        except lifecycle.LifecycleError:
            logger.warning("crystallize: couldn't restore %s", t["job_name"], exc_info=True)
            return  # still in trial, so it's tried again next time
    t.update({"status": "kept" if result["ok"] else "reverted", "result": result, "finished_at": time.time()})
    store.save_tuning(t["job_name"], t)


# ---------------------------------------------------------------- what to show

def measured(name: str, t: dict[str, Any]) -> Optional[dict[str, Any]]:
    """What a job's cheaper settings have done so far, from its real runs."""
    since = t.get("first_started_at") or t.get("started_at")
    before, runs = _split(name, since)
    runs = [r for r in runs if r.outcome in (None, "completed")]
    if not before or not runs or t["status"] not in ("trial", "kept"):
        return None
    metric, old, new = _measure(before, runs)
    if old is None or new is None:
        return None
    field = {"cost": lambda r: r.cost_usd, "api cost": lambda r: r.api_cost_usd}.get(metric)
    saved = sum(max(0.0, old - field(r)) for r in runs if field(r) is not None) if field else None
    return {"metric": metric, "before": old, "after": new, "runs": len(runs), "saved": saved, "run_list": runs}


def _settings(t: dict[str, Any]) -> str:
    parts = [f"reasoning '{t['level']}'"] if t.get("level") else []
    if t.get("toolsets") is not None:
        parts.append("tools: " + (", ".join(t["toolsets"]) or "none"))
    if t.get("clean"):
        parts.append("cleaned results")
    if t.get("hint"):
        parts.append("efficiency instruction")
    if t.get("new_only"):
        parts.append("new items only")
    return " + ".join(parts) or "current settings"


def status_text(t: Optional[dict[str, Any]]) -> str:
    if not t:
        return "Not tried yet"
    res = t.get("result") or {}
    if t["status"] == "trial":
        before, trial = _split(t["job_name"], t["started_at"])
        answers = history.final_answers([r.session_id for r in before + trial if r.session_id])
        done = len(counted_runs(before, trial, answers))
        return f"Trying {_settings(t)}: {min(done, TRIAL_RUNS)} of {TRIAL_RUNS} answers so far"
    if t["status"] == "kept":
        metric = res.get("metric", "cost")
        where = " at API prices" if metric == "api cost" else ""
        return f"Kept {_settings(t)}: {_amount(metric, res.get('before', res.get('cost_before')))} → {_amount(metric, res.get('after', res.get('cost_after')))} a run{where}"
    if t["status"] == "reverted":
        return f"Went back to the old setting: {res.get('why', 'the check failed')}"
    return "You put the old setting back"


def sample_answers(name: str, t: dict[str, Any]) -> dict[str, str]:
    """The latest answer from before and from the lower level, to read side by side."""
    before, trial = _split(name, t["started_at"])
    ids = {"before": before[-1].session_id if before else None, "after": trial[-1].session_id if trial else None}
    answers = history.final_answers([s for s in ids.values() if s])
    return {k: answers.get(v or "", "") for k, v in ids.items()}


def _all_jobs() -> list[dict[str, Any]]:
    return [j for j in lifecycle.all_cron_jobs() if j.get("state") != "completed"]  # finished for good


def job_settings(job: dict[str, Any]) -> dict[str, Any]:
    """What a job is set to right now, as Hermes has it."""
    return {"model": modelinfo.route(job).get("model"), "reasoning": modelinfo.current_level(job),
            "toolsets": job.get("enabled_toolsets")}


def cards() -> list[dict[str, Any]]:
    """One line per AI cron job, biggest cost first. Includes jobs with no routine."""
    all_runs = _finished_scheduled_all()
    out = []
    for job in _all_jobs():
        if job.get("no_agent"):
            continue
        name = job.get("name") or job.get("id")
        t = store.get_tuning(name)
        runs = all_runs.get(name, [])
        out.append({
            "name": name,
            "job_id": job.get("id"),
            "effort": job.get("reasoning_effort"),
            "settings": job_settings(job),
            "runs": len(runs),
            "cost_now": _mid([r.cost_usd for r in runs[-BEFORE_WINDOW:] if r.cost_usd is not None]),
            "api_cost_now": _mid([r.api_cost_usd for r in runs[-BEFORE_WINDOW:] if r.api_cost_usd is not None]),
            "included_now": any(r.included for r in runs[-BEFORE_WINDOW:]),
            "tokens_now": _mid([r.total_tokens for r in runs[-BEFORE_WINDOW:] if r.total_tokens is not None]),
            "reasoning_share": _reasoning_share(runs[-BEFORE_WINDOW:], job),
            "status": t["status"] if t else None,
            "status_text": status_text(t),
            "can_try": (not t or t["status"] in ("reverted", "undone") or (t["status"] == "kept" and not t.get("clean"))) and len(_split(name, time.time())[0]) >= MIN_BEFORE_RUNS,
            "cleaned_chars": store.get_cleaned(name) or None,
            "can_undo": bool(t and t["status"] in ("trial", "kept")),
        })
    out.sort(key=lambda c: c["cost_now"] or 0.0, reverse=True)
    return out


def _finished_scheduled_all() -> dict[str, list[history.Run]]:
    grouped: dict[str, list[history.Run]] = {}
    for r in history.load_runs():
        if r.scheduled and r.finished:
            grouped.setdefault(r.job_name or r.job_id or "?", []).append(r)
    return grouped


def _reasoning_share(runs: list[history.Run], job: dict[str, Any]) -> Optional[float]:
    """Roughly how much of these runs' cost was reasoning, at Hermes's price for the model."""
    price = modelinfo.output_price(modelinfo.route(job))
    paid = [r for r in runs if r.cost_usd and r.reasoning_tokens is not None]
    if price and paid:
        return min(1.0, sum(r.reasoning_tokens * price for r in paid) / sum(r.cost_usd for r in paid))
    # Included in a subscription: use the share of what it would cost at API prices.
    r = modelinfo.route(job)
    included = [run for run in runs if run.api_cost_usd and run.reasoning_tokens is not None]
    if not included:
        return None
    reasoning = sum(modelinfo.api_cost(r.get("model"), r.get("provider"), {"output": run.reasoning_tokens}) or 0.0
                    for run in included)
    return min(1.0, reasoning / sum(run.api_cost_usd for run in included))
