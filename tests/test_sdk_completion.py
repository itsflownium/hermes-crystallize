"""Public-boundary regressions run without Hermes, networking or credentials."""
from types import SimpleNamespace

import pytest

from crystallize import host_api, lifecycle, modelinfo, prerun, store, tuning


@pytest.fixture(autouse=True)
def _reset_host_cache(monkeypatch):
    monkeypatch.setattr(modelinfo, "_lookups", {})
    host_api.set_context(None)
    yield
    host_api.set_context(None)


def entry(**changes):
    values = dict(input_cost_per_million=2.0, output_cost_per_million=8.0,
                  cache_read_cost_per_million=None, cache_write_cost_per_million=3.0,
                  tier_threshold_tokens=200_000, input_cost_per_million_above=4.0,
                  output_cost_per_million_above=16.0, request_cost=.003,
                  source="user_override", pricing_version="v1", source_url="https://example.test/pricing")
    values.update(changes)
    return SimpleNamespace(**values)


def test_spend_preflight_prices_only_the_exact_endpoint_and_keeps_tiers(monkeypatch):
    seen = []
    monkeypatch.setattr(modelinfo, "_pricing_entry", lambda *args: seen.append(args) or entry())
    monkeypatch.setattr(modelinfo, "_price_route", lambda *args: pytest.fail("API equivalents cannot authorize spend"))
    prices = modelinfo.exact_token_prices("m", "custom:p", "https://endpoint.test/v1")
    assert seen == [("m", "custom:p", "https://endpoint.test/v1")]
    assert prices["input"] == 2e-6 and prices["output_above"] == 16e-6
    assert prices["cache_read"] is None and prices["cache_read_above"] is None
    assert prices["tier_threshold_tokens"] == 200_000 and prices["request_cost"] == .003
    assert prices["source"] == "user_override" and prices["pricing_version"] == "v1"


def test_exact_zero_is_a_price_and_does_not_request_api_equivalents(monkeypatch):
    monkeypatch.setattr(modelinfo, "_pricing_entry", lambda *args: entry(input_cost_per_million=0,
        output_cost_per_million=0, request_cost=None, pricing_version="included-route"))
    prices = modelinfo.exact_token_prices("m", "subscription")
    assert prices["input"] == 0 and prices["output"] == 0 and prices["request_cost"] == 0
    assert modelinfo.output_price({"model": "m", "provider": "subscription"}) == 0


@pytest.mark.parametrize("changes", [
    {"input_cost_per_million": None}, {"output_cost_per_million": None},
    {"output_cost_per_million": float("nan")}, {"output_cost_per_million_above": float("inf")},
    {"cache_read_cost_per_million": -1}, {"request_cost": -1}, {"tier_threshold_tokens": True},
])
def test_missing_or_invalid_exact_prices_cannot_authorize_spend(monkeypatch, changes):
    monkeypatch.setattr(modelinfo, "_pricing_entry", lambda *args: entry(**changes))
    assert modelinfo.exact_token_prices("m", "p") is None


def test_price_cache_is_endpoint_scoped_and_callers_cannot_overwrite_it(monkeypatch):
    calls = []
    monkeypatch.setattr(modelinfo, "_pricing_entry", lambda *args: calls.append(args) or entry())
    first = modelinfo.exact_token_prices("m", "p", "https://one.test")
    first["output"] = 0
    assert modelinfo.exact_token_prices("m", "p", "https://one.test")["output"] == 8e-6
    modelinfo.exact_token_prices("m", "p", "https://two.test")
    assert len(calls) == 2


@pytest.mark.parametrize("provider", [None, "", "auto"])
def test_unresolved_provider_has_no_exact_billing_price(monkeypatch, provider):
    monkeypatch.setattr(modelinfo, "_pricing_entry", lambda *args: pytest.fail("No exact route was identified"))
    assert modelinfo.exact_token_prices("m", provider) is None


def test_interpreter_edits_use_documented_cli_and_preserve_clear_semantics(monkeypatch):
    calls = []
    monkeypatch.setattr(lifecycle, "_edit_job", lambda *args: calls.append(args))
    lifecycle._set_job_fields("immutable-job-id", {"interpreter": "/venv/bin/python"})
    lifecycle._set_job_fields("immutable-job-id", {"interpreter": None})
    assert calls == [("immutable-job-id", "--interpreter", "/venv/bin/python"),
                     ("immutable-job-id", "--interpreter", "")]
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle._set_job_fields("immutable-job-id", {"unrecognized": "x"})
    assert len(calls) == 2


def test_script_prompt_and_interpreter_promote_and_restore_in_one_cli_edit(monkeypatch):
    job = {"id": "j", "name": "job", "prompt": "Read current state", "script": "own.py",
           "interpreter": "/standalone/python", "workdir": "/work", "no_agent": False}
    store.save_routine("r", name="job", source="cron", source_ref="j", status="testing",
                       steps={"steps": []}, meta={"ready": True, "matches": 3})
    monkeypatch.setattr(lifecycle, "all_cron_jobs", lambda: [job])
    monkeypatch.setattr(lifecycle, "check_drift", lambda routine: True)
    monkeypatch.setattr(lifecycle, "_snapshot_campaign", lambda routine: None)
    monkeypatch.setattr(prerun, "write_script", lambda *args, **kwargs: "crystallize/r.py")
    calls = []

    def edit(job_id, *flags):
        calls.append((job_id, flags))
        fields = {"--prompt": "prompt", "--script": "script", "--interpreter": "interpreter"}
        for flag, value in zip(flags[::2], flags[1::2]):
            job[fields[flag]] = value or None

    monkeypatch.setattr(lifecycle, "_edit_job", edit)
    lifecycle.promote("r")
    assert len(calls) == 1 and "--interpreter" in calls[0][1]
    routine = store.get_routine("r")
    assert lifecycle._restore_attachment(routine, routine["meta"])
    assert len(calls) == 2 and "--interpreter" in calls[1][1]
    assert job["prompt"] == "Read current state" and job["script"] == "own.py"
    assert job["interpreter"] == "/standalone/python"


def test_both_generations_of_efficiency_hint_are_ignored_for_drift(monkeypatch):
    monkeypatch.setattr(tuning, "EFFICIENCY_HINT", "\nNew quality-preserving hint")
    monkeypatch.setattr(tuning, "LEGACY_EFFICIENCY_HINT", "\nLegacy hint", raising=False)
    assert lifecycle.normalize_prompt("Keep complete evidence\nLegacy hint\nNew quality-preserving hint") == "Keep complete evidence"


def test_raw_public_dispatch_is_not_reported_as_guarded_or_catalog_ready(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Compatibility diagnostics must not execute tools, models or CLIs")
    ctx = SimpleNamespace(dispatch_tool=unexpected)
    monkeypatch.setattr(modelinfo, "_pricing_entry", unexpected)
    monkeypatch.setattr(lifecycle, "_hermes_cmd", unexpected)
    result = host_api.report(ctx)
    assert result["public_dispatch_method_present"] is True
    assert result["public_guarded_dispatch_available"] is False
    assert result["catalog_ready"] is False and result["raw_registry_fallback_enabled"] is False
    assert result["reasoning_reductions_enabled"] is False
    imports = {site["target"] for site in result["private_core_imports"]}
    assert "model_tools.handle_function_call" in imports
    assert "agent.usage_pricing.get_pricing_entry" in imports
    assert "cron.jobs.update_job" in imports
    assert {gap["id"] for gap in result["sdk_gaps"]} >= {
        "guarded_tool_dispatch", "model_metadata_and_pricing", "cron_toolsets_and_atomic_update"}
