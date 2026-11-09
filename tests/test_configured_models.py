"""Declared/configured model inventory never authenticates, probes or guesses."""

import json
import sys
import threading
import types
from types import SimpleNamespace

import pytest

from crystallize import configured_models as models, lifecycle, modelinfo, store


@pytest.fixture
def world(monkeypatch):
    profiles, jobs, calls = [], [], []
    cfg = {}
    provider_module = types.ModuleType("providers")
    provider_module.list_providers = lambda: list(profiles)
    def get_profile(name):
        calls.append(name)
        return next((p for p in profiles if p.name == name or name in getattr(p, "aliases", ())), None)
    provider_module.get_provider_profile = get_profile
    monkeypatch.setitem(sys.modules, "providers", provider_module)
    monkeypatch.setattr(modelinfo, "_config", lambda: cfg)
    monkeypatch.setattr(lifecycle, "all_cron_jobs", lambda: list(jobs))
    return SimpleNamespace(profiles=profiles, jobs=jobs, cfg=cfg, calls=calls, provider_module=provider_module)


def profile(name="public-cloud", **kwargs):
    def no_probe(*args, **kwargs):
        pytest.fail("inventory invoked a credential, catalog, setup, or probe callback")
    return SimpleNamespace(name=name, display_name="Public declaration", aliases=(), hidden=False,
                           static_models=kwargs.pop("static_models", ()),
                           fallback_models=kwargs.pop("fallback_models", ()),
                           model_capabilities=kwargs.pop("model_capabilities", {}),
                           default_aux_model=kwargs.pop("default_aux_model", ""),
                           picker_catalog=no_probe, fetch_models=no_probe, discover_models=no_probe,
                           probe_models=no_probe, account_status=no_probe, auth_handler=no_probe,
                           get_usage_cost=no_probe, model_setup_flow=no_probe, **kwargs)


def rows(inventory):
    return {(p["slug"], m["id"]): m for p in inventory["providers"] for m in p["models"]}


def test_public_declared_ids_only_and_no_callbacks_prices_or_auth(world, monkeypatch):
    world.profiles.append(profile(static_models=("static-id",), fallback_models=("fallback-id",),
                                  model_capabilities={"capability-id": {"supports_reasoning": True}},
                                  default_aux_model="default-aux"))
    monkeypatch.setattr(modelinfo, "token_prices", lambda *a, **k: pytest.fail("inventory fetched prices"))
    data = models.inventory()
    actual = rows(data)
    assert set(actual) == {("public-cloud", name) for name in ("static-id", "fallback-id", "capability-id", "default-aux")}
    assert actual["public-cloud", "capability-id"]["reasoning"] is True
    assert actual["public-cloud", "static-id"]["reasoning"] is None
    assert data["authentication_verified"] is False and data["availability"] == "configured_and_declared_only"
    assert all(m["availability"] == "declared_unverified" and m["input_per_million"] is None for m in actual.values())
    assert all(p["authentication_verified"] is False and "authenticated" not in p for p in data["providers"])


def test_sanitized_main_aux_cron_pins_and_plugin_routes(world):
    world.cfg.update({
        "model": {"provider": "main-cloud", "default": "main-model", "api_key": "never-export-main-key", "base_url": "https://secret.invalid/?token=hidden"},
        "auxiliary": {"helper": {"provider": "aux-cloud", "model": "aux-model", "api_key": "never-export-aux-key"},
                      "no-provider": {"model": "unknown-provider-model"}},
        "cron": {"model_provider": "cron-cloud", "model": "cron-default"},
        "plugins": {"entries": {"crystallize": {"additional_routes": [{"provider": "config-cloud", "model": "config-model", "api_key": "never-export-config-key"}]}}},
    })
    world.jobs.extend([{"provider": "pinned-cloud", "model": "pinned-model", "prompt": "private task", "name": "private job"},
                       {"model": "inherited-cron-provider"}])
    data = models.inventory()
    actual = rows(data)
    assert set(actual) == {("main-cloud", "main-model"), ("aux-cloud", "aux-model"),
                           ("cron-cloud", "cron-default"), ("pinned-cloud", "pinned-model"),
                           ("cron-cloud", "inherited-cron-provider"), ("config-cloud", "config-model")}
    assert actual["cron-cloud", "inherited-cron-provider"]["provenance"] == ["cron_pin_configuration"]
    text = json.dumps(data)
    assert all(secret not in text for secret in ("never-export", "secret.invalid", "private task", "private job", "api_key", "base_url"))
    assert any("no explicit provider" in warning for warning in data["warnings"])


def test_unspecified_or_auto_main_provider_is_not_guessed_from_model_prefix(world):
    world.cfg["model"] = {"provider": "auto", "default": "vendor/model"}
    assert not rows(models.inventory())
    world.cfg["model"] = "vendor/model"
    assert not rows(models.inventory())


def test_plugin_settings_additional_routes_use_explicit_sanitized_identifiers(world):
    world.cfg["plugins"] = {"entries": {"crystallize": {"settings": {
        "additional_routes": [{"provider": "configured-cloud", "model": "configured-model", "api_key": "not-read-or-exported"}]
    }}}}
    actual = rows(models.inventory())
    assert set(actual) == {("configured-cloud", "configured-model")}
    assert actual["configured-cloud", "configured-model"]["provenance"] == ["plugin_route_configuration"]
    assert "not-read-or-exported" not in json.dumps(actual["configured-cloud", "configured-model"])


def test_no_account_or_credential_fields_are_read(world):
    class SelectedConfig(dict):
        def get(self, key, default=None):
            assert key not in {"api_key", "token", "access_token", "auth", "credentials", "accounts", "env_vars", "base_url"}
            return super().get(key, default)
    world.cfg.update({"model": SelectedConfig(provider="public-cloud", default="explicit-model", api_key="forbidden")})
    assert ("public-cloud", "explicit-model") in rows(models.inventory())


def test_declarations_are_not_a_fixed_allowlist_and_manual_route_does_not_activate(world):
    data = models.configure_route("future-cloud", "future-vendor/new-model:2027")
    assert data["ok"] and not data["activated"]
    assert data["configured_route"] == {"provider": "future-cloud", "model": "future-vendor/new-model:2027"}
    saved = rows(data)["future-cloud", "future-vendor/new-model:2027"]
    assert saved["availability"] == "configured_unverified" and saved["provenance"] == ["explicit_plugin_route"]
    assert store.get_setting("delegation_consent") is None
    assert world.cfg == {}


def test_manual_route_entry_survives_public_declaration_gap(world):
    def missing():
        raise ImportError("public provider declarations missing")
    world.provider_module.list_providers = missing
    result = models.configure_route("cloud-alias", "new-model")
    assert result["ok"]
    assert rows(result)["cloud-alias", "new-model"]["authentication_verified"] is False
    assert any("unavailable" in warning for warning in result["warnings"])


def test_explicit_candidate_not_in_declarations_is_annotated_unverified(world):
    world.profiles.append(profile(static_models=("declared",)))
    actual = models.describe_routes([{"provider": "manual-cloud", "model": "manual-model"}])[0]
    assert actual["availability"] == "explicit_selection_unverified"
    assert actual["authentication_verified"] is False and actual["provenance"] == ["explicit_user_selection"]
    assert store.get_setting("additional_model_routes") is None


def test_configured_route_and_declared_source_are_merged_without_false_auth(world):
    world.profiles.append(profile(static_models=("chosen", "unselected")))
    world.cfg["model"] = {"provider": "public-cloud", "model": "chosen"}
    data = models.inventory()
    actual = rows(data)["public-cloud", "chosen"]
    assert actual["configured"] and actual["featured"]
    assert set(actual["provenance"]) == {"provider_static_models", "main_configuration"}
    assert data["providers"][0]["models"][0]["id"] == "chosen"


def test_hidden_declarations_only_appear_when_explicitly_selected(world):
    p = profile(static_models=("hidden-default",))
    p.hidden = True
    world.profiles.append(p)
    assert not models.inventory()["providers"]
    world.cfg["model"] = {"provider": "public-cloud", "model": "explicit-hidden-model"}
    assert set(rows(models.inventory())) == {("public-cloud", "explicit-hidden-model")}


@pytest.mark.parametrize("provider,model", [("", "m"), ("auto", "m"), ("p", ""), ("p", "m\nsecret"),
                                            ("https://key@provider.invalid", "m"), ("p", "m?api_key=secret"),
                                            ("p", "m " * 300), (None, "m"), ("p", True)])
def test_manual_route_rejects_credential_shaped_urls_and_malformed_identifiers(world, provider, model):
    with pytest.raises(ValueError, match="identifier"):
        models.configure_route(provider, model)
    assert store.get_setting("additional_model_routes") is None


def test_repeated_route_entry_is_idempotent_and_bounded(world, monkeypatch):
    monkeypatch.setattr(models, "MAX_ADDITIONAL_ROUTES", 2)
    models.configure_route("cloud", "one")
    models.configure_route("cloud", "one")
    models.configure_route("cloud", "two")
    assert len(store.get_setting("additional_model_routes")) == 2
    with pytest.raises(ValueError, match="At most 2"):
        models.configure_route("cloud", "three")
    assert len(store.get_setting("additional_model_routes")) == 2


def test_concurrent_manual_entries_do_not_lose_routes(world):
    errors = []
    def add(index):
        try:
            models.configure_route("cloud", f"model-{index}")
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=add, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not errors and all(not thread.is_alive() for thread in threads)
    assert {entry["model"] for entry in store.get_setting("additional_model_routes")} == {f"model-{i}" for i in range(8)}


def test_known_prices_show_without_fetching(world, monkeypatch):
    from crystallize import price_discovery as pd, public_pricing

    world.profiles.append(profile(static_models=("listed", "unlisted", "carded")))
    store.set_setting(pd.SETTING, True)
    monkeypatch.setattr(pd, "_fetch_json", lambda url: pytest.fail("the picker fetched prices"))
    monkeypatch.setitem(pd._memory, "public-cloud|", {"fetched_at": 0, "models": {
        "listed": {"input": 1e-7, "output": 5e-7, "cache_read": 1e-8, "cache_write": 1e-7, "request_cost": 0.0}}})
    public_pricing.save_rate_card({"provider": "public-cloud", "model": "carded", "currency": "USD", "unit": "per_token",
                                   "input": 2e-6, "output": 8e-6, "cache_read": 2e-7, "cache_write": 2e-6,
                                   "request_cost": 0, "tiers_known": True})
    try:
        actual = rows(models.inventory())
        assert actual["public-cloud", "listed"]["input_per_million"] == pytest.approx(0.1)
        assert actual["public-cloud", "listed"]["price_source"] == "list_price"
        assert actual["public-cloud", "carded"]["output_per_million"] == pytest.approx(8.0)
        assert actual["public-cloud", "carded"]["price_source"] == "your_rate_card"
        assert actual["public-cloud", "unlisted"]["input_per_million"] is None
    finally:
        public_pricing.delete_rate_card("public-cloud", "carded")
        store.set_setting(pd.SETTING, False)
