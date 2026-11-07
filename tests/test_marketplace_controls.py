"""Offline regressions for the public-only marketplace controls and their limits."""
import argparse
import asyncio
from types import SimpleNamespace

import pytest

from crystallize import chat_savings, cli, configured_models, host_api, lifecycle, modelinfo, public_pricing
from crystallize.dashboard import plugin_api as api


def call(fn, *args):
    return asyncio.run(fn(*args))


def card(**changes):
    result = dict(schema_version=1, provider="future-cloud", model="general/model-v2",
                  base_url=None, currency="USD", unit="per_million_tokens",
                  input=1, output=4, cache_read=None, cache_write=None,
                  request_cost=0, tier_threshold_tokens=None)
    result.update(changes)
    return result


def modules(monkeypatch):
    monkeypatch.setattr(api, "_module", {"configured_models": configured_models,
                                       "public_pricing": public_pricing}.__getitem__)
    monkeypatch.setattr(modelinfo, "_config", lambda: {})
    monkeypatch.setattr(lifecycle, "all_cron_jobs", lambda: [])
    monkeypatch.setattr(configured_models, "_profiles", lambda: ([], lambda provider: None, []))
    monkeypatch.setattr(public_pricing, "_config", lambda: {})


def test_route_declaration_can_add_a_future_model_without_selecting_or_authenticating(monkeypatch):
    modules(monkeypatch)
    monkeypatch.setattr(chat_savings, "set_model", lambda *args: pytest.fail("Declaring a route selected a helper"))
    result = call(api.configure_model_route, {"provider": "future-cloud", "model": "general/model-v2"})
    assert result["ok"] and result["authentication_verified"] is False
    assert result["catalog_complete"] is False
    assert any(p["slug"] == "future-cloud" and p["models"][0]["id"] == "general/model-v2"
               for p in result["providers"])
    assert not call(api.configure_model_route, {"provider": "--bad", "model": "m"})["ok"]


def test_price_acknowledgment_and_unknown_buckets_survive_api_roundtrip(monkeypatch):
    modules(monkeypatch)
    assert call(api.save_pricing_card, {"card": card(), "confirmed": False})["code"] == "invalid_request"
    assert public_pricing.list_rate_cards() == []
    saved = call(api.save_pricing_card, {"card": card(), "confirmed": True})
    assert saved["ok"] and saved["card"]["verified"] is False
    assert saved["card"]["cache_read"] is None and saved["card"]["request_cost"] == 0
    listed = call(api.pricing_cards)
    assert listed["prices_are_estimates"] is True and listed["cards"][0]["input"] == 1e-6
    assert call(api.save_pricing_card, {"card": card(output=float("nan")), "confirmed": True})["code"] == "invalid_request"
    deleted = call(api.delete_pricing_card, {"provider": "future-cloud", "model": "general/model-v2", "confirmed": True})
    assert deleted["removed"] is True and deleted["configured_cards_require_config_edit"] is True
    assert call(api.pricing_cards)["cards"] == []
    assert call(api.delete_pricing_card, {"provider": "future-cloud", "model": "general/model-v2", "confirmed": True})["removed"] is False


def test_ordinary_host_tool_promotion_is_explicit_and_does_not_claim_replay(monkeypatch):
    calls = []
    def promote(routine_id, *, mode):
        calls.append((routine_id, mode))
        return {"name": "Daily research"}
    monkeypatch.setattr(api, "_lifecycle", lambda: SimpleNamespace(promote=promote))
    result = call(api.promote_routine, {"routine_id": "r-a", "mode": "tool"})
    assert result["ok"] and "normal host-tool guidance" in result["message"]
    assert calls == [("r-a", "tool")]
    result = call(api.promote_routine, {"routine_id": "r-b"})
    assert result["ok"] and "normal host-tool guidance" in result["message"]
    assert calls[-1] == ("r-b", "tool")
    assert call(api.promote_routine, {"routine_id": "r-a", "mode": "override"})["code"] == "invalid_request"
    assert len(calls) == 2


def test_cli_can_use_host_tool_mode_and_requires_explicit_price_acknowledgment(monkeypatch, tmp_path):
    parser = argparse.ArgumentParser()
    cli._setup(parser)
    assert parser.parse_args(["promote", "daily"]).mode == "tool"
    assert parser.parse_args(["promote", "daily", "--mode", "tool"]).mode == "tool"
    args = parser.parse_args(["rate-card", "--file", str(tmp_path / "card.json")])
    monkeypatch.setattr(public_pricing, "save_rate_card", lambda value: pytest.fail("Unconfirmed card was saved"))
    with pytest.raises(SystemExit, match="--confirm"):
        cli._completion_action("rate-card", args)


def test_compatibility_does_not_hide_dynamic_or_attribute_violations(monkeypatch):
    findings = [{"kind": "private_core_import", "target": "core.private"},
                {"kind": "private_dynamic_import", "target": "computed.private"},
                {"kind": "private_core_attribute", "target": "ctx._private"}]
    monkeypatch.setattr(host_api, "audit", lambda root: {"ok": False, "findings": findings})
    report = host_api.report()
    assert report["private_core_imports"] == findings
    assert report["technical_public_api_ready"] is False and report["admission"]["listed"] is False


def test_cli_observation_handles_no_fresh_runs_without_claiming_execution(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_routine_for", lambda job: None)
    monkeypatch.setattr(lifecycle, "test_now", lambda *args: {"tests": [], "matches": 0,
                       "executed": False, "why": "No fresh completed normal host runs are available yet"})
    cli._lifecycle_action("test", "daily")
    output = capsys.readouterr().out
    assert output.startswith("Observation:") and "No fresh" in output
    assert "Test run:" not in output


def test_cli_tool_mode_describes_guidance_without_claiming_pre_run_data(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_routine_for", lambda job: {"id": "r", "name": "Daily research"})
    monkeypatch.setattr(lifecycle, "set_mode", lambda *args: None)
    cli._lifecycle_action("mode", "daily", SimpleNamespace(mode="tool"))
    output = capsys.readouterr().out
    assert "ordinary host tools" in output and "no automatic replay" in output
    assert "pre-run data" not in output
