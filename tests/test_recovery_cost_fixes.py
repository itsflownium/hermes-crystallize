"""Offline regressions for recovery failures and price-estimate provenance."""
import sqlite3
from types import SimpleNamespace

import pytest

from crystallize import capture, chat, chat_savings as cs, handoffs, overview, store


PICKED = {"model": "helper", "provider": "provider"}
PRICES = dict(input=10e-6, output=20e-6, cache_read=1e-6, cache_write=20e-6)


@pytest.fixture
def originals(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_full_dir", lambda: tmp_path / "originals")
    return "Complete retained deliverable.\n" * 100


@pytest.mark.parametrize("operation", ["get_setting", "set_setting", "add_chat_event"])
@pytest.mark.parametrize("destination", ["tool", "reply"])
def test_retained_text_survives_each_accounting_storage_failure(originals, monkeypatch, operation, destination):
    result_id = cs._keep_full(originals, "owner")
    marker = "{{crystal:" + result_id + "}}"

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("isolated bookkeeping failure")

    monkeypatch.setattr(store, operation, broken)
    ctx = SimpleNamespace(current_cron_execution=lambda: None)
    if destination == "tool":
        args = {"path": "report.txt", "content": marker}
        directive = capture.make_pre_tool_call(ctx)(
            tool_name="write_file", args=args, session_id="owner", turn_id="turn")
        assert directive == {"action": "modify", "args": {"path": "report.txt", "content": originals}}
        assert args["content"] == marker  # no tool or file write is performed
    else:
        result = capture.make_transform_llm_output(ctx)(
            response_text="Report:\n" + marker, session_id="owner", turn_id="turn")
        assert result == "Report:\n" + originals
    assert cs._load_full(result_id, "owner") == originals


def test_unexpected_recovery_failure_blocks_tool_and_explains_reply(originals, monkeypatch):
    result_id = cs._keep_full(originals, "owner")
    marker = "{{crystal:" + result_id + "}}"

    def broken(*args, **kwargs):
        raise RuntimeError("isolated recovery failure")

    monkeypatch.setattr(cs, "_load_full", broken)
    ctx = SimpleNamespace(current_cron_execution=lambda: None)
    directive = capture.make_pre_tool_call(ctx)(
        tool_name="write_file", args={"content": marker}, session_id="owner")
    assert directive["action"] == "block"
    assert "recover" in directive["message"].lower()
    result = capture.make_transform_llm_output(ctx)(response_text=marker, session_id="owner")
    assert "could not recover" in result.lower() and "{{crystal:" not in result
    assert capture.make_transform_llm_output(ctx)(response_text="ordinary text", session_id="owner") is None


def test_placeholder_requires_session_and_recovery_survives_unrelated_cron_observer_error(originals):
    result_id = cs._keep_full(originals, "owner")
    args = {"content": "{{crystal:" + result_id + "}}"}

    def broken():
        raise RuntimeError("isolated cron observation failure")

    ctx = SimpleNamespace(current_cron_execution=broken)
    hook = capture.make_pre_tool_call(ctx)
    assert hook(tool_name="write_file", args=args, session_id="owner")["args"]["content"] == originals
    assert hook(tool_name="write_file", args=args)["action"] == "block"


@pytest.mark.parametrize("cache,expected", [
    ({}, 100 * PRICES["cache_write"]),
    ({"cache_read_tokens": None, "cache_write_tokens": None}, 100 * PRICES["cache_write"]),
    ({"cache_read_tokens": 40}, 40 * PRICES["cache_read"] + 60 * PRICES["cache_write"]),
    ({"cache_write_tokens": 20}, 20 * PRICES["cache_write"] + 80 * PRICES["input"]),
    ({"cache_read_tokens": 0, "cache_write_tokens": 0}, 100 * PRICES["input"]),
    ({"cache_read_tokens": 40, "cache_write_tokens": 20},
     40 * PRICES["cache_read"] + 20 * PRICES["cache_write"] + 40 * PRICES["input"]),
])
def test_helper_cache_counts_use_known_buckets_and_bound_only_unknown_remainder(monkeypatch, cache, expected):
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda *args: PRICES)
    usage = dict(input_tokens=100, output_tokens=10, **cache)
    tokens, cost = cs._usage_cost(usage, PICKED)
    assert tokens == 110 and cost == pytest.approx(expected + 10 * PRICES["output"])
    source = cs._cost_source(usage, cost)
    unknown = any(cache.get(key) is None for key in ("cache_read_tokens", "cache_write_tokens"))
    assert source == ("list_price_upper_bound_unknown_cache" if unknown else "list_price_estimate")


@pytest.mark.parametrize("cache", [
    {"cache_read_tokens": 101}, {"cache_write_tokens": 101},
    {"cache_read_tokens": 80, "cache_write_tokens": 30},
])
def test_inconsistent_helper_cache_counts_stay_unknown(monkeypatch, cache):
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda *args: PRICES)
    assert cs._usage_cost(dict(input_tokens=100, output_tokens=10, **cache), PICKED) == (110, None)


def test_reported_zero_cost_takes_precedence_over_missing_cache_details(monkeypatch):
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda *args: pytest.fail("reported cost needs no price lookup"))
    usage = {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.0}
    assert cs._usage_cost(usage, PICKED) == (110, 0.0)
    assert cs._cost_source(usage, 0.0) == "host_estimate"


def test_upper_bound_helper_cost_is_distinct_in_all_ledger_groups():
    store.add_chat_event("owner", turn_id="turn", kind="routed", helper_call=True,
                         cheap_cost=0.0022, cost_source="list_price_upper_bound_unknown_cache")
    store.add_chat_event("owner", turn_id="turn", kind="routed", helper_call=True,
                         cheap_cost=0.001, cost_source="host_estimate")
    for rows in (store.list_chat_savings("owner"), store.chat_turn_savings("owner"),
                 store.chat_savings_breakdown("owner")):
        assert rows[0]["upper_bound_cost_calls"] == 1
        assert rows[0]["cheap_cost"] == pytest.approx(0.0032)


def test_helper_call_records_bound_and_preserves_requested_reasoning(monkeypatch):
    store.set_chat_enabled("owner", True)
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda *args: PRICES)
    calls = []

    def complete(messages, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text="Complete answer", **PICKED,
                               usage=SimpleNamespace(input_tokens=100, output_tokens=10))

    ctx = SimpleNamespace(llm=SimpleNamespace(complete=complete))
    answer, _ = cs._call(ctx, [], "isolated fixture", session_id="owner", picked=PICKED)
    assert answer.text == "Complete answer"
    row = store.chat_events("owner")[0]
    assert row["cheap_cost"] == pytest.approx(0.0022)
    assert row["cost_source"] == "list_price_upper_bound_unknown_cache"
    assert "reasoning" not in calls[0] and "reasoning_effort" not in calls[0]


def test_all_historical_cards_expose_current_projection_route_and_rates(monkeypatch):
    active = {"model": "original-main", "provider": "provider", "input": 1e-5, "output": 2e-5}
    monkeypatch.setattr(cs, "chosen_model", lambda: {"model": None, "provider": None})
    monkeypatch.setattr(cs, "_main_model", lambda sid: dict(active))
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda *args: dict(active))
    monkeypatch.setattr(cs, "status_for", lambda sid: {"enabled": True})
    monkeypatch.setattr(cs, "_chat_title", lambda sid: None)
    monkeypatch.setattr(chat, "analyse", lambda sid: {})
    monkeypatch.setattr(overview, "_chat_spent", lambda sid: None)
    store.add_chat_event("owner", turn_id="old-turn", chars=4000)
    before = cs.summary("owner")
    recorded = store.chat_events("owner")
    active.update(model="new-main", input=1e-3, output=2e-3)
    after = cs.summary("owner")
    assert store.chat_events("owner") == recorded
    assert before["this_chat"]["net_once"] == pytest.approx(0.01)
    assert after["this_chat"]["net_once"] == pytest.approx(1.0)
    for report, model, rate in ((before, "original-main", 1e-5), (after, "new-main", 1e-3)):
        pricing = report["projection_pricing"]
        assert pricing == dict(model=model, provider="provider", input_usd_per_token=rate,
                               output_usd_per_token=rate * 2, reprices_with_current_main=True)
        for card in [report["this_chat"], *report["turns"], *report["breakdown"]]:
            assert card["cost_basis"] == "one_read_at_current_main_list_price"
            assert card["projection_pricing"] == pricing and card["savings_are_estimates"]
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda *args: None)
    unknown = cs.summary("owner")
    assert unknown["this_chat"]["net_once"] is None
    assert unknown["projection_pricing"]["input_usd_per_token"] is None
