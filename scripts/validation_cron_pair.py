#!/usr/bin/env python3
"""Paired live cron test: the same job twice, one attached to a drafted snapshot script.

Both jobs use one model, provider and reasoning setting and read the same file on the real scheduler.
After three normal runs, Crystallize drafts a script for one of them and attaches it in monitor mode.
The file changes once, so the attached job should call the model on its first run and that change only.

Needs a running gateway on the code under test. Paid calls happen only with --run.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path


def _write(path: Path, status: str) -> None:
    path.write_text(json.dumps({"services": [{"name": f"svc-{i}", "status": "ok", "region": "eu-west"} for i in range(30)]
                                + [{"name": "billing", "status": status}]}, indent=2), encoding="utf-8")


def _finished(history, job_id):
    return [r for r in history.runs_for_job(job_id) if r.scheduled and r.finished and r.outcome not in ("running", "claimed")]


def _wait(history, job_id, count, deadline):
    while time.time() < deadline:
        if len(_finished(history, job_id)) >= count:
            return True
        time.sleep(5)
    return False


def _row(run, history):
    return {"execution": run.execution_id, "started": run.started_ts, "outcome": run.outcome, "api_calls": run.api_calls,
            "input_tokens": run.input_tokens, "output_tokens": run.output_tokens, "cache_read_tokens": run.cache_read_tokens,
            "reasoning_tokens": run.reasoning_tokens, "cost_usd": run.cost_usd, "api_cost_usd": run.api_cost_usd,
            "requested_reasoning": run.reasoning_effort, "comparable": history.comparison_identity(run) is not None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--minutes", type=float, default=15)
    parser.add_argument("--after-ticks", type=int, default=4)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = Path(args.report)
    if report.exists():
        raise SystemExit("Use a new report file; existing evidence is never overwritten.")
    if not args.run:
        print("Add --run to create two temporary cron jobs and make paid calls.")
        return
    from cron.jobs import create_job, remove_job

    from crystallize import history, lifecycle, native_cron, starter_script

    deadline = time.time() + args.minutes * 60
    result = {"schema": 1, "model": args.model, "provider": args.provider, "requested_reasoning": args.reasoning, "events": []}
    with tempfile.TemporaryDirectory(prefix="crystal-cron-") as folder:
        data = Path(folder) / "status.json"
        _write(data, "ok")
        prompt = f"Run `cat {data}` with the terminal tool, then say in one line whether any service is not ok, naming it."
        jobs = [create_job(prompt=prompt, schedule="every 1m", name=f"crystal-pair-{name}", model=args.model, provider=args.provider)
                for name in ("normal", "attached")]
        normal, attached = jobs[0]["id"], jobs[1]["id"]
        try:
            for job in jobs:
                lifecycle._edit_job(job["id"], "--reasoning-effort", args.reasoning)
            if not all(_wait(history, j, 3, deadline) for j in (normal, attached)):
                result["stopped"] = "too few baseline runs before the deadline"
                return
            draft = starter_script.draft(attached)
            result["draft"] = {k: draft.get(k) for k in ("ok", "why", "commands", "urls", "left_out", "complete", "source_script")}
            if not draft.get("ok"):
                result["stopped"] = "no draft"
                return
            preview = native_cron.preview(attached, draft["source_script"], snapshot_only=True)
            result["blockers"] = preview["blockers"]
            if not preview["can_apply"]:
                result["stopped"] = "attachment refused"
                return
            native_cron.apply(attached, draft["source_script"], snapshot_only=True, expected_preview_sha256=preview["preview_sha256"], confirmed=True)
            attached_at = time.time()
            base = {j: len(_finished(history, j)) for j in (normal, attached)}
            for tick in range(1, args.after_ticks + 1):
                if tick == args.after_ticks - 1:
                    _write(data, "failed")
                    result["events"].append({"changed_data_at": time.time(), "tick": tick})
                _wait(history, normal, base[normal] + tick, deadline)
                _wait(history, attached, base[attached] + tick, deadline)
                report.with_suffix(".partial").write_text(json.dumps({"tick": tick}))
            result["attached_at"] = attached_at
            for name, job_id in (("normal", normal), ("attached", attached)):
                rows = [_row(r, history) for r in _finished(history, job_id)]
                result[name] = {"before": [r for r in rows if r["started"] < attached_at], "after": [r for r in rows if r["started"] >= attached_at]}
        finally:
            for job in jobs:
                try:
                    if job["id"] == attached:
                        state = native_cron.status(attached)
                        if state["state"].get("status") in ("pending", "applied"):
                            native_cron.undo(attached, expected_config_fingerprint=state["config_fingerprint"], confirmed=True)
                except Exception as exc:  # noqa: BLE001 — the job is removed either way
                    result.setdefault("cleanup_notes", []).append(str(exc))
                remove_job(job["id"])
            report.with_suffix(".partial").unlink(missing_ok=True)
            result["limits"] = ["Costs are recorded host estimates, not bills.", "One provider and model, on a small synthetic status file.",
                                "How often a real job's data stays unchanged decides the saving."]
            report.write_text(json.dumps(result, indent=1))
            print(json.dumps({k: result.get(k) for k in ("stopped", "blockers", "cleanup_notes")}))


if __name__ == "__main__":
    main()
