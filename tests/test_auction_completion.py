"""Bounded model auctions use real evidence, public calls and honest ledgers."""

import copy
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from crystallize import auction, blind, chat_savings, contracts, experiments as ex, history, modelinfo, receipts, store, tools


@pytest.fixture
def world(monkeypatch):
    job = {"id": "j-auction", "name": "Recorded source summary", "prompt": "Return the observed value and source URL."}
    routes = [{"provider": "connected-a", "model": "model-one"},
              {"provider": "connected-b", "model": "model-two"}]
    config = {"plugins": {"entries": {"crystallize": {"llm": {
        "allow_provider_override": True, "allow_model_override": True}}}}}
    prices = {"input": 0.000001, "output": 0.000002, "cache_read": 0.0000005,
              "cache_write": 0.00000125, "request_cost": 0.0, "source": "host_test_price_list"}
    runs = []
    monkeypatch.setattr(ex.lifecycle, "cron_job", lambda value: job if value in {job["id"], job["name"]} else None)
    monkeypatch.setattr(modelinfo, "_config", lambda: config)
    monkeypatch.setattr(modelinfo, "exact_token_prices", lambda *args, **kwargs: dict(prices), raising=False)
    monkeypatch.setattr(modelinfo, "api_cost", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_savings, "connected_models", lambda: [
        {"slug": "connected-a", "models": [{"id": "model-one"}, {"id": "model-three"}]},
        {"slug": "connected-b", "models": [{"id": "model-two"}]}])
    monkeypatch.setattr(history, "load_runs", lambda *args, **kwargs: list(runs))
    monkeypatch.setattr(history, "_session_costs", lambda *args, **kwargs: {})
    monkeypatch.setattr(tools, "dispatch", lambda *args, **kwargs: pytest.fail("auction replayed a tool"))
    ex.set_context(SimpleNamespace(llm=SimpleNamespace(complete=lambda *args, **kwargs: pytest.fail("unexpected live facade call"))))

    def trace(text='{"value":19.75,"url":"https://source.example/current"}', **changes):
        row = {"ts": 1.0, "session_id": "session-auction", "tool_call_id": "captured-read",
               "tool_name": "read_file", "args_json": '{"path":"/tmp/observed.json"}',
               "result_sha256": hashlib.sha256(text.encode()).hexdigest(), "result_len": len(text),
               "result_preview": text, "status": "ok", "cron_job_id": job["id"], "cron_job_name": job["name"],
               "cron_execution_id": "execution-auction", "cron_scheduled": 1}
        row.update(changes)
        store.add_trace(row)
        if not runs:
            runs.append(history.Run(execution_id="execution-auction", job_id=job["id"], job_name=job["name"],
                                    session_id="session-auction", started_ts=1.0, scheduled=True,
                                    outcome="completed", finished=True))
        return row

    yield SimpleNamespace(job=job, routes=routes, config=config, prices=prices, trace=trace, runs=runs)
    ex.set_context(None)


def completion(calls, *, actual=0.001, estimated=None, input_tokens=50, output_tokens=10,
               text='{"value":19.75,"url":"https://source.example/current"}', **extra):
    def complete(messages, *, provider, model, max_tokens, timeout, purpose):
        calls.append((copy.deepcopy(messages), {"provider": provider, "model": model, "max_tokens": max_tokens,
                                               "timeout": timeout, "purpose": purpose}))
        messages[-1]["content"] = "caller-mutated messages"
        usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                                total_tokens=input_tokens + output_tokens, cost_usd=estimated)
        if actual is not None:
            usage.actual_cost_usd = actual
        return SimpleNamespace(text=text, provider=provider, model=model, agent_id="default", usage=usage, **extra)
    return complete


def run(world, calls, **kwargs):
    return auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1.0,
                       confirmed=True, completion=completion(calls), **kwargs)


def test_explicit_confirmation_and_positive_cap_required_before_calls(world):
    calls = []
    with pytest.raises(auction.AuctionError, match="explicitly confirmed"):
        auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1.0, completion=completion(calls))
    assert not calls
    assert auction.history(world.job["id"]) == []


@pytest.mark.parametrize("cap", [None, 0, -1, True, float("nan"), float("inf"), 101, "1"])
def test_invalid_caps_make_no_calls(world, cap):
    world.trace()
    calls = []
    with pytest.raises(auction.AuctionError, match="cost_cap_usd"):
        auction.run(world.job["id"], world.routes, cost_cap_usd=cap, confirmed=True, completion=completion(calls))
    assert not calls


@pytest.mark.parametrize("max_tokens", [0, -1, 4097, 1.5, True, "64"])
def test_output_cap_must_be_a_bounded_integer(world, max_tokens):
    with pytest.raises(auction.AuctionError, match="max_tokens"):
        auction.run(world.job["id"], world.routes, max_tokens=max_tokens, cost_cap_usd=1, confirmed=True)


@pytest.mark.parametrize("routes", [[], [{"provider": "connected-a", "model": "model-one"}],
                                    [{"provider": "p", "model": f"m{i}"} for i in range(7)],
                                    [{"provider": "connected-a", "model": "model-one"}] * 2,
                                    [{"provider": "p", "model": "m", "reasoning_effort": "low"}] * 2])
def test_explicit_distinct_route_bounds(world, routes):
    with pytest.raises(auction.AuctionError):
        auction.run(world.job["id"], routes, cost_cap_usd=1, confirmed=True)


def test_missing_host_grant_blocks_even_inventory_or_injected_calls(world, monkeypatch):
    world.config["plugins"]["entries"]["crystallize"]["llm"]["allow_model_override"] = False
    monkeypatch.setattr(chat_savings, "connected_models", lambda: pytest.fail("inventory read before grant"))
    calls = []
    result = auction.preview(world.job["id"], world.routes, cost_cap_usd=1)
    assert not result["available"] and "allow_model_override" in result["why"]
    with pytest.raises(auction.AuctionError, match="allow_model_override"):
        run(world, calls)
    assert not calls


def test_legacy_lookalike_grants_do_not_authorize_request_overrides(world):
    world.config.clear()
    world.config.update({"plugins": {"crystallize": {"llm": {"allow_request_overrides": True}}}})
    with pytest.raises(auction.AuctionError, match="allow_provider_override"):
        run(world, [])


@pytest.mark.parametrize("key,allowed", [("providers", ["connected-a"]), ("models", ["model-one"]), ("models", [])])
def test_host_route_allowlists_are_enforced(world, key, allowed):
    world.config["plugins"]["entries"]["crystallize"]["llm"]["allowed_" + key] = allowed
    with pytest.raises(auction.AuctionError, match="outside Hermes"):
        run(world, [])


def test_exact_connected_inventory_required(world):
    world.routes[1]["model"] = "invented-model"
    with pytest.raises(auction.AuctionError, match="current connected"):
        run(world, [])


def test_inventory_failure_is_not_silently_assumed_connected(world, monkeypatch):
    monkeypatch.setattr(chat_savings, "connected_models", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    result = auction.preview(world.job["id"], world.routes, cost_cap_usd=1)
    assert not result["available"] and "inventory is unavailable" in result["why"]


def test_unknown_price_rejects_before_calls_even_in_estimated_mode(world, monkeypatch):
    world.trace()
    monkeypatch.setattr(modelinfo, "exact_token_prices", lambda *args, **kwargs: None)
    calls = []
    with pytest.raises(auction.AuctionError, match="Known exact-route"):
        run(world, calls, accept_estimated_cap=True)
    assert not calls and auction.history(world.job["id"]) == []


@pytest.mark.parametrize("key,value", [("input", -1), ("output", None), ("cache_write", float("inf")),
                                        ("input_above", -2), ("request_cost", -1)])
def test_invalid_price_categories_fail_closed(world, key, value):
    world.trace()
    world.prices[key] = value
    with pytest.raises(auction.AuctionError):
        run(world, [])


def test_preflight_reserves_full_input_max_output_cache_tiers_and_request_fees(world):
    world.trace()
    world.prices.update(input_above=0.00001, output_above=0.00002, cache_write_above=0.00003,
                        request_cost=0.01, tier_threshold_tokens=100)
    preview = auction.preview(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1)
    assert preview["available"]
    expected = preview["input_tokens_estimate"] * 0.00003 + 64 * 0.00002 + 0.01
    assert all(r["reservation_usd"] == pytest.approx(expected) for r in preview["models"])
    assert preview["estimated_reservation_usd"] == pytest.approx(2 * expected)
    calls = []
    with pytest.raises(auction.AuctionError, match="reservation"):
        auction.run(world.job["id"], world.routes, max_tokens=64,
                    cost_cap_usd=preview["estimated_reservation_usd"] / 2,
                    confirmed=True, completion=completion(calls))
    assert not calls


def test_preview_history_and_result_reads_make_no_calls(world):
    world.trace()
    preview = auction.preview(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1)
    assert preview["available"] and preview["cap_mode"] == "actual_ledger"
    assert "provider billing guarantee" in preview["cap_notice"]
    assert auction.history(world.job["id"]) == []
    assert auction.get("does-not-exist") is None


def test_same_complete_frozen_task_public_facade_and_durable_result(world):
    text = '{"value":19.75,"url":"https://source.example/current","padding":"' + "evidence " * 10000 + '"}'
    world.trace(text=text)
    contracts.set(world.job["id"], {"required_fields": ["value", "url"], "required_urls": ["https://source.example/current"]})
    original_config = copy.deepcopy(world.config)
    calls = []
    result = run(world, calls)
    assert len(calls) == 2 and calls[0][0] == calls[1][0]
    captured = json.loads(calls[0][0][-1]["content"].split("\n", 1)[1])
    assert captured["observations"][0]["output"] == text
    assert calls[0][0][1]["content"] == world.job["prompt"]
    assert all(set(c[1]) == {"provider", "model", "max_tokens", "timeout", "purpose"} for c in calls)
    assert world.config == original_config
    assert result["status"] == "completed" and all(c["status"] == "completed" for c in result["calls"])
    assert result["budget"]["actual_usd"] == pytest.approx(0.002)
    assert result["budget"]["actual_spend_complete"] and not result["budget"]["billing_cap_guaranteed"]
    assert all(row["structural_checks"]["ok"] for row in result["scoreboard"])
    assert not result["quality_proven"] and not result["efficiency_savings_claim"]
    assert all(not row["quality_proven"] and not row["efficiency_savings_claim"] for row in result["scoreboard"])
    assert auction.get(result["id"])["input_sha256"] == result["input_sha256"]
    assert auction.history(world.job["id"])[0]["id"] == result["id"]
    with store._lock:
        frozen = store._db().execute("SELECT data_json FROM auction_inputs WHERE input_sha256=?", (result["input_sha256"],)).fetchone()
    saved = json.loads(frozen[0])
    assert saved["messages"] == calls[0][0]
    assert saved["source"]["complete"] is True
    receipt = receipts.receipt(world.job["id"])
    assert receipt["comparison_call_count"] == 2
    assert receipt["overhead_by_kind"]["comparison"]["actual"]["total_usd"] == pytest.approx(0.002)


def test_changed_prompt_after_preview_requires_fresh_confirmation(world):
    world.trace()
    before = auction.preview(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1)
    world.job["prompt"] += " Include a new requirement."
    calls = []
    with pytest.raises(auction.AuctionError, match="changed since preview"):
        run(world, calls, expected_input_sha256=before["input_sha256"])
    assert not calls


@pytest.mark.parametrize("changes", [{"result_preview": "cut"}, {"result_sha256": "0" * 64},
                                     {"result_len": None}, {"args_json": '{"path":"cut'}])
def test_incomplete_source_evidence_rejected_before_auction(world, changes):
    world.trace(**changes)
    calls = []
    with pytest.raises(ex.ComparisonError):
        run(world, calls)
    assert not calls


def test_failed_or_unfinished_source_execution_rejected(world):
    world.trace()
    world.runs[0].outcome = "failed"
    with pytest.raises(ex.ComparisonError, match="completed execution"):
        run(world, [])


def test_recorded_actions_are_excluded_and_cannot_pass_action_contract(world):
    row = world.trace()
    store.add_trace({**row, "tool_name": "send_message", "tool_call_id": "recorded-send",
                     "args_json": '{"recipient":"someone@example.com"}'})
    contracts.set(world.job["id"], {"required_actions": [{"tool": "send_message", "target": "someone@example.com"}]})
    calls = []
    result = run(world, calls)
    assert all("send_message" not in messages[-1]["content"] for messages, _ in calls)
    assert all(not row["structural_checks"]["ok"] for row in result["scoreboard"])
    assert not result["quality_proven"]


def test_actual_spend_unknown_stops_before_second_route_even_with_host_estimate(world):
    world.trace()
    calls = []
    result = auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                         confirmed=True, completion=completion(calls, actual=None, estimated=0.01))
    assert len(calls) == 1 and result["status"] == "stopped_unknown_spend"
    assert result["budget"]["actual_usd"] is None and result["budget"]["unknown_call_count"] == 1
    assert result["scoreboard"][1]["status"] == "skipped"
    assert result["calls"][0]["cost"]["estimated_usd"] == 0.01
    assert receipts.receipt(world.job["id"])["comparison_call_count"] == 1


def test_explicit_estimated_cap_uses_measured_usage_and_labels_unknown_bill(world):
    world.trace()
    calls = []
    result = auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                         accept_estimated_cap=True, confirmed=True, completion=completion(calls, actual=None))
    assert len(calls) == 2 and result["status"] == "completed"
    assert result["accept_estimated_cap"] is True and result["budget"]["cap_mode"] == "estimated_ledger"
    assert not result["budget"]["actual_spend_complete"] and result["budget"]["actual_usd"] is None
    expected = (50 * 0.00000125 + 10 * 0.000002) * 2
    assert result["budget"]["accounted_usd"] == pytest.approx(expected)
    assert all(call["budget_basis"] == "conservative_listed_price_estimate" for call in result["calls"])
    assert result["calls"][0]["cost"]["actual_usd"] is None
    assert receipts.receipt(world.job["id"])["overhead_by_kind"]["comparison"]["api_equivalent"]["total_usd"] == pytest.approx(expected)


def test_estimated_cap_still_stops_missing_or_zero_placeholder_usage(world):
    world.trace()
    calls = []
    result = auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                         accept_estimated_cap=True, confirmed=True,
                         completion=completion(calls, actual=None, estimated=0.0001, input_tokens=0, output_tokens=0))
    assert len(calls) == 1 and result["status"] == "stopped_unknown_spend"
    assert result["calls"][0]["budget_basis"] == "unknown_token_usage"


def test_over_cap_actual_charge_is_retained_and_stops_later_calls(world):
    world.trace()
    calls = []
    result = auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                         confirmed=True, completion=completion(calls, actual=2.0))
    assert len(calls) == 1 and result["status"] == "stopped_cap"
    assert result["budget"]["actual_usd"] == result["budget"]["accounted_usd"] == 2.0
    assert receipts.receipt(world.job["id"])["overhead_by_kind"]["comparison"]["actual"]["total_usd"] == 2.0


def test_remaining_reservation_can_stop_before_cap_is_exhausted(world):
    world.trace()
    calls = []
    result = auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                         confirmed=True, completion=completion(calls, actual=0.999999))
    assert len(calls) == 1 and result["status"] == "stopped_cap"
    assert result["budget"]["accounted_usd"] < 1


def test_explicit_actual_zero_is_known_and_not_replaced_by_positive_estimate(world):
    world.trace()
    calls = []
    result = auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                         confirmed=True, completion=completion(calls, actual=0, estimated=4))
    assert len(calls) == 2 and result["budget"]["actual_usd"] == 0
    assert result["budget"]["actual_spend_complete"]
    assert all(c["cost"]["estimated_usd"] is None for c in result["calls"])


def test_failed_paid_call_keeps_usage_and_receipt(world):
    world.trace()
    calls = []
    class ChargedError(RuntimeError):
        usage = SimpleNamespace(actual_cost_usd=0.1, input_tokens=50, output_tokens=0)
    def fail(messages, **kwargs):
        calls.append(messages)
        raise ChargedError("upstream failure after billing")
    result = auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                         confirmed=True, completion=fail)
    assert len(calls) == 2 and all(c["status"] == "failed" for c in result["calls"])
    assert result["budget"]["accounted_usd"] == pytest.approx(0.2)
    assert auction.get(result["id"])["calls"][0]["cost"]["actual_usd"] == 0.1
    assert receipts.receipt(world.job["id"])["overhead_by_kind"]["comparison"]["actual"]["total_usd"] == pytest.approx(0.2)
    assert not result["blind_pairs"]


def test_failed_unknown_paid_call_stops_and_survives_in_history(world):
    world.trace()
    calls = []
    def fail(messages, **kwargs):
        calls.append(messages)
        raise RuntimeError("unknown paid failure")
    result = auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                         confirmed=True, completion=fail)
    assert len(calls) == 1 and result["status"] == "stopped_unknown_spend"
    assert auction.history(world.job["id"])[0]["calls"][0]["status"] == "failed"
    assert receipts.receipt(world.job["id"])["comparison_call_count"] == 1


@pytest.mark.parametrize("extra,output_tokens", [({"finish_reason": "length"}, 10), ({"truncated": True}, 10), ({}, 64)])
def test_incomplete_model_output_is_preserved_but_never_scored_complete(world, extra, output_tokens):
    world.trace()
    calls = []
    result = auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                         confirmed=True, completion=completion(calls, output_tokens=output_tokens, **extra))
    assert all(c["status"] == "failed" for c in result["calls"])
    assert all(c["output"] and not c["completion_check"]["ok"] for c in result["calls"])
    assert all(row["structural_checks"] is None for row in result["scoreboard"])
    assert not result["blind_pairs"]


def test_host_fallback_is_not_attributed_to_selected_model_or_estimated_cheap_route(world):
    world.trace()
    calls = []
    public = completion(calls, actual=None)
    def fallback(messages, **kwargs):
        result = public(messages, **kwargs)
        result.provider = "unselected-provider"
        return result
    result = auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                         accept_estimated_cap=True, confirmed=True, completion=fallback)
    assert len(calls) == 1 and result["status"] == "stopped_unknown_spend"
    assert not result["calls"][0]["route_match"]
    assert result["calls"][0]["answered_by"]["provider"] == "unselected-provider"
    assert result["calls"][0]["status"] == "failed"


def test_host_grant_revoked_between_routes_stops_without_next_call(world):
    world.trace()
    calls = []
    public = completion(calls)
    def revoke(messages, **kwargs):
        result = public(messages, **kwargs)
        world.config["plugins"]["entries"]["crystallize"]["llm"]["allow_provider_override"] = False
        return result
    result = auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                         confirmed=True, completion=revoke)
    assert len(calls) == 1 and result["status"] == "stopped_policy"


def test_no_retry_and_concurrent_profile_budget_is_serialized(world):
    world.trace()
    entered, release = threading.Event(), threading.Event()
    calls, result, errors = [], [], []
    public = completion(calls)
    def block(messages, **kwargs):
        entered.set()
        assert release.wait(5), "test did not release the public call"
        return public(messages, **kwargs)
    def worker():
        try:
            result.append(auction.run(world.job["id"], world.routes, max_tokens=64, cost_cap_usd=1,
                                      confirmed=True, completion=block))
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(auction.AuctionError, match="profile's budget"):
            run(world, [])
        assert auction.history(world.job["id"])[0]["calls"][0]["status"] == "pending"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert len(calls) == 2 and result[0]["status"] == "completed"


def test_interrupted_pending_record_keeps_durable_budget_lock(world, monkeypatch):
    world.trace()
    calls = []
    original_save = auction._save
    def broken_save(record):
        if record["calls"] and record["calls"][0]["status"] != "pending":
            raise OSError("storage unavailable after paid call")
        original_save(record)
    monkeypatch.setattr(auction, "_save", broken_save)
    with pytest.raises(OSError, match="storage unavailable"):
        run(world, calls)
    assert len(calls) == 1
    monkeypatch.setattr(auction, "_save", original_save)
    with pytest.raises(auction.AuctionError, match="interrupted call"):
        run(world, [])
    saved = auction.history(world.job["id"])[0]
    assert saved["status"] == "running" and saved["calls"][0]["status"] == "pending"
    assert receipts.receipt(world.job["id"])["comparison_call_count"] == 1


def test_two_successful_answers_support_anonymous_pairwise_review(world):
    world.trace()
    result = run(world, [])
    assert len(result["blind_pairs"]) == 1
    review_id = result["blind_pairs"][0]["comparison_id"]
    before = blind.present(review_id)
    assert before["can_choose"] and not before["revealed"]
    assert "reveal" not in before and "mapping" not in before
    selected = blind.choose(review_id, "tie")
    assert selected["revealed"] and selected["choice"] == "tie"
    assert selected["reveal"]["efficiency_savings_claimed"] is False
