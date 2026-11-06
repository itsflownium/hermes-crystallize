"""Optional, explicitly approved cadence changes for noncritical snapshot jobs.

The heatmap counts complete observed tool-result digests, never inferred change
times. A quiet history does not establish that a task can safely run less often.
Only interval schedules and a narrow, action-free snapshot subset are supported.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shlex
import statistics
import subprocess
import threading
import time
from datetime import datetime, timezone as dt_timezone
from functools import lru_cache
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import analyze, contracts, history, lifecycle, runner, store

_KEY = "rhythm:"
_MUTATION_LOCK = threading.RLock()
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
_DIGEST = re.compile(r"[0-9a-fA-F]{64}\Z")
_READ_TOOLS = {"web_extract", "web_search", "read_file", "search_files"}
_CONTEXT_TOOLS = {"skill_view", "skills_list", "todo_read", "memory_view"}
_SENSITIVE = re.compile(
    r"\b(?:alerts?|alerting|notif(?:y|ies|ication|ications)|remind(?:er|ers|ing)?|"
    r"deadlines?|urgent|immediately|real[ -]?time|monitor(?:ing)?|watch(?:ing)?|"
    r"detect(?:ion)?|trigger|webhooks?|unread|inbox|transactions?|payments?|"
    r"send|email|post|publish|deploy|create|delete|remove|edit|update|write|"
    r"append|save|sync|backup|execute|hourly|daily|weekly|monthly|morning|evening)\b|"
    r"\bas soon as\b|\b(?:each|every|all)\s+(?:new\s+)?(?:changes?|events?|messages?|items?)\b|"
    r"\b(?:before|by|at)\s+\d{1,2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?\b|"
    r"\b(?:last|past)\s+\d+\s*(?:minutes?|hours?|days?|weeks?|m|h|d|w)\b",
    re.IGNORECASE,
)
_SNAPSHOT = re.compile(r"\b(?:snapshot|summari[sz]e|summary|digest|overview|report|read|list|fetch|extract)\b", re.I)
_TEMPORAL_ARGUMENTS = {"since", "until", "after", "before", "cursor", "offset", "unread", "new_only",
                       "changed_since", "last_seen", "last_id", "since_id", "start_time", "end_time"}
_TEMPORAL_TEXT = re.compile(r"\b(?:since|until|unread|breaking|cursor|incremental)\b|"
                            r"\b(?:last|past)\s+\d+\s*(?:minutes?|hours?|days?|weeks?|m|h|d|w)\b", re.I)
_RUNTIME_FIELDS = {"last_run_at", "last_run", "next_run_at", "next_run", "last_status", "last_error",
                   "last_dispatch", "last_execution_id", "last_delivery", "last_output", "run_count",
                   "started_at", "finished_at", "updated_at", "schedule_display"}
CONCURRENCY_LIMIT = (
    "Crystallize checks the current configuration immediately before the host CLI edit. "
    "The CLI has no atomic compare-and-swap option, so an external schedule edit in the "
    "short check/call window can race. Only --schedule is sent; other job settings are preserved."
)
ACKNOWLEDGEMENT = (
    "This job is a noncritical, latest-state snapshot. It does not need every transient "
    "event, notification, deadline or action, and the stated maximum staleness is acceptable."
)


class RhythmError(lifecycle.LifecycleError):
    """A user-facing validation, ownership or host-capability failure."""


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _job(job_id: str) -> dict[str, Any]:
    if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
        raise RhythmError("Choose an exact, stable cron job ID.")
    job = lifecycle.cron_job(job_id)
    if not job or job.get("id") != job_id:
        raise RhythmError("That stable cron job ID was not found. Refresh the job list.")
    return copy.deepcopy(job)


def _zone(name: str) -> ZoneInfo:
    if not isinstance(name, str) or not name or len(name) > 100:
        raise RhythmError("Choose a valid IANA timezone, for example Australia/Sydney or UTC.")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RhythmError("Choose a valid IANA timezone, for example Australia/Sydney or UTC.") from exc


def _positive_int(value: Any, label: str, maximum: int = 525600) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RhythmError(f"{label} must be a positive whole number.")
    if value != int(value) or not 1 <= value <= maximum:
        raise RhythmError(f"{label} must be a whole number between 1 and {maximum}.")
    return int(value)


def _interval(job: dict[str, Any]) -> Optional[int]:
    schedule = job.get("schedule")
    if not isinstance(schedule, dict) or schedule.get("kind") != "interval":
        return None
    if set(schedule) - {"kind", "minutes", "display"}:
        return None  # unknown schedule semantics must not disappear in a CLI replacement
    try:
        return _positive_int(schedule.get("minutes"), "Current interval")
    except RhythmError:
        return None


def _schedule_fingerprint(job: dict[str, Any]) -> str:
    interval = _interval(job)
    return _hash({"kind": "interval", "minutes": interval} if interval else job.get("schedule"))


def _configuration(job: dict[str, Any]) -> dict[str, Any]:
    value = {k: v for k, v in job.items() if k not in _RUNTIME_FIELDS}
    if isinstance(value.get("repeat"), dict):
        value["repeat"] = {k: v for k, v in value["repeat"].items() if k != "completed"}
    return {"job": value, "contract": contracts.get(job["id"])}


def configuration_fingerprint(job: dict[str, Any]) -> str:
    """Fingerprint task, route, reasoning, schedule and outcome requirements."""
    return _hash(_configuration(job))


@lru_cache(maxsize=16)
def _probe_schedule_flag(command: tuple[str, ...], home: str) -> tuple[bool, str]:
    # Public help is a read-only capability check, not a trial edit or an import of
    # the host's private parser. The profile home is part of the capability cache.
    try:
        result = subprocess.run([*command, "cron", "edit", "--help"], capture_output=True,
                                text=True, timeout=10, env=lifecycle.hermes_env())
    except (OSError, subprocess.SubprocessError):
        return False, "The host cron-edit capability could not be verified. Export a suggestion instead."
    help_text = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode != 0 or not re.search(r"(?<![\w-])--schedule(?=[\s=,\[]|$)", help_text):
        return False, "This host does not advertise cron edit --schedule. Export a suggestion instead."
    return True, "Verified the host's public cron edit --schedule flag with read-only CLI help."


def _control_capability() -> tuple[bool, str]:
    try:
        command = tuple(lifecycle._hermes_cmd())
    except (lifecycle.LifecycleError, ImportError, ValueError):
        return False, "The host CLI is unavailable. Export a suggestion instead."
    return _probe_schedule_flag(command, str(history._hermes_home()))


def _temporal_args(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(k).lower() in _TEMPORAL_ARGUMENTS or _temporal_args(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_temporal_args(v) for v in value)
    if isinstance(value, str):
        return bool(_TEMPORAL_TEXT.search(value))
    return False


def _safety_blockers(job: dict[str, Any], runs: list[history.Run]) -> list[str]:
    blockers = []
    if _interval(job) is None:
        blockers.append("Only plain recurring interval schedules are supported; timed cron and one-shot jobs are excluded.")
    if job.get("enabled") is False or job.get("state") in {"paused", "completed", "cancelled", "error"} or job.get("paused_at"):
        blockers.append("This job is paused, disabled or terminal.")
    repeat = job.get("repeat")
    if repeat is not None and (not isinstance(repeat, dict) or repeat.get("times") is not None):
        blockers.append("Finite-repeat jobs may have time-dependent obligations and are excluded.")
    if job.get("script") or job.get("skills") or job.get("skill"):
        blockers.append("Jobs with scripts or skills can perform unobserved actions and are excluded.")
    if job.get("deliver") not in (None, "local") or any(job.get(k) for k in ("deliver_to", "delivery_target", "webhook")):
        blockers.append("Jobs with external delivery or notification obligations are excluded.")
    prompt = lifecycle.normalize_prompt(job.get("prompt") or "")
    if _SENSITIVE.search(prompt):
        blockers.append("Alerts, reminders, deadlines, event tracking, scheduled delivery and action tasks are excluded.")
    if not _SNAPSHOT.search(prompt):
        blockers.append("The task is not clearly a latest-state read or snapshot; cadence safety cannot be inferred.")
    if (contracts.get(job["id"]) or {}).get("required_actions"):
        blockers.append("This job's outcome contract requires actions on each run.")
    if job["id"] in history.running_job_ids() or job.get("state") in {"running", "claimed"}:
        blockers.append("Wait for the current execution to finish before changing its schedule.")
    observed = [r for r in runs if r.scheduled and r.job_id == job["id"]]
    content_steps = [s for r in observed for s in r.steps if s.tool_name not in _CONTEXT_TOOLS]
    if not content_steps:
        blockers.append("No recorded source reads establish this job's action-free observation scope.")
    elif any(s.tool_name not in _READ_TOOLS or analyze.observation_safety_error(s.tool_name, s.args)
             or _temporal_args(s.args) for s in content_steps):
        blockers.append("Observed calls include actions, unsupported tools or event-window/cursor inputs; less frequent runs could miss work.")
    return blockers


def _content_fingerprint(run: history.Run) -> Optional[str]:
    if not run.scheduled or run.outcome != "completed" or not run.steps:
        return None
    parts = []
    for step in run.steps:
        if step.tool_name in _CONTEXT_TOOLS:
            continue
        if (step.status not in (None, "ok", "success", "completed")
                or not isinstance(step.result_sha256, str) or not _DIGEST.fullmatch(step.result_sha256)
                or isinstance(step.result_len, bool) or not isinstance(step.result_len, int) or step.result_len < 1):
            return None
        # A full digest is authoritative even if the display preview was cut. A
        # complete error preview still disqualifies the run rather than teaching
        # Rhythm that an empty/error response is quiet source data.
        if step.result_preview and runner.step_failure(step.result_preview):
            return None
        parts.append({"tool": step.tool_name, "args": step.args,
                      "result_sha256": step.result_sha256.lower(), "result_len": step.result_len})
    return _hash(parts) if parts else None


def _observations(job_id: str, runs: list[history.Run], zone: ZoneInfo) -> tuple[list[dict], dict]:
    cells = [{"weekday": day, "hour": hour, "observations": 0, "comparisons": 0,
              "changes": 0, "unchanged": 0} for day in range(7) for hour in range(24)]
    stamps: dict[str, float] = {}
    for row in store.cron_trace_rows(job_id):
        if row.get("cron_job_id") == job_id and row.get("cron_scheduled"):
            stamp = row.get("ts")
            if isinstance(stamp, (int, float)) and math.isfinite(stamp):
                key = str(row.get("cron_execution_id"))
                stamps[key] = max(stamp, stamps.get(key, stamp))
    observations, completed = [], 0
    previous = None
    comparisons = changes = 0
    for run in sorted((r for r in runs if r.job_id == job_id and r.scheduled), key=lambda r: r.started_ts):
        completed += run.outcome == "completed"
        fingerprint = _content_fingerprint(run)
        stamp = stamps.get(str(run.execution_id), run.started_ts)
        if not fingerprint or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            previous = None  # unknown/failed executions break a consecutive comparison
            continue
        try:
            local = datetime.fromtimestamp(stamp, dt_timezone.utc).astimezone(zone)
        except (ValueError, OverflowError, OSError):
            previous = None
            continue
        cell = cells[local.weekday() * 24 + local.hour]
        cell["observations"] += 1
        if previous is not None:
            changed = fingerprint != previous
            cell["comparisons"] += 1
            cell["changes"] += int(changed)
            cell["unchanged"] += int(not changed)
            comparisons += 1
            changes += int(changed)
        observations.append(stamp)
        previous = fingerprint
    span = (max(observations) - min(observations)) / 86400 if observations else 0.0
    evidence = {"completed_runs": completed, "complete_observations": len(observations),
                "comparisons": comparisons, "changes": changes, "span_days": span,
                "first_observed_at": min(observations) if observations else None,
                "last_observed_at": max(observations) if observations else None,
                "reliable_for_suggestion": len(observations) >= 12 and comparisons >= 3 and span >= 7,
                "fingerprint_source": "ordered full tool-result SHA-256 digests and complete arguments",
                "limitations": ["Buckets show when changed content was observed, not when a source actually changed.",
                                "The first observation and observations after failed or incomplete runs are not change comparisons.",
                                "Complete observations have full result digests; missing capture coverage cannot be reconstructed, and historical runs may predate the current task configuration.",
                                "Unobserved transient changes and future behavior cannot be inferred from a quiet history."]}
    return cells, evidence


def _state(job_id: str) -> dict[str, Any]:
    value = store.get_setting(_KEY + job_id)
    return value if isinstance(value, dict) else {}


def _report(job: dict[str, Any], timezone: str, runs: list[history.Run]) -> dict[str, Any]:
    zone = _zone(timezone)
    cells, evidence = _observations(job["id"], runs, zone)
    blockers = _safety_blockers(job, runs)
    supported, reason = _control_capability()
    state = _state(job["id"])
    owned = (state.get("status") in {"pending", "applied"}
             and state.get("applied_schedule_fingerprint") == _schedule_fingerprint(job))
    interval = _interval(job)
    recommendation = None
    if (not owned and not blockers and interval and evidence["reliable_for_suggestion"]
            and evidence["changes"] <= evidence["comparisons"] * .2 and interval <= 262800):
        recommendation = {"interval_minutes": interval * 2, "classification": "suggestion",
                          "requires_acknowledgement": True,
                          "reason": "Most consecutive complete observations matched. Consider twice the interval only if the explicit snapshot and staleness acknowledgement fits this task; history does not prove safety."}
    return {"ok": True, "job_id": job["id"], "job_name": job.get("name") or job["id"],
            "timezone": timezone, "timezone_source": "UTC default" if timezone == "UTC" else "explicit",
            "config_fingerprint": configuration_fingerprint(job), "current_interval_minutes": interval,
            "heatmap": cells, "evidence": evidence, "eligibility": {"eligible": not blockers, "blockers": blockers},
            "recommendation": recommendation, "acknowledgement": ACKNOWLEDGEMENT,
            "control": {"mode": "cli" if supported else "export_only", "supported": supported,
                        "reason": reason, "concurrency_limit": CONCURRENCY_LIMIT,
                        "active": owned, "can_undo": bool(owned and supported),
                        "status": state.get("status", "unchanged")},
            "message": "Observed source changes in " + timezone + ". Schedule changes always require your explicit approval."}


def report(job_id: str, timezone: str = "UTC") -> dict[str, Any]:
    """Read observations and suggestions without changing schedules or consent."""
    job = _job(job_id)
    return _report(job, timezone, history.load_runs(job_id))


def _export(job_id: str, interval: int) -> dict[str, Any]:
    schedule = f"every {interval}m"
    argv = ["hermes", "cron", "edit", job_id, "--schedule", schedule]
    return {"argv": argv, "command": shlex.join(argv), "schedule": schedule,
            "scope": "Run only in the owning Hermes profile after reviewing the current job."}


def preview(job_id: str, interval_minutes: int, *, timezone: str = "UTC", noncritical: bool = False,
            allowed_staleness_minutes: Optional[int] = None, horizon_days: int = 30) -> dict[str, Any]:
    """A rate and historical-cost projection, never a measured saving or a write."""
    desired = _positive_int(interval_minutes, "Desired interval")
    horizon = _positive_int(horizon_days, "Projection days", 366)
    staleness = (_positive_int(allowed_staleness_minutes, "Allowed staleness")
                 if allowed_staleness_minutes is not None else None)
    job = _job(job_id)
    runs = [r for r in history.load_runs(job_id) if r.job_id == job_id]
    value = _report(job, timezone, runs)
    current = value["current_interval_minutes"]
    blockers = list(value["eligibility"]["blockers"])
    if noncritical is not True:
        blockers.append("Explicitly acknowledge that this is a noncritical latest-state snapshot without transient-event or per-run obligations.")
    if staleness is None or staleness < desired:
        blockers.append("Explicitly allow maximum staleness at least as long as the desired interval.")
    if current is not None and desired < current:
        blockers.append("Rhythm only proposes equal or less frequent intervals.")
    if value["evidence"]["complete_observations"] < 3 or value["evidence"]["comparisons"] < 2:
        blockers.append("At least three complete scheduled observations and two consecutive comparisons are required.")
    known_costs = [(r.cost_usd, r.cost_provenance) for r in runs if r.scheduled and r.outcome == "completed"
                   and _content_fingerprint(r) is not None and isinstance(r.cost_usd, (int, float))
                   and not isinstance(r.cost_usd, bool) and math.isfinite(r.cost_usd) and r.cost_usd >= 0
                   and r.cost_provenance in {"actual", "estimated", "included"}]
    mean = statistics.mean(c for c, _ in known_costs) if known_costs else None
    before = math.floor(horizon * 1440 / current) if current else None
    after = math.floor(horizon * 1440 / desired) if current else None
    avoided = max(0, before - after) if before is not None else None
    projection = {"classification": "projection", "horizon_days": horizon,
                  "current_scheduled_invocations": before, "proposed_scheduled_invocations": after,
                  "avoided_scheduled_invocations": avoided, "estimated_avoided_usd": avoided * mean if avoided is not None and mean is not None else None,
                  "mean_observed_cost_usd": mean, "cost_samples": len(known_costs),
                  "cost_basis": sorted({p for _, p in known_costs}) or ["unknown"],
                  "limitations": ["Configured interval rates assume continuous scheduling; phase, retries, pauses and future edits are not modeled.",
                                  "Dollar amounts are estimates from historical billed or estimated per-run costs, not measured savings; future routes, costs and tool fees may differ.",
                                  "Unknown-cost observations are excluded; subscription-included observations contribute zero billed cost."]}
    value.update(interval_minutes=desired, frequency_factor=desired / current if current else None,
                 allowed_staleness_minutes=staleness, noncritical=noncritical is True,
                 projection=projection, blockers=blockers, can_export=not blockers,
                 can_apply=bool(not blockers and value["control"]["supported"]),
                 export=_export(job_id, desired) if not blockers else None,
                 message="Review projected avoided scheduled invocations and maximum staleness. No schedule has changed.")
    return value


def _check_expected(job: dict[str, Any], expected: str) -> None:
    if not isinstance(expected, str) or not _DIGEST.fullmatch(expected):
        raise RhythmError("Preview the current job and provide its configuration fingerprint.")
    if configuration_fingerprint(job) != expected:
        raise RhythmError("The job configuration changed after preview. Refresh and review the new configuration; nothing was changed.")


def apply(job_id: str, interval_minutes: int, *, expected_config_fingerprint: str,
          noncritical: bool, allowed_staleness_minutes: int, confirmed: bool = False,
          timezone: str = "UTC") -> dict[str, Any]:
    """Change only --schedule through the public host CLI after explicit approval."""
    if confirmed is not True:
        raise RhythmError("Explicit confirmation is required before changing a schedule.")
    desired = _positive_int(interval_minutes, "Desired interval")
    _positive_int(allowed_staleness_minutes, "Allowed staleness")
    if not isinstance(expected_config_fingerprint, str) or not _DIGEST.fullmatch(expected_config_fingerprint):
        raise RhythmError("Preview the current job and provide its configuration fingerprint.")
    with _MUTATION_LOCK:
        job = _job(job_id)
        state = _state(job_id)
        # A response retry is safe only while the same installed schedule is still
        # ours, with exactly the same acknowledgement as the approved request.
        if (state.get("status") in {"pending", "applied"}
                and state.get("applied_interval_minutes") == desired
                and state.get("applied_schedule_fingerprint") == _schedule_fingerprint(job)
                and expected_config_fingerprint in {state.get("original_config_fingerprint"), configuration_fingerprint(job)}
                and noncritical is True and allowed_staleness_minutes == state.get("allowed_staleness_minutes")):
            return {"ok": True, "applied": False, "already_applied": True,
                    "config_fingerprint": configuration_fingerprint(job), "can_undo": _control_capability()[0],
                    "concurrency_limit": CONCURRENCY_LIMIT, "message": "That interval is already installed. No additional edit was made."}
        _check_expected(job, expected_config_fingerprint)
        proposed = preview(job_id, desired, timezone=timezone, noncritical=noncritical,
                           allowed_staleness_minutes=allowed_staleness_minutes)
        if proposed["blockers"]:
            raise RhythmError(" ".join(proposed["blockers"]))
        if not proposed["control"]["supported"]:
            return {"ok": True, "applied": False, "already_applied": False, "can_undo": False,
                    "config_fingerprint": configuration_fingerprint(job), "control": proposed["control"],
                    "export": proposed["export"], "message": proposed["control"]["reason"]}
        # This last read catches edits while evidence/capability checks were in
        # progress. The public CLI has a disclosed narrow read/call race window.
        current = _job(job_id)
        _check_expected(current, expected_config_fingerprint)
        if _interval(current) == desired:
            return {"ok": True, "applied": False, "already_applied": True,
                    "config_fingerprint": configuration_fingerprint(current), "can_undo": proposed["control"]["can_undo"],
                    "message": "The job already has that interval. No edit was made."}
        still_owned = (state.get("status") in {"pending", "applied"}
                       and state.get("applied_schedule_fingerprint") == _schedule_fingerprint(current))
        original = state.get("original_schedule") if still_owned else current["schedule"]
        record = {"version": 1, "job_id": job_id, "status": "pending", "requested_at": time.time(),
                  "original_schedule": copy.deepcopy(original),
                  "original_config_fingerprint": expected_config_fingerprint,
                  "applied_interval_minutes": desired,
                  "applied_schedule_fingerprint": _hash({"kind": "interval", "minutes": desired}),
                  "allowed_staleness_minutes": proposed["allowed_staleness_minutes"],
                  "acknowledgement": ACKNOWLEDGEMENT,
                  "history": list(state.get("history") or []) + [{"at": time.time(), "action": "apply", "interval_minutes": desired}]}
        store.set_setting(_KEY + job_id, record)  # durable recovery data before mutation
        try:
            _check_expected(_job(job_id), expected_config_fingerprint)
        except RhythmError:
            # Persisting recovery data must not extend an unchecked edit window.
            # Preserve ownership of an earlier successful change if this newer
            # proposal was cancelled before it reached the host.
            record["status"] = "cancelled"
            store.set_setting(_KEY + job_id, state or record)
            raise
        try:
            lifecycle._edit_job(job_id, "--schedule", f"every {desired}m")
        except Exception as exc:
            record["status"] = "pending"  # timeout/error can have an unknown host outcome
            record["last_error"] = "Host CLI did not confirm the schedule edit. Refresh the current schedule before retrying."
            store.set_setting(_KEY + job_id, record)
            raise RhythmError(record["last_error"]) from exc
        after = _job(job_id)
        if _schedule_fingerprint(after) != record["applied_schedule_fingerprint"]:
            record["status"] = "conflict"
            store.set_setting(_KEY + job_id, record)
            raise RhythmError("The host did not retain the requested interval, or another edit intervened. Refresh the schedule; no further edit was attempted.")
        record.update(status="applied", applied_at=time.time(), applied_config_fingerprint=configuration_fingerprint(after))
        store.set_setting(_KEY + job_id, record)
        return {"ok": True, "applied": True, "already_applied": False,
                "config_fingerprint": configuration_fingerprint(after), "can_undo": True,
                "concurrency_limit": CONCURRENCY_LIMIT,
                "message": f"Changed the interval to every {desired} minutes through Hermes. Model and reasoning settings were not edited."}


def undo(job_id: str, *, expected_config_fingerprint: str, confirmed: bool = False) -> dict[str, Any]:
    """Restore only an owned interval, preserving intervening non-schedule edits."""
    if confirmed is not True:
        raise RhythmError("Explicit confirmation is required before restoring a schedule.")
    with _MUTATION_LOCK:
        job = _job(job_id)
        state = _state(job_id)
        if not state or state.get("status") == "undone":
            return {"ok": True, "undone": False, "config_fingerprint": configuration_fingerprint(job),
                    "message": "There is no active Crystallize schedule change to undo."}
        _check_expected(job, expected_config_fingerprint)
        original = _interval({"schedule": state.get("original_schedule")})
        if (state.get("undo_requested_at") and original is not None
                and _schedule_fingerprint(job) == _hash({"kind": "interval", "minutes": original})):
            state.update(status="undone", undone_at=time.time())
            store.set_setting(_KEY + job_id, state)
            return {"ok": True, "undone": False, "already_undone": True,
                    "config_fingerprint": configuration_fingerprint(job),
                    "message": "The original interval is already restored. No additional host edit was made."}
        if state.get("status") not in {"pending", "applied"} or _schedule_fingerprint(job) != state.get("applied_schedule_fingerprint"):
            raise RhythmError("The current schedule no longer matches Crystallize's change. Undo preserves the newer schedule and made no edit.")
        if original is None:
            raise RhythmError("The saved original interval cannot be verified. No schedule edit was made.")
        supported, reason = _control_capability()
        if not supported:
            return {"ok": True, "undone": False, "config_fingerprint": configuration_fingerprint(job),
                    "export": _export(job_id, original), "message": reason}
        current = _job(job_id)
        _check_expected(current, expected_config_fingerprint)
        if _schedule_fingerprint(current) != state.get("applied_schedule_fingerprint"):
            raise RhythmError("The schedule changed before Undo. The newer schedule was preserved.")
        # Persist the undo intent before dispatch. Keeping the applied ownership
        # signature allows recovery if a failed command did not change anything.
        state["undo_requested_at"] = time.time()
        store.set_setting(_KEY + job_id, state)
        _check_expected(_job(job_id), expected_config_fingerprint)
        try:
            lifecycle._edit_job(job_id, "--schedule", f"every {original}m")
        except Exception as exc:
            raise RhythmError("The host did not confirm Undo. Refresh the schedule before retrying; recovery data was retained.") from exc
        after = _job(job_id)
        if _schedule_fingerprint(after) != _hash({"kind": "interval", "minutes": original}):
            raise RhythmError("The original interval was not retained, or another edit intervened. No further edit was attempted.")
        state.update(status="undone", undone_at=time.time())
        state["history"] = list(state.get("history") or []) + [{"at": time.time(), "action": "undo", "interval_minutes": original}]
        store.set_setting(_KEY + job_id, state)
        return {"ok": True, "undone": True, "config_fingerprint": configuration_fingerprint(after),
                "concurrency_limit": CONCURRENCY_LIMIT,
                "message": "Restored the original interval through Hermes. Other job settings were preserved."}
