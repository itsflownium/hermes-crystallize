"""Exact-route manual cards and public provider totals, without host internals."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from crystallize import modelinfo, public_pricing as pricing, store


@pytest.fixture(autouse=True)
def no_host_config(monkeypatch):
    monkeypatch.setattr(pricing, "_config", lambda: {})
    monkeypatch.setattr(modelinfo, "_config", lambda: {})
    monkeypatch.setattr(modelinfo, "_profile", lambda provider: None)


def card(**changes):
    raw = {"schema_version": 1, "provider": "new-cloud", "model": "v/model", "base_url": None,
           "currency": "USD", "unit": "per_million_tokens", "input": 2, "output": 8,
           "cache_read": .2, "cache_write": 3, "request_cost": .001,
           "tier_threshold_tokens": None, "pricing_version": "contract-v1",
           "source_url": "https://example.test/pricing"}
    raw.update(changes)
    return raw


def test_explicit_card_roundtrip_preserves_rates_units_and_provenance():
    saved = pricing.save_rate_card(card())
    assert saved["unit"] == "per_token" and saved["input"] == 2e-6
    assert saved["request_cost"] == .001
    assert saved["source"] == "user_rate_card" and saved["provenance"] == "user_supplied_rate_card"
    assert saved["user_authorized"] is True and saved["verified"] is False
    assert pricing.exact_rate_card("v/model", "new-cloud") == saved
    quote = modelinfo.cost_quote("v/model", "new-cloud", {"input": 100, "output": 10, "cache_read": 1000})
    assert quote["amount_usd"] == pytest.approx(.00148) and quote["status"] == "estimated"
    assert modelinfo.api_cost("v/model", "new-cloud", {"input": 100, "output": 10, "cache_read": 1000}) == quote["amount_usd"]


def test_manual_zero_and_unknown_are_distinct():
    pricing.save_rate_card(card(input=0, output=0, cache_read=None, request_cost=None))
    rates = modelinfo.exact_token_prices("v/model", "new-cloud")
    assert rates["input"] == rates["output"] == 0 and rates["request_cost"] is None
    assert rates["cache_read"] is None and modelinfo.token_prices("v/model", "new-cloud") is None
    assert modelinfo.output_price({"model": "v/model", "provider": "new-cloud"}) == 0
    assert modelinfo.api_cost("v/model", "new-cloud", {"input": 100, "output": 50}) is None
    pricing.save_rate_card(card(input=0, output=0, cache_read=0, cache_write=0, request_cost=0))
    assert modelinfo.api_cost("v/model", "new-cloud", {"input": 100, "output": 50}) == 0


@pytest.mark.parametrize("change", [{"input": -1}, {"output": float("nan")},
    {"request_cost": float("inf")}, {"cache_read": True}, {"cache_write": "0.25"},
    {"currency": "EUR"}, {"unit": "unknown"}, {"provider": "*"}, {"model": "auto"},
    {"tier_threshold_tokens": False}, {"tier_threshold_tokens": 0}, {"api_key": "do-not-store"},
    {"source_url": "https://user:password@example.test/pricing"},
    {"base_url": "https://endpoint.test/v1?key=do-not-store"}])
def test_invalid_cards_are_refused_without_persistence(change):
    with pytest.raises(pricing.PricingError):
        pricing.save_rate_card(card(**change))
    assert pricing.list_rate_cards() == []


def test_partial_card_does_not_invent_missing_fee_or_tier_information():
    raw = card()
    raw.pop("request_cost")
    raw.pop("tier_threshold_tokens")
    pricing.save_rate_card(raw)
    rates = pricing.exact_rate_card("v/model", "new-cloud")
    assert rates["request_cost"] is None and rates["tiers_known"] is False
    assert modelinfo.api_cost("v/model", "new-cloud", {"input": 1}) is None


def test_legacy_flat_math_cannot_drop_request_fees_or_context_tiers():
    pricing.save_rate_card(card())
    assert modelinfo.token_prices("v/model", "new-cloud") is None
    pricing.save_rate_card(card(request_cost=0))
    assert modelinfo.token_prices("v/model", "new-cloud")["output"] == 8e-6
    pricing.save_rate_card(card(request_cost=0, tier_threshold_tokens=100, input_above=4, output_above=16))
    assert modelinfo.token_prices("v/model", "new-cloud") is None


def test_tier_rates_cover_the_whole_request_and_missing_charged_bucket_is_unknown():
    pricing.save_rate_card(card(tier_threshold_tokens=100, input_above=4, output_above=16,
                               cache_read_above=.4, cache_write_above=6))
    assert modelinfo.api_cost("v/model", "new-cloud", {"input": 100, "output": 10}) == pytest.approx(.00128)
    assert modelinfo.api_cost("v/model", "new-cloud", {"input": 101, "output": 10}) == pytest.approx(.001564)
    assert modelinfo.api_cost("v/model", "new-cloud", {"input": 101, "output": 10, "request_count": 2}) is None
    pricing.save_rate_card(card(tier_threshold_tokens=100, input_above=4))
    assert modelinfo.api_cost("v/model", "new-cloud", {"input": 101}) == pytest.approx(.001404)
    assert modelinfo.api_cost("v/model", "new-cloud", {"input": 101, "output": 1}) is None


def test_profile_data_cards_are_exact_and_never_spill_to_another_endpoint_or_vendor():
    pricing.save_rate_card(card(base_url="https://ENDPOINT.test/v1/"))
    assert pricing.exact_rate_card("v/model", "new-cloud", "https://endpoint.test/v1") is not None
    for model, provider, base_url in [("model", "new-cloud", "https://endpoint.test/v1"),
        ("V/model", "new-cloud", "https://endpoint.test/v1"),
        ("v/model", "other-cloud", "https://endpoint.test/v1"),
        ("v/model", "new-cloud", "https://other.test/v1"), ("v/model", "new-cloud", None)]:
        assert pricing.exact_rate_card(model, provider, base_url) is None


def test_save_delete_and_expiry_take_effect_immediately_without_price_cache():
    pricing.save_rate_card(card(output=8))
    assert modelinfo.exact_token_prices("v/model", "new-cloud")["output"] == 8e-6
    pricing.save_rate_card(card(output=12))
    assert modelinfo.exact_token_prices("v/model", "new-cloud")["output"] == 12e-6
    pricing.save_rate_card(card(expires_at=1))
    assert modelinfo.exact_token_prices("v/model", "new-cloud") is None
    assert pricing.delete_rate_card("new-cloud", "v/model")["removed"]
    assert pricing.list_rate_cards() == []


def test_config_cards_and_profile_data_precedence_are_explicit():
    config = {"plugins": {"entries": {"crystallize": {"settings": {pricing.SETTING: [card(output=9)]}}}}}
    assert pricing.exact_rate_card("v/model", "new-cloud", config=config)["origin"] == "config"
    pricing.save_rate_card(card(output=7))
    assert pricing.exact_rate_card("v/model", "new-cloud", config=config)["output"] == 7e-6
    pricing.delete_rate_card("new-cloud", "v/model")
    assert pricing.exact_rate_card("v/model", "new-cloud", config=config)["output"] == 9e-6
    config["plugins"]["entries"]["crystallize"]["settings"][pricing.SETTING].append({"input": -1})
    report = pricing.rate_cards_report(config)
    assert len(report["cards"]) == 1 and report["invalid_cards"][0]["origin"] == "config"


def test_provider_quote_uses_public_usage_fields_and_never_becomes_a_rate_card(monkeypatch):
    calls = []
    def quote(model, usage):
        calls.append((model, usage))
        assert usage.prompt_tokens == 160 and usage.total_tokens == 180
        assert usage.reasoning_tokens == 10 and usage.request_count == 1
        assert usage.raw_usage == {"served_tier": "standard"}
        return SimpleNamespace(amount_usd=Decimal("0.125"), status="estimated", source="custom_contract", pricing_version="p1", notes=())
    profile = SimpleNamespace(get_usage_cost=quote, base_url="https://default.test/v1")
    monkeypatch.setattr(modelinfo, "_profile", lambda provider: profile)
    result = modelinfo.cost_quote("a-new-model", "new-cloud", {"input": 100, "output": 20,
        "cache_read": 50, "cache_write": 10, "reasoning": 10, "raw_usage": {"served_tier": "standard"}})
    assert result["amount_usd"] == .125 and result["provenance"] == "provider_profile_quote"
    assert calls[0][0] == "a-new-model"
    assert modelinfo.exact_token_prices("a-new-model", "new-cloud") is None
    assert modelinfo.token_prices("a-new-model", "new-cloud") is None


@pytest.mark.parametrize("amount,status,want", [(0, "included", 0), (0, "actual", 0),
    (None, "unknown", None), (1, "included", None), (-1, "estimated", None), (float("nan"), "actual", None)])
def test_public_cost_status_and_unknown_are_preserved(monkeypatch, amount, status, want):
    profile = SimpleNamespace(get_usage_cost=lambda *args: SimpleNamespace(amount_usd=amount, status=status))
    monkeypatch.setattr(modelinfo, "_profile", lambda provider: profile)
    assert modelinfo.api_cost("m", "new-cloud", {"input": 1}) == want


def test_public_hook_with_no_endpoint_parameter_cannot_price_an_unrelated_endpoint():
    def unexpected(*args):
        pytest.fail("An unrelated endpoint must not borrow the profile's quote")
    profile = SimpleNamespace(base_url="https://default.test/v1", get_usage_cost=unexpected)
    assert pricing.quote_cost("m", "p", pricing.Usage(input_tokens=1), "https://other.test/v1", profile=profile) is None


@pytest.mark.parametrize("tokens", [{"input": None}, {"input": -1}, {"output": True},
    {"cache_write": 1.2}, {"input": 1, "request_count": 0}])
def test_unknown_or_invalid_usage_does_not_become_zero(tokens):
    with pytest.raises(pricing.PricingError):
        pricing.usage_from_tokens(tokens)


def test_profile_local_storage_is_used_without_a_card_for_other_profiles(monkeypatch):
    pricing.save_rate_card(card())
    original = store.get_setting
    monkeypatch.setattr(store, "get_setting", lambda key, default=None: default)
    assert pricing.exact_rate_card("v/model", "new-cloud") is None
    monkeypatch.setattr(store, "get_setting", original)
    assert pricing.exact_rate_card("v/model", "new-cloud") is not None
