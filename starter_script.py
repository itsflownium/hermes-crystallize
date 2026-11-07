"""Draft a snapshot script from what a cron job has actually done.

Automatic cron skipping needs a script that reads the job's source data. Writing one by hand is the
hard part, so this drafts it from the job's own repeated read steps. It only drafts: nothing runs and
nothing attaches until you review the source in the normal preview. Anything that isn't a stable,
read-only step is left out and named, so you can tell whether the script is complete.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import analyze, history, lifecycle, store

MIN_RUNS = 3
DRAFT_DIR = "crystallize-drafts"
# Without this the AI reads the same data again with its tools, paying for it twice.
COMPLETE_NOTE = "This is the current result of every read this job normally does, so you don't need to run them again."
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")

_TEMPLATE = '''#!/usr/bin/env python3
"""Draft snapshot for cron job {name}, written by Crystallize from its recorded reads.

Review this before attaching it. It must print everything the job needs, and only data
that matters: a timestamp in the output would look like a change on every run.
Any failed read exits with an error, so Hermes does a normal full run instead of skipping.
"""
import json
import subprocess
import sys
import urllib.request

COMMANDS = {commands}
URLS = {urls}
TIMEOUT_SECONDS = 60
NOTE = {note}


def compact(text):
    """Wasted JSON formatting would be read on every AI call of the run."""
    try:
        return json.loads(text)
    except ValueError:
        return text


snapshot = {{}}
for command in COMMANDS:
    done = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    if done.returncode != 0:
        sys.exit(f"Read failed: {{command}}")
    snapshot[command] = compact(done.stdout)
for url in URLS:
    request = urllib.request.Request(url, headers={{"User-Agent": "crystallize-snapshot"}})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        snapshot[url] = compact(response.read().decode("utf-8", "replace"))
if NOTE:
    print(NOTE)
print(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
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
                              urls=json.dumps(found["urls"], indent=4), note=repr(COMPLETE_NOTE if found["complete"] else ""))
    relative = f"{DRAFT_DIR}/{_SAFE_NAME.sub('-', job_id)}.py"
    path = history._hermes_home() / "scripts" / relative
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(source, encoding="utf-8")
    store.set_setting("starter_script:" + job_id, {"source_script": relative})
    message = ("Drafted from this job's repeated reads. Review it in the preview before attaching." if found["complete"] else
               "Drafted, but some steps couldn't be included. Add them yourself or keep the normal run, because a "
               "snapshot that misses a source would skip runs it shouldn't.")
    return {**found, "source_script": relative, "source": source, "message": message}


BREAK_EVEN_SHARE = 0.8      # attached runs cost a little more, so a job needs some unchanged runs to gain
RECENT_RUNS = 20


def fit(job_id: str) -> dict[str, Any]:
    """Would skipping unchanged runs help this job? Judged from what its own recent runs read."""
    from . import rhythm

    runs = history.runs_for_job(job_id, only_usable=True)[-RECENT_RUNS:]
    seen = [found for found in (rhythm._content_fingerprint(run) for run in runs) if found]
    if len(seen) < MIN_RUNS:
        return {"verdict": "unknown", "message": f"After {MIN_RUNS} finished runs, this shows how often its data changes and whether skipping would help."}
    changes = sum(earlier != later for earlier, later in zip(seen, seen[1:]))
    checks = len(seen) - 1
    share = changes / checks
    counted = f"Its data changed in {changes} of its last {checks} runs"
    if share >= BREAK_EVEN_SHARE:
        return {"verdict": "poor", "share": share, "message": f"{counted}. Attaching a script would likely cost more, because almost nothing gets skipped and each AI run reads a little extra."}
    if share <= 0.5:
        return {"verdict": "good", "share": share, "message": f"{counted}. This is a good fit: unchanged runs make no AI call."}
    return {"verdict": "mixed", "share": share, "message": f"{counted}. It should save a little, but check its results after attaching."}
