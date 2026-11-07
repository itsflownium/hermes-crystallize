"""Explicit native cron sources through public CLI controls, independent of tool replay."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
import uuid

from . import analyze, contracts, history, lifecycle, native_cron_runtime as runtime, store

_KEY = "native_cron:"
_LOCK = threading.RLock()
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
CONCURRENCY_LIMIT = "The public CLI has no atomic compare-and-swap. Configuration is checked before each edit; concurrent external edits can still race. Only the listed native-source fields are sent."
ACKNOWLEDGEMENT = "The reviewed Python source is read-only and supplies the complete latest-state input. It runs under ordinary Hermes cron permissions. This job has no per-run action, deadline, reminder or required repeated delivery."


class NativeCronError(lifecycle.LifecycleError):
    pass


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _job(job_id):
    if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
        raise NativeCronError("Choose an exact stable cron job ID.")
    job = lifecycle._bound_job(job_id)
    if not job:
        raise NativeCronError("The stable cron job ID was not found.")
    return job


def _fingerprint(job):
    # A job's run count moves on every run; only its limit is configuration.
    fields = {k: v for k, v in job.items() if (k not in runtime._VOLATILE or k == "repeat") and v is not None}
    if isinstance(fields.get("repeat"), dict):
        fields["repeat"] = {k: v for k, v in fields["repeat"].items() if k != "completed"}
    return _hash({"job": fields,
                  "contract": contracts.get(job["id"]),
                  "profile_config_sha256": runtime._profile_config_hash(history._hermes_home())})


def _source(reference):
    if not isinstance(reference, str) or not reference or len(reference) > 400:
        raise NativeCronError("Choose a Python source relative to this profile's scripts directory.")
    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".py":
        raise NativeCronError("The source must be a relative .py file under this profile's scripts directory.")
    root = history._hermes_home() / "scripts"
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents) if part != root and root in part.parents):
        raise NativeCronError("Symlink sources are not supported.")
    try:
        if not path.resolve(strict=True).is_relative_to(root.resolve()):
            raise NativeCronError("The source leaves the profile's scripts directory.")
        if not stat.S_ISREG(path.stat().st_mode):
            raise NativeCronError("Choose a regular Python source file.")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise NativeCronError("Choose a regular Python source file.")
            code = stream.read(runtime.SOURCE_LIMIT + 1)
        if not code or len(code) > runtime.SOURCE_LIMIT:
            raise NativeCronError("The reviewed source must contain between 1 and 65,536 bytes.")
        text = code.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise NativeCronError("The Python source could not be read as UTF-8.") from exc
    return {"reference": relative.as_posix(), "sha256": hashlib.sha256(code).hexdigest(), "text": text}


def _capability(mode):
    try:
        proc = subprocess.run([*lifecycle._hermes_cmd(), "cron", "edit", "--help"],
                              capture_output=True, text=True, timeout=10, env=lifecycle.hermes_env())
        flags = {"--monitor-script"} if mode == "monitor" else {"--script", "--no-agent", "--agent"}
        help_text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return proc.returncode == 0 and all(re.search(r"(?<![\w-])" + re.escape(f) + r"(?=[\s=,\[]|$)", help_text) for f in flags)
    except (OSError, subprocess.SubprocessError, lifecycle.LifecycleError):
        return False


def status(job_id):
    job = _job(job_id)
    state = store.get_setting(_KEY + job_id) or {}
    runtime_sha = hashlib.sha256(Path(runtime.__file__).read_bytes()).hexdigest()
    from . import starter_script

    return {"ok": True, "job_id": job_id, "config_fingerprint": _fingerprint(job),
            "state": state, "acknowledgement": ACKNOWLEDGEMENT, "fit": starter_script.fit(job_id),
            "runtime_sha256": runtime_sha,
            "runtime_review_required": state.get("status") in {"pending", "applied"} and state.get("runtime_sha256") != runtime_sha,
            "available_modes": ["monitor", "verbatim"], "automatic_tool_replay": False,
            "scope": "Explicitly reviewed, complete local Python snapshot sources only; no model or reasoning edit."}


def preview(job_id, source_script, *, mode="monitor", snapshot_only=False, verbatim_output=False, skipped_delivery_ok=False):
    job = _job(job_id)
    if mode not in {"monitor", "verbatim"}:
        raise NativeCronError("Choose monitor or verbatim mode.")
    source = _source(source_script)
    blockers = []
    if snapshot_only is not True:
        blockers.append("Confirm that the source is the complete read-only input for a latest-state snapshot.")
    if mode == "verbatim" and verbatim_output is not True:
        blockers.append("Confirm that the source's exact stdout is the complete requested final answer.")
    if mode == "verbatim" and contracts.get(job_id) is not None:
        blockers.append("Verbatim mode does not implement arbitrary outcome contracts. Keep the main agent for this job.")
    if job.get("script") or job.get("monitor_script") or job.get("monitor_url") or job.get("no_agent"):
        blockers.append("This job already has a native source or no-agent attachment. Undo it before replacing it.")
    if job.get("skills") or job.get("skill") or (contracts.get(job_id) or {}).get("required_actions"):
        blockers.append("Jobs with skills or required actions cannot be replaced or suppressed by this snapshot source.")
    if job.get("deliver") not in (None, "local") or any(job.get(k) for k in ("deliver_to", "delivery_target", "webhook")):
        # An unchanged run sends nothing, so the people receiving it need to be fine with silence.
        if mode != "monitor":
            blockers.append("Script-only mode supports local delivery only. Use monitor mode for a job that sends its answer somewhere.")
        elif skipped_delivery_ok is not True:
            blockers.append("This job sends its answer somewhere. Confirm that nothing should be sent when the data hasn't changed.")
    if analyze.regular_actions(history.runs_for_job(job_id, only_usable=True)):
        blockers.append("Recorded per-run actions must remain with the normal agent.")
    if job_id in history.running_job_ids() or job.get("state") in {"running", "claimed", "completed", "cancelled"}:
        blockers.append("Wait for current work to finish; completed or cancelled jobs cannot be attached.")
    supported = _capability(mode)
    if not supported:
        blockers.append("The host does not advertise the required public native cron CLI flags.")
    runtime_sha = hashlib.sha256(Path(runtime.__file__).read_bytes()).hexdigest()
    proposal = {"job_id": job_id, "mode": mode, "source_script": source["reference"],
                "source_sha256": source["sha256"], "runtime_sha256": runtime_sha,
                "config_fingerprint": _fingerprint(job), "snapshot_only": snapshot_only is True,
                "verbatim_output": verbatim_output is True, "skipped_delivery_ok": skipped_delivery_ok is True}
    return {"ok": True, **proposal, "preview_sha256": _hash(proposal), "source": source,
            "can_apply": not blockers, "blockers": blockers, "host_supported": supported,
            "model_calls": 0, "savings_verified": False, "automatic_tool_replay": False,
            "acknowledgement": ACKNOWLEDGEMENT, "concurrency_limit": CONCURRENCY_LIMIT,
            "behavior": ("The host calls the main model only on first/changed data. Failure or unknown evidence forces a fresh normal run."
                         if mode == "monitor" else "The host delivers the reviewed source's text without inference, subject to its normal outer-whitespace trimming and secret redaction.")}


def _write_exclusive(path, code):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or path.is_symlink():
        raise NativeCronError("The owned native-source directory must not be a symlink.")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() != code:
            raise NativeCronError("The owned native-source artifact changed. No cron edit was made.")
        return
    with os.fdopen(fd, "wb") as stream:
        stream.write(code)


def apply(job_id, source_script, *, mode="monitor", snapshot_only=False, verbatim_output=False,
          skipped_delivery_ok=False, expected_preview_sha256, confirmed=False):
    if confirmed is not True:
        raise NativeCronError("Review the complete source and explicitly confirm before attaching it.")
    with _LOCK:
        proposal = preview(job_id, source_script, mode=mode, snapshot_only=snapshot_only, verbatim_output=verbatim_output,
                           skipped_delivery_ok=skipped_delivery_ok)
        if proposal["preview_sha256"] != expected_preview_sha256:
            raise NativeCronError("The source, job or runtime changed after preview. Review it again; nothing was attached.")
        if not proposal["can_apply"]:
            raise NativeCronError(" ".join(proposal["blockers"]))
        job = _job(job_id)
        home = history._hermes_home()
        stem = f"{hashlib.sha256(job_id.encode()).hexdigest()[:16]}-{proposal['preview_sha256'][:20]}-{uuid.uuid4().hex[:12]}"
        source_ref = f"crystallize-native/{stem}-source.py"
        wrapper_ref = f"crystallize-native/{stem}.py"
        approval_ref = f"crystallize-native/{stem}.approval.json"
        config = {"job_id": job_id, "mode": mode, "home": str(home.resolve()), "source_ref": source_ref,
                  "source_workdir": str((home / "scripts" / proposal["source_script"]).parent.resolve()),
                  "source_sha256": proposal["source_sha256"], "wrapper_ref": wrapper_ref,
                  "approval_ref": approval_ref, "preview_sha256": expected_preview_sha256,
                  "protected_configuration": runtime.protected_configuration(job, mode),
                  "profile_config_sha256": runtime._profile_config_hash(home)}
        code = Path(runtime.__file__).read_text() + "\nif __name__ == '__main__':\n    raise SystemExit(run(json.loads(" + repr(json.dumps(config)) + ")))\n"
        _write_exclusive(home / "scripts" / source_ref, proposal["source"]["text"].encode())
        _write_exclusive(home / "scripts" / wrapper_ref, code.encode())
        _write_exclusive(home / "scripts" / approval_ref, json.dumps({"valid": True,
            "preview_sha256": expected_preview_sha256, "job_id": job_id}).encode())
        owned = {"monitor_script": wrapper_ref} if mode == "monitor" else {"script": wrapper_ref, "no_agent": True}
        original = {key: bool(job.get(key)) if key == "no_agent" else job.get(key) or None for key in owned}
        record = {"version": 1, "status": "pending", "job_id": job_id, "mode": mode,
                  "runtime_sha256": proposal["runtime_sha256"],
                  "source_script": source_script, "source_sha256": proposal["source_sha256"],
                  "preview_sha256": expected_preview_sha256, "original": original, "owned": owned,
                  "approval_ref": approval_ref,
                  "requested_at": time.time(), "acknowledgement": ACKNOWLEDGEMENT}
        store.set_setting(_KEY + job_id, record)
        if _fingerprint(_job(job_id)) != proposal["config_fingerprint"]:
            record["status"] = "cancelled"
            store.set_setting(_KEY + job_id, record)
            raise NativeCronError("The job changed before the native-source edit. Nothing was attached.")
        flags = ("--monitor-script", wrapper_ref) if mode == "monitor" else ("--script", wrapper_ref, "--no-agent")
        lifecycle._edit_job(job_id, *flags)
        after = _job(job_id)
        if any(after.get(key) != value for key, value in owned.items()):
            raise NativeCronError("The host did not retain the native-source fields. Recovery is pending; refresh before retrying.")
        record.update(status="applied", applied_at=time.time())
        store.set_setting(_KEY + job_id, record)
        return {**status(job_id), "applied": True, "message": "Attached the reviewed native source through Hermes. Model and reasoning fields were not edited."}


def invalidate(job_id):
    """Revoke source approval before an outcome-contract change takes effect."""
    state = store.get_setting(_KEY + job_id) or {}
    if state.get("status") not in {"pending", "applied"}:
        return
    reference = state.get("approval_ref") or ""
    relative = Path(reference)
    root = history._hermes_home() / "scripts"
    path = root / relative
    if (relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 2
            or relative.parts[0] != "crystallize-native" or not relative.name.endswith(".approval.json")
            or path.is_symlink() or path.parent.is_symlink()):
        raise NativeCronError("The native-source approval record could not be safely revoked. Undo it before editing the contract.")
    # Replace atomically: a partial approval document must never look valid.
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    _write_exclusive(temporary, json.dumps({"valid": False, "job_id": job_id}).encode())
    os.replace(temporary, path)
    store.set_setting(_KEY + job_id, {**state, "approval_revoked": True})


def undo(job_id, *, expected_config_fingerprint, confirmed=False):
    if confirmed is not True:
        raise NativeCronError("Explicit confirmation is required before Undo.")
    with _LOCK:
        job = _job(job_id)
        state = store.get_setting(_KEY + job_id) or {}
        if not state or state.get("status") in {"undone", "cancelled"}:
            return {**status(job_id), "undone": False}
        if _fingerprint(job) != expected_config_fingerprint:
            raise NativeCronError("The job changed after review. Refresh before Undo.")
        owned, original = state["owned"], state["original"]
        if all(job.get(key) == value for key, value in original.items()):
            state.update(status="undone", undone_at=time.time())
            store.set_setting(_KEY + job_id, state)
            return {**status(job_id), "undone": False, "already_undone": True}
        if any(job.get(key) != value for key, value in owned.items()):
            raise NativeCronError("The native-source fields were edited elsewhere. The newer configuration was preserved.")
        if job_id in history.running_job_ids():
            raise NativeCronError("Wait for the current execution to finish before Undo.")
        flags = ("--monitor-script", original["monitor_script"] or "") if state["mode"] == "monitor" else (
            "--script", original["script"] or "", "--no-agent" if original["no_agent"] else "--agent")
        state["undo_requested_at"] = time.time()
        store.set_setting(_KEY + job_id, state)
        if _fingerprint(_job(job_id)) != expected_config_fingerprint:
            raise NativeCronError("The job changed before Undo. Recovery was retained without another edit.")
        lifecycle._edit_job(job_id, *flags)
        after = _job(job_id)
        if any(after.get(key) != value for key, value in original.items()):
            raise NativeCronError("The host did not confirm native-source Undo. Recovery was retained.")
        state.update(status="undone", undone_at=time.time())
        store.set_setting(_KEY + job_id, state)
        return {**status(job_id), "undone": True, "message": "Restored the native-source fields; other job settings were preserved."}
