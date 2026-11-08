"""Draft a snapshot script from what a cron job has actually done.

Automatic cron skipping needs a script that reads the job's source data. Writing one by hand is the
hard part, so this drafts it from the job's own repeated read steps. It only drafts: nothing runs and
nothing attaches until you review the source in the normal preview. Anything that isn't a stable,
read-only step is left out and named, so you can tell whether the script is complete.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

from . import analyze, history, lifecycle, store

MIN_RUNS = 3
DRAFT_DIR = "crystallize-drafts"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")

_TEMPLATE = '''#!/usr/bin/env python3
"""Draft change check for cron job {name}, written by Crystallize from its recorded reads.

Review this before attaching it. It reads what the job reads and prints only a fingerprint, so the
AI runs when that changes and reads the data itself, once, exactly as it does now. Anything that
always changes, such as a timestamp in the output, would wake the job on every run.
Any failed read exits with an error, so Hermes does a normal full run instead of skipping.
"""
import hashlib
import json
import subprocess
import sys
import urllib.request

COMMANDS = {commands}
URLS = {urls}
TIMEOUT_SECONDS = 60


def stable(text):
    """The same data in different formatting isn't a change."""
    try:
        return json.loads(text)
    except ValueError:
        return text


snapshot = {{}}
for command in COMMANDS:
    done = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    if done.returncode != 0:
        sys.exit(f"Read failed: {{command}}")
    snapshot[command] = stable(done.stdout)
for url in URLS:
    request = urllib.request.Request(url, headers={{"User-Agent": "crystallize-snapshot"}})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        snapshot[url] = stable(response.read().decode("utf-8", "replace"))
encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
print("The data this job reads has changed (" + hashlib.sha256(encoded.encode()).hexdigest()[:16] + "). Do the task as normal.")
'''


def _urls(args: Any) -> list[str]:
    found = args.get("urls") if isinstance(args, dict) else None
    if isinstance(found, str):
        found = [found]
    return [u for u in found or [] if isinstance(u, str) and u.startswith("https://")]


def plan(job_id: str) -> dict[str, Any]:
    """Which of this job's recorded steps a script can cover, and which it can't."""
    runs = history.runs_for_job(job_id, only_usable=True)
    if len(runs) < MIN_RUNS:
        return {"ok": False, "why": f"This job needs {MIN_RUNS} finished runs to learn from ({len(runs)} so far)."}
    if analyze.regular_actions(runs):
        return {"ok": False, "why": "This job sends or changes things on its runs, so skipping them isn't safe."}
    commands: list[str] = []
    urls: list[str] = []
    left_out: list[dict[str, str]] = []
    for step in analyze.analyze_runs(runs, analyze.job_prompt(job_id)).steps:
        args, tool = step.args, step.tool_name
        unsafe = analyze.observation_safety_error(tool, args)
        if step.kind != "fixed":
            left_out.append({"tool": tool, "why": "its input changes between runs"})
        elif unsafe:
            left_out.append({"tool": tool, "why": unsafe})
        elif tool in ("terminal", "shell") and isinstance(args, dict) and isinstance(args.get("command"), str):
            commands.append(args["command"])
        elif tool == "web_extract" and _urls(args):
            urls.extend(_urls(args))
        else:
            left_out.append({"tool": tool, "why": "a script can't repeat this tool directly"})
    commands, urls = list(dict.fromkeys(commands)), list(dict.fromkeys(urls))
    if not commands and not urls:
        return {"ok": False, "why": "No stable read steps were found to put in a script.", "left_out": left_out}
    return {"ok": True, "commands": commands, "urls": urls, "left_out": left_out, "complete": not left_out}


def draft(job_id: str) -> dict[str, Any]:
    """Write the draft under this profile's scripts folder. It doesn't run it or attach it."""
    job = lifecycle._bound_job(job_id)
    if not job:
        raise ValueError("The cron job was not found.")
    found = plan(job_id)
    if not found["ok"]:
        return found
    source = _TEMPLATE.format(name=json.dumps(job.get("name") or job_id), commands=json.dumps(found["commands"], indent=4),
                              urls=json.dumps(found["urls"], indent=4))
    relative = f"{DRAFT_DIR}/{_SAFE_NAME.sub('-', job_id)}.py"
    path = history._hermes_home() / "scripts" / relative
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(source, encoding="utf-8")
    store.set_setting("starter_script:" + job_id, {"source_script": relative})
    message = ("Drafted from this job's repeated reads. Review it in the preview before attaching." if found["complete"] else
               "Drafted, but some steps couldn't be included. Add them yourself or keep the normal run, because a "
               "snapshot that misses a source would skip runs it shouldn't.")
    return {**found, "source_script": relative, "source": source, "message": message}


# A woken run reads a one-line notice on top of a normal run, which measured about 5% more in one live
# pair. Until a job has its own numbers, it should skip well over that break-even.
BREAK_EVEN_SHARE = 0.8
GOOD_SHARE = 0.5
RECENT_RUNS = 20


def fit(job_id: str, runs: Any = None) -> dict[str, Any]:
    """Would skipping unchanged runs help this job? Judged from what its own recent runs read."""
    from . import rhythm

    runs = (history.runs_for_job(job_id, only_usable=True) if runs is None else runs)[-RECENT_RUNS:]
    seen = [found for found in (rhythm._content_fingerprint(run) for run in runs) if found]
    if len(seen) < MIN_RUNS:
        return {"verdict": "unknown", "message": f"After {MIN_RUNS} finished runs, this shows how often its data changes and whether skipping would help."}
    changes = sum(earlier != later for earlier, later in zip(seen, seen[1:]))
    checks = len(seen) - 1
    share = changes / checks
    counted = f"Its data changed in {changes} of its last {checks} runs"
    if share >= BREAK_EVEN_SHARE:
        return {"verdict": "poor", "share": share, "message": f"{counted}. Attaching a script would likely cost more, because too few runs get skipped to repay the extra cost of each run that wakes."}
    if share <= GOOD_SHARE:
        return {"verdict": "good", "share": share, "message": f"{counted}. This is a good fit: unchanged runs make no AI call."}
    return {"verdict": "mixed", "share": share, "message": f"{counted}. It should save a little, but check its results after attaching."}


def checks_since(job_id: str, since: float) -> Optional[int]:
    """Scheduled checks that finished since a time, from Hermes's own cron records. These include
    the checks that skipped the AI, which leave no session behind."""
    from datetime import datetime

    conn = history._open_readonly(history._hermes_home() / "cron" / "executions.db")
    if conn is None:
        return None
    try:
        rows = conn.execute("SELECT started_at FROM executions WHERE job_id = ? AND status = 'completed' "
                            "AND scheduled_instant IS NOT NULL", (job_id,)).fetchall()
    except Exception:  # noqa: BLE001 — an unknown schema means no count
        return None
    finally:
        conn.close()
    count = 0
    for (started,) in rows:
        try:
            when = datetime.fromisoformat(started).timestamp() if isinstance(started, str) else float(started)
        except (TypeError, ValueError):
            continue
        count += when >= since
    return count


def since_attached(job_id: str, state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """What this job has really cost since its script was attached, against its own earlier runs."""
    from . import rhythm

    attached_at = state.get("applied_at")
    if state.get("status") != "applied" or not attached_at:
        return None
    counted = checks_since(job_id, attached_at)
    if counted is None:  # no cron records to read, so estimate from the schedule
        job = lifecycle._bound_job(job_id)
        minutes = rhythm._interval(job) if job else None
        if not minutes:
            return None
        counted = int((time.time() - attached_at) // (minutes * 60))
    finished = [r for r in history.runs_for_job(job_id) if r.scheduled and r.finished]
    before = [r.cost_usd for r in finished if r.started_ts < attached_at and r.cost_usd is not None]
    after = [r for r in finished if r.started_ts >= attached_at]
    checks = counted
    if len(before) < MIN_RUNS or checks < MIN_RUNS or any(r.cost_usd is None for r in after):
        return None
    normal = sum(before) / len(before)
    spent = sum(r.cost_usd for r in after)
    expected = normal * checks
    saved = expected - spent
    if saved < 0:
        message = (f"Since attaching, this job has cost about ${spent:.4f} against an expected ${expected:.4f} without it. "
                   "That's more, so undo it if this keeps up.")
    else:
        message = f"Since attaching, this job has cost about ${spent:.4f} against an expected ${expected:.4f} without it."
    return {"spent_usd": spent, "expected_usd": expected, "saved_usd": saved, "woke": len(after), "checks": checks,
            "costing_more": saved < 0, "message": message}
