"""Learn from scheduled observations and attach reversible host-tool guidance.

On the reviewed public SDK, testing observes ordinary host runs and promotion
defaults to a recipe the main agent executes with its normal tools. Automatic
shadow replay, pre-run scripts and no-AI modes require verified guarded dispatch
and remain unavailable. Legacy attachments retain their recovery/Undo support.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import analyze, contracts, history, modelinfo, runner, steplist, store

logger = logging.getLogger(__name__)

MATCHES_NEEDED = 3
FAILURES_BEFORE_UNDO = 2
MARKER = "[Crystallize routine"


class LifecycleError(RuntimeError):
    """A user-facing reason an action can't happen."""


class PublicToolsetRecovery(LifecycleError):
    """A legacy value needs a normal host edit that the public CLI cannot perform."""


# ---------------------------------------------------------------- cron job access

STALE_PROMOTE = 300         # a Promote still unfinished after this many seconds was cut short


def _jobs_file() -> Path:
    return history._hermes_home() / "cron" / "jobs.json"


def cron_job(job_id: str) -> Optional[dict[str, Any]]:
    """Read one cron job (read-only)."""
    jobs = all_cron_jobs()
    return next((j for j in jobs if j.get("id") == job_id), None) or next(
        (j for j in jobs if j.get("name") == job_id), None)


def _bound_job(job_id: str) -> Optional[dict[str, Any]]:
    """A saved attachment never follows a reused display name to a different job."""
    job = cron_job(job_id)
    return job if job and job.get("id") == job_id else None


def all_cron_jobs() -> list[dict[str, Any]]:
    """Every cron job Hermes knows about, including ones that have finished (read-only)."""
    try:
        data = json.loads(_jobs_file().read_text())
    except (OSError, ValueError):
        return []
    jobs = data.get("jobs", data) if isinstance(data, dict) else data
    return [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []


def _hermes_cmd() -> list[str]:
    """Hermes's own CLI. Inside Hermes we run it with Hermes's Python, which also
    works on Windows, where `hermes` is usually a .cmd wrapper."""
    import importlib.util

    if importlib.util.find_spec("hermes_cli") is not None:  # running inside Hermes
        return [sys.executable, "-m", "hermes_cli.main"]
    exe = shutil.which("hermes")
    if not exe:
        raise LifecycleError("Couldn't find the hermes command.")
    return [exe]


def hermes_env() -> dict[str, str]:
    """Keep CLI children in the caller's resolved Hermes profile."""
    return {**sanitized_child_env(), "HERMES_HOME": str(history._hermes_home())}


def sanitized_child_env() -> dict[str, str]:
    """Public CLI edits inherit normal profile context, never a YOLO override."""
    env = dict(os.environ)
    env.pop("HERMES_YOLO_MODE", None)
    return env


def _edit_job(job_id: str, *flags: str) -> None:
    """Change a cron job through Hermes's own CLI (`hermes cron edit`)."""
    proc = subprocess.run(
        [*_hermes_cmd(), "cron", "edit", job_id, *flags],
        capture_output=True, text=True, timeout=60, env=hermes_env(),
    )
    if proc.returncode != 0:
        raise LifecycleError(f"Hermes couldn't update the job: {(proc.stderr or proc.stdout).strip()[:300]}")


def set_toolsets(job_id: str, toolsets: Optional[list[str]]) -> None:
    """Confirm a legacy restoration, or retain recovery for a manual host edit.

    Hermes has no documented CLI toolset mutation. Never patch its job files or
    call private job APIs to work around that missing public operation.
    """
    if toolsets is not None and (not isinstance(toolsets, list) or any(
            not isinstance(name, str) or not name.strip() for name in toolsets)):
        raise LifecycleError("Toolsets must be a list of nonempty names, or null for the cron default.")
    job = _bound_job(job_id)
    if not job or _same_tools(job.get("enabled_toolsets"), toolsets):
        return  # deleted jobs and already-restored fields require no mutation
    desired = "the host's default toolsets" if toolsets is None else (", ".join(toolsets) or "an explicit empty toolset list")
    raise PublicToolsetRecovery(f"Legacy toolset recovery for cron job {job_id} is pending. Hermes has no public "
                         f"CLI toolset edit flag. Ask Hermes through its normal cronjob_manage job controls to restore {desired}; "
                         "then retry Undo. Crystallize has kept the recovery record and will not edit jobs.json.")


def replay_support() -> dict[str, Any]:
    """Current documented guarded-dispatch support; raw method presence is insufficient."""
    from . import dispatch_support

    return dispatch_support.support()


def _replay_available() -> bool:
    support = replay_support()
    return bool(support.get("guarded_dispatch_available") and support.get("automatic_nested_tools"))


def promotion_availability(routine: Any) -> dict[str, Any]:
    """Read-only prerequisites for UI controls, including the public host-plan fallback."""
    if isinstance(routine, str):
        routine = store.get_routine(routine)
    support = replay_support()
    guarded = bool(support.get("guarded_dispatch_available") and support.get("automatic_nested_tools"))
    routine = routine or {}
    meta = routine.get("meta") or {}
    job = _bound_job(routine.get("source_ref") or "") if routine.get("source_ref") else None
    saved = meta.get("config_snapshot") or {}
    current = configuration_fingerprint(job, routine) if job else {}
    binding = bool(job and saved.get("fingerprint") == current.get("fingerprint") and not meta.get("promoting"))
    try:
        safe = bool(routine.get("steps") and analyze.replay_safety_error(routine["steps"]) is None)
    except (ValueError, TypeError, KeyError):
        safe = False
    ready = bool(meta.get("ready"))
    eligible = bool(job and not job.get("no_agent") and job.get("state") != "completed" and binding and safe and ready
                    and routine.get("status") == "testing")
    reason = support.get("reason") or "The public Hermes SDK has no verified guarded nested-tool dispatch."
    ordinary_reason = ("Ready for the main agent to execute and verify the recipe through normal host tools."
                       if eligible else "A current read-only recipe and matching normal host observations are required.")
    spec = contracts.get(job["id"]) if job else None
    actions = bool((routine.get("steps") or {}).get("leaves_to_ai") or (spec or {}).get("required_actions"))
    prerequisites = {"job_present": bool(job), "configuration_current": binding, "read_recipe_safe": safe,
                     "observations_ready": ready, "main_agent_present": bool(job and not job.get("no_agent")),
                     "guarded_dispatch_available": guarded}
    return {"default_mode": "tool", "suggested_available_mode": "tool", "ready": ready,
            "available": eligible, "automatic_replay_available": guarded,
            "guarded_dispatch_available": guarded,
            "public_dispatch_method_present": bool(support.get("public_dispatch_method_present")),
            "validation_mode": meta.get("validation_mode", "legacy_unspecified"),
            "automatic_replay_validated": bool(guarded and meta.get("automatic_replay_validated")),
            "fallback": "normal_host_tool_plan", "reason": reason, "prerequisites": prerequisites,
            "try_available": bool(job and job.get("state") != "completed" and routine.get("status") != "live"),
            "try_execution": "guarded_replay" if guarded else "observe_normal_host_runs",
            "test_available": bool(job and binding and safe and routine.get("status") == "testing"),
            "test_execution": "guarded_replay" if guarded else "observe_normal_host_runs",
            "modes": {"tool": {"available": eligible, "reason": ordinary_reason, "execution": "normal_host_tools",
                                "requires": ["current learned read-only recipe", "normal host observations", "main agent tool pipeline"]},
                      "prerun": {"available": bool(eligible and guarded), "reason": ordinary_reason if guarded else reason,
                                 "execution": "guarded_replay"},
                      "no_ai": {"available": bool(eligible and guarded and not actions),
                                "reason": "The job's actions require its main agent." if actions else ordinary_reason if guarded else reason,
                                "execution": "guarded_replay"}}}


def analysis_projection(analysis: analyze.Analysis) -> dict[str, Any]:
    """Keep a structural replay estimate separate from a usable/measured saving."""
    available = _replay_available()
    return {"status": "hypothetical" if available else "unavailable",
            "requires": "verified_guarded_automatic_replay",
            "cost_after_usd": analysis.est_cost_after_usd if available else None,
            "api_calls_after": analysis.est_api_calls_after if available else None,
            "savings_verified": False,
            "reason": ("Unmeasured structural projection from call counts; not a price or quality guarantee."
                       if available else "Automatic replay is unavailable. Host-tool guidance keeps the main agent running; "
                       "its savings must be measured separately.")}


def _set_job_fields(job_id: str, fields: dict[str, Any]) -> None:
    """Use documented CLI edits for supported fields; do not fall back to internals."""
    if set(fields) - {"interpreter"}:
        raise LifecycleError("These job fields have no supported Crystallize CLI adapter.")
    if fields:
        value = fields["interpreter"]
        if value is not None and not isinstance(value, str):
            raise LifecycleError("The job's script interpreter must be a path or null.")
        _edit_job(job_id, "--interpreter", value or "")


def _set_prompt(job_id: str, prompt: str) -> None:
    _edit_job(job_id, "--prompt", prompt)


def _legacy_crystallized_prompt(routine_id: str, original: str) -> str:
    return (
        f"{MARKER} {routine_id}] First call the crystal_run tool with routine_id \"{routine_id}\". "
        "It runs this job's data-collection steps and returns their output. Use that output to "
        "complete the task below without fetching the same data again. If crystal_run returns "
        "ok=false or an error, ignore it and do the whole task yourself.\n\n"
        f"Task:\n{original}"
    )


def crystallized_prompt(routine_id: str, original: str) -> str:
    """A recipe for the main agent's ordinary, guarded host tool calls."""
    return (
        f"{MARKER} {routine_id}] Call crystal_run with routine_id \"{routine_id}\" to read this job's learned tool plan. "
        "A pending_host_tools plan is guidance, not executed work or current results. Resolve every date, identifier, "
        "dependency and file for this run, then use your normal host tools and approvals to execute and verify the task. "
        "Use all additional tools, skills, research and checks needed for the best answer. Do every requested action on "
        "every run. Keep the configured main model and reasoning. If the plan is unavailable or unsuitable, do the whole "
        "task yourself.\n\n"
        f"Task:\n{original}"
    )


def prerun_prompt(routine_id: str, original: str) -> str:
    """Prompt for a job whose data now arrives in the Script Output block."""
    return (
        f"{MARKER} {routine_id}] The Script Output block above holds this job's data, collected "
        "just now by a tested Crystallize routine. It is current and complete: do not fetch, "
        "re-check or re-read it with tools, and do not open files it mentions unless their "
        "content is missing. Write the answer straight from it. If it says the data hasn't "
        "changed, repeat the answer it gives word for word. Only if it says the routine failed, "
        "do the whole task yourself.\n\n"
        f"Task:\n{original}"
    )


# ---------------------------------------------------------------- configuration drift

def unwrap_prompt(prompt: str, routine_id: Optional[str] = None) -> Optional[str]:
    """Remove only a recognized installed wrapper, keeping the current task text.

    We deliberately do not restore a saved original over a subsequent user edit.
    An unfamiliar/edited wrapper is left alone and keeps its fallback script.
    """
    match = re.match(r"^\[Crystallize routine ([^\]\n]+)\]", prompt)
    if not match or (routine_id and match[1] != routine_id):
        return None
    for builder in (crystallized_prompt, _legacy_crystallized_prompt, prerun_prompt, action_prompt):
        prefix = builder(match[1], "")
        if prompt.startswith(prefix):
            return prompt[len(prefix):]
    return None


def normalize_prompt(prompt: str) -> str:
    """Ignore our installed wrappers/hints and harmless prose whitespace."""
    from . import tuning

    unwrapped = unwrap_prompt(prompt)
    prompt = unwrapped if unwrapped is not None else prompt
    for name in ("EFFICIENCY_HINT", "LEGACY_EFFICIENCY_HINT"):
        hint = getattr(tuning, name, "")
        if hint:
            prompt = prompt.replace(hint, "")
    prompt = prompt.replace("\r\n", "\n")
    # Whitespace in fenced code can change a program, so retain it exactly.
    parts = re.split(r"(```[\s\S]*?```)", prompt)
    return "".join(part if part.startswith("```") else " ".join(part.split()) for part in parts).strip()


def _tuning_for_job(job: dict[str, Any]) -> Optional[dict[str, Any]]:
    t = store.get_tuning(job.get("name") or job["id"])
    if t and (not t.get("job_id") or t["job_id"] == job["id"]):
        return t
    return next((t for t in store.list_tuning() if t.get("job_id") == job["id"]), None)


def _same_tools(first: Any, second: Any) -> bool:
    return first is second if first is None or second is None else set(first) == set(second)


def _same_level(first: Any, second: Any) -> bool:
    return str(first or "").strip().lower() == str(second or "").strip().lower()


def configuration(job: dict[str, Any], routine: Optional[dict[str, Any]] = None,
                  tuning: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The user's current task/configuration, with recognized optimizer changes removed.

    Installed reasoning/tool lists are mapped to their saved baseline only while
    they still equal what we installed. A user override remains visible as drift.
    """
    configured = dict(job)
    tune = tuning if tuning is not None else _tuning_for_job(job)
    if tune and tune.get("status") in ("starting", "trial", "kept"):
        if tune.get("level") and _same_level(job.get("reasoning_effort"), tune["level"]):
            configured["reasoning_effort"] = tune.get("previous")
        if tune.get("tools_changed") and _same_tools(job.get("enabled_toolsets"), tune.get("toolsets")):
            configured["enabled_toolsets"] = tune.get("previous_toolsets")
    meta = (routine or {}).get("meta") or {}
    script = job.get("script")
    routine_id = (routine or {}).get("id")
    installed_script = meta.get("installed_script") or (f"crystallize/{routine_id}.py" if routine_id else None)
    if installed_script and script == installed_script:
        script = meta.get("own_script")
    interpreter = job.get("interpreter")
    if meta.get("installed_interpreter") and interpreter == meta["installed_interpreter"]:
        interpreter = meta.get("own_interpreter")
    steps = (routine or {}).get("steps") or {}
    cache = store.get_setting("fetch_cache:" + job["id"])
    if cache is not None:
        from . import fetch_cache

        cache = fetch_cache.validate(cache)
    # Include parameter bindings, declared input schemas and cache rules. Baseline
    # measurements/source execution IDs are evidence, not executable configuration.
    routine_schema = {k: v for k, v in steps.items() if k not in ("baseline", "source_execution_id")}
    return {"schema": 1, "job_id": job["id"], "prompt": normalize_prompt(job.get("prompt") or ""),
            "route": modelinfo.route(configured), "reasoning": modelinfo.current_level(configured),
            "toolsets": sorted(configured["enabled_toolsets"]) if configured.get("enabled_toolsets") is not None else None,
            "skills": configured.get("skills", configured.get("skill")), "script": script,
            "interpreter": interpreter, "workdir": configured.get("workdir"),
            "no_agent": meta.get("original_no_agent", False) if meta.get("mode") == "no_ai" and job.get("no_agent") else bool(job.get("no_agent")),
            "contract": contracts.fingerprint(contracts.get(job["id"])), "fetch_cache": cache, "routine": routine_schema}


def configuration_fingerprint(job: dict[str, Any], routine: Optional[dict[str, Any]] = None,
                              tuning: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    snapshot = configuration(job, routine, tuning)
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return {"fingerprint": digest, "configuration": snapshot}


def _fresh_meta(routine: dict[str, Any]) -> dict[str, Any]:
    current = store.get_routine(routine["id"])
    return dict((current or routine).get("meta") or {})


def _snapshot_campaign(routine: Optional[dict[str, Any]]) -> None:
    if routine:
        from . import receipts

        snapshot = getattr(receipts, "snapshot_routine", None)
        if snapshot:
            snapshot(routine)


SDK_FALLBACK_NOTE = ("[Crystallize SDK fallback] Automatic nested tool replay is unavailable. Complete this job with its "
                     "configured main model and reasoning, using the normal host tools and approvals. Any Crystallize "
                     "wrapper below is recipe context, not evidence of current data.\n\n")


def _restore_attachment(routine: dict[str, Any], meta: dict[str, Any], *, unsupported_dispatch: bool = False) -> bool:
    """Detach only fields we still own; return whether the script can be removed."""
    job = _bound_job(meta.get("job_id") or routine["source_ref"])
    if not job:
        return True
    prompt = job.get("prompt") or ""
    body = unwrap_prompt(prompt, routine["id"])
    flags = []
    if body is not None:
        from . import tuning

        tune = _tuning_for_job(job)
        if (tune and tune.get("hint_added", True) and
                (tune.get("status") not in ("starting", "trial", "kept") or not tune.get("hint"))):
            for name in ("EFFICIENCY_HINT", "LEGACY_EFFICIENCY_HINT"):
                hint = getattr(tuning, name, "")
                if hint:
                    body = body.replace(hint, "")
        flags += ["--prompt", body]
    # If someone edited the generated wrapper's instructions, keep the script in
    # fallback mode. Their text stays intact and the agent sees a failed routine.
    unrecognized_wrapper = MARKER in prompt and body is None
    installed_script = meta.get("installed_script", f"crystallize/{routine['id']}.py")
    script_owned = bool(installed_script) and job.get("script") == installed_script
    if unsupported_dispatch and unrecognized_wrapper and not prompt.startswith(SDK_FALLBACK_NOTE):
        flags += ["--prompt", SDK_FALLBACK_NOTE + prompt]
    if script_owned and (not unrecognized_wrapper or unsupported_dispatch):
        flags += ["--script", meta.get("own_script") or ""]
    if meta.get("mode") == "no_ai" and job.get("no_agent"):
        if not meta.get("original_no_agent", False):
            flags.append("--agent")
    if ((not unrecognized_wrapper or unsupported_dispatch) and meta.get("installed_interpreter") and
            job.get("interpreter") == meta["installed_interpreter"]):
        flags += ["--interpreter", meta.get("own_interpreter") or ""]
    if flags:
        _edit_job(job["id"], *flags)
    if script_owned and unrecognized_wrapper and not unsupported_dispatch:
        from . import prerun

        kwargs = ({"own_interpreter": meta.get("own_interpreter"), "own_workdir": meta.get("own_workdir"),
                   "hermes_python": sys.executable} if meta.get("own_interpreter") or meta.get("own_workdir") else {})
        prerun.write_script(routine["id"], routine["name"], meta.get("own_script"), False, **kwargs)
        # The retained script must return a normal-agent fallback even when the
        # attachment previously skipped the agent entirely.
        store.update_routine(routine["id"], meta={**_fresh_meta(routine), "mode": "prerun"})
    return not (script_owned and unrecognized_wrapper and not unsupported_dispatch)


def _uses_automatic_replay(routine: dict[str, Any]) -> bool:
    meta = routine.get("meta") or {}
    return meta.get("mode") in ("prerun", "no_ai") or bool(meta.get("installed_script") and meta.get("mode") != "tool")


def restore_unsupported_replay(routine: Any) -> bool:
    """Restore owned attachments on SDK downgrade; keep pending recovery on failure.

    A cron job remains scheduled. Its original agent/script settings are restored
    through public CLI fields, while later user edits remain intact.
    """
    from . import prerun

    if isinstance(routine, str):
        routine = store.get_routine(routine)
    if not routine or routine.get("status") != "live" or not _uses_automatic_replay(routine) or _replay_available():
        return False
    meta = _fresh_meta(routine)
    _snapshot_campaign(routine)
    reason = replay_support().get("reason") or "Guarded nested tool dispatch is unavailable in the public Hermes SDK."
    try:
        if _restore_attachment(routine, meta, unsupported_dispatch=True):
            prerun.remove_script(routine["id"])
    except Exception as exc:  # noqa: BLE001 — never discard the only recovery record
        meta["dispatch_recovery"] = {"pending": True, "reason": reason, "error": str(exc),
            "instructions": "Retry Undo in Crystallize. If Hermes CLI edits remain unavailable, use Hermes job controls "
                            "to restore the saved original script/interpreter and enable the job's main agent."}
        meta["ready"] = False
        store.update_routine(routine["id"], meta=meta)
        logger.warning("crystallize: public SDK attachment recovery pending for %s", routine["id"], exc_info=True)
        return True
    now = time.time()
    meta = _fresh_meta(routine)
    meta.update({"mode": "tool", "matches": 0, "ready": False, "automatic_replay_validated": False,
                 "last_fingerprint": None, "undone_at": now, "undo_reason": reason,
                 "dispatch_recovery": {"pending": False, "restored_at": now, "reason": reason},
                 "revalidation": {"at": now, "reason": reason, "needs_rebuild": False,
                                  "previous_mode": (routine.get("meta") or {}).get("mode"),
                                  "validation_mode": "normal_host_observations"}})
    job = _bound_job(routine["source_ref"])
    if job:
        meta["config_snapshot"] = configuration_fingerprint(job, {**routine, "meta": meta})
    store.update_routine(routine["id"], status="testing", meta=meta)
    _snapshot_campaign(store.get_routine(routine["id"]))
    return True


def invalidate(routine_id: str, reason: str = "source schema changed", *,
               snapshot: Optional[dict[str, Any]] = None) -> None:
    """Fall back to normal execution and rebuild/retest using fresh normal runs."""
    from . import prerun

    routine = store.get_routine(routine_id)
    if not routine or routine["status"] == "retired":
        return
    _snapshot_campaign(routine)
    meta = _fresh_meta(routine)
    previous_mode = meta.get("mode")
    if routine["status"] == "live":
        if _restore_attachment(routine, meta):
            prerun.remove_script(routine_id)
    meta = _fresh_meta(routine)
    now = time.time()
    meta.update({"matches": 0, "ready": False, "last_fingerprint": None,
                 "revalidation": {"at": now, "reason": reason, "needs_rebuild": True,
                                  "previous_mode": previous_mode}})
    if snapshot:
        meta["config_snapshot"] = snapshot
    store.update_routine(routine_id, status="testing", meta=meta)
    _snapshot_campaign(store.get_routine(routine_id))


def check_drift(routine: Any) -> bool:
    """True only while a routine still matches its recorded task/configuration."""
    if isinstance(routine, str):
        routine = store.get_routine(routine)
    if not routine or routine.get("status") == "retired":
        return False
    if restore_unsupported_replay(routine):
        return False
    if (routine.get("meta") or {}).get("promoting"):
        return False  # an in-flight/crash-recovery transaction is not configuration drift
    if not routine.get("source_ref"):
        return False  # malformed/unbound recipes never execute against a guessed job
    job = _bound_job(routine["source_ref"])
    if not job:
        invalidate(routine["id"], "the original cron job no longer exists")
        return False
    current = configuration_fingerprint(job, routine)
    saved = (routine.get("meta") or {}).get("config_snapshot")
    if not saved:
        # Existing routines acquire a baseline without silently changing jobs.
        store.update_routine(routine["id"], meta={**_fresh_meta(routine), "config_snapshot": current})
        return True
    if saved.get("fingerprint") == current["fingerprint"]:
        return True
    changed = sorted(k for k, v in current["configuration"].items() if (saved.get("configuration") or {}).get(k) != v)
    invalidate(routine["id"], "configuration changed: " + ", ".join(changed), snapshot=current)
    return False


def refresh_drift() -> None:
    for summary in store.list_routines():
        routine = store.get_routine(summary["id"])
        if routine and routine.get("source") == "cron":
            check_drift(routine)


# ---------------------------------------------------------------- actions

def try_job(job: str) -> dict[str, Any]:
    a = analyze.analyze_job(job)
    if a.job_id is None:
        raise LifecycleError(f"No recorded runs for {job!r}.")
    found = _bound_job(a.job_id)
    if not found or found.get("state") == "completed":
        raise LifecycleError(f"{a.job_name or job} has finished, so there's nothing left to test.")
    existing = store.routine_for_job(a.job_id)
    if existing and existing["status"] == "live":
        raise LifecycleError(f"{a.job_name} is already live.")
    try:
        routine = steplist.build(a)
    except steplist.NotCrystallizable as exc:
        raise LifecycleError(str(exc)) from exc
    if analyze.regular_actions(history.runs_for_job(a.job_id, only_usable=True)):
        routine["leaves_to_ai"] = True  # the best-run path skips action steps, but the job still takes them
    rid = steplist.routine_id(a.job_id)
    _snapshot_campaign(store.get_routine(rid))
    data = {"id": rid, "source_ref": a.job_id, "steps": routine, "meta": {}}
    store.save_routine(
        rid, name=a.job_name or a.job_id, source="cron", source_ref=a.job_id, status="testing",
        steps=routine,
        meta={"matches": 0, "needed": MATCHES_NEEDED, "tests": [], "created_at": time.time(),
              "validation_mode": "guarded_replay" if _replay_available() else "normal_host_observations",
              "automatic_replay_validated": False,
              "config_snapshot": configuration_fingerprint(found, data)},
    )
    saved = store.get_routine(rid)
    if saved:
        saved["promotion_availability"] = promotion_availability(saved)
    return saved


def _align(routine_steps: list[dict], traces: list[dict]) -> list[Optional[dict]]:
    """Match each routine step to the same tool in the real run, in order."""
    pairs = analyze._lcs([s["tool"] for s in routine_steps], [t["tool_name"] for t in traces])
    matched: list[Optional[dict]] = [None] * len(routine_steps)
    for i, j in pairs:
        matched[i] = traces[j]
    return matched


def _reference_traces(routine: dict[str, Any], execution_id: str) -> list[dict]:
    """What a test run is compared against.

    Common-steps routines: the real run that just happened (same steps every run).
    Best-run routines: the run the steps were frozen from, since Hermes takes a
    different path each time and the latest run may not use these tools at all.
    """
    steps = routine.get("steps") or {}
    if steps.get("strategy") == "best-run" and steps.get("source_execution_id"):
        return store.execution_traces(steps["source_execution_id"])
    return store.execution_traces(execution_id)


def compare(routine: dict[str, Any], result: dict[str, Any], traces: list[dict], *,
            answer: Any = None) -> tuple[bool, str]:
    if not result.get("ok"):
        return False, f"step {result.get('failed_step')} ({result.get('tool')}) failed: {result.get('reason')}"
    originals = _align(routine["steps"]["steps"], traces)
    if len(result.get("outputs") or []) != len(routine["steps"]["steps"]):
        return False, "the routine returned an incomplete step list"
    for out, orig, step in zip(result["outputs"], originals, routine["steps"]["steps"]):
        if orig is None:
            return False, f"the real run didn't use {step['tool']} this time"
        ok, why = runner.same_shape(out["output"], orig.get("result_preview"), orig.get("result_len"))
        if not ok:
            return False, f"step {out['step']} ({step['tool']}): {why}"
    spec = contracts.get(routine["source_ref"]) if routine.get("source_ref") else None
    if spec is not None:
        report = contracts.evaluate(spec, answer=answer, sources=result["outputs"], actions=traces)
        if not report["ok"]:
            return False, "outcome contract: " + report["why"]
    return True, "all steps matched"


def record_test(routine: dict[str, Any], ok: bool, why: str, source: str, *,
                outputs: Optional[list[dict[str, Any]]] = None, execution_id: Optional[str] = None,
                validation_mode: str = "guarded_replay") -> dict[str, Any]:
    meta = _fresh_meta(routine)
    meta["matches"] = (meta.get("matches", 0) + 1) if ok else 0
    tests = list(meta.get("tests", []))[-9:]
    tests.append({"at": time.time(), "ok": ok, "why": why, "source": source,
                  "execution_id": execution_id, "validation_mode": validation_mode})
    meta["tests"] = tests
    meta["ready"] = meta["matches"] >= meta.get("needed", MATCHES_NEEDED)
    meta["validation_mode"] = validation_mode
    meta["automatic_replay_validated"] = bool(meta["ready"] and validation_mode == "guarded_replay")
    if ok and outputs is not None:
        from . import prerun

        meta["source_schemas"] = [prerun._source_schema(out.get("output")) for out in outputs]
    if meta["ready"] and meta.get("revalidation"):
        meta["revalidation"] = {**meta["revalidation"], "validated_at": time.time()}
    store.update_routine(routine["id"], meta=meta)
    return meta


def _observation_match(routine: dict[str, Any], traces: list[dict], answer: Any) -> tuple[bool, str]:
    """Validate recorded normal host work without dispatching any recipe step."""
    steps = (routine.get("steps") or {}).get("steps") or []
    if not steps or analyze.replay_safety_error(routine["steps"]):
        return False, "The learned read recipe is incomplete or unsafe."
    aligned = _align(steps, traces)
    for step, trace in zip(steps, aligned):
        if trace is None:
            return False, f"The normal run did not use recipe tool {step['tool']}."
        try:
            args = json.loads(trace.get("args_json") or "{}")
        except (ValueError, TypeError):
            return False, "A recorded tool's arguments are incomplete."
        if analyze.observation_safety_error(step["tool"], args):
            return False, "A normal run used a tool operation outside the supported read recipe."
        text = trace.get("result_preview")
        length = trace.get("result_len")
        if not isinstance(text, str) or not isinstance(length, int) or len(text) != length:
            return False, "Complete current result evidence is required for a normal host observation."
        if trace.get("status") not in (None, "ok", "success", "succeeded", "completed") or runner.step_failure(text):
            return False, "A normal host read did not complete successfully."
        digest = trace.get("result_sha256")
        if digest and digest != hashlib.sha256(text.encode("utf-8", "replace")).hexdigest():
            return False, "The captured result does not match its complete-result digest."
    spec = contracts.get(routine["source_ref"])
    report = contracts.evaluate(spec, answer=answer, sources=traces, actions=traces)
    if not report["ok"]:
        return False, "outcome contract: " + report["why"]
    return True, "Normal host observations match the read recipe; no routine tools were executed."


def observe_host_runs(routine: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Learn readiness from fresh normal host runs when nested replay is unavailable."""
    meta = _fresh_meta(routine)
    since = (meta.get("revalidation") or {}).get("at", meta.get("created_at", 0))
    seen = {test.get("execution_id") for test in meta.get("tests") or [] if test.get("execution_id")}
    cursor = meta.get("observation_cursor") or {}
    cursor_at = cursor.get("started", since)
    cursor_ids = set(cursor.get("execution_ids_at_started") or [])
    runs = [run for run in history.runs_for_job(routine["source_ref"])
            if run.scheduled and run.finished and run.outcome in (None, "completed")
            and (run.session_started or run.started_ts) >= max(since, cursor_at)
            and not ((run.session_started or run.started_ts) == cursor_at and run.execution_id in cursor_ids)
            and run.execution_id not in seen]
    if not runs:
        return None
    answers = history.final_answers([run.session_id for run in runs if run.session_id])
    for run in sorted(runs, key=lambda item: (item.session_started or item.started_ts, item.execution_id))[-10:]:
        traces = store.execution_traces(run.execution_id)
        if not traces:
            continue
        ok, why = _observation_match(routine, traces, answers.get(run.session_id or ""))
        meta = record_test(routine, ok, why, "normal host observation", execution_id=run.execution_id,
                           validation_mode="normal_host_observations")
        started = run.session_started or run.started_ts
        ids = cursor_ids | {run.execution_id} if started == cursor_at else {run.execution_id}
        meta["observation_cursor"] = {"started": started, "execution_ids_at_started": sorted(ids)}
        cursor_at, cursor_ids = started, ids
        store.update_routine(routine["id"], meta=meta)
    return {**meta, "execution": "normal_host_observations", "executed": False}


def _rebuild_after_drift(routine: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Learn new targets/bindings from fresh agent runs instead of retesting stale steps."""
    meta = _fresh_meta(routine)
    revalidation = meta.get("revalidation") or {}
    if not revalidation.get("needs_rebuild"):
        return routine
    job = _bound_job(routine["source_ref"])
    if not job:
        return None
    fresh = [r for r in history.runs_for_job(job["id"], only_usable=True)
             if (r.session_started or r.started_ts) >= revalidation["at"]]
    if len(fresh) < analyze.MIN_RUNS:
        return None
    analysis = analyze.analyze_runs(fresh, job.get("prompt") or "")
    try:
        steps = steplist.build(analysis)
    except steplist.NotCrystallizable:
        return None
    if analyze.regular_actions(fresh):
        steps["leaves_to_ai"] = True
    updated = {**routine, "steps": steps, "meta": meta}
    meta.update({"matches": 0, "ready": False,
                 "revalidation": {**revalidation, "needs_rebuild": False, "rebuilt_at": time.time()},
                 "config_snapshot": configuration_fingerprint(job, updated)})
    store.save_routine(routine["id"], name=job.get("name") or routine["name"], source="cron", source_ref=job["id"],
                       status="testing", steps=steps, meta=meta)
    return store.get_routine(routine["id"])


def _execution_answer(execution_id: str, job_id: str) -> Optional[str]:
    run = next((r for r in history.runs_for_job(job_id) if r.execution_id == execution_id), None)
    if not run or not run.session_id:
        return None
    return history.final_answers([run.session_id]).get(run.session_id)


def test_after(execution_id: str, job_id: str, dispatch: runner.Dispatch) -> Optional[dict[str, Any]]:
    """After a normal scheduled run: run the routine's steps and compare."""
    routine = store.routine_for_job(job_id)
    if not routine or routine["status"] != "testing":
        return None
    if not check_drift(routine):
        return None
    routine = _rebuild_after_drift(routine)
    if not routine:
        return None
    if not _replay_available():
        return observe_host_runs(routine)
    traces = _reference_traces(routine, execution_id)
    if not traces:
        return None
    result = runner.run(routine["steps"], dispatch)
    answer = _execution_answer(execution_id, job_id) if contracts.get(job_id) is not None else None
    ok, why = compare(routine, result, traces, answer=answer)
    return record_test(routine, ok, why, "scheduled run", outputs=result.get("outputs"))


def test_now(job: str, dispatch: runner.Dispatch) -> dict[str, Any]:
    """Run the routine once right now against the latest recorded run."""
    found = cron_job(job)
    a_runs = history.runs_for_job((found or {}).get("id") or job, only_usable=True)
    if not a_runs:
        raise LifecycleError(f"No recorded runs for {job!r}.")
    routine = store.routine_for_job(a_runs[-1].job_id or "")
    if not routine or routine["status"] != "testing":
        raise LifecycleError("Try the job first so there's a routine to test.")
    if not check_drift(routine):
        raise LifecycleError("The job changed. Its normal runs are being collected to rebuild and retest the routine.")
    routine = _rebuild_after_drift(routine)
    if not routine:
        raise LifecycleError("More fresh scheduled runs are needed to rebuild this changed job's routine.")
    if not _replay_available():
        observed = observe_host_runs(routine)
        if observed is not None:
            return observed
        return {**_fresh_meta(routine), "execution": "normal_host_observations", "executed": False,
                "why": "No fresh completed normal host runs are available yet. Crystallize did not execute any tools."}
    result = runner.run(routine["steps"], dispatch)
    answer = history.final_answers([a_runs[-1].session_id]).get(a_runs[-1].session_id) if a_runs[-1].session_id else None
    ok, why = compare(routine, result, _reference_traces(routine, a_runs[-1].execution_id), answer=answer)
    return record_test(routine, ok, why, "manual", outputs=result.get("outputs"))


def action_prompt(routine_id: str, original: str) -> str:
    """Prompt for a job whose routine fetches the data but which still acts on it."""
    return (
        f"{MARKER} {routine_id}] The Script Output block above holds the data this job fetches, "
        "collected just now by a tested Crystallize routine. Don't fetch it again. Use it to do "
        "the rest of the task, including anything it says to send or change, on every run, even "
        "if it looks done already. Only if the Script "
        "Output says the routine failed, do the whole task yourself.\n\n"
        f"Task:\n{original}"
    )


def promote(routine_id: str, mode: str = "tool") -> dict[str, Any]:
    """Switch the job over to its routine.

    "tool" installs recipe guidance for normal main-agent tool calls. Automatic
    "prerun"/"no_ai" require a documented, verified guarded nested-dispatch SDK.
    """
    from . import prerun

    if mode not in ("tool", "prerun", "no_ai"):
        raise LifecycleError(f"Unknown mode {mode!r}.")
    routine = store.get_routine(routine_id)
    if not routine:
        raise LifecycleError("That routine doesn't exist.")
    _snapshot_campaign(routine)
    meta = dict(routine.get("meta") or {})
    if routine["status"] == "live":
        raise LifecycleError("Already live.")
    if mode in ("prerun", "no_ai") and not _replay_available():
        raise LifecycleError("Automatic pre-run and no-AI modes are unavailable: the public Hermes SDK has no verified "
                             "guarded nested-tool dispatch. Choose explicit mode 'tool' for a plan the main agent "
                             "executes through its normal tools and approvals; its model and reasoning stay unchanged.")
    if not check_drift(routine):
        raise LifecycleError("The job changed and its routine needs fresh matching tests before promotion.")
    from . import parameters

    try:
        parameters.validate(routine["steps"])
        unsafe = analyze.replay_safety_error(routine["steps"])
    except (ValueError, TypeError, KeyError) as exc:
        raise LifecycleError("The learned read recipe is incomplete and needs fresh observations.") from exc
    if unsafe:
        raise LifecycleError("The learned read recipe cannot be promoted: " + unsafe)
    meta = _fresh_meta(routine)
    if not meta.get("ready"):
        raise LifecycleError(
            f"It needs {meta.get('needed', MATCHES_NEEDED)} matching test runs in a row first "
            f"(has {meta.get('matches', 0)}).")
    job = _bound_job(routine["source_ref"])
    if not job:
        raise LifecycleError("The cron job no longer exists.")
    if mode == "tool" and job.get("no_agent"):
        raise LifecycleError("Host-tool plans need the job's main agent. Enable it in Hermes job controls before promoting this mode.")
    original = job.get("prompt") or ""
    if MARKER in original:
        raise LifecycleError("This job's prompt already uses a Crystallize routine.")
    spec = contracts.get(job["id"])
    acts = (isinstance(routine["steps"], dict) and routine["steps"].get("leaves_to_ai")) or bool((spec or {}).get("required_actions"))
    if mode == "no_ai" and acts:
        raise LifecycleError("This job sends or changes something, so it needs the AI.")
    own_script = job.get("script") or None
    own_interpreter, own_workdir = job.get("interpreter"), job.get("workdir")
    script_kwargs = ({"own_interpreter": own_interpreter, "own_workdir": own_workdir,
                      "hermes_python": sys.executable} if own_interpreter or own_workdir else {})
    script = None if mode == "tool" else prerun.write_script(routine_id, routine["name"], own_script, mode == "no_ai", **script_kwargs)
    prompt = crystallized_prompt(routine_id, original) if mode == "tool" else action_prompt(routine_id, original) if acts else prerun_prompt(routine_id, original)
    flags = ["--prompt", prompt]
    if mode != "tool":
        flags += ["--script", script]
    if mode == "no_ai":
        flags.append("--no-agent")
    if own_interpreter and mode != "tool":
        flags += ["--interpreter", sys.executable]
    # Saved first, so a failure or a crash during the change can always be undone.
    recovery = {"job_id": job["id"], "original_prompt": original, "own_script": own_script, "at": time.time(),
                "installed_prompt": prompt, "installed_script": script, "mode": mode,
                "own_interpreter": own_interpreter, "own_workdir": own_workdir,
                "installed_interpreter": sys.executable if own_interpreter and mode != "tool" else None,
                "original_no_agent": bool(job.get("no_agent"))}
    store.update_routine(routine_id, meta={**meta, "promoting": recovery})
    try:
        _edit_job(job["id"], *flags)
    except Exception as exc:
        try:
            if _restore_attachment(routine, recovery):
                prerun.remove_script(routine_id)
            store.update_routine(routine_id, meta=meta)
        except Exception:
            logger.warning("crystallize: unfinished promotion needs recovery for %s", routine_id, exc_info=True)
        if isinstance(exc, LifecycleError):
            raise
        raise LifecycleError("Hermes could not finish the public CLI update. Recovery data is preserved; retry Undo.") from exc
    if job["id"] != routine["source_ref"]:
        store.set_routine_job(routine_id, job["id"])
    baseline = (routine["steps"].get("baseline") or {}).get("cost_usd") if isinstance(routine["steps"], dict) else None
    now = time.time()
    # "Before" numbers are the job's original full runs: keep the first Promote time
    # if the routine was promoted before (re-promoted, or moved to a new mode).
    meta.setdefault("first_promoted_at", meta.get("promoted_at") or now)
    meta.pop("promoting", None)
    meta.update({**{k: v for k, v in recovery.items() if k != "at"}, "promoted_at": now, "failures": 0, "live_runs": 0,
                 "baseline_cost": baseline, "skips": [], "no_ai_runs": [], "last_fingerprint": None})
    meta["new_only"] = False  # legacy delta flags cannot omit current source evidence
    # Canonicalization makes the installed wrapper/interpreter/no-agent setting
    # equivalent to the user's original configuration.
    installed = {**job, "prompt": prompt}
    if mode != "tool":
        installed.update({"script": script, "no_agent": mode == "no_ai"})
    if own_interpreter and mode != "tool":
        installed["interpreter"] = sys.executable
    meta["config_snapshot"] = configuration_fingerprint(installed, {**routine, "meta": meta})
    meta.pop("revalidation", None)
    meta.pop("undone_at", None)
    meta.pop("undo_reason", None)
    store.update_routine(routine_id, status="live", meta=meta)
    saved = store.get_routine(routine_id)
    _snapshot_campaign(saved)
    if saved:
        saved["promotion_availability"] = promotion_availability(saved)
    return saved


def recover_promotions() -> None:
    """A Promote cut short by a crash may have changed the job. Put the job back so it can be promoted again."""
    from . import prerun

    for routine in store.list_routines():
        meta = dict(routine.get("meta") or {})
        recovery = meta.get("promoting")
        unsupported = bool(recovery and recovery.get("mode") in {"prerun", "no_ai"} and not _replay_available())
        if not recovery or routine["status"] == "live" or (time.time() - recovery.get("at", 0) < STALE_PROMOTE and not unsupported):
            continue
        try:
            if _restore_attachment(routine, recovery, unsupported_dispatch=unsupported):
                prerun.remove_script(routine["id"])
        except Exception as exc:
            if unsupported:
                meta["dispatch_recovery"] = {"pending": True, "error": str(exc), "reason": replay_support().get("reason"),
                    "instructions": "Retry Undo to restore this unfinished script/no-agent attachment through Hermes CLI."}
                store.update_routine(routine["id"], meta=meta)
            logger.warning("crystallize: couldn't put %s back yet", routine["name"], exc_info=True)
            continue
        meta = _fresh_meta(routine)
        meta.pop("promoting", None)
        if unsupported:
            now = time.time()
            meta.update({"ready": False, "matches": 0, "automatic_replay_validated": False,
                         "dispatch_recovery": {"pending": False, "restored_at": now, "reason": replay_support().get("reason")},
                         "revalidation": {"at": now, "needs_rebuild": False, "validation_mode": "normal_host_observations",
                                          "reason": "An unfinished automatic attachment was restored because guarded dispatch is unavailable."}})
        store.update_routine(routine["id"], meta=meta)


def undo(routine_id: str, reason: str = "you chose Undo") -> dict[str, Any]:
    from . import prerun

    routine = store.get_routine(routine_id)
    if not routine:
        raise LifecycleError("That routine doesn't exist.")
    meta = _fresh_meta(routine)
    recovery = meta.get("promoting")
    attachment = recovery if isinstance(recovery, dict) else meta
    if recovery or (routine["status"] == "live" and meta.get("original_prompt") is not None):
        unsupported = attachment.get("mode") in {"prerun", "no_ai"} and not _replay_available()
        if _restore_attachment(routine, attachment, unsupported_dispatch=unsupported):
            prerun.remove_script(routine_id)
    meta = _fresh_meta(routine)
    meta.pop("promoting", None)
    if meta.get("dispatch_recovery"):
        meta["dispatch_recovery"] = {**meta["dispatch_recovery"], "pending": False, "restored_at": time.time()}
    meta.update({"undone_at": time.time(), "undo_reason": reason})
    store.update_routine(routine_id, status="retired", meta=meta)
    saved = store.get_routine(routine_id)
    _snapshot_campaign(saved)
    return saved


def set_mode(routine_id: str, mode: str) -> dict[str, Any]:
    """Switch a live routine between "prerun" (AI writes the answer) and "no_ai".
    A routine promoted the older way (crystal_run tool) is moved to the script."""
    from . import prerun

    if mode not in ("tool", "prerun", "no_ai"):
        raise LifecycleError(f"Unknown mode {mode!r}.")
    if mode in ("prerun", "no_ai") and not _replay_available():
        raise LifecycleError("Automatic pre-run/no-AI mode requires verified public guarded nested-tool dispatch. "
                             "The main agent can use explicit 'tool' plan mode with its current model and reasoning.")
    routine = store.get_routine(routine_id)
    if not routine or routine["status"] != "live":
        raise LifecycleError("Only a live routine can change mode.")
    if not check_drift(routine):
        raise LifecycleError("The job changed and is being revalidated.")
    meta = _fresh_meta(routine)
    if mode == "no_ai" and ((isinstance(routine["steps"], dict) and routine["steps"].get("leaves_to_ai")) or
                            (contracts.get(routine["source_ref"]) or {}).get("required_actions")):
        raise LifecycleError("This job sends or changes something, so it needs the AI.")
    current = meta.get("mode") or "tool"
    if current == mode:
        return routine
    if current == "tool" or mode == "tool":
        undo(routine_id, reason=f"switching to {mode}")
        store.update_routine(routine_id, status="testing")
        return promote(routine_id, mode=mode)
    script_kwargs = ({"own_interpreter": meta.get("own_interpreter"), "own_workdir": meta.get("own_workdir"),
                      "hermes_python": sys.executable} if meta.get("own_interpreter") or meta.get("own_workdir") else {})
    prerun.write_script(routine_id, routine["name"], meta.get("own_script"), mode == "no_ai", **script_kwargs)
    try:
        _edit_job(routine["source_ref"], "--no-agent" if mode == "no_ai" else "--agent")
    except LifecycleError:
        prerun.write_script(routine_id, routine["name"], meta.get("own_script"), current == "no_ai", **script_kwargs)
        raise
    changed_at = time.time()
    _snapshot_campaign({**routine, "meta": {**meta, "undone_at": changed_at}})
    meta = _fresh_meta(routine)
    meta.update({"mode": mode, "last_fingerprint": None, "promoted_at": changed_at})
    store.update_routine(routine_id, meta=meta)
    saved = store.get_routine(routine_id)
    _snapshot_campaign(saved)
    return saved


def set_when_unchanged(routine_id: str, choice: str) -> None:
    """What a run does when its data hasn't changed: "repeat", "quiet", "run", or "auto"."""
    choice = "run" if choice == "off" else choice
    if choice not in ("auto", "repeat", "quiet", "run"):
        raise LifecycleError(f"Unknown choice {choice!r}.")
    routine = store.get_routine(routine_id)
    if not routine:
        raise LifecycleError("That routine doesn't exist.")
    if choice != "run" and not _replay_available():
        raise LifecycleError("Automatic unchanged-run behavior is unavailable without verified public guarded nested-tool "
                             "dispatch. Use 'run' or 'off' to keep normal main-agent execution and clear legacy choices.")
    meta = _fresh_meta(routine)
    if choice != "run" and meta.get("mode") == "tool":
        raise LifecycleError("Host-tool plan mode does not execute automatic unchanged-run checks. Keep 'run', or use "
                             "a supported automatic mode after verified public guarded dispatch becomes available.")
    meta.pop("skip_unchanged", None)
    meta.pop("when_unchanged", None)
    if choice != "auto":
        meta["when_unchanged"] = choice
    store.update_routine(routine_id, meta=meta)


def live_result(routine_id: str, ok: bool, reason: str = "") -> None:
    """Bookkeeping for crystal_run; undoes the routine after repeated failures."""
    routine = store.get_routine(routine_id)
    if not routine:
        return
    meta = _fresh_meta(routine)
    if ok:
        meta["failures"] = 0
        meta["live_runs"] = meta.get("live_runs", 0) + 1
        store.update_routine(routine_id, meta=meta)
        return
    meta["failures"] = meta.get("failures", 0) + 1
    meta["last_failure"] = {"at": time.time(), "reason": reason}
    store.update_routine(routine_id, meta=meta)
    if meta["failures"] >= FAILURES_BEFORE_UNDO:
        try:
            undo(routine_id, reason=f"failed {meta['failures']} times in a row: {reason}")
        except LifecycleError:
            logger.warning("crystallize: automatic undo failed for %s", routine_id, exc_info=True)
