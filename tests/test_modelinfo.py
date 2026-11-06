"""Offline route, declaration and pricing behavior; no installed credentials needed."""

import sys
import types

import pytest

from crystallize import modelinfo


@pytest.fixture(autouse=True)
def _isolated_host(monkeypatch):
    monkeypatch.setattr(modelinfo, "_lookups", {})
    monkeypatch.setattr(modelinfo, "_config", lambda: {})
    monkeypatch.setattr(modelinfo, "_profile", lambda provider: None)


@pytest.mark.parametrize("declared,want", [
    ((), []),
    (("high", "low", "max"), ["low", "high", "max"]),
    (("none", "bespoke-tier"), []),
    (None, []),
])
def test_only_explicit_provider_levels_are_reported(monkeypatch, declared, want):
    profile = types.SimpleNamespace(supported_reasoning_efforts=lambda model: declared)
    monkeypatch.setattr(modelinfo, "_profile", lambda provider: profile)
    assert modelinfo.reasoning_levels({"provider": "new-provider", "model": "m"}) == want


def test_declared_no_reasoning_and_user_override_are_respected(monkeypatch):
    profile = types.SimpleNamespace(model_capabilities={"m": {"supports_reasoning": False}},
                                    supported_reasoning_efforts=lambda model: ("low", "high"))
    monkeypatch.setattr(modelinfo, "_profile", lambda provider: profile)
    assert modelinfo.reasoning_levels({"provider": "p", "model": "m"}) == []
    monkeypatch.setattr(modelinfo, "_config", lambda: {"model_overrides": {"p": {"m": {"supports_reasoning": True}}}})
    assert modelinfo.reasoning_levels({"provider": "p", "model": "m"}) == ["low", "high"]


@pytest.mark.parametrize("value", [None, False, "none", "low", "medium", "high", "max", "bespoke-tier"])
def test_no_reasoning_reduction_is_proposed_even_for_known_levels(monkeypatch, value):
    def unexpected(*args):
        raise AssertionError("Reasoning policy must not query a cheaper level")
    monkeypatch.setattr(modelinfo, "reasoning_levels", unexpected)
    assert modelinfo.levels_to_try({"reasoning_effort": value}) == []


def test_route_prefers_job_then_cron_defaults_without_leaking_another_endpoint(monkeypatch):
    monkeypatch.setattr(modelinfo, "_config", lambda: {
        "model": {"default": "main", "provider": "main-provider", "base_url": "https://main", "api_mode": "responses"},
        "cron": {"model": "scheduled", "model_provider": "cron-provider"}})
    main = modelinfo.route({})
    assert main["model"] == "main" and main["base_url"] == "https://main"
    scheduled = modelinfo.route({"id": "j"})
    assert scheduled == {"model": "scheduled", "provider": "cron-provider", "base_url": None, "api_mode": None}
    pinned = modelinfo.route({"id": "j", "model": "pin", "provider": "other", "base_url": "https://other"})
    assert pinned["model"] == "pin" and pinned["provider"] == "other" and pinned["base_url"] == "https://other"


@pytest.mark.parametrize("value,want", [(False, "none"), ("disabled", "none"), ("high", "high"),
                                        ({"enabled": False}, "none"), ({"effort": "bespoke"}, "bespoke")])
def test_explicit_job_reasoning_survives_without_the_host(monkeypatch, value, want):
    monkeypatch.setitem(sys.modules, "hermes_constants", None)
    monkeypatch.setattr(modelinfo, "_config", lambda: {"agent": {"reasoning_effort": "max"}})
    assert modelinfo.current_level({"reasoning_effort": value}) == want


def test_per_model_reasoning_off_survives_without_the_host(monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_constants", None)
    monkeypatch.setattr(modelinfo, "_config", lambda: {"model": {"default": "m"},
        "agent": {"reasoning_effort": "max", "reasoning_overrides": {"m": False}}})
    assert modelinfo.current_level({}) == "none"


def test_public_reasoning_resolver_receives_the_configured_cron_model(monkeypatch):
    host = types.ModuleType("hermes_constants")
    host.parse_reasoning_effort = lambda pin: None
    seen = []
    host.resolve_reasoning_config = lambda cfg, model: seen.append(model) or {"enabled": True, "effort": "custom"}
    monkeypatch.setitem(sys.modules, "hermes_constants", host)
    monkeypatch.setattr(modelinfo, "_config", lambda: {"cron": {"model": "scheduled"}})
    assert modelinfo.current_level({"id": "j"}) == "custom"
    assert seen == ["scheduled"]


def test_unknown_tool_means_leave_tools_alone(monkeypatch):
    fake = types.ModuleType("tools.registry")
    fake.discover_builtin_tools = lambda: None
    fake.registry = types.SimpleNamespace(get_tool_to_toolset_map=lambda: {"web_extract": "web", "web_search": "web"})
    monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.registry", fake)
    assert modelinfo.toolsets_for({"a_tool_nobody_has"}) is None
    assert modelinfo.toolsets_for({"tool_describe"}) == []
    assert modelinfo.toolsets_for({"web_extract", "web_search", "tool_search"}) == ["web"]


def test_price_route_uses_only_exact_catalog_name_matches(monkeypatch):
    monkeypatch.setattr(modelinfo, "_token_usage", lambda tokens: tokens)
    monkeypatch.setattr(modelinfo, "_listed_models", lambda: {"some-model": ("vendor/some-model", "catalog")})
    monkeypatch.setattr(modelinfo, "_estimate", lambda name, vendor, usage: .5 if (name, vendor) == ("vendor/some-model", "catalog") else None)
    assert modelinfo._price_route("SOME-MODEL", "direct-provider") == ("vendor/some-model", "catalog")
    assert modelinfo._price_route("some-model-highspeed", "direct-provider") is None


def test_arbitrary_hyphenated_provider_is_not_rewritten_as_a_vendor(monkeypatch):
    monkeypatch.setattr(modelinfo, "_token_usage", lambda tokens: tokens)
    monkeypatch.setattr(modelinfo, "_listed_models", lambda: {})
    seen = []
    monkeypatch.setattr(modelinfo, "_estimate", lambda name, vendor, usage: seen.append(vendor) or None)
    assert modelinfo._price_route("m", "local-thing") is None
    assert seen and set(seen) == {"local-thing"}


def test_declared_provider_pricing_id_works_without_a_named_branch(monkeypatch):
    monkeypatch.setattr(modelinfo, "_token_usage", lambda tokens: tokens)
    monkeypatch.setattr(modelinfo, "_profile", lambda provider: types.SimpleNamespace(name="p", models_dev_id="vendor"))
    monkeypatch.setattr(modelinfo, "_listed_models", lambda: {})
    monkeypatch.setattr(modelinfo, "_estimate", lambda name, vendor, usage: .1 if vendor == "vendor" else None)
    assert modelinfo._price_route("m", "p-alias") == ("m", "vendor")


def test_known_exact_price_avoids_catalog_fetch_and_preserves_zero(monkeypatch):
    monkeypatch.setattr(modelinfo, "_token_usage", lambda tokens: tokens)
    monkeypatch.setattr(modelinfo, "_estimate", lambda *args: 0.0)
    monkeypatch.setattr(modelinfo, "_listed_models", lambda: pytest.fail("Unnecessary metadata fetch"))
    assert modelinfo._price_route("m", "p") == ("m", "p")


def test_subscription_api_equivalent_mapping_is_explicit_and_separate(monkeypatch):
    monkeypatch.setattr(modelinfo, "_token_usage", lambda tokens: tokens)
    monkeypatch.setattr(modelinfo, "_estimate", lambda name, vendor, usage: .1 if vendor == "openai" else None)
    assert modelinfo._price_route("m", "openai-codex") == ("m", "openai")


def test_missing_cache_rate_fallback_remains_an_estimate(monkeypatch):
    monkeypatch.setattr(modelinfo, "_price_route", lambda *args: ("m", "p"))
    monkeypatch.setattr(modelinfo, "_token_usage", lambda tokens: None)
    monkeypatch.setattr(modelinfo, "_estimate", lambda *args: None)
    monkeypatch.setattr(modelinfo, "token_prices", lambda *args: {"input": 2.0, "output": 3.0, "cache_read": 2.0, "cache_write": 2.0})
    assert modelinfo.api_cost("m", "p", {"input": 1, "output": 1, "cache_read": 1}) == 7.0
