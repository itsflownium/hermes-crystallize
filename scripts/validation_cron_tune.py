#!/usr/bin/env python3
"""Live test of `tune` on the real scheduler: normal runs, then a trial, then its own verdict.

The job reads pretty-printed JSON, so cleaning has something to remove. The model and reasoning are
pinned, and the report records the settings each run sent. Paid calls happen only with --run.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--minutes", type=float, default=15)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = Path(args.report)
    if report.exists():
        raise SystemExit("Use a new report file; existing evidence is never overwritten.")
    if not args.run:
        print("Add --run to create a temporary cron job and make paid calls.")
        return
    from cron.jobs import create_job, remove_job

    from crystallize import history, lifecycle, tuning

    deadline = time.time() + args.minutes * 60
    result = {"schema": 1, "model": args.model, "provider": args.provider, "requested_reasoning": args.reasoning}

    def finished():
        return [r for r in history.runs_for_job(job["id"]) if r.scheduled and r.finished and r.outcome not in ("running", "claimed")]

    with tempfile.TemporaryDirectory(prefix="crystal-tune-") as folder:
        data = Path(folder) / "status.json"
        data.write_text(json.dumps({"items": [{"id": i, "state": "ok", "owner": f"team-{i % 4}", "tags": ["a", "b"]} for i in range(150)]
                                    + [{"id": 999, "state": "blocked", "owner": "team-9"}]}, indent=4))
        job = create_job(prompt=f"Run `cat {data}` with the terminal tool, then name the one item whose state isn't ok.",
                         schedule="every 1m", name="crystal-tune-test", model=args.model, provider=args.provider)
        try:
            lifecycle._edit_job(job["id"], "--reasoning-effort", args.reasoning)
            while time.time() < deadline and len(finished()) < tuning.MIN_BEFORE_RUNS:
                time.sleep(5)
            result["trial"] = {"started": tuning.start(job["id"])["status"]}
            trial_at = time.time()
            final = None
            while time.time() < deadline:
                tuning.refresh()
                final = lifecycle._tuning_for_job(job)
                if final and final["status"] in ("kept", "reverted"):
                    break
                time.sleep(10)
            result["status"] = (final or {}).get("status")
            result["result"] = {k: (final or {}).get("result", {}).get(k) for k in
                                ("ok", "why", "kind", "metric", "before", "after", "reasoning_before", "reasoning_after")}
            result["runs"] = [{"started": r.started_ts, "in_trial": r.started_ts >= trial_at, "outcome": r.outcome, "cost_usd": r.cost_usd,
                               "input_tokens": r.input_tokens, "cache_read_tokens": r.cache_read_tokens, "output_tokens": r.output_tokens,
                               "reasoning": r.reasoning_effort, "comparable": history.comparison_identity(r) is not None} for r in finished()]
        finally:
            try:
                final = lifecycle._tuning_for_job(job)
                if final and final["status"] in ("starting", "trial", "kept"):
                    tuning.undo(job["id"])
            except Exception as exc:  # noqa: BLE001 — the job is removed either way
                result["cleanup_note"] = str(exc)
            remove_job(job["id"])
            result["limits"] = ["One job, one model and a small sample.", "Costs are recorded host estimates, not bills."]
            report.write_text(json.dumps(result, indent=1))
            print(json.dumps({k: result.get(k) for k in ("status", "result", "cleanup_note")}))


if __name__ == "__main__":
    main()
