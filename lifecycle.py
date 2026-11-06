"""A routine's life: Try → test runs → Promote → live (→ automatic Undo on failures).

  try_job      Save the job's step list as a routine in "testing".
  test_after   After each normal scheduled run, run the routine's steps too (tool
               calls only, no AI) and compare their outputs with what the real run
               got. Three matching runs in a row make it ready to promote.
  promote      Give the cron job a pre-run script that runs the routine before the
               AI starts and puts its data in the prompt (or skips the AI when the
               data hasn't changed). The original prompt is kept for Undo.
  undo         Put the original prompt back exactly and remove the script.
  live_result  Called by crystal_run. Two failures in a row undo the routine
               automatically, so a broken routine never keeps a job broken.
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
    return {**os.environ, "HERMES_HOME": str(history._hermes_home())}


def _edit_job(job_id: str, *flags: str) -> None:
    """Change a cron job through Hermes's own CLI (`hermes cron edit`)."""
    proc = subprocess.run(
        [*_hermes_cmd(), "cron", "edit", job_id, *flags],
        capture_output=True, text=True, timeout=60, env=hermes_env(),
    )
    if proc.returncode != 0:
        raise LifecycleError(f"Hermes couldn't update the job: {(proc.stderr or proc.stdout).strip()[:300]}")


def set_toolsets(job_id: str, toolsets: Optional[list[str]]) -> None:
    """Limit a job to these toolsets. None goes back to Hermes's default for cron jobs.
    Hermes's CLI has no flag for this, so it goes through Hermes's own job update."""
    try:
        from cron.jobs import update_job

        if not update_job(job_id, {"enabled_toolsets": toolsets}):
            raise LifecycleError("Hermes couldn't find that job to update its tools.")
    except ImportError as exc:
        raise LifecycleError("This version of Hermes can't limit a job's tools.") from exc


def _set_job_fields(job_id: str, fields: dict[str, Any]) -> None:
    """Update host fields without inventing CLI flags that older Hermes lacks."""
    try:
        from cron.jobs import update_job

        if not update_job(job_id, fields):
            raise LifecycleError("Hermes couldn't find that job to update.")
    except ImportError as exc:
        raise LifecycleError("This version of Hermes can't update the job's script interpreter.") from exc


def _set_prompt(job_id: str, prompt: str) -> None:
    _edit_job(job_id, "--prompt", prompt)


def crystallized_prompt(routine_id: str, original: str) -> str:
    """Prompt for the older tool-call mode (kept for routines promoted that way)."""
    return (
        f"{MARKER} {routine_id}] First call the crystal_run tool with routine_id \"{routine_id}\". "
        "It runs this job's data-collection steps and returns their output. Use that output to "
        "complete the task below without fetching the same data again. If crystal_run returns "
        "ok=false or an error, ignore it and do the whole task yourself.\n\n"
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
    for builder in (crystallized_prompt, prerun_prompt, action_prompt):
        prefix = builder(match[1], "")
        if prompt.startswith(prefix):
            return prompt[len(prefix):]
    return None


def normalize_prompt(prompt: str) -> str:
    """Ignore our installed wrappers/hints and harmless prose whitespace."""
    from . import tuning

    unwrapped = unwrap_prompt(prompt)
    prompt = unwrapped if unwrapped is not None else prompt
    prompt = prompt.replace(tuning.EFFICIENCY_HINT, "").replace("\r\n", "\n")
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
    if routine and script == meta.get("installed_script", f"crystallize/{routine['id']}.py"):
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


def _restore_attachment(routine: dict[str, Any], meta: dict[str, Any]) -> bool:
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
            body = body.replace(tuning.EFFICIENCY_HINT, "")
        flags += ["--prompt", body]
    # If someone edited the generated wrapper's instructions, keep the script in
    # fallback mode. Their text stays intact and the agent sees a failed routine.
    unrecognized_wrapper = MARKER in prompt and body is None
    installed_script = meta.get("installed_script", f"crystallize/{routine['id']}.py")
    script_owned = job.get("script") == installed_script
    if script_owned and not unrecognized_wrapper:
        flags += ["--script", meta.get("own_script") or ""]
    if meta.get("mode") == "no_ai" and job.get("no_agent"):
        flags.append("--agent")
    if flags:
        _edit_job(job["id"], *flags)
    if (not unrecognized_wrapper and meta.get("installed_interpreter") and
            job.get("interpreter") == meta["installed_interpreter"]):
        _set_job_fields(job["id"], {"interpreter": meta.get("own_interpreter")})
    if script_owned and unrecognized_wrapper:
        from . import prerun

        kwargs = ({"own_interpreter": meta.get("own_interpreter"), "own_workdir": meta.get("own_workdir"),
                   "hermes_python": sys.executable} if meta.get("own_interpreter") or meta.get("own_workdir") else {})
        prerun.write_script(routine["id"], routine["name"], meta.get("own_script"), False, **kwargs)
        # The retained script must return a normal-agent fallback even when the
        # attachment previously skipped the agent entirely.
        store.update_routine(routine["id"], meta={**_fresh_meta(routine), "mode": "prerun"})
    return not (script_owned and unrecognized_wrapper)


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
    if analyze.regular_actions(history.load_runs(a.job_name or a.job_id, only_usable=True)):
        routine["leaves_to_ai"] = True  # the best-run path skips action steps, but the job still takes them
    rid = steplist.routine_id(a.job_id)
    _snapshot_campaign(store.get_routine(rid))
    data = {"id": rid, "source_ref": a.job_id, "steps": routine, "meta": {}}
    store.save_routine(
        rid, name=a.job_name or a.job_id, source="cron", source_ref=a.job_id, status="testing",
        steps=routine,
        meta={"matches": 0, "needed": MATCHES_NEEDED, "tests": [], "created_at": time.time(),
              "config_snapshot": configuration_fingerprint(found, data)},
    )
    return store.get_routine(rid)


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
                outputs: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    meta = _fresh_meta(routine)
    meta["matches"] = (meta.get("matches", 0) + 1) if ok else 0
    tests = list(meta.get("tests", []))[-9:]
    tests.append({"at": time.time(), "ok": ok, "why": why, "source": source})
    meta["tests"] = tests
    meta["ready"] = meta["matches"] >= meta.get("needed", MATCHES_NEEDED)
    if ok and outputs is not None:
        from . import prerun

        meta["source_schemas"] = [prerun._source_schema(out.get("output")) for out in outputs]
    if meta["ready"] and meta.get("revalidation"):
        meta["revalidation"] = {**meta["revalidation"], "validated_at": time.time()}
    store.update_routine(routine["id"], meta=meta)
    return meta


def _rebuild_after_drift(routine: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Learn new targets/bindings from fresh agent runs instead of retesting stale steps."""
    meta = _fresh_meta(routine)
    revalidation = meta.get("revalidation") or {}
    if not revalidation.get("needs_rebuild"):
        return routine
    job = _bound_job(routine["source_ref"])
    if not job:
        return None
    fresh = [r for r in history.load_runs(job["id"], only_usable=True)
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
    run = next((r for r in history.load_runs(job_id) if r.execution_id == execution_id), None)
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
    traces = _reference_traces(routine, execution_id)
    if not traces:
        return None
    result = runner.run(routine["steps"], dispatch)
    answer = _execution_answer(execution_id, job_id) if contracts.get(job_id) is not None else None
    ok, why = compare(routine, result, traces, answer=answer)
    return record_test(routine, ok, why, "scheduled run", outputs=result.get("outputs"))


def test_now(job: str, dispatch: runner.Dispatch) -> dict[str, Any]:
    """Run the routine once right now against the latest recorded run."""
    a_runs = history.load_runs(job, only_usable=True)
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


def promote(routine_id: str, mode: str = "prerun") -> dict[str, Any]:
    """Switch the job over to its routine.

    mode "prerun" (default): a pre-run script runs the routine, the AI gets the
    data in its prompt and writes the answer; unchanged data skips the AI.
    mode "no_ai": the routine's output is the job's result; no AI at all.
    """
    from . import prerun

    if mode not in ("prerun", "no_ai"):
        raise LifecycleError(f"Unknown mode {mode!r}.")
    routine = store.get_routine(routine_id)
    if not routine:
        raise LifecycleError("That routine doesn't exist.")
    _snapshot_campaign(routine)
    meta = dict(routine.get("meta") or {})
    if routine["status"] == "live":
        raise LifecycleError("Already live.")
    if not check_drift(routine):
        raise LifecycleError("The job changed and its routine needs fresh matching tests before promotion.")
    meta = _fresh_meta(routine)
    if not meta.get("ready"):
        raise LifecycleError(
            f"It needs {meta.get('needed', MATCHES_NEEDED)} matching test runs in a row first "
            f"(has {meta.get('matches', 0)}).")
    job = _bound_job(routine["source_ref"])
    if not job:
        raise LifecycleError("The cron job no longer exists.")
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
    script = prerun.write_script(routine_id, routine["name"], own_script, mode == "no_ai", **script_kwargs)
    prompt = action_prompt(routine_id, original) if acts else prerun_prompt(routine_id, original)
    flags = ["--prompt", prompt, "--script", script]
    if mode == "no_ai":
        flags.append("--no-agent")
    # Saved first, so a failure or a crash during the change can always be undone.
    recovery = {"job_id": job["id"], "original_prompt": original, "own_script": own_script, "at": time.time(),
                "installed_prompt": prompt, "installed_script": script, "mode": mode,
                "own_interpreter": own_interpreter, "own_workdir": own_workdir,
                "installed_interpreter": sys.executable if own_interpreter else None,
                "original_no_agent": bool(job.get("no_agent"))}
    store.update_routine(routine_id, meta={**meta, "promoting": recovery})
    try:
        _edit_job(job["id"], *flags)
        if own_interpreter:
            _set_job_fields(job["id"], {"interpreter": sys.executable})
    except LifecycleError:
        try:
            if _restore_attachment(routine, recovery):
                prerun.remove_script(routine_id)
            store.update_routine(routine_id, meta=meta)
        except LifecycleError:
            logger.warning("crystallize: unfinished promotion needs recovery for %s", routine_id, exc_info=True)
        raise
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
    # A job with cheaper settings already on gets new-items-only too, unless it acts on its data.
    tune = _tuning_for_job(job)
    if tune and tune.get("status") in ("trial", "kept") and mode == "prerun" and not acts:
        meta["new_only"] = True
        tune["new_only"] = True
        store.save_tuning(tune.get("job_name") or routine["name"], tune)
    # Canonicalization makes the installed wrapper/interpreter/no-agent setting
    # equivalent to the user's original configuration.
    installed = {**job, "prompt": prompt, "script": script, "no_agent": mode == "no_ai"}
    if own_interpreter:
        installed["interpreter"] = sys.executable
    meta["config_snapshot"] = configuration_fingerprint(installed, {**routine, "meta": meta})
    meta.pop("revalidation", None)
    meta.pop("undone_at", None)
    meta.pop("undo_reason", None)
    store.update_routine(routine_id, status="live", meta=meta)
    saved = store.get_routine(routine_id)
    _snapshot_campaign(saved)
    return saved


def recover_promotions() -> None:
    """A Promote cut short by a crash may have changed the job. Put the job back so it can be promoted again."""
    from . import prerun

    for routine in store.list_routines():
        meta = dict(routine.get("meta") or {})
        recovery = meta.get("promoting")
        if not recovery or routine["status"] == "live" or time.time() - recovery.get("at", 0) < STALE_PROMOTE:
            continue
        try:
            if _restore_attachment(routine, recovery):
                prerun.remove_script(routine["id"])
        except LifecycleError:
            logger.warning("crystallize: couldn't put %s back yet", routine["name"], exc_info=True)
            continue
        meta = _fresh_meta(routine)
        meta.pop("promoting", None)
        store.update_routine(routine["id"], meta=meta)


def undo(routine_id: str, reason: str = "you chose Undo") -> dict[str, Any]:
    from . import prerun

    routine = store.get_routine(routine_id)
    if not routine:
        raise LifecycleError("That routine doesn't exist.")
    meta = _fresh_meta(routine)
    if routine["status"] == "live" and meta.get("original_prompt") is not None:
        if _restore_attachment(routine, meta):
            prerun.remove_script(routine_id)
    meta = _fresh_meta(routine)
    meta.update({"undone_at": time.time(), "undo_reason": reason})
    store.update_routine(routine_id, status="retired", meta=meta)
    saved = store.get_routine(routine_id)
    _snapshot_campaign(saved)
    return saved


def set_mode(routine_id: str, mode: str) -> dict[str, Any]:
    """Switch a live routine between "prerun" (AI writes the answer) and "no_ai".
    A routine promoted the older way (crystal_run tool) is moved to the script."""
    from . import prerun

    if mode not in ("prerun", "no_ai"):
        raise LifecycleError(f"Unknown mode {mode!r}.")
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
    if current == "tool":
        undo(routine_id, reason=f"switching to {mode}")
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
    if choice not in ("auto", "repeat", "quiet", "run"):
        raise LifecycleError(f"Unknown choice {choice!r}.")
    routine = store.get_routine(routine_id)
    if not routine:
        raise LifecycleError("That routine doesn't exist.")
    meta = _fresh_meta(routine)
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
