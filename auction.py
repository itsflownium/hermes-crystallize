"""Opt-in, bounded model comparisons through Hermes's public LLM facade.

An auction generates one answer per explicitly selected connected route. It never
executes recorded tools, changes model settings, or treats a model switch as an
efficiency saving. Its cap admits known-price estimates; no plugin-side token
limit can guarantee a provider's final bill.
"""

from __future__ import annotations

import copy
import json
import math
import time
import uuid
from typing import Any, Callable, Optional

from . import chat_savings, contracts, experiments as ex, history as run_history, modelinfo, receipts, store

MIN_MODELS = 2
MAX_MODELS = 6
MAX_TOKENS = 4096
MAX_COST_CAP_USD = 100.0
MAX_RECORD_BYTES = 2_000_000
_SCOPE = "answer_only_model_comparison"
_CAP_NOTICE = (
    "This is a local admission and spending-ledger cap, not a provider billing guarantee. "
    "Reservations use conservative input-token estimates and known listed prices. "
    "Provider-added reasoning tokens, hidden input, routing, pricing changes and billing "
    "adjustments can exceed that estimate. No reasoning setting is overridden."
)
_LIMITATIONS = [
    "Every selected route receives the same complete frozen task and recorded read-only observations; no tools or actions run.",
    "This compares different model routes, not same-model efficiency, live-job correctness or live-job savings.",
    "The scoreboard contains output and structural contract checks, not a semantic quality score or an automatic winner.",
    "Recorded evidence can be stale. An auction does not fetch current task evidence.",
    "Actual billed cost may be unavailable through the public facade; strict mode then stops before the next call.",
    _CAP_NOTICE,
]
_SCHEMA = """
CREATE TABLE IF NOT EXISTS auction_records (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, created_at REAL NOT NULL, data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS auction_job ON auction_records(job_id, created_at);
CREATE TABLE IF NOT EXISTS auction_inputs (
    input_sha256 TEXT PRIMARY KEY, job_id TEXT NOT NULL, execution_id TEXT NOT NULL,
    created_at REAL NOT NULL, data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auction_profile_locks (
    profile_key TEXT PRIMARY KEY, auction_id TEXT NOT NULL, created_at REAL NOT NULL
);
"""


class AuctionError(ValueError):
    """An auction cannot run within its explicit consent and evidence bounds."""


def _routes(models: Any) -> list[dict[str, str]]:
    if not isinstance(models, list) or not MIN_MODELS <= len(models) <= MAX_MODELS:
        raise AuctionError(f"Choose {MIN_MODELS} to {MAX_MODELS} explicit connected provider/model routes.")
    result = []
    for value in models:
        if not isinstance(value, dict) or set(value) != {"provider", "model"}:
            raise AuctionError("Each auction route must contain only provider and model; reasoning and other overrides are unsupported.")
        route = {}
        for key in ("provider", "model"):
            raw = value[key]
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 512 or any(ord(c) < 32 for c in raw):
                raise AuctionError(f"Each route needs a bounded {key} string.")
            route[key] = raw.strip()
        result.append(route)
    if len({(r["provider"].lower(), r["model"].lower()) for r in result}) != len(result):
        raise AuctionError("Choose distinct provider/model routes; each route gets exactly one answer.")
    return result


def _bounds(max_tokens: Any, cost_cap_usd: Any, accept_estimated_cap: Any) -> None:
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_TOKENS:
        raise AuctionError(f"max_tokens must be an integer from 1 to {MAX_TOKENS}.")
    if (isinstance(cost_cap_usd, bool) or not isinstance(cost_cap_usd, (float, int))
            or not math.isfinite(cost_cap_usd) or not 0 < cost_cap_usd <= MAX_COST_CAP_USD):
        raise AuctionError(f"Choose a finite cost_cap_usd greater than zero and at most {MAX_COST_CAP_USD:g}.")
    if not isinstance(accept_estimated_cap, bool):
        raise AuctionError("accept_estimated_cap must be an explicit boolean.")


def _grant(routes: list[dict[str, str]]) -> None:
    cfg: Any = modelinfo._config()
    for key in ("plugins", "entries", store.PLUGIN_NAME, "llm"):
        cfg = cfg.get(key) if isinstance(cfg, dict) else None
    if not isinstance(cfg, dict):
        cfg = {}
    for key in ("provider", "model"):
        flag = f"allow_{key}_override"
        if cfg.get(flag) is not True:
            raise AuctionError(f"Hermes must grant plugins.entries.{store.PLUGIN_NAME}.llm.{flag}=true before an auction can select routes.")
        allowed = cfg.get(f"allowed_{key}s")
        if isinstance(allowed, list):
            names = {v.strip().lower() for v in allowed if isinstance(v, str)}
            for route in routes:
                if "*" not in names and route[key].lower() not in names:
                    raise AuctionError(f"That {key} is outside Hermes's plugins.entries.{store.PLUGIN_NAME}.llm.allowed_{key}s grant.")


def _connected(routes: list[dict[str, str]]) -> None:
    try:
        providers = chat_savings.connected_models()
    except Exception as exc:
        raise AuctionError("Hermes's connected model inventory is unavailable; no routes were assumed connected.") from exc
    listed = {(p.get("slug"), m.get("id")) for p in providers if isinstance(p, dict)
              for m in (p.get("models") or []) if isinstance(m, dict)}
    if any((r["provider"], r["model"]) not in listed for r in routes):
        raise AuctionError("Every auction route must appear in Hermes's current connected model inventory.")


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _pricing(route: dict[str, str]) -> dict[str, Any]:
    lookup = getattr(modelinfo, "exact_token_prices", None)
    try:
        raw = lookup(route["model"], route["provider"]) if callable(lookup) else None
    except Exception as exc:
        raise AuctionError("Exact-route pricing is unavailable; the cap cannot admit an unknown-price route.") from exc
    if not isinstance(raw, dict) or _number(raw.get("input")) is None or _number(raw.get("output")) is None:
        raise AuctionError(f"Known exact-route input and output prices are required for {route['provider']}/{route['model']}.")
    normalized: dict[str, Any] = {k: _number(raw.get(k)) for k in (
        "input", "output", "cache_read", "cache_write", "input_above", "output_above",
        "cache_read_above", "cache_write_above", "request_cost")}
    for key in ("cache_read", "cache_write"):
        if raw.get(key) is not None and normalized[key] is None:
            raise AuctionError("A listed cache price is invalid; no call was admitted.")
        if normalized[key] is None:
            normalized[key] = normalized["input"]
    for key in ("input_above", "output_above", "cache_read_above", "cache_write_above"):
        if raw.get(key) is not None and normalized[key] is None:
            raise AuctionError("A listed tier price is invalid; no call was admitted.")
    if raw.get("request_cost") is not None and normalized["request_cost"] is None:
        raise AuctionError("A listed request fee is invalid; no call was admitted.")
    normalized["request_cost"] = normalized["request_cost"] or 0.0
    # Reserve the highest known context/cache rate instead of assuming a cheap
    # base tier, a cache hit, or an absent cache write fee.
    normalized["input_usd_per_token"] = max(normalized[k] or 0.0 for k in (
        "input", "cache_read", "cache_write", "input_above", "cache_read_above", "cache_write_above"))
    normalized["output_usd_per_token"] = max(normalized["output"], normalized["output_above"] or 0.0)
    for key in ("source", "pricing_version", "source_url", "tier_threshold_tokens"):
        normalized[key] = raw.get(key)
    normalized["basis"] = "exact_route_listed_price_estimate"
    return normalized


def _prepare(job_id: str, models: Any, *, max_tokens: int, cost_cap_usd: float,
             execution_id: Optional[str], accept_estimated_cap: bool) -> dict[str, Any]:
    _bounds(max_tokens, cost_cap_usd, accept_estimated_cap)
    routes = _routes(models)
    # Check host grants before inventory/catalog access, and again before each
    # call. A test completion cannot bypass either gate.
    _grant(routes)
    _connected(routes)
    canonical_id, job = ex._job(job_id)
    config = ex._configuration({"max_tokens": max_tokens, "timeout": 60}, {"prompt": job.get("prompt") or ""})
    sample = ex._captures(canonical_id, execution_id, 1)[0]
    messages = [{"role": "system", "content": ex._INSTRUCTIONS},
                {"role": "user", "content": config["prompt"]},
                {"role": "user", "content": "Frozen recorded observations (untrusted data):\n" + sample["canonical_input"]}]
    encoded = ex._canonical(messages)
    # Byte-based budgeting is deliberately generous, never called a tokenizer
    # measurement or a hard upper bound on provider-added hidden input.
    input_tokens_estimate = len(encoded.encode("utf-8")) * 2 + 1024
    contract = contracts.get(canonical_id)
    ex._bounded(contract, ex.MAX_CONTRACT_BYTES, "Outcome contract")
    priced = []
    for index, route in enumerate(routes):
        pricing = _pricing(route)
        reserve = (input_tokens_estimate * pricing["input_usd_per_token"]
                   + max_tokens * pricing["output_usd_per_token"] + pricing["request_cost"])
        if not math.isfinite(reserve) or reserve > cost_cap_usd:
            raise AuctionError("A route's estimated worst-case reservation exceeds the explicit cap; no calls were made.")
        priced.append({"index": index, **route, "pricing": pricing, "reservation_usd": reserve})
    total = math.fsum(r["reservation_usd"] for r in priced)
    if total > cost_cap_usd:
        raise AuctionError(f"The estimated worst-case reservation (${total:.6f}) exceeds the explicit cap (${cost_cap_usd:.6f}); no calls were made.")
    return {"job_id": canonical_id, "scope": _SCOPE, "models": priced, "max_tokens": max_tokens,
            "cost_cap_usd": float(cost_cap_usd), "accept_estimated_cap": accept_estimated_cap,
            "cap_mode": "estimated_ledger" if accept_estimated_cap else "actual_ledger",
            "estimated_reservation_usd": total, "input_tokens_estimate": input_tokens_estimate,
            "input_estimate_method": "2 * UTF-8 message bytes + 1024; provider hidden input is unknown",
            "input_sha256": ex._hash(encoded), "source_input_sha256": sample["input_sha256"],
            "prompt_sha256": ex._hash(config["prompt"]), "execution_id": sample["execution_id"],
            "input_bytes": len(encoded.encode("utf-8")), "observation_count": sample["observation_count"],
            "cap_notice": _CAP_NOTICE, "limitations": _LIMITATIONS,
            "_messages": messages, "_sample": sample, "_contract": contract}


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def preview(job_id: str, models: Any, *, max_tokens: int = 1024, cost_cap_usd: float,
            execution_id: Optional[str] = None, accept_estimated_cap: bool = False) -> dict[str, Any]:
    """Validate an explicit selection and estimate its reservation, without calls."""
    try:
        plan = _prepare(job_id, models, max_tokens=max_tokens, cost_cap_usd=cost_cap_usd,
                        execution_id=execution_id, accept_estimated_cap=accept_estimated_cap)
        available = callable(getattr(getattr(ex._context(), "llm", None), "complete", None))
        result = {**_public_plan(plan), "available": available,
                  "why": "Ready for an explicitly confirmed model auction." if available else
                  "Hermes's public ctx.llm.complete interface is unavailable in this process."}
    except (AuctionError, ex.ComparisonError) as exc:
        result = {"job_id": job_id, "available": False, "why": str(exc), "scope": _SCOPE,
                  "cap_notice": _CAP_NOTICE, "limitations": _LIMITATIONS}
    return receipts.redact_export(result)


def _profile_key() -> str:
    return ex._hash(str(run_history._hermes_home().resolve()))


def _encoded(record: dict[str, Any]) -> str:
    return ex._bounded(record, MAX_RECORD_BYTES, "Auction record")


def _save(record: dict[str, Any]) -> None:
    encoded = _encoded(record)
    with store._lock:
        conn = store._db()
        conn.executescript(_SCHEMA)
        conn.execute("INSERT INTO auction_records VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json",
                     (record["id"], record["job_id"], record["created_at"], encoded))
        conn.commit()


def _start(record: dict[str, Any], plan: dict[str, Any], profile_key: str) -> None:
    encoded = _encoded(record)
    frozen = ex._bounded({"messages": plan["_messages"], "source": plan["_sample"],
                          "contract": plan["_contract"]}, MAX_RECORD_BYTES, "Frozen auction input")
    with store._lock:
        conn = store._db()
        conn.executescript(_SCHEMA)
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT auction_id FROM auction_profile_locks WHERE profile_key=?", (profile_key,)).fetchone()
            if existing:
                raise AuctionError("An auction already holds this profile's budget. A running or interrupted call must be reconciled before another auction starts.")
            conn.execute("INSERT INTO auction_records VALUES (?, ?, ?, ?)",
                         (record["id"], record["job_id"], record["created_at"], encoded))
            conn.execute("INSERT OR IGNORE INTO auction_inputs VALUES (?, ?, ?, ?, ?)",
                         (record["input_sha256"], record["job_id"], record["execution_id"], record["created_at"], frozen))
            conn.execute("INSERT INTO auction_profile_locks VALUES (?, ?, ?)", (profile_key, record["id"], record["created_at"]))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _release(profile_key: str, auction_id: str) -> None:
    with store._lock:
        conn = store._db()
        conn.execute("DELETE FROM auction_profile_locks WHERE profile_key=? AND auction_id=?", (profile_key, auction_id))
        conn.commit()


def _receipt(record: dict[str, Any], call: dict[str, Any]) -> None:
    cost = call["cost"]
    receipts.record_overhead(record["job_id"], kind="comparison", source_id=call["call_id"],
                             actual_cost_usd=cost.get("actual_usd"), estimated_cost_usd=cost.get("estimated_usd"),
                             api_equivalent_usd=cost.get("api_equivalent_usd"), provenance=cost.get("provenance", "unknown"),
                             status=call["status"], tokens=cost.get("tokens"), duration_ms=call.get("duration_ms"),
                             details={"experiment_type": "model_auction", "auction_id": record["id"],
                                      "index": call["index"], "answered_by": call.get("answered_by"),
                                      "input_sha256": record["input_sha256"]})


def _call(completion: Callable[..., Any], route: dict[str, Any], record: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    config = {"provider": route["provider"], "model": route["model"], "max_tokens": plan["max_tokens"], "timeout": 60}
    call = {"index": route["index"], "call_id": f"auction:{record['id']}:{route['index']}", "status": "pending", "complete": False,
            "input_sha256": record["input_sha256"], "source_execution_id": record["execution_id"],
            "requested": {**config, "prompt_sha256": plan["prompt_sha256"]}, "started_at": time.time(),
            "reservation_usd": route["reservation_usd"], "cost": {"actual_usd": None, "estimated_usd": None,
            "api_equivalent_usd": None, "provenance": "unknown"}}
    record["calls"].append(call)
    _save(record)
    _receipt(record, call)
    started = time.monotonic()
    completion_duration_ms = None
    try:
        result = completion(copy.deepcopy(plan["_messages"]),
                            purpose="Crystallize explicitly confirmed answer-only model auction", **config)
        completion_duration_ms = max(0.0, (time.monotonic() - started) * 1000)
        call["cost"] = ex._usage(result)
        call["answered_by"] = {key: ex._get(result, key) for key in ("provider", "model", "agent_id")}
        text = ex._get(result, "text")
        if not isinstance(text, str) or not text.strip():
            raise AuctionError("The model returned no usable answer.")
        call.update(output_sha256=ex._hash(text), output_chars=len(text))
        if len(text) > ex.MAX_OUTPUT_CHARS:
            raise AuctionError("The output exceeds the complete-answer storage limit; it was not truncated or scored as complete.")
        call["output"] = text
        call["completion_check"] = ex._completion_check(result, config)
        if not call["completion_check"]["ok"]:
            raise AuctionError("The answer was incomplete, failed, or reached its output-token ceiling.")
        # Exact matching avoids silently attributing a host fallback to the user
        # selected model. A canonical alias remains visible for manual review.
        call["route_match"] = all(isinstance(call["answered_by"].get(key), str)
                                  and call["answered_by"][key].strip().lower() == config[key].lower()
                                  for key in ("provider", "model"))
        if not call["route_match"]:
            raise AuctionError("The host reported a different or unverified route. Its answer and cost are retained, but it is not ranked as the selected model.")
        call["validation"] = ex._validation(plan["_contract"], text, plan["_sample"]["observations"])
        call.update(status="completed", complete=True)
    except Exception as exc:
        call.update(status="failed", error=str(exc)[:2000])
        if not call["cost"].get("tokens") and ex._get(exc, "usage") is not None:
            call["cost"] = ex._usage({"usage": ex._get(exc, "usage"), "provider": route["provider"], "model": route["model"]})
    call["duration_ms"] = completion_duration_ms if completion_duration_ms is not None else max(0.0, (time.monotonic() - started) * 1000)
    call["finished_at"] = time.time()
    _receipt(record, call)
    _save(record)
    return call


def _account(call: dict[str, Any], route: dict[str, Any], estimated_ok: bool) -> tuple[Optional[float], str]:
    cost = call["cost"]
    actual = _number(cost.get("actual_usd"))
    if actual is not None:
        return actual, "host_actual"
    if not estimated_ok:
        return None, "unknown_actual_spend"
    if call.get("route_match") is not True:
        return None, "unknown_served_route_price"
    tokens = cost.get("tokens") or {}
    # The facade can substitute zero when usage is missing. A nonempty answer
    # with zero output/input usage is not a measured free call.
    if any(isinstance(tokens.get(k), bool) or not isinstance(tokens.get(k), int) or tokens[k] <= 0
           for k in ("input_tokens", "output_tokens")):
        return None, "unknown_token_usage"
    pricing = route["pricing"]
    input_count = sum(tokens.get(k) or 0 for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens"))
    output_count = (tokens.get("output_tokens") or 0) + (tokens.get("reasoning_tokens") or 0)
    estimate = (input_count * pricing["input_usd_per_token"]
                + output_count * pricing["output_usd_per_token"] + pricing["request_cost"])
    # Report the route estimate separately even when a host estimate is larger.
    cost["auction_api_equivalent_usd"] = estimate
    cost["api_equivalent_usd"] = max(estimate, _number(cost.get("api_equivalent_usd")) or 0.0)
    if cost.get("provenance") == "unknown":
        cost["provenance"] = "auction_exact_route_api_equivalent"
    return max(estimate, _number(cost.get("estimated_usd")) or 0.0,
               _number(cost.get("api_equivalent_usd")) or 0.0), "conservative_listed_price_estimate"


def _scoreboard(record: dict[str, Any]) -> list[dict[str, Any]]:
    calls = {call["index"]: call for call in record["calls"]}
    rows = []
    for route in record["models"]:
        call = calls.get(route["index"], {})
        rows.append({"index": route["index"], "provider": route["provider"], "model": route["model"],
                     "status": call.get("status", "skipped"), "answered_by": call.get("answered_by"),
                     "error": call.get("error"), "structural_checks": call.get("validation"),
                     "completion_check": call.get("completion_check"), "cost": call.get("cost"),
                     "duration_ms": call.get("duration_ms"), "output_sha256": call.get("output_sha256"),
                     "quality_proven": False, "human_review": "not_reviewed",
                     "efficiency_savings_claim": False})
    return rows


def _blind_pairs(record: dict[str, Any]) -> list[dict[str, Any]]:
    from . import blind
    good = [c for c in record["calls"] if c["status"] == "completed"]
    result = []
    for pos, first in enumerate(good):
        for second in good[pos + 1:]:
            comparison_id = f"auction:{record['id']}:{first['index']}:{second['index']}"
            pair = {"sample_index": 0, "source_execution_id": record["execution_id"],
                    "input_sha256": record["input_sha256"], "same_input": True,
                    "both_completed": True, "quality_proven": False}
            snapshot = {"id": comparison_id, "job_id": record["job_id"], "created_at": record["created_at"],
                        "status": "completed", "scope": ex._SCOPE, "sample_count": 1,
                        "calls": [{**copy.deepcopy(first), "side": "original", "sample_index": 0},
                                  {**copy.deepcopy(second), "side": "candidate", "sample_index": 0}],
                        "pairs": [pair], "limitations": _LIMITATIONS, "experiment_kind": "model_comparison",
                        "experiment_label": "Auction model comparison", "efficiency_comparison": False,
                        "efficiency_savings_claim": False}
            blind.register(comparison_id, snapshot)
            result.append({"comparison_id": comparison_id, "left_index": first["index"], "right_index": second["index"]})
    return result


def run(job_id: str, models: Any, *, max_tokens: int = 1024, cost_cap_usd: float,
        execution_id: Optional[str] = None, expected_input_sha256: Optional[str] = None,
        accept_estimated_cap: bool = False, confirmed: bool = False,
        completion: Optional[Callable[..., Any]] = None) -> dict[str, Any]:
    """Run once only after explicit confirmation of routes, cap and cap mode."""
    if confirmed is not True:
        raise AuctionError("A model auction must be explicitly confirmed before any model calls can run.")
    plan = _prepare(job_id, models, max_tokens=max_tokens, cost_cap_usd=cost_cap_usd,
                    execution_id=execution_id, accept_estimated_cap=accept_estimated_cap)
    if expected_input_sha256 is not None and expected_input_sha256 != plan["input_sha256"]:
        raise AuctionError("The task or frozen input changed since preview; preview and confirm the new input before making calls.")
    if completion is None:
        completion = getattr(getattr(ex._context(), "llm", None), "complete", None)
    if not callable(completion):
        raise AuctionError("Hermes's public ctx.llm.complete interface is unavailable in this process.")
    record = {**_public_plan(plan), "id": uuid.uuid4().hex, "created_at": time.time(), "status": "running",
              "calls": [], "scoreboard": [], "blind_pairs": [], "quality_proven": False,
              "efficiency_savings_claim": False, "contract": plan["_contract"],
              "budget": {"cap_usd": plan["cost_cap_usd"], "cap_mode": plan["cap_mode"],
                         "estimated_reservation_usd": plan["estimated_reservation_usd"],
                         "accounted_usd": 0.0, "actual_usd": 0.0, "actual_spend_complete": True,
                         "known_actual_usd": 0.0, "unknown_actual_call_count": 0,
                         "unknown_call_count": 0, "billing_cap_guaranteed": False}}
    profile_key = _profile_key()
    _start(record, plan, profile_key)
    # A crash/persistence failure leaves the durable profile lock in place. No
    # time-based lease may silently retry a call whose billing is unresolved.
    for route in plan["models"]:
        try:
            _grant([{k: route[k] for k in ("provider", "model")}])
        except AuctionError as exc:
            record.update(status="stopped_policy", stop_reason=str(exc))
            break
        call = _call(completion, route, record, plan)
        accounted, basis = _account(call, route, accept_estimated_cap)
        call["budget_basis"] = basis
        call["accounted_usd"] = accounted
        budget = record["budget"]
        actual = _number(call["cost"].get("actual_usd"))
        if actual is None:
            budget["actual_spend_complete"] = False
            budget["actual_usd"] = None
            budget["unknown_actual_call_count"] += 1
        else:
            budget["known_actual_usd"] = math.fsum((budget["known_actual_usd"], actual))
            if budget["actual_usd"] is not None:
                budget["actual_usd"] = math.fsum((budget["actual_usd"], actual))
        if accounted is None:
            budget["unknown_call_count"] += 1
            record.update(status="stopped_unknown_spend", stop_reason="Spend or required token usage is unknown; no later route was called.")
            _receipt(record, call)
            _save(record)
            break
        budget["accounted_usd"] = math.fsum((budget["accounted_usd"], accounted))
        remaining = math.fsum(r["reservation_usd"] for r in plan["models"] if r["index"] > route["index"])
        budget["remaining_reservation_usd"] = remaining
        budget["remaining_cap_usd"] = plan["cost_cap_usd"] - budget["accounted_usd"]
        _receipt(record, call)
        if budget["accounted_usd"] > plan["cost_cap_usd"] or budget["accounted_usd"] + remaining > plan["cost_cap_usd"]:
            record.update(status="stopped_cap", stop_reason="Reported or estimated spend exceeded the cap or the remaining reservation; no later route was called.")
            _save(record)
            break
        _save(record)
    if record["status"] == "running":
        record["status"] = "completed"
    record["failed_call_count"] = sum(c["status"] == "failed" for c in record["calls"])
    record["all_answers_completed"] = len(record["calls"]) == len(record["models"]) and record["failed_call_count"] == 0
    record["finished_at"] = time.time()
    record["scoreboard"] = _scoreboard(record)
    try:
        record["blind_pairs"] = _blind_pairs(record)
    except Exception as exc:
        # Blind presentation is optional; its persistence cannot erase paid calls.
        record["blind_review_error"] = str(exc)[:500]
    _save(record)
    _release(profile_key, record["id"])
    return receipts.redact_export(record)


def get(auction_id: str) -> Optional[dict[str, Any]]:
    """Read a durable result without making calls."""
    with store._lock:
        conn = store._db()
        conn.executescript(_SCHEMA)
        row = conn.execute("SELECT data_json FROM auction_records WHERE id=?", (str(auction_id),)).fetchone()
    return receipts.redact_export(json.loads(row[0])) if row else None


def history(job_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Read current-profile auctions, including failed and interrupted records."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise AuctionError("History limit must be between 1 and 100.")
    canonical_id, _ = ex._job(job_id)
    with store._lock:
        conn = store._db()
        conn.executescript(_SCHEMA)
        rows = conn.execute("SELECT data_json FROM auction_records WHERE job_id=? ORDER BY created_at DESC,id DESC LIMIT ?",
                            (canonical_id, limit)).fetchall()
    return [receipts.redact_export(json.loads(row[0])) for row in rows]
