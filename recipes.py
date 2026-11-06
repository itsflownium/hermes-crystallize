"""Import explicit read recipes without silently promoting them."""
from __future__ import annotations

import json
import time
from typing import Any

from . import analyze, fetch_cache, lifecycle, parameters, steplist, store


def install(job_ref: str, recipe: dict[str, Any]) -> dict[str, Any]:
    job = lifecycle.cron_job(job_ref)
    if not job:
        raise ValueError("Choose an existing cron job for this recipe.")
    if not isinstance(recipe, dict) or set(recipe) - {"version", "steps", "leaves_to_ai", "cache", "description"}:
        raise ValueError("A recipe accepts version, steps, leaves_to_ai, cache and description.")
    if recipe.get("version", 2) != 2 or not isinstance(recipe.get("leaves_to_ai", True), bool):
        raise ValueError("Use recipe version 2 and a boolean leaves_to_ai setting.")
    current = store.routine_for_job(job["id"])
    if current and current.get("status") == "live":
        raise ValueError("Undo the live routine before replacing its recipe.")
    # JSON round-trip makes an owned, immutable-at-save copy; no Python values.
    plan = json.loads(json.dumps(recipe, allow_nan=False))
    plan.update(version=2, job_id=job["id"], job_name=job.get("name") or job["id"])
    plan.setdefault("leaves_to_ai", True)
    plan["cache"] = fetch_cache.validate(plan.get("cache"))
    parameters.validate(plan)
    for step in plan["steps"]:
        if set(step) - {"id", "tool", "args", "check"}:
            raise ValueError("A recipe step accepts id, tool, args and check only.")
        if step.get("check") is not None:
            check = step["check"]
            if not isinstance(check, dict) or set(check) - {"min_len"}:
                raise ValueError("A step check accepts min_len only.")
            minimum = check.get("min_len", 1)
            if isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= 1_000_000:
                raise ValueError("A step minimum output length must be from 1 to 1000000.")
    reason = analyze.replay_safety_error(plan)
    if reason:
        raise ValueError(reason)
    rid = steplist.routine_id(job["id"])
    meta = {"matches": 0, "needed": lifecycle.MATCHES_NEEDED, "tests": [], "created_at": time.time(),
            "explicit_recipe": True}
    meta["config_snapshot"] = lifecycle.configuration_fingerprint(job, {"id": rid, "source_ref": job["id"], "steps": plan, "meta": meta})
    store.save_routine(rid, name=job.get("name") or job["id"], source="cron", source_ref=job["id"],
                       status="testing", steps=plan, meta=meta)
    return store.get_routine(rid)
