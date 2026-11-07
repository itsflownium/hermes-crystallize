"""Helper facade defaults and per-result reply recovery, without provider calls."""
import ast
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Optional

import pytest

from crystallize import capture, chat_savings as cs, store

PICKED = dict(model="fixture-helper", provider="fixture")
PRICES = dict(input=10e-6, output=20e-6, cache_read=1e-6, cache_write=20e-6)


@dataclass
class FacadeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: Optional[float] = None


def call_with_usage(monkeypatch, usage, *, failed=False):
    store.set_chat_enabled("owner", True)
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda *args: PRICES)

    def complete(*args, **kwargs):
        if failed:
            error = RuntimeError("isolated failed helper")
            error.usage = usage
            raise error
        return SimpleNamespace(text="Complete fixture answer", usage=usage, **PICKED)

    answer, _ = cs._call(SimpleNamespace(llm=SimpleNamespace(complete=complete)), [],
                         "isolated usage fixture", session_id="owner", picked=PICKED)
    assert (answer is None) == failed
    return store.chat_events("owner")[0]


@pytest.mark.parametrize("failed", [False, True])
def test_facade_default_cache_zeroes_are_unknown_in_success_and_failure(monkeypatch, failed):
    usage = FacadeUsage(input_tokens=100, output_tokens=10)
    row = call_with_usage(monkeypatch, usage, failed=failed)
    assert row["cheap_cost"] == pytest.approx(0.0022)
    assert row["cost_source"] == "list_price_upper_bound_unknown_cache"
    assert row["status"] == ("failed" if failed else "answered")
    assert usage.cache_read_tokens == usage.cache_write_tokens == 0  # host DTO unchanged


@pytest.mark.parametrize("usage", [FacadeUsage(), FacadeUsage(output_tokens=10), FacadeUsage(input_tokens=100)])
def test_ambiguous_default_input_or_output_zero_never_invents_a_known_cost(monkeypatch, usage):
    row = call_with_usage(monkeypatch, usage)
    assert row["cheap_cost"] is None and row["cost_source"] == "unknown"


@pytest.mark.parametrize("reported", [0.0, 0.015])
def test_reported_host_cost_wins_over_ambiguous_zero_counters(monkeypatch, reported):
    row = call_with_usage(monkeypatch, FacadeUsage(cost_usd=reported))
    assert row["cheap_cost"] == reported and row["cost_source"] == "host_estimate"


def test_positive_facade_cache_buckets_remain_evidence(monkeypatch):
    usage = FacadeUsage(input_tokens=100, output_tokens=10, cache_read_tokens=40, cache_write_tokens=20)
    row = call_with_usage(monkeypatch, usage)
    assert row["cheap_cost"] == pytest.approx(0.00104)
    assert row["cost_source"] == "list_price_estimate"


def test_reviewed_sdk_source_cannot_turn_missing_cache_metadata_into_a_low_estimate(monkeypatch):
    """Required in the helper-sdk-contract CI job; compile only usage conversion.

    No Hermes imports, registry, host startup, authentication or provider access.
    """
    source = os.environ.get("CRYSTAL_HERMES_LLM_SOURCE")
    if not source:
        pytest.skip("The dedicated CI job supplies the reviewed SDK source.")
    tree = ast.parse(Path(source).read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
             and node.name in {"PluginLlmUsage", "_extract_usage"}]
    assert len(nodes) == 2, "Review the upstream usage contract before changing the SDK pin."
    module = ModuleType("isolated_crystal_sdk_usage")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    module.__dict__.update(dataclass=dataclass, Any=Any, Optional=Optional)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 "verbatim-reviewed-sdk-usage", "exec"), module.__dict__)
    raw = SimpleNamespace(prompt_tokens=100, completion_tokens=10)
    usage = module._extract_usage(SimpleNamespace(usage=raw))
    assert usage.cache_read_tokens == usage.cache_write_tokens == 0
    row = call_with_usage(monkeypatch, usage)
    assert row["cheap_cost"] == pytest.approx(0.0022)
    assert row["cost_source"] == "list_price_upper_bound_unknown_cache"


@pytest.fixture
def originals(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_full_dir", lambda: tmp_path / "originals")


def marker(result_id):
    return "{{crystal:" + result_id + "}}"


@pytest.mark.parametrize("missing", ["expired", "deleted", "foreign", "no_session"])
def test_unavailable_result_gets_actionable_reply_notice_and_no_savings(originals, missing):
    result_id = cs._keep_full("Owner-only complete text", "owner")
    path = cs._session_dir("owner") / (result_id + ".json")
    session = "owner"
    if missing == "expired":
        payload = json.loads(path.read_text())
        payload["expires_at"] = 0
        path.write_text(json.dumps(payload))
    elif missing == "deleted":
        path.unlink()
    else:
        session = "other" if missing == "foreign" else ""
    result = capture.make_transform_llm_output(None)(
        response_text="Before\n" + marker(result_id) + "\nAfter", session_id=session)
    assert result.startswith("Before\n") and result.endswith("\nAfter")
    assert "unavailable" in result.lower() and "crystal_full_result" in result
    assert result_id in result and "{{crystal:" not in result
    assert "Owner-only complete text" not in result
    assert not store.list_chat_savings()


def test_one_failed_recovery_preserves_other_text_and_credits_only_delivered_result(originals, monkeypatch):
    good = cs._keep_full("Complete delivered content " * 20, "owner")
    bad = cs._keep_full("Unavailable private content", "owner")
    load = cs._load_full

    def failing_load(result_id, session):
        if result_id == bad:
            raise OSError("isolated read failure")
        return load(result_id, session)

    monkeypatch.setattr(cs, "_load_full", failing_load)
    response = "First: " + marker(good) + "\nSecond: " + marker(bad) + "\nTail intact"
    result = capture.make_transform_llm_output(None)(response_text=response, session_id="owner")
    assert result.startswith("First: Complete delivered content ")
    assert "unavailable" in result.lower() and result.endswith("Tail intact")
    assert "{{crystal:" not in result and "Unavailable private content" not in result
    credited = store.list_chat_savings("owner")[0]["output_chars"]
    assert credited == len(load(good, "owner")) - len(marker(good))
    assert capture.make_transform_llm_output(None)(response_text=response, session_id="owner") == result
    assert store.list_chat_savings("owner")[0]["output_chars"] == credited
