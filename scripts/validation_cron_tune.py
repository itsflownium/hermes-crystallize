#!/usr/bin/env python3
"""Live test of `tune` on the real scheduler: normal runs, then a trial, then its own verdict.

The job reads pretty-printed JSON, so cleaning has something to remove. The model and reasoning are
pinned, and the report records the settings each run sent. Paid calls happen only with --run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path


def _create_job(lifecycle, name, schedule, prompt, model, provider, reasoning):
    """Through Hermes's public command, then found by its unique name."""
    subprocess.run(["hermes", "cron", "create", schedule, prompt, "--name", name, "--model", model, "--provider", provider,
                    "--reasoning-effort", reasoning], check=True, capture_output=True, text=True)
    return next(job for job in lifecycle.all_cron_jobs() if job.get("name") == name)


def _remove_job(job_id):
    subprocess.run(["hermes", "cron", "remove", job_id], check=False, capture_output=True, text=True)



def temporary_rate_cards(routes, catalog_provider):
    """Save rate cards for these exact routes from a public catalog's list prices, and return a
    function that removes them. For routes such as a subscription that publish no price list."""
    from crystallize import price_discovery, public_pricing

    catalog = price_discovery.parse_catalog(price_discovery._fetch_json(price_discovery.CATALOG_URL), catalog_provider)
    saved = []
    existing = {(card["provider"], card["model"]) for card in public_pricing.list_rate_cards()}
    for provider, model in routes:
        if (provider, model) in existing:
            continue  # someone else's card: use it, and leave it in place afterwards
        found = catalog.get(model)
        if not found:
            raise SystemExit(f"No list price for {model} under {catalog_provider}.")
        public_pricing.save_rate_card({"provider": provider, "model": model, "currency": "USD", "unit": "per_token",
                                       **{k: found[k] for k in ("input", "output", "cache_read", "cache_write")},
                                       "request_cost": 0, "tiers_known": True, "source_url": price_discovery.CATALOG_URL})
        saved.append((provider, model))

    def remove():
        for provider, model in saved:
            public_pricing.delete_rate_card(provider, model)
    return remove

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--minutes", type=float, default=15)
    parser.add_argument("--before-runs", type=int, default=4, help="Normal runs before the trial, so the comparison isn't all noise")
    parser.add_argument("--report", required=True)
    parser.add_argument("--prices-from", help="Price these routes with this catalog provider's list prices, for a subscription route with no price list")
    parser.add_argument("--helper", help="provider/model for the helper in hand-off mode, priced the same way")
    args = parser.parse_args()
    report = Path(args.report)
    if report.exists():
        raise SystemExit("Use a new report file; existing evidence is never overwritten.")
    if not args.run:
        print("Add --run to create a temporary cron job and make paid calls.")
        return
    from crystallize import history, lifecycle, tuning

    remove_cards = temporary_rate_cards([(args.provider, args.model)], args.prices_from) if args.prices_from else (lambda: None)
    deadline = time.time() + args.minutes * 60
    result = {"schema": 1, "model": args.model, "provider": args.provider, "requested_reasoning": args.reasoning}

    def finished():
        return [r for r in history.runs_for_job(job["id"]) if r.scheduled and r.finished and r.outcome not in ("running", "claimed")]

    with tempfile.TemporaryDirectory(prefix="crystal-tune-") as folder:
        data = Path(folder) / "status.json"
        data.write_text(json.dumps({"items": [{"id": i, "state": "ok", "owner": f"team-{i % 4}", "tags": ["a", "b"]} for i in range(150)]
                                    + [{"id": 999, "state": "blocked", "owner": "team-9"}]}, indent=4))
        job = _create_job(lifecycle, f"crystal-tune-test-{int(time.time())}", "every 1m",
                          f"Run `cat {data}` with the terminal tool, then name the one item whose state isn't ok.",
                          args.model, args.provider, args.reasoning)
        try:
            while time.time() < deadline and len(finished()) < max(args.before_runs, tuning.MIN_BEFORE_RUNS):
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
            _remove_job(job["id"])
            result["limits"] = ["One job, one model and a small sample.", "Costs are recorded host estimates, not bills."]
            remove_cards()
            report.write_text(json.dumps(result, indent=1))
            print(json.dumps({k: result.get(k) for k in ("status", "result", "cleanup_note")}))


if __name__ == "__main__":
    main()
