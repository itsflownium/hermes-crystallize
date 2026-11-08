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
            "cost_basis": "host" if run.cost_usd else "list price",
            "requested_reasoning": run.reasoning_effort, "comparable": history.comparison_identity(run) is not None}



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
    parser.add_argument("--after-ticks", type=int, default=4)
    parser.add_argument("--report", required=True)
    parser.add_argument("--prices-from", help="Price these routes with this catalog provider's list prices, for a subscription route with no price list")
    parser.add_argument("--helper", help="provider/model for the helper in hand-off mode, priced the same way")
    args = parser.parse_args()
    report = Path(args.report)
    if report.exists():
        raise SystemExit("Use a new report file; existing evidence is never overwritten.")
    if not args.run:
        print("Add --run to create two temporary cron jobs and make paid calls.")
        return
    from crystallize import history, lifecycle, native_cron, starter_script

    remove_cards = temporary_rate_cards([(args.provider, args.model)], args.prices_from) if args.prices_from else (lambda: None)
    deadline = time.time() + args.minutes * 60
    result = {"schema": 1, "model": args.model, "provider": args.provider, "requested_reasoning": args.reasoning, "events": []}
    with tempfile.TemporaryDirectory(prefix="crystal-cron-") as folder:
        data = Path(folder) / "status.json"
        _write(data, "ok")
        prompt = f"Run `cat {data}` with the terminal tool, then say in one line whether any service is not ok, naming it."
        stamp = int(time.time())
        jobs = [_create_job(lifecycle, f"crystal-pair-{name}-{stamp}", "every 1m", prompt, args.model, args.provider, args.reasoning)
                for name in ("normal", "attached")]
        normal, attached = jobs[0]["id"], jobs[1]["id"]
        try:
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
                    _write(data, "failed")  # the last tick is there to see the attached job wake
                    result["events"].append({"changed_data_at": time.time(), "tick": tick})
                # Only the normal job paces this: an unchanged attached job has no run to wait for.
                _wait(history, normal, base[normal] + tick, deadline)
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
                _remove_job(job["id"])
            report.with_suffix(".partial").unlink(missing_ok=True)
            result["limits"] = ["Costs are recorded host estimates, not bills.", "One provider and model, on a small synthetic status file.",
                                "How often a real job's data stays unchanged decides the saving."]
            remove_cards()
            report.write_text(json.dumps(result, indent=1))
            print(json.dumps({k: result.get(k) for k in ("stopped", "blockers", "cleanup_notes")}))


if __name__ == "__main__":
    main()
