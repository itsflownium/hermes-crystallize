"""Reasoning levels come from Hermes, whatever the model."""

import sys
import types

import pytest

from crystallize import modelinfo

pytest.importorskip("agent.reasoning_effort")


@pytest.fixture(autouse=True)
def _fresh_lookups(monkeypatch):
    monkeypatch.setattr(modelinfo, "_lookups", {})


def _levels(monkeypatch, caps, current, pin=None):
    monkeypatch.setattr(modelinfo, "_catalog_caps", lambda r: caps)
    monkeypatch.setattr(modelinfo, "_config", lambda: {"agent": {"reasoning_effort": current},
                                                       "model": {"default": "m", "provider": "nous"}})
    return modelinfo.levels_to_try({"reasoning_effort": pin} if pin else {})


@pytest.mark.parametrize("listed,current,want", [
    (["max", "xhigh", "high", "medium", "low"], "max", ["low", "medium", "high", "xhigh"]),  # weakest first
    (["high", "medium", "low"], "max", ["low", "medium"]),   # max really runs as high here
    (["max", "high", "low"], "high", ["low"]),               # only what the model lists
    (["high", "max"], "high", []),                           # already at its floor
    (["xhigh", "high", "none"], "max", ["high"]),            # "none" is never a target
])
def test_levels_follow_the_model(monkeypatch, listed, current, want):
    assert _levels(monkeypatch, {"supports_reasoning": True, "supported_efforts": listed}, current) == want


def test_model_without_reasoning_has_nothing_to_try(monkeypatch):
    assert _levels(monkeypatch, {"supports_reasoning": False}, "max") == []


def test_unlisted_or_unknown_model_still_gets_a_try(monkeypatch):
    assert _levels(monkeypatch, {"supports_reasoning": True, "supported_efforts": None}, "max") == ["low", "medium"]
    assert _levels(monkeypatch, None, "high") == ["low", "medium"]
    assert _levels(monkeypatch, None, None) == ["low", "medium"]  # following the provider's default


def test_job_pin_beats_global_setting(monkeypatch):
    assert _levels(monkeypatch, None, "max", pin="medium") == ["low"]


def test_route_prefers_the_job_and_drops_base_url_for_another_provider(monkeypatch):
    monkeypatch.setattr(modelinfo, "_config", lambda: {"model": {"default": "m", "provider": "nous", "base_url": "u"}})
    assert modelinfo.route({})["base_url"] == "u"
    r = modelinfo.route({"model": "x", "provider": "other"})
    assert r["model"] == "x" and r["provider"] == "other" and r["base_url"] is None


def test_unknown_tool_means_leave_the_tools_alone(monkeypatch):
    # This repo's own tools.py shadows Hermes's tools package when tests run from here.
    fake = types.ModuleType("tools.registry")
    fake.discover_builtin_tools = lambda: None
    fake.registry = types.SimpleNamespace(get_tool_to_toolset_map=lambda: {"web_extract": "web", "web_search": "web"})
    monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.registry", fake)
    assert modelinfo.toolsets_for({"a_tool_nobody_has"}) is None
    assert modelinfo.toolsets_for({"tool_describe"}) == []
    assert modelinfo.toolsets_for({"web_extract", "web_search", "tool_search"}) == ["web"]


def test_api_cost_uses_hermes_prices_for_a_subscription_model():
    pytest.importorskip("agent.usage_pricing")
    tokens = {"input": 1_000_000, "output": 1_000_000}
    cost = modelinfo.api_cost("gpt-6-luna", "openai-codex", tokens)
    assert cost and cost > 0
    assert modelinfo.api_cost("a-model-nobody-prices", "local-thing", tokens) is None
    assert modelinfo.api_cost(None, "openai-codex", tokens) is None


@pytest.mark.parametrize("declared,want", [
    ((), []),                                  # the provider says: no reasoning settings at all
    (("high", "low"), ["low", "high"]),        # its own list, weakest first
    (("none", "bespoke-tier"), []),            # nothing Crystallize can safely aim for
    (None, ["low", "medium", "high"]),         # it doesn't say: the widely accepted levels
])
def test_any_provider_profile_answer_is_handled(monkeypatch, declared, want):
    monkeypatch.setattr(modelinfo, "_catalog_caps", lambda r: None)
    monkeypatch.setattr(modelinfo, "_profile_levels", lambda r: declared)
    monkeypatch.setattr(modelinfo, "_models_dev_says_no_reasoning", lambda r: False)
    assert modelinfo.reasoning_levels({"provider": "some-provider", "model": "m"}) == want


def test_a_model_known_to_have_no_reasoning_is_skipped(monkeypatch):
    monkeypatch.setattr(modelinfo, "_catalog_caps", lambda r: None)
    monkeypatch.setattr(modelinfo, "_profile_levels", lambda r: None)
    monkeypatch.setattr(modelinfo, "_models_dev_says_no_reasoning", lambda r: True)
    assert modelinfo.reasoning_levels({"provider": "some-provider", "model": "m"}) == []


@pytest.mark.parametrize("provider,model", [(None, None), ("", ""), ("auto", "x"), ("not-a-provider", "a/b/c")])
def test_odd_routes_never_raise(monkeypatch, provider, model):
    route = {"provider": provider, "model": model, "base_url": None, "api_mode": None}
    assert isinstance(modelinfo.reasoning_levels(route), list)
    assert modelinfo.api_cost(model, provider, {"input": 1}) is None or provider
    modelinfo.output_price(route)
    modelinfo.token_prices(model, provider)
