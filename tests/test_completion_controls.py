"""Public controls preserve user reasoning, explicit scope and anonymous choices."""
import argparse
import asyncio
import json
import sys
import types

import pytest

from crystallize import cli, delegation, store, tool_discovery
from crystallize.dashboard import plugin_api as api


def test_reasoning_reduction_controls_are_rejected(monkeypatch):
    store.set_setting("lower_reasoning", False)
    result = asyncio.run(api.reasoning_setting({"on": True}))
    assert not result["ok"] and "preserves reasoning" in result["message"]
    assert store.get_setting("lower_reasoning") is False
    with pytest.raises(SystemExit, match="preserves reasoning"):
        cli._tune(argparse.Namespace(job=None, reasoning="lower"))


def test_delegation_requires_separate_explicit_profile_consent(monkeypatch):
    from crystallize import chat_savings
    monkeypatch.setattr(chat_savings, "connected_models", lambda: [{"slug": "vendor", "models": [{"id": "general"}]}])
    with pytest.raises(ValueError, match="Confirm"):
        delegation.configure(True, "vendor", "general")
    assert not delegation.summary()["enabled"]
    result = delegation.configure(True, "vendor", "general", confirmed=True)
    assert result["enabled"] and "never selects" in result["activation"]
    with pytest.raises(ValueError, match="connected"):
        delegation.configure(True, "other", "general", confirmed=True)


def test_delegation_never_reduces_inherited_reasoning_or_copies_endpoint_secrets(monkeypatch):
    class RouteError(ValueError):
        pass
    module = types.ModuleType("agent.delegation_provider")
    module.DelegationProvider = object
    module.DelegationRouteError = RouteError
    monkeypatch.setitem(sys.modules, "agent.delegation_provider", module)
    backend = delegation.provider_class()()
    config = {"reasoning_effort": "max", "provider": "parent", "model": "main",
              "base_url": "https://parent.invalid", "api_key": "synthetic-secret", "backends": {}}
    original = json.loads(json.dumps(config))
    inherit = backend.route_task({}, config)
    assert inherit["reasoning_effort"] == "max" and inherit["model"] == "main"
    with pytest.raises(RouteError, match="not consented"):
        backend.route_task({"crystallize_route": "helper"}, config)
    store.set_setting("delegation_consent", {"enabled": True, "provider": "vendor", "model": "general"})
    route = backend.route_task({"crystallize_route": "helper"}, config)
    assert route["reasoning_effort"] == "max" and route["provider"] == "vendor"
    assert route["fallback_providers"] == [] and "service_tier" not in route
    assert "api_key" not in route and "base_url" not in route
    assert config == original


def test_protected_tool_deferral_never_calls_host_cli(monkeypatch):
    called = []
    monkeypatch.setattr(tool_discovery.subprocess, "run", lambda *a, **kw: called.append(a))
    for name in ("clarify", "tool_search", "crystal_full_result"):
        with pytest.raises(ValueError, match="stay directly"):
            tool_discovery.configure([name], confirmed=True, expected_config_fingerprint="synthetic")
    assert not called


def test_tool_deferral_concurrent_edit_rejected(monkeypatch):
    monkeypatch.setattr(tool_discovery, "preview", lambda: {"current": [], "config_fingerprint": "changed"})
    with pytest.raises(ValueError, match="changed"):
        tool_discovery.configure(["unused"], confirmed=True, expected_config_fingerprint="old")


def test_anonymous_api_does_not_return_full_paid_comparison(monkeypatch):
    modules = {
        "experiments": types.SimpleNamespace(compare=lambda *a, **kw: {"id": "pair-a", "status": "completed", "secret_route": "hidden"}),
        "blind": types.SimpleNamespace(present=lambda ident: {"id": ident, "revealed": False, "samples": []}),
    }
    monkeypatch.setattr(api, "_module", modules.__getitem__)
    result = asyncio.run(api.compare_job({"job_id": "job-a", "confirmed": True}))
    assert result["ok"] and result["comparison"]["revealed"] is False
    assert "secret_route" not in json.dumps(result)


def test_cli_new_controls_parse_without_model_calls():
    parser = argparse.ArgumentParser()
    cli._setup(parser)
    args = parser.parse_args(["reread", "chat-a", "--enabled", "--no-writes"])
    assert args.enabled is True and args.writes is False and args.results is None
    args = parser.parse_args(["auction", "job-a", "--models", "routes.json", "--cap-usd", "0.1"])
    assert not args.run and not args.confirm and not args.accept_estimated_cap


def test_auction_action_forwards_frozen_preview_guard_and_consent(monkeypatch):
    seen = []
    def run(job, **kw):
        seen.append((job, kw))
        return {"status": "stopped_unknown_spend"}
    monkeypatch.setattr(api, "_job_id", lambda job: job)
    monkeypatch.setattr(api, "_module", lambda name: types.SimpleNamespace(run=run))
    result = asyncio.run(api.run_auction({"job_id": "job-a", "models": [], "confirmed": True,
                    "cost_cap_usd": 0.1, "accept_estimated_cap": True, "expected_input_sha256": "frozen"}))
    assert not result["ok"]
    assert seen[0][1]["expected_input_sha256"] == "frozen"
    assert seen[0][1]["accept_estimated_cap"] is True
    assert seen[0][1]["confirmed"] is True


def test_rhythm_domain_blocks_remain_actionable(monkeypatch):
    class RhythmError(RuntimeError):
        pass
    def fail(*a, **kw):
        raise RhythmError("Alert jobs cannot be slowed down.")
    monkeypatch.setattr(api, "_job_id", lambda job: job)
    monkeypatch.setattr(api, "_module", lambda name: types.SimpleNamespace(apply=fail))
    result = asyncio.run(api.rhythm_apply({"job_id": "job-a", "confirmed": True,
                                          "noncritical": True, "interval_minutes": 30}))
    assert result["code"] == "action_blocked" and "Alert jobs" in result["message"]
