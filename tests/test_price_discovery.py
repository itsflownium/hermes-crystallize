"""Prices come from public lists, exact routes only, and never hold up a chat."""

from __future__ import annotations

import threading

import pytest

from crystallize import modelinfo, price_discovery as pd, public_pricing, store

ENDPOINT = {"data": [
    {"id": "vendor/fast", "pricing": {"prompt": "0.000002", "completion": "0.00001", "input_cache_read": "0.0000002"}},
    {"id": "vendor/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
    {"id": "vendor/no-price"},
]}
CATALOG = {"acme": {"models": {"big": {"cost": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75}},
                               "free": {"cost": {"input": 0, "output": 0}}, "noprice": {}}},
           "other": {"models": {"elsewhere": {"cost": {"input": 1, "output": 1}}}}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    store.set_setting(pd.SETTING, True)
    pd._memory.clear()
    pd._running.clear()
    monkeypatch.setattr(pd, "_profile_urls", lambda provider: ("https://api.example.com/v1", "https://api.example.com/v1/models"))
    yield
    pd._memory.clear()


def _serve(monkeypatch, endpoint=None, catalog=None):
    calls = []

    def fetch(url):
        calls.append(url)
        data = catalog if url == pd.CATALOG_URL else endpoint
        if data is None:
            raise OSError("unavailable")
        return data

    monkeypatch.setattr(pd, "_fetch_json", fetch)
    return calls


def test_endpoint_prices_are_per_token_and_cache_write_falls_back_to_input():
    models = pd.parse_endpoint(ENDPOINT)
    assert models["vendor/fast"]["input"] == pytest.approx(2e-6) and models["vendor/fast"]["cache_read"] == pytest.approx(2e-7)
    assert models["vendor/fast"]["cache_write"] == pytest.approx(2e-6) and models["vendor/fast"]["cache_rates_assumed"]


def test_varying_or_missing_prices_are_not_prices():
    models = pd.parse_endpoint(ENDPOINT)
    assert "vendor/auto" not in models and "vendor/no-price" not in models


def test_catalog_prices_are_per_million_and_only_for_that_provider():
    models = pd.parse_catalog(CATALOG, "acme")
    assert models["big"]["output"] == pytest.approx(15e-6) and models["free"]["input"] == 0
    assert "elsewhere" not in models and "noprice" not in models
    assert pd.parse_catalog(CATALOG, "unknown") == {}


def test_a_routes_own_list_beats_the_catalog(monkeypatch):
    _serve(monkeypatch, {"data": [{"id": "big", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]}, CATALOG)
    models = pd.refresh("acme")["models"]
    assert models["big"]["input"] == pytest.approx(1e-6) and models["free"]["input"] == 0


def test_a_custom_endpoint_never_borrows_catalog_prices(monkeypatch):
    calls = _serve(monkeypatch, {"data": []}, CATALOG)
    assert pd.refresh("acme", "https://my.example.org/v1")["models"] == {}
    assert calls == ["https://my.example.org/v1/models"]


@pytest.mark.parametrize("url", ["http://api.example.com/v1/models", "https://user:secret@api.example.com/models",
                                 "https://api.example.com/models?key=abc", "file:///etc/passwd", "", None])
def test_only_plain_https_lists_are_read(url):
    assert not pd._public_https(url)


def test_a_failed_fetch_keeps_the_old_prices(monkeypatch):
    _serve(monkeypatch, None, CATALOG)
    assert pd.refresh("acme")["models"]
    _serve(monkeypatch, None, None)
    assert pd.refresh("acme")["models"]["big"]


def test_looking_up_a_price_never_waits_on_the_network(monkeypatch):
    release, done = threading.Event(), threading.Event()

    def slow(url):
        release.wait(5)
        done.set()
        return CATALOG

    monkeypatch.setattr(pd, "_fetch_json", slow)
    assert pd.rates("big", "acme") is None      # returned at once, while the fetch is running
    release.set()
    assert done.wait(5)


def test_prices_are_remembered_across_restarts(monkeypatch):
    _serve(monkeypatch, None, CATALOG)
    pd.refresh("acme")
    pd._memory.clear()
    calls = _serve(monkeypatch, None, None)
    assert pd.rates("big", "acme")["input"] == pytest.approx(3e-6) and calls == []


def test_turning_it_off_stops_all_fetching(monkeypatch):
    store.set_setting(pd.SETTING, False)
    calls = _serve(monkeypatch, ENDPOINT, CATALOG)
    assert pd.rates("big", "acme") is None and calls == []


def test_discovered_prices_flow_into_token_prices_and_quotes(monkeypatch):
    _serve(monkeypatch, None, CATALOG)
    pd.refresh("acme")
    monkeypatch.setattr(modelinfo, "_config", lambda: {})
    assert modelinfo.token_prices("big", "acme") == {"input": 3e-6, "output": 15e-6, "cache_read": 3e-7, "cache_write": 3.75e-6}
    quote = modelinfo.cost_quote("big", "acme", {"input": 1000, "output": 100})
    assert quote["amount_usd"] == pytest.approx(0.0045) and quote["provenance"] == "public_list_price"


def test_your_own_rate_card_wins(monkeypatch):
    _serve(monkeypatch, None, CATALOG)
    pd.refresh("acme")
    monkeypatch.setattr(modelinfo, "_config", lambda: {})
    public_pricing.save_rate_card({"provider": "acme", "model": "big", "currency": "USD", "unit": "per_token",
                                   "input": 1e-9, "output": 1e-9, "cache_read": 1e-9, "cache_write": 1e-9,
                                   "request_cost": 0, "tiers_known": True})
    assert modelinfo.token_prices("big", "acme")["input"] == 1e-9


def test_a_similar_model_name_is_not_a_match(monkeypatch):
    _serve(monkeypatch, None, CATALOG)
    pd.refresh("acme")
    assert pd.rates("big-2", "acme") is None and pd.rates("BIG", "acme") is None


def test_a_subscription_route_uses_the_same_models_api_list_price(monkeypatch):
    catalog = {"openai": {"models": {"gpt-x": {"cost": {"input": 2, "output": 12, "cache_read": 0.2, "cache_write": 2.5}}}}}
    _serve(monkeypatch, None, catalog)
    pd.refresh("openai-codex")
    found = pd.rates("gpt-x", "openai-codex")
    assert found["input"] == pytest.approx(2e-6) and found["list_price_of"] == "openai"
    assert pd.rates("gpt-x-mini", "openai-codex") is None
    card = public_pricing.exact_rate_card("gpt-x", "openai-codex")
    assert card["list_price_of"] == "openai" and card["provenance"] == "public_list_price"


def test_other_routes_never_borrow_another_vendors_prices(monkeypatch):
    catalog = {"openai": {"models": {"gpt-x": {"cost": {"input": 2, "output": 12}}}}}
    _serve(monkeypatch, None, catalog)
    pd.refresh("acme")
    assert pd.rates("gpt-x", "acme") is None


def test_time_of_day_prices_follow_the_clock(monkeypatch):
    import calendar

    listing = {"data": [{"id": "vendor/night", "pricing": {
        "prompt": "0.000000132", "completion": "0.000000528", "input_cache_read": "0.000000033",
        "overrides": [{"utc_start": 0, "utc_end": 1600, "prompt": "0.000000132", "completion": "0.000000528", "input_cache_read": "0.000000033"},
                      {"utc_start": 1600, "utc_end": 0, "prompt": "0.0000000825", "completion": "0.00000033", "input_cache_read": "0.000000020625"},
                      {"utc_start": "bad", "utc_end": 0, "prompt": "1"}]}}]}
    _serve(monkeypatch, listing, None)
    pd.refresh("acme")
    day = calendar.timegm((2026, 11, 9, 10, 0, 0))
    night = calendar.timegm((2026, 11, 9, 20, 30, 0))
    after_midnight = calendar.timegm((2026, 11, 10, 0, 5, 0))
    assert pd.rates("vendor/night", "acme", when=day)["input"] == pytest.approx(1.32e-7)
    assert pd.rates("vendor/night", "acme", when=night)["input"] == pytest.approx(8.25e-8)
    assert pd.rates("vendor/night", "acme", when=night)["cache_read"] == pytest.approx(2.0625e-8)
    assert pd.rates("vendor/night", "acme", when=after_midnight)["input"] == pytest.approx(1.32e-7)
    assert "windows" not in pd.rates("vendor/night", "acme", when=day)


def test_a_flat_price_has_no_windows(monkeypatch):
    _serve(monkeypatch, ENDPOINT, None)
    pd.refresh("acme")
    found = pd.rates("vendor/fast", "acme")
    assert "windows" not in found and "time_of_day_price" not in found
