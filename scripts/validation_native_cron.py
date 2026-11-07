#!/usr/bin/env python3
"""Real due script-only validation through public CLI commands in a temporary profile.

No credential copying, provider inference, gateway restart or production job edit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def validate(command, model, *, cwd=None):
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
        raise ValueError("The Hermes command must be a nonempty JSON argv array.")
    result = {"scope": "isolated public-CLI scheduled native-verbatim check", "paid_provider_calls_requested": 0,
              "gateway_restarted": False, "production_jobs_modified": False,
              "native_runtime_sha256": hashlib.sha256((ROOT / "native_cron_runtime.py").read_bytes()).hexdigest()}
    with tempfile.TemporaryDirectory(prefix="crystallize-native-validation-") as directory:
        home = Path(directory)
        (home / "plugins").mkdir()
        (home / "plugins/crystallize").symlink_to(ROOT, target_is_directory=True)
        (home / "scripts").mkdir()
        (home / "scripts/snapshot.py").write_text('print("complete scheduled snapshot: 42")\n')
        (home / "config.yaml").write_text("model: " + json.dumps({"provider": "nous", "default": model})
            + "\nagent: {reasoning_effort: max}\nplugins: {enabled: [crystallize]}\n")
        env = {k: v for k, v in os.environ.items() if not any(part in k.upper()
               for part in ("API_KEY", "ACCESS_TOKEN", "REFRESH_TOKEN", "AUTH_TOKEN", "HERMES_YOLO"))}
        env.update(HERMES_HOME=str(home), PYTHONDONTWRITEBYTECODE="1", HERMES_DISABLE_LAZY_INSTALLS="1",
                   XDG_CACHE_HOME=str(home / "cache"), XDG_CONFIG_HOME=str(home / "xdg"))
        def cli(*args):
            proc = subprocess.run([*command, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=90)
            if proc.returncode:
                raise RuntimeError("The public CLI command failed: " + " ".join(args[:3]))
            return proc.stdout
        def job():
            data = json.loads((home / "cron/jobs.json").read_text())
            return (data.get("jobs") if isinstance(data, dict) else data)[0]
        try:
            instant = (datetime.now(timezone.utc) + timedelta(seconds=25)).isoformat()
            cli("cron", "create", instant, "Return the complete reviewed snapshot text.", "--name", "isolated-native-validation",
                "--deliver", "local", "--model", model, "--provider", "nous", "--reasoning-effort", "max")
            before = job()
            options = ("--source-script", "snapshot.py", "--mode", "verbatim", "--snapshot-only", "--verbatim-output")
            preview = json.loads(cli("crystal", "native-cron", before["id"], *options))
            if not preview["can_apply"]:
                raise RuntimeError("Native source preview was blocked.")
            attached = json.loads(cli("crystal", "native-cron", before["id"], *options,
                                     "--apply", "--confirm", "--fingerprint", preview["preview_sha256"]))
            if not attached["applied"]:
                raise RuntimeError("The native source was not attached.")
            delay = (datetime.fromisoformat(instant) - datetime.now(timezone.utc)).total_seconds()
            if delay > 0:
                time.sleep(min(delay + 1, 30))
            cli("cron", "tick")
            with sqlite3.connect((home / "cron/executions.db").as_uri() + "?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                rows = [dict(row) for row in db.execute("SELECT id,job_id,source,status,scheduled_instant FROM executions")]
            after = job()
            preserved = all(before.get(k) == after.get(k) for k in ("model", "provider", "model_provider", "reasoning_effort", "interpreter", "workdir"))
            output = any("complete scheduled snapshot: 42" in path.read_text() for path in (home / "cron/output").rglob("*.md"))
            result.update(executions=rows, model_and_reasoning_preserved=preserved, expected_text_in_local_output=output,
                          passed=len(rows) == 1 and rows[0]["source"] == "builtin" and rows[0]["status"] == "completed"
                          and bool(rows[0]["scheduled_instant"]) and preserved and output)
        except Exception as exc:
            result.update(passed=False, error_type=type(exc).__name__, error="Public native validation failed; private diagnostics omitted.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-command", default='["hermes"]', help="JSON argv; never evaluated as shell code")
    parser.add_argument("--hermes-cwd", help="Optional source checkout cwd for a Python -m command")
    parser.add_argument("--model", required=True, help="Exact route for the isolated job; no inference is requested")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = validate(json.loads(args.hermes_command), args.model, cwd=args.hermes_cwd)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
