"""Stdlib runtime copied into an explicitly approved native cron source.

No Hermes imports, nested tools, provider calls or self-update. The ordinary host
cron engine executes the reviewed Python source. Unknown evidence wakes the main
agent in monitor mode; verbatim mode fails visibly instead of inventing output.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

SOURCE_LIMIT = 65_536
OUTPUT_LIMIT = 6_000  # native monitor context is capped at 8,000 characters
_VOLATILE = frozenset({"monitor_state", "next_run_at", "last_run_at", "last_run", "next_run",
    "last_status", "last_error", "last_dispatch", "last_execution_id", "last_delivery", "last_output",
    "run_count", "started_at", "finished_at", "updated_at", "schedule_display", "run_claim",
    "state", "enabled", "paused_at", "paused_reason", "completed_at", "failure_streak", "last_failure", "repeat"})
_VOLATILE |= frozenset({"fire_claim", "pending_slot", "last_delivery_error", "canonical_completions"})


def _digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def protected_configuration(job, mode):
    owned = {"monitor_script"} if mode == "monitor" else {"script", "no_agent"}
    value = {key: val for key, val in job.items() if key not in _VOLATILE | owned and val is not None}
    if isinstance(value.get("repeat"), dict):
        value["repeat"] = {key: val for key, val in value["repeat"].items() if key != "completed"}
    return _digest(value)


def _profile_config_hash(home):
    path = Path(home) / "config.yaml"
    return _digest(path.read_bytes()) if path.is_file() else None


def _job(config):
    data = json.loads((Path(config["home"]) / "cron" / "jobs.json").read_text())
    rows = data.get("jobs", []) if isinstance(data, dict) else data
    return next(row for row in rows if row.get("id") == config["job_id"])


def _retry_anchor(home, job_id):
    """A failed agent must get another chance even when its source is unchanged.

    Native monitor hashes are saved before inference. Include retained failed,
    unknown and cancelled executions in the source hash, so a failure causes
    a fresh wake without resetting the monitor or touching the host ledger.
    A missed schedule slot has no model answer. Complete latest-state snapshots
    do not require a new answer solely because the schedule was paused or late.
    """
    path = Path(home) / "cron" / "executions.db"
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        failures = db.execute("SELECT id, status FROM executions WHERE job_id=? AND status IN "
                              "('failed','unknown','cancelled') ORDER BY id LIMIT 10001",
                              (job_id,)).fetchall()
        active = db.execute("SELECT COUNT(*) FROM executions WHERE job_id=? AND status IN ('claimed','running')",
                            (job_id,)).fetchone()[0]
    # More than one concurrent attempt cannot establish a completed answer.
    if active > 1 or len(failures) > 10000:
        raise ValueError("concurrent_execution")
    # Include every retained failure, not only a timestamp winner: late receipt
    # persistence or equal timestamps must not hide a newly failed attempt.
    return {"failure_set_sha256": _digest(failures), "failures_retained": len(failures)} if failures else None


def _fallback(reason, checkpoint="verification"):
    print(json.dumps({"crystallize_native": {"kind": "diagnostic_only", "snapshot_available": False,
                      "status": reason, "checkpoint": checkpoint,
                      "fresh_run_nonce": f"diagnostic-{time.time_ns():x}"},
                      "instruction": "No verified snapshot was supplied. These status fields and the diagnostic nonce are not task data. "
                      "Use normal tools and configured reasoning to verify the original task. If verification is unavailable, "
                      "state that the task could not be verified; do not invent values or treat this diagnostic as the snapshot."}))


def run(config):
    mode = config["mode"]
    checkpoint = "profile_binding"
    try:
        home = Path(config["home"]).resolve()
        if Path(__file__).resolve() != (home / "scripts" / config["wrapper_ref"]).resolve():
            raise ValueError("configuration_changed")
        checkpoint = "source_approval"
        approval = json.loads((home / "scripts" / config["approval_ref"]).read_text())
        if approval.get("valid") is not True or approval.get("preview_sha256") != config["preview_sha256"]:
            raise ValueError("configuration_changed")
        job = _job(config)
        field = "monitor_script" if mode == "monitor" else "script"
        checkpoint = "native_attachment"
        if job.get(field) != config["wrapper_ref"] or bool(job.get("no_agent")) != (mode == "verbatim"):
            raise ValueError("configuration_changed")
        checkpoint = "task_configuration"
        if protected_configuration(job, mode) != config["protected_configuration"]:
            raise ValueError("configuration_changed")
        checkpoint = "profile_configuration"
        profile_config_sha256 = _profile_config_hash(home)
        checkpoint = "source_integrity"
        source = home / "scripts" / config["source_ref"]
        if source.is_symlink() or not source.resolve().is_relative_to((home / "scripts").resolve()):
            raise ValueError("source_changed")
        code = source.read_bytes()
        if len(code) > SOURCE_LIMIT or _digest(code) != config["source_sha256"]:
            raise ValueError("source_changed")
        env = dict(os.environ)
        env.pop("HERMES_YOLO_MODE", None)
        # File capture avoids unbounded in-memory stdout/stderr. Source execution
        # remains ordinary user-approved cron code, not a sandbox.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            checkpoint = "source_execution"
            completed = subprocess.run([sys.executable, str(source)], cwd=job.get("workdir") or config["source_workdir"],
                                       env=env, stdout=out, stderr=err, timeout=30, check=False)
            out.seek(0)
            raw = out.read(OUTPUT_LIMIT * 4 + 1)
        if completed.returncode:
            raise ValueError("source_failed")
        text = raw.decode("utf-8", errors="strict")
        if not text.strip() or len(raw) > OUTPUT_LIMIT * 4 or len(text) > OUTPUT_LIMIT:
            raise ValueError("source_empty_or_too_large")
        if mode == "verbatim":
            # The host reserves a final wakeAgent=false line as a silent signal.
            # Refuse it so an exact-output request cannot disappear as success.
            try:
                last = json.loads(text.strip().splitlines()[-1])
            except ValueError:
                last = None
            if isinstance(last, dict) and last.get("wakeAgent") is False:
                raise ValueError("reserved_silent_gate")
            sys.stdout.write(text)
            return 0
        try:
            retry = _retry_anchor(home, config["job_id"])
        except (OSError, ValueError, sqlite3.Error):
            retry = {"unverified_ledger_nonce": time.time_ns()}
        payload = {"crystallize_native": {"source_sha256": config["source_sha256"], "retry_after": retry,
                                         "profile_config_sha256": profile_config_sha256},
                   "snapshot": text}
        output = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(output) > 7_000:
            raise ValueError("source_too_large_for_monitor_context")
        print(output)
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) and str(exc) in {
            "configuration_changed", "source_changed", "source_failed", "source_empty_or_too_large",
            "reserved_silent_gate", "source_too_large_for_monitor_context"} else "source_verification_failed"
        if mode == "monitor":
            _fallback(reason, checkpoint)
            return 0
        print(f"Crystallize native source failed ({reason}; {checkpoint}). Review the source or use native-cron --undo.", file=sys.stderr)
        return 1
