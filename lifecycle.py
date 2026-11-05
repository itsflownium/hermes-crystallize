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

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import analyze, history, runner, steplist, store

logger = logging.getLogger(__name__)

MATCHES_NEEDED = 3
FAILURES_BEFORE_UNDO = 2
MARKER = "[Crystallize routine"


class LifecycleError(RuntimeError):
    """A user-facing reason an action can't happen."""


# ---------------------------------------------------------------- cron job access

def _jobs_file() -> Path:
    return history._hermes_home() / "cron" / "jobs.json"


def cron_job(job_id: str) -> Optional[dict[str, Any]]:
    """Read one cron job (read-only)."""
    try:
        data = json.loads(_jobs_file().read_text())
    except (OSError, ValueError):
        return None
    jobs = data.get("jobs", data) if isinstance(data, dict) else data
    for job in jobs if isinstance(jobs, list) else []:
        if job.get("id") == job_id or job.get("name") == job_id:
            return job
    return None


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


def _edit_job(job_id: str, *flags: str) -> None:
    """Change a cron job through Hermes's own CLI (`hermes cron edit`)."""
    proc = subprocess.run(
        [*_hermes_cmd(), "cron", "edit", job_id, *flags],
        capture_output=True, text=True, timeout=60, env={**os.environ},
    )
    if proc.returncode != 0:
        raise LifecycleError(f"Hermes couldn't update the job: {(proc.stderr or proc.stdout).strip()[:300]}")


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
        "content is missing. Write the answer straight from it. Only if the Script Output says "
        "the routine failed, do the whole task yourself.\n\n"
        f"Task:\n{original}"
    )


# ---------------------------------------------------------------- actions

def try_job(job: str) -> dict[str, Any]:
    a = analyze.analyze_job(job)
    if a.job_id is None:
        raise LifecycleError(f"No recorded runs for {job!r}.")
    existing = store.routine_for_job(a.job_id)
    if existing and existing["status"] == "live":
        raise LifecycleError(f"{a.job_name} is already live.")
    try:
        routine = steplist.build(a)
    except steplist.NotCrystallizable as exc:
        raise LifecycleError(str(exc)) from exc
    rid = steplist.routine_id(a.job_id)
    store.save_routine(
        rid, name=a.job_name or a.job_id, source="cron", source_ref=a.job_id, status="testing",
        steps=routine,
        meta={"matches": 0, "needed": MATCHES_NEEDED, "tests": [], "created_at": time.time()},
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


def compare(routine: dict[str, Any], result: dict[str, Any], traces: list[dict]) -> tuple[bool, str]:
    if not result.get("ok"):
        return False, f"step {result.get('failed_step')} ({result.get('tool')}) failed: {result.get('reason')}"
    originals = _align(routine["steps"]["steps"], traces)
    for out, orig, step in zip(result["outputs"], originals, routine["steps"]["steps"]):
        if orig is None:
            return False, f"the real run didn't use {step['tool']} this time"
        ok, why = runner.same_shape(out["output"], orig.get("result_preview"), orig.get("result_len"))
        if not ok:
            return False, f"step {out['step']} ({step['tool']}): {why}"
    return True, "all steps matched"


def record_test(routine: dict[str, Any], ok: bool, why: str, source: str) -> dict[str, Any]:
    meta = dict(routine.get("meta") or {})
    meta["matches"] = (meta.get("matches", 0) + 1) if ok else 0
    tests = list(meta.get("tests", []))[-9:]
    tests.append({"at": time.time(), "ok": ok, "why": why, "source": source})
    meta["tests"] = tests
    meta["ready"] = meta["matches"] >= meta.get("needed", MATCHES_NEEDED)
    store.update_routine(routine["id"], meta=meta)
    return meta


def test_after(execution_id: str, job_id: str, dispatch: runner.Dispatch) -> Optional[dict[str, Any]]:
    """After a normal scheduled run: run the routine's steps and compare."""
    routine = store.routine_for_job(job_id)
    if not routine or routine["status"] != "testing":
        return None
    traces = _reference_traces(routine, execution_id)
    if not traces:
        return None
    result = runner.run(routine["steps"], dispatch)
    ok, why = compare(routine, result, traces)
    return record_test(routine, ok, why, "scheduled run")


def test_now(job: str, dispatch: runner.Dispatch) -> dict[str, Any]:
    """Run the routine once right now against the latest recorded run."""
    a_runs = history.load_runs(job, only_usable=True)
    if not a_runs:
        raise LifecycleError(f"No recorded runs for {job!r}.")
    routine = store.routine_for_job(a_runs[-1].job_id or "")
    if not routine or routine["status"] != "testing":
        raise LifecycleError("Try the job first so there's a routine to test.")
    result = runner.run(routine["steps"], dispatch)
    ok, why = compare(routine, result, _reference_traces(routine, a_runs[-1].execution_id))
    return record_test(routine, ok, why, "manual")


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
    meta = dict(routine.get("meta") or {})
    if routine["status"] == "live":
        raise LifecycleError("Already live.")
    if not meta.get("ready"):
        raise LifecycleError(
            f"It needs {meta.get('needed', MATCHES_NEEDED)} matching test runs in a row first "
            f"(has {meta.get('matches', 0)}).")
    job = cron_job(routine["source_ref"]) or cron_job(routine["name"])
    if not job:
        raise LifecycleError("The cron job no longer exists.")
    original = job.get("prompt") or ""
    if MARKER in original:
        raise LifecycleError("This job's prompt already uses a Crystallize routine.")
    if job.get("script"):
        raise LifecycleError("This job already has its own pre-run script, so Crystallize leaves it alone.")
    script = prerun.write_script(routine_id, routine["name"])
    flags = ["--prompt", prerun_prompt(routine_id, original), "--script", script]
    if mode == "no_ai":
        flags.append("--no-agent")
    try:
        _edit_job(job["id"], *flags)
    except LifecycleError:
        prerun.remove_script(routine_id)
        raise
    if job["id"] != routine["source_ref"]:
        store.set_routine_job(routine_id, job["id"])
    baseline = (routine["steps"].get("baseline") or {}).get("cost_usd") if isinstance(routine["steps"], dict) else None
    now = time.time()
    # "Before" numbers are the job's original full runs: keep the first Promote time
    # if the routine was promoted before (re-promoted, or moved to a new mode).
    meta.setdefault("first_promoted_at", meta.get("promoted_at") or now)
    meta.update({"original_prompt": original, "promoted_at": now, "failures": 0, "live_runs": 0,
                 "baseline_cost": baseline, "mode": mode, "skips": [], "last_fingerprint": None,
                 "skip_unchanged": meta.get("skip_unchanged", True)})
    store.update_routine(routine_id, status="live", meta=meta)
    return store.get_routine(routine_id)


def undo(routine_id: str, reason: str = "you chose Undo") -> dict[str, Any]:
    from . import prerun

    routine = store.get_routine(routine_id)
    if not routine:
        raise LifecycleError("That routine doesn't exist.")
    meta = dict(routine.get("meta") or {})
    if routine["status"] == "live" and meta.get("original_prompt") is not None:
        flags = ["--prompt", meta["original_prompt"]]
        if meta.get("mode") in ("prerun", "no_ai"):
            flags += ["--script", ""]
        if meta.get("mode") == "no_ai":
            flags.append("--agent")
        _edit_job(routine["source_ref"], *flags)
        prerun.remove_script(routine_id)
    meta.update({"undone_at": time.time(), "undo_reason": reason})
    store.update_routine(routine_id, status="retired", meta=meta)
    return store.get_routine(routine_id)


def set_mode(routine_id: str, mode: str) -> dict[str, Any]:
    """Switch a live routine between "prerun" (AI writes the answer) and "no_ai".
    A routine promoted the older way (crystal_run tool) is moved to the script."""
    routine = store.get_routine(routine_id)
    if not routine or routine["status"] != "live":
        raise LifecycleError("Only a live routine can change mode.")
    meta = dict(routine.get("meta") or {})
    current = meta.get("mode") or "tool"
    if current == mode:
        return routine
    if current == "tool":
        undo(routine_id, reason=f"switching to {mode}")
        return promote(routine_id, mode=mode)
    _edit_job(routine["source_ref"], "--no-agent" if mode == "no_ai" else "--agent")
    meta.update({"mode": mode, "last_fingerprint": None})
    store.update_routine(routine_id, meta=meta)
    return store.get_routine(routine_id)


def set_skip_unchanged(routine_id: str, on: bool) -> None:
    routine = store.get_routine(routine_id)
    if not routine:
        raise LifecycleError("That routine doesn't exist.")
    meta = dict(routine.get("meta") or {})
    meta["skip_unchanged"] = bool(on)
    store.update_routine(routine_id, meta=meta)


def live_result(routine_id: str, ok: bool, reason: str = "") -> None:
    """Bookkeeping for crystal_run; undoes the routine after repeated failures."""
    routine = store.get_routine(routine_id)
    if not routine:
        return
    meta = dict(routine.get("meta") or {})
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
