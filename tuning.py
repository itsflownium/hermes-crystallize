"""Reduce repeated Cron input while preserving its model and reasoning settings.

Trials compare only the same recorded model, provider and reasoning configuration.
Complete observations set advisory presentation budgets and safe read-schema choices;
uncertain evidence always leaves full output and original host tool definitions.
Observable contracts and actions remain required; structural checks are not a proof
of factual accuracy or semantic equivalence.
"""

from __future__ import annotations

import logging
import math
import re
import statistics
import time
from typing import Any, Optional

from . import adaptive, analyze, contracts, history, lifecycle, modelinfo, store

logger = logging.getLogger(__name__)

TRIAL_RUNS = 2
MAX_TRIAL_RUNS = 6  # a job that had always answered gets this long to say something again
STALE_FAILURES = 3  # kept settings that fail this many runs in a row go back by themselves
LEGACY_EFFICIENCY_HINT = ("\n\nWork efficiently: use only the tools this task needs, once each. Don't load skills, "
                   "re-check results or research beyond what the task asks. Still do every action it asks "
                   "for, on every run, and keep every detail it asks for, such as links. Then answer.")
EFFICIENCY_HINT = ("\n\nReuse complete, current evidence when it already answers this task. Use every tool, skill, "
                   "verification and research step needed for the best answer. Still do every action it asks "
                   "for, on every run, and keep every detail it asks for, such as links. Preserve the task's "
                   "quality, model and reasoning settings.")
MIN_BEFORE_RUNS = 2
BEFORE_WINDOW = 8
MIN_LENGTH_RATIO = 0.5
MIN_LINES_RATIO = 0.6
_URL = re.compile(r"https?://")
_SILENT = "[SILENT]"
STALE_START = 300           # a change still "starting" after this many seconds was cut short
_FAILURE = "[CRON_FAILURE]"


def _mid(values: list[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def counted_runs(before: list[history.Run], trial: list[history.Run], answers: dict[str, str]) -> list[history.Run]:
    """The trial runs to judge. "Nothing new" can be a fair answer, but silence says nothing about
    quality, so a job that has ever really answered waits for real answers."""
    said = lambda r: answers.get(r.session_id or "", "").strip()  # noqa: E731
    if not any(said(r) and not history.is_silent(said(r)) for r in before):
        return trial  # it has only ever stayed silent, so silence is all there is to compare
    return [r for r in trial if not history.is_silent(said(r))]


def _measure(before: list[history.Run], trial: list[history.Run]) -> tuple[str, Optional[float], Optional[float]]:
    """What to compare: real cost if both sides have one. A subscription run costs $0 and some
    routes have no price, so those compare the cost at listed API prices. A model on no price
    list compares all the tokens it used, since prompt tokens are usually most of the saving.
    Time isn't money, so a faster run alone is never a saving."""
    if not history.comparable_runs(before, trial):
        return "nothing", None, None
    fields = (("cost", lambda r: r.cost_usd), ("api cost", lambda r: r.api_cost_usd),
              ("tokens", lambda r: r.total_tokens))
    for name, field in fields:
        if name == "api cost" and any(getattr(r, "pricing_uncertain", False) for r in before + trial):
            continue  # aggregate session totals cannot establish request-tier savings
        if any(not isinstance(field(r), (int, float)) or isinstance(field(r), bool)
               or not math.isfinite(field(r)) or field(r) < 0 for r in before + trial):
            continue  # an unmeasured run must not disappear from a comparison
        old = _mid([field(r) for r in before if field(r) is not None])
        new = _mid([field(r) for r in trial if field(r) is not None])
        if old is not None and old > 0 and new is not None:
            return name, old, new
    return "nothing", None, None


def _measurements(runs: list[history.Run], metric: str) -> list[float]:
    field = {"cost": "cost_usd", "api cost": "api_cost_usd", "tokens": "total_tokens", "seconds": "duration_s"}.get(metric)
    return [getattr(run, field) for run in runs] if field and all(getattr(run, field) is not None for run in runs) else []


def _amount(metric: Optional[str], value: Optional[float]) -> str:
    if value is None:
        return "?"
    return {"cost": f"${value:.4f}", "api cost": f"${value:.4f}", "tokens": f"{value:,.0f} tokens", "seconds": f"{value:.0f}s"}.get(metric or "", str(value))


def _tokens_mislead(before: list[history.Run], trial: list[history.Run]) -> Optional[str]:
    """Output costs far more than cached input, so fewer tokens can still cost more."""
    def typical(runs: list[history.Run], field: str) -> Optional[float]:
        values = [getattr(r, field, None) for r in runs]
        return _mid(values) if values and all(isinstance(v, (int, float)) for v in values) else None

    for field, label in (("api_cost_usd", "its listed price"), ("output_tokens", "its output")):
        old, new = typical(before, field), typical(trial, field)
        if old is not None and new is not None and new > old:
            return f"it used fewer tokens overall, but {label} went up"
    return None


def evaluate(before: list[history.Run], trial: list[history.Run],
             answers: dict[str, str], required: Optional[list[str]] = None,
             contract: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Do the trial runs' answers hold up against earlier runs, and did the cost fall?"""
    def texts(runs):
        return [answers.get(r.session_id or "", "").strip() for r in runs]

    old, new = texts(before), texts(trial)
    metric, old_value, new_value = _measure(before, trial)
    result: dict[str, Any] = {
        "ok": False, "metric": metric, "before": old_value, "after": new_value,
        "reasoning_before": _mid([r.reasoning_tokens for r in before if r.reasoning_tokens is not None]),
        "reasoning_after": _mid([r.reasoning_tokens for r in trial if r.reasoning_tokens is not None]),
        "limitations": ["Length, item and configured outcome checks do not prove factual correctness or semantic equivalence."],
        "quality": {"contract_configured": contract is not None, "semantic_equivalence_proven": False},
    }

    def fail(why: str, kind: str = "quality") -> dict[str, Any]:
        return {**result, "why": why, "kind": kind,
                "quality": {**result["quality"], "ok": kind != "quality", "why": why if kind == "quality" else "structural checks passed"}}

    if any(r.outcome not in (None, "completed") for r in trial):
        return fail("a run with these settings failed")
    if any(not t or t.startswith(_FAILURE) for t in new):
        return fail("a run with these settings gave no usable answer")
    if contract is not None:
        reports = [{"execution_id": r.execution_id,
                    **contracts.evaluate(contract, answer=answers.get(r.session_id or ""), sources=r.steps,
                                         actions=r.steps)} for r in trial]
        result["contract_checks"] = reports
        result["quality"]["contract_checks"] = reports
        failed = next((r for r in reports if not r["ok"]), None)
        if failed:
            return fail("outcome contract: " + failed["why"])
    # What the job did before any trial stays required, even if a later baseline lost it.
    lost = {t for t in analyze.regular_actions(before) | set(required or []) if not all(t in analyze.run_actions(r) for r in trial)}
    if lost:
        return fail(f"it stopped doing something it normally does ({', '.join(sorted(lost))})")
    real_old = [t for t in old if t and not history.is_silent(t)]
    real_new = [t for t in new if not history.is_silent(t)]
    if real_old and real_new:
        if _mid([len(t) for t in real_new]) < MIN_LENGTH_RATIO * _mid([len(t) for t in real_old]):
            return fail("its answers got much shorter")
        if _mid([_lines(t) for t in real_new]) < MIN_LINES_RATIO * _mid([_lines(t) for t in real_old]):
            return fail("its answers lost items")
        linked = sum(1 for t in real_old if _URL.search(t))
        if linked * 2 > len(real_old) and not all(_URL.search(t) for t in real_new):
            return fail("its answers stopped including links")
    if old_value is None or new_value is None:
        if not history.comparable_runs(before, trial):
            return fail("model, provider and reasoning evidence must be known and identical to compare savings", "identity")
        return fail("there were no numbers to compare", "saving")
    if metric == "tokens":
        why = _tokens_mislead(before, trial)
        if why:
            return fail(why, "saving")
    uncertainty = adaptive.savings_evidence(_measurements(before, metric), _measurements(trial, metric))
    result["saving_evidence"] = uncertainty
    if not uncertainty.get("ok"):
        return fail(f"it wasn't cheaper beyond observed variation and sample uncertainty "
                    f"({_amount(metric, new_value)} vs {_amount(metric, old_value)} a run)", "saving")
    if metric == "tokens":
        return {**result, "ok": True, "why": "structural checks passed and it used fewer tokens; no price was available to show it got cheaper",
                "money_saving_shown": False, "quality": {**result["quality"], "ok": True, "why": "structural checks passed"}}
    return {**result, "ok": True, "why": "structural checks passed and it got cheaper",
            "quality": {**result["quality"], "ok": True, "why": "structural checks passed"}}


# ---------------------------------------------------------------- runs

def _finished_scheduled(name: str) -> list[history.Run]:
    # A run's session closes just before Hermes records its outcome. Until then it isn't
    # finished, and judging it would count a good run as failed.
    return [r for r in history.load_runs(name)
            if r.scheduled and r.finished and r.session_started is not None and r.outcome not in ("running", "claimed")]


def _split(name: str, started_at: float) -> tuple[list[history.Run], list[history.Run]]:
    found = lifecycle.cron_job(name)
    job_id = (found or {}).get("id") or name
    runs = [r for r in _finished_scheduled(job_id) if r.job_id == job_id]
    before = [r for r in runs if r.session_started < started_at and r.outcome in (None, "completed")]
    trial = [r for r in runs if r.session_started >= started_at]
    if len(before) < BEFORE_WINDOW:  # older runs from Hermes's own records
        known = {r.session_id for r in runs}
        older = [r for r in history.ledger_runs(job_id)
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
    found = lifecycle.cron_job(name)
    if found:
        return lifecycle._tuning_for_job(found)
    # A deleted job's saved record can still be inspected by ID, but a new job
    # reusing its name must never inherit the attachment.
    return next((t for t in store.list_tuning() if t.get("job_id") == name), None) or store.get_tuning(name)


def _snapshot(found: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    return lifecycle.configuration_fingerprint(found, store.routine_for_job(found["id"]), data)


def _save(name: str, data: dict[str, Any]) -> None:
    """Keep immutable campaign boundaries when the mutable control row changes."""
    from . import receipts

    store.save_tuning(data.get("job_id") or name, data)
    receipts.snapshot_tuning(data)


def _restore_pending(t: dict[str, Any], exc: Exception) -> None:
    """Keep recovery actionable when a legacy field has no public mutation API."""
    t["needs_restore"] = True
    t["restore_error"] = str(exc)
    toolsets = isinstance(exc, lifecycle.PublicToolsetRecovery)
    t["recovery"] = {"pending": True, "job_id": t.get("job_id"), "reason": str(exc),
                     "desired_toolsets": t.get("previous_toolsets"),
                     "instructions": ("Ask Hermes through its normal cronjob_manage controls to restore the saved enabled_toolsets. "
                                      "Then retry Undo; Crystallize will confirm the value without editing jobs.json."
                                      if toolsets else "Retry Undo after Hermes CLI edits become available. The recovery record is preserved.")}
    if toolsets and t.get("previous_toolsets") is not None:
        t["recovery"]["normal_host_tool_plan"] = {"tool": "cronjob_manage", "executed": False,
            "arguments": {"action": "update", "job_id": t.get("job_id"), "enabled_toolsets": t["previous_toolsets"]}}
    _save(t["job_name"], t)


def _clear_restore_pending(t: dict[str, Any]) -> None:
    t.pop("needs_restore", None)
    t.pop("restore_error", None)
    if t.get("recovery"):
        t["recovery"] = {**t["recovery"], "pending": False, "restored_at": time.time()}


def _tool_choices(found: dict[str, Any], name: str) -> tuple[Optional[list[str]], Optional[list[str]]]:
    """Compatibility planner retaining the union of every observed job branch."""
    runs = [r for r in history.runs_for_job(found["id"]) if r.scheduled and r.steps]
    if not runs:
        return None, None
    used = {s.tool_name for r in runs for s in r.steps}
    fixed: set[str] = set(analyze.regular_actions(runs))
    routine = store.routine_for_job(found["id"])
    for step in ((routine or {}).get("steps") or {}).get("steps", []) if routine else []:
        fixed.add(step["tool"])  # needed for the full task if the routine fails
    # An uncommon branch still belongs to the job. Cost never justifies losing it.
    cheapest = used
    wide = modelinfo.toolsets_for(used | fixed)
    narrow = modelinfo.toolsets_for(cheapest | fixed)
    if wide is None:
        return None, None
    current = found.get("enabled_toolsets")
    if current is not None:
        if set(wide) - set(current):
            return None, None  # it used something its own list no longer allows: leave that alone
        if len(wide) >= len(current) and (narrow is None or len(narrow) >= len(current)):
            return None, None
    return (narrow if narrow is not None else wide), wide


def plan_stages(levels: list[str], narrow: Optional[list[str]], wide: Optional[list[str]]) -> list[dict[str, Any]]:
    """Compatible stage shape; reasoning levels are deliberately ignored."""
    stages = [{"level": None, "toolsets": narrow}]
    if narrow != wide:
        stages.append({"level": None, "toolsets": wide})
    return stages


def _apply_stage(job_id: str, stage: dict[str, Any], t: Optional[dict[str, Any]] = None) -> None:
    if stage.get("level"):
        raise lifecycle.LifecycleError("Crystallize preserves reasoning; legacy reasoning stages must be restored first.")
    if stage.get("toolsets") is not None:
        lifecycle.set_toolsets(job_id, stage["toolsets"])
    elif t and t.get("tools_changed"):  # this stage gives all the tools back
        lifecycle.set_toolsets(job_id, t.get("previous_toolsets"))


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


def lowers_reasoning() -> bool:
    """Retained for API compatibility; stale settings cannot reduce capability."""
    return False


def start(job: str, level: Optional[str] = None) -> dict[str, Any]:
    """Start an input-efficiency trial at exactly the job's configured reasoning."""
    if level is not None:
        raise lifecycle.LifecycleError("Reasoning changes are disabled. Tune preserves your model and reasoning settings.")
    found = _job_or_error(job)
    name = found.get("name") or found["id"]
    if found.get("no_agent"):
        raise lifecycle.LifecycleError(f"{name} runs with no AI, so it's already free.")
    if found.get("state") == "completed":
        raise lifecycle.LifecycleError(f"{name} has finished.")
    current = lifecycle._tuning_for_job(found)
    if current and _migrate_legacy(current):
        found = _job_or_error(found["id"])
    if current:
        from . import receipts

        receipts.snapshot_tuning(current)
    upgrade = bool(current and current["status"] == "kept" and not current.get("clean"))
    if current and (current.get("needs_restore") or current["status"] in ("starting", "trial", "kept")) and not upgrade:
        raise lifecycle.LifecycleError(f"{name} already has cheaper settings ({status_text(current)}).")
    now = time.time()
    before, _ = _split(found["id"], now)
    if current and current.get("status") == "revalidation":
        before = [r for r in before if (r.session_started or r.started_ts) >= current.get("revalidation_at", now)]
    if len(before) < MIN_BEFORE_RUNS:
        raise lifecycle.LifecycleError(
            f"{name} needs {MIN_BEFORE_RUNS} finished runs to compare with first (has {len(before)}).")
    # Keep the host toolsets intact. Per-request pruning only hides unused
    # host-declared read schemas, retaining all action/unknown/recovery tools.
    stages = plan_stages([], None, None)
    if upgrade:  # a capability-preserving campaign can add the new presentation checks
        stages = [{"level": None,
                   "toolsets": stage["toolsets"] if stage["toolsets"] is not None else current.get("toolsets")}
                  for stage in stages]
    # What the job did before any trial stays required through every later trial.
    original = _split(found["id"], current.get("first_started_at") or current["started_at"])[0] if upgrade else before
    required = sorted(analyze.regular_actions(original) | set((current or {}).get("required_actions") or []))
    # Saved first, so a change that fails or a crash can always be undone.
    data = {"job_name": name, "job_id": found["id"], "status": "starting", **stages[0], "stages": stages[1:],
            "required_actions": required, "tools_changed": stages[0]["toolsets"] is not None,
            "previous": found.get("reasoning_effort"), "started_at": now, "first_started_at": now, "baseline_until": now,
            "previous_toolsets": found.get("enabled_toolsets"), "clean": True, "hint": True,
            "hint_added": EFFICIENCY_HINT not in (found.get("prompt") or ""), "new_only": False,
            "reasoning_preserved": True, "policy_version": 2, "schema_pruning": True,
            "presentation_budgets": adaptive.presentation_budgets(before),
            "baseline_run_ids": [r.execution_id for r in before]}
    if upgrade:
        # Judged against its kept settings, and put back to them if it doesn't help.
        data.update({"baseline_from": current.get("first_started_at") or current["started_at"],
                     "first_started_at": current.get("first_started_at") or current["started_at"]})
    data["config_snapshot"] = _snapshot(found, data)
    data["configuration_id"] = data["config_snapshot"]["fingerprint"]
    _save(name, data)
    try:
        _apply_stage(found["id"], stages[0])
        prompt = found.get("prompt") or ""
        if EFFICIENCY_HINT not in prompt:
            lifecycle._edit_job(found["id"], "--prompt", prompt + EFFICIENCY_HINT)
    except Exception as exc:
        _roll_back(name, data, current if upgrade else None)
        raise lifecycle.LifecycleError(f"Couldn't change {name}, so its old settings are back: {exc}") from exc
    data["status"] = "trial"
    _save(name, data)
    return data


def _roll_back(name: str, data: dict[str, Any], kept: Optional[dict[str, Any]]) -> None:
    """Put a job back after a failed change. If that fails too, it's tried again on the next check."""
    try:
        _restore(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("crystallize: couldn't restore %s yet", name, exc_info=True)
        _restore_pending(data, exc)
        return
    _save(name, kept or {**data, "status": "reverted", "finished_at": time.time(),
                                     "result": {"ok": False, "why": "its settings couldn't be changed"}})


def start_all() -> dict[str, Any]:
    """Start a trial on every job that can have one. One job's problem doesn't stop the rest."""
    started, skipped = [], {}
    for job in _all_jobs():
        name = job.get("name") or job.get("id")
        try:
            start(job["id"])
            started.append(name)
        except lifecycle.LifecycleError as exc:
            skipped[name] = str(exc)
    return {"started": started, "skipped": skipped}


def cleans_results(name: str) -> bool:
    t = status(name)
    return bool(t and t.get("clean") and not _legacy_campaign(t) and t["status"] in ("trial", "kept"))


def _legacy_campaign(t: dict[str, Any]) -> bool:
    return bool(t.get("level") or any(stage.get("level") for stage in t.get("stages") or [])
                or (t.get("policy_version", 0) < 2 and (t.get("toolsets") is not None or any(t.get(key) for key in
                    ("hint", "tools_changed", "clean", "new_only")))))


def _migrate_legacy(t: dict[str, Any]) -> bool:
    """Restore our installed legacy changes, without overwriting later user edits."""
    if t.get("status") not in {"starting", "trial", "kept"} or not _legacy_campaign(t):
        return False
    if t.get("status") == "kept":
        t.setdefault("kept_at", t.get("finished_at"))
        t.setdefault("trial_finished_at", t.get("finished_at"))
    t.setdefault("legacy_settings", {"level": t.get("level"), "previous": t.get("previous"),
                                     "stages": t.get("stages") or []})
    try:
        _restore(t)
    except Exception as exc:  # noqa: BLE001 — retain recovery data and retry next refresh
        _restore_pending(t, exc)
        logger.warning("crystallize: legacy settings still need restoration for %s", t["job_name"], exc_info=True)
        return True
    now = time.time()
    _clear_restore_pending(t)
    t.update({"status": "revalidation", "revalidation_at": now, "reverted_at": now, "finished_at": now,
              "level": None, "stages": [], "reasoning_preserved": True, "policy_version": 2,
              "schema_pruning": False, "new_only": False,
              "result": {"ok": False, "kind": "reasoning", "why": "legacy efficiency settings restored; "
                         "fresh baseline runs at the original model and reasoning are required",
                         "quality": {"ok": False, "stale": True, "semantic_equivalence_proven": False}}})
    _save(t["job_name"], t)
    return True


def _restore(t: dict[str, Any]) -> None:
    found = lifecycle._bound_job(t["job_id"]) if t.get("job_id") else None
    if not found:  # a job that has finished for good has nothing to restore
        return
    # Every part is tried, so one failure doesn't leave the others changed. Each is safe to repeat.
    parts = []
    if t.get("level") and lifecycle._same_level(found.get("reasoning_effort"), t["level"]):
        previous = t.get("previous")
        previous_flag = "none" if previous is False else "" if previous is None else str(previous)
        parts.append(lambda: lifecycle._edit_job(found["id"], "--reasoning-effort", previous_flag))
    if (t.get("toolsets") is not None or t.get("tools_changed")) and lifecycle._same_tools(found.get("enabled_toolsets"), t.get("toolsets")):
        parts.append(lambda: lifecycle.set_toolsets(found["id"], t.get("previous_toolsets")))
    hints = (EFFICIENCY_HINT, LEGACY_EFFICIENCY_HINT)
    if t.get("hint") and t.get("hint_added", True) and any(hint in (found.get("prompt") or "") for hint in hints):
        prompt = found["prompt"]
        for hint in hints:
            prompt = prompt.replace(hint, "")
        parts.append(lambda: lifecycle._edit_job(found["id"], "--prompt", prompt))
    if t.get("new_only"):
        parts.append(lambda: _set_new_only(found["id"], False))
    problem = None
    for part in parts:
        try:
            part()
        except lifecycle.LifecycleError as exc:
            problem = exc
    if problem:
        raise problem


def undo(job: str) -> dict[str, Any]:
    found = lifecycle.cron_job(job)
    name = (found or {}).get("name") or job
    t = status(job)
    if not t or t["status"] not in ("starting", "trial", "kept"):
        raise lifecycle.LifecycleError(f"{name} has no cheaper settings to undo.")
    try:
        _restore(t)
    except Exception as exc:
        _restore_pending(t, exc)
        raise
    _clear_restore_pending(t)
    now = time.time()
    if t.get("status") == "kept":
        t.setdefault("kept_at", t.get("finished_at"))
        t.setdefault("trial_finished_at", t.get("finished_at"))
    t.update({"status": "undone", "undone_at": now, "finished_at": now})
    _save(t["job_name"], t)
    return t


def _finish_restore(t: dict[str, Any]) -> None:
    """A change was cut short, by a failure or a crash. Put the job back."""
    try:
        _restore(t)
    except lifecycle.LifecycleError as exc:
        logger.warning("crystallize: couldn't restore %s yet", t["job_name"], exc_info=True)
        _restore_pending(t, exc)
        return
    _clear_restore_pending(t)
    t.update({"status": "reverted", "finished_at": time.time(),
              "result": {**(t.get("result") or {}), "ok": False, "why": "its settings couldn't be changed"}})
    _save(t["job_name"], t)


def refresh() -> None:
    """Decide any trial that has enough runs. Never raises: the panel calls this on every load."""
    for check in (lifecycle.recover_promotions, lifecycle.refresh_drift):
        try:
            check()
        except Exception:  # noqa: BLE001
            logger.warning("crystallize: %s failed", check.__name__, exc_info=True)
    try:
        trials = store.list_tuning()
    except Exception:  # noqa: BLE001
        logger.warning("crystallize: reading efficiency trials failed", exc_info=True)
        return
    for t in trials:
        # Each job stands alone: a job Hermes can't edit must not block rollbacks for the others.
        try:
            _refresh_one(t)
        except Exception:  # noqa: BLE001
            logger.warning("crystallize: checking the trial for %s failed", t.get("job_name"), exc_info=True)


def _refresh_one(t: dict[str, Any]) -> None:
    if _migrate_legacy(t):
        return
    if t.get("needs_restore") or (t["status"] == "starting" and time.time() - t["started_at"] > STALE_START):
        _finish_restore(t)
    elif t["status"] in ("trial", "kept") and _configuration_drift(t):
        return
    elif t["status"] == "trial":
        _decide(t)
    elif t["status"] == "kept":
        _guard_kept(t)


def _configuration_drift(t: dict[str, Any]) -> bool:
    """Invalidate a campaign rather than judging it against an obsolete task."""
    found = lifecycle._bound_job(t["job_id"]) if t.get("job_id") else None
    if not found:
        return False
    current = _snapshot(found, t)
    saved = t.get("config_snapshot")
    if not saved:
        t["config_snapshot"] = current
        _save(t["job_name"], t)
        return False
    if saved.get("fingerprint") == current["fingerprint"]:
        return False
    changed = sorted(k for k, v in current["configuration"].items() if (saved.get("configuration") or {}).get(k) != v)
    _restore(t)
    now = time.time()
    if t.get("status") == "kept":
        t.setdefault("kept_at", t.get("finished_at"))
        t.setdefault("trial_finished_at", t.get("finished_at"))
    t.update({"status": "revalidation", "revalidation_at": now, "reverted_at": now, "finished_at": now,
              "result": {**(t.get("result") or {}), "ok": False, "kind": "drift",
                         "why": "configuration changed: " + ", ".join(changed),
                         "quality": {"ok": False, "stale": True, "why": "configuration drift requires fresh checks"}},
              "config_snapshot": current})
    _save(t["job_name"], t)
    return True


def _guard_kept(t: dict[str, Any]) -> None:
    """Models and jobs change after a trial. Settings that keep failing stop being kept."""
    recent = _finished_scheduled(t.get("job_id") or t["job_name"])
    since = t.get("kept_at") or t.get("finished_at") or 0
    recent = [r for r in recent if (r.session_started or 0) >= since]
    spec = contracts.get(t["job_id"]) if t.get("job_id") else None
    failure = None
    if spec is not None and recent:
        answers = history.final_answers([r.session_id for r in recent if r.session_id])
        for run in recent:
            report = contracts.evaluate(spec, answer=answers.get(run.session_id or ""), sources=run.steps,
                                        actions=run.steps)
            if not report["ok"]:
                failure = {"ok": False, "kind": "quality", "why": "outcome contract: " + report["why"],
                           "contract_checks": [{"execution_id": run.execution_id, **report}],
                           "quality": {"ok": False, "contract_checks": [report], "semantic_equivalence_proven": False}}
                break
    tail = recent[-STALE_FAILURES:]
    if failure is None:
        if len(tail) < STALE_FAILURES or any(r.outcome != "failed" for r in tail):
            return
        failure = {"ok": False, "why": f"it failed {STALE_FAILURES} runs in a row after being kept",
                   "quality": {"ok": False, "why": "scheduled runs failed"}}
    try:
        _restore(t)
    except lifecycle.LifecycleError:
        logger.warning("crystallize: couldn't restore %s", t["job_name"], exc_info=True)
        return
    t.setdefault("kept_at", t.get("finished_at"))
    t.setdefault("trial_finished_at", t.get("finished_at"))
    t.update({"status": "reverted", "reverted_at": time.time(), "finished_at": time.time(),
              "result": {**(t.get("result") or {}), **failure}})
    _save(t["job_name"], t)


def _is_close(result: dict[str, Any]) -> bool:
    old, new = result.get("before"), result.get("after")
    evidence = result.get("saving_evidence") or {}
    return (result.get("kind") == "saving" and old is not None and new is not None and old > 0
            and evidence.get("sufficient_samples", False)
            and new - old <= evidence.get("required_drop", 0))


def _trial_runs(t: dict[str, Any]) -> tuple[list[history.Run], list[history.Run]]:
    """Runs from before this trial, and from its current stage. Earlier stages already had
    cheaper settings, so they never count as "before"."""
    before, trial = _split(t.get("job_id") or t["job_name"], t["started_at"])
    until = t.get("baseline_until") or (None if t.get("baseline_from") else t.get("first_started_at"))
    if until:
        before = [r for r in before if (r.session_started or 0) < until]
    if t.get("baseline_from"):
        before = [r for r in before if (r.session_started or 0) >= t["baseline_from"]]
    return before, trial


def _decide(t: dict[str, Any]) -> None:
    before, trial = _trial_runs(t)
    answers = history.final_answers([r.session_id for r in before + trial if r.session_id])
    counted = counted_runs(before, trial, answers)
    job = lifecycle._bound_job(t["job_id"]) if t.get("job_id") else lifecycle.cron_job(t["job_name"])
    alive = job is not None and job.get("state") != "completed"
    spec = contracts.get(t["job_id"]) if t.get("job_id") else None
    # Silence does not waive an explicit per-run requirement. A pair of good
    # answers must not hide a missing source/action on an intervening quiet run.
    all_checks = [{"execution_id": r.execution_id,
                   **contracts.evaluate(spec, answer=answers.get(r.session_id or ""), sources=r.steps, actions=r.steps)}
                  for r in trial] if spec is not None else []
    failed = next((report for report in all_checks if not report["ok"]), None)
    if failed:
        result = {"ok": False, "kind": "quality", "why": "outcome contract: " + failed["why"],
                  "contract_checks": all_checks, "quality": {"ok": False, "contract_checks": all_checks}}
    elif len(counted) >= TRIAL_RUNS:
        result = evaluate(before, counted, answers, t.get("required_actions"), spec)
        if _is_close(result) and alive and len(counted) < MAX_TRIAL_RUNS:
            t["pending_result"] = result
            _save(t["job_name"], t)
            return  # one odd run can decide a close call, so it gets more runs
    elif len(trial) >= MAX_TRIAL_RUNS:
        result = {"ok": False, "why": f"it said nothing to report on {len(trial) - len(counted)} runs in a row"}
    elif not alive:
        result = {"ok": False, "why": "the job finished before there were enough runs to compare"}
    else:
        return
    # A weaker answer may recover with the original tool definitions. Reasoning stays fixed.
    if not result["ok"] and result.get("kind") == "quality" and t.get("stages") and alive:
        stage, *rest = t["stages"]
        # Saved first, so a change that fails or a crash can always be undone.
        t.update({**stage, "stages": rest, "started_at": time.time(), "last_setback": result["why"],
                  "tools_changed": t.get("tools_changed") or stage.get("toolsets") is not None})
        _save(t["job_name"], t)
        try:
            _apply_stage(t["job_id"], stage, t)
        except lifecycle.LifecycleError:
            logger.warning("crystallize: couldn't change %s", t["job_name"], exc_info=True)
            t["needs_restore"] = True
            _save(t["job_name"], t)
        return
    if not result["ok"]:
        try:
            _restore(t)
        except lifecycle.LifecycleError:
            logger.warning("crystallize: couldn't restore %s", t["job_name"], exc_info=True)
            return  # still in trial, so it's tried again next time
    now = time.time()
    t.pop("pending_result", None)
    t.update({"status": "kept" if result["ok"] else "reverted", "result": result,
              "finished_at": now, "trial_finished_at": now})
    if result["ok"]:
        t["kept_at"] = now
    else:
        t["reverted_at"] = now
    _save(t["job_name"], t)


# ---------------------------------------------------------------- what to show

def measured(name: str, t: dict[str, Any]) -> Optional[dict[str, Any]]:
    """What a job's cheaper settings have done so far, from its real runs."""
    since = t.get("first_started_at") or t.get("started_at")
    before, every = _split(t.get("job_id") or name, since)
    runs = [r for r in every if r.outcome in (None, "completed")]
    if not before or t["status"] not in ("trial", "kept", "reverted"):
        return None
    if t["status"] == "reverted":  # what a failed trial cost still counts after it's undone
        ended = t.get("reverted_at") or t.get("finished_at") or time.time()
        every = [r for r in every if (r.session_started or 0) < ended]
        runs = [r for r in every if r.outcome in (None, "completed")]
    if not runs:
        return None
    metric, old, new = _measure(before, runs)
    if old is None or new is None:
        return None
    field = {"cost": lambda r: r.cost_usd, "api cost": lambda r: r.api_cost_usd}.get(metric)
    # Failed runs count too: they cost money and delivered nothing.
    comparable = [r for r in every if history.comparable_runs(before, [r])]
    saved = sum(history.net_saving(r, old, field(r)) for r in comparable if field(r) is not None) if field else None
    return {"metric": metric, "before": old, "after": new, "runs": len(comparable), "saved": saved,
            "run_list": comparable, "excluded_identity_runs": len(every) - len(comparable)}


def _settings(t: dict[str, Any]) -> str:
    parts = [f"reasoning '{t['level']}'"] if t.get("level") else []
    if t.get("toolsets") is not None:
        parts.append("tools: " + (", ".join(t["toolsets"]) or "none"))
    if t.get("clean"):
        parts.append("cleaned results")
    if t.get("schema_pruning"):
        parts.append("observed read schemas")
    if t.get("hint"):
        parts.append("efficiency instruction")
    if t.get("new_only"):
        parts.append("new items only")
    return " + ".join(parts) or "current settings"


def status_text(t: Optional[dict[str, Any]]) -> str:
    if not t:
        return "Not tried yet"
    if t.get("needs_restore"):
        return "Restoration pending: " + (t.get("restore_error") or "retry Undo after checking the saved Hermes job settings")
    res = t.get("result") or {}
    if t["status"] == "trial":
        before, trial = _trial_runs(t)
        answers = history.final_answers([r.session_id for r in before + trial if r.session_id])
        done = len(counted_runs(before, trial, answers))
        if done >= TRIAL_RUNS and t.get("pending_result"):
            return f"Trying {_settings(t)}: {done} answers; gathering more comparable evidence (up to {MAX_TRIAL_RUNS})"
        return f"Trying {_settings(t)}: {min(done, TRIAL_RUNS)} of {TRIAL_RUNS} answers so far"
    if t["status"] == "kept":
        metric = res.get("metric", "cost")
        where = " at API prices" if metric == "api cost" else ""
        return f"Kept {_settings(t)}: {_amount(metric, res.get('before', res.get('cost_before')))} → {_amount(metric, res.get('after', res.get('cost_after')))} a run{where}"
    if t["status"] == "reverted":
        return f"Went back to the old setting: {res.get('why', 'the check failed')}"
    if t["status"] == "revalidation":
        return f"Revalidating after a change: {res.get('why', 'fresh scheduled runs are needed')}"
    return "You put the old setting back"


def sample_answers(name: str, t: dict[str, Any]) -> dict[str, str]:
    """The latest baseline and efficiency-trial answers, to read side by side."""
    before, trial = _trial_runs({**t, "job_name": name})
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
        t = lifecycle._tuning_for_job(job)
        runs = all_runs.get(job["id"], [])
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
            "can_try": (not t or t["status"] in ("reverted", "undone", "revalidation") or (t["status"] == "kept" and not t.get("clean"))) and len(
                [r for r in _split(job["id"], time.time())[0] if not t or t.get("status") != "revalidation" or
                 (r.session_started or r.started_ts) >= t.get("revalidation_at", 0)]) >= MIN_BEFORE_RUNS,
            "cleaned_chars": (store.get_cleaned(job["id"]) + store.get_cleaned(name)) or None,
            "can_undo": bool(t and t["status"] in ("starting", "trial", "kept")),
        })
    out.sort(key=lambda c: c["cost_now"] or 0.0, reverse=True)
    return out


def _finished_scheduled_all() -> dict[str, list[history.Run]]:
    grouped: dict[str, list[history.Run]] = {}
    for r in history.load_runs():
        if r.scheduled and r.finished:
            grouped.setdefault(r.job_id or r.job_name or "?", []).append(r)
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
