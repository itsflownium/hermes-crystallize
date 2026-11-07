"""Offline cross-domain conditions, comparison integrity and real-pin packaging.

Every completion below is a local fake. No provider, credential reader, live
tool, user file, production cron job or gateway is invoked.
"""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from crystallize import prerun
from scripts import validation_cross_domain as validation
from scripts import validation_fixtures as fixtures
from catalog import generate_entry as catalog


ROOT = Path(__file__).resolve().parents[1]
SETTINGS = {"provider": "nous", "model": "main-model", "reasoning": {"effort": "max"},
            "source": "public_host_configuration"}


def fake_context(fixture, *, usage=None, response_changes=None, error=None):
    calls = []

    def complete(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if error:
            raise error
        result = {"text": json.dumps(fixture.reference), "provider": "nous", "model": "main-model",
                  "usage": usage if usage is not None else SimpleNamespace(input_tokens=100, output_tokens=50,
                      total_tokens=150, cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.01)}
        result.update(response_changes or {})
        # Caller mutation must not contaminate another request or fixture.
        kwargs["messages"][-1]["content"] = "mutated by fake"
        return SimpleNamespace(**result)

    def action(*args, **kwargs):
        pytest.fail("The answer-only harness attempted a tool or action.")

    return SimpleNamespace(llm=SimpleNamespace(complete=complete), dispatch_tool=action,
                           execute_tool=action, restart=action), calls


def run(fixture=None, *, ctx=None, snapshot=None, budget=None, **kwargs):
    fixture = fixture or fixtures.FIXTURES[0]
    if ctx is None:
        ctx, _ = fake_context(fixture)
    return validation.run_fixture(ctx, snapshot or (lambda: copy.deepcopy(SETTINGS)), fixture,
        expected_settings=SETTINGS, allowed_providers={"nous"}, budget=budget or validation.Budget(2, 0.1),
        cleaner=prerun.clean_result, **kwargs)


@pytest.mark.parametrize("fixture", fixtures.FIXTURES, ids=lambda f: f.domain)
def test_complete_frozen_evidence_and_observable_reference_checks(fixture):
    inputs = validation.prepared(fixture, prerun.clean_result)
    assert inputs["baseline"] == fixture.observation
    assert len(inputs["candidate"]) < len(inputs["baseline"])
    assert json.loads(inputs["candidate"]) == json.loads(inputs["baseline"])
    assert all(literal in inputs["candidate"] for literal in fixture.required_literals)
    result = fixtures.check_answer(fixture, json.dumps(fixture.reference))
    assert result["ok"]
    assert result["semantic_equivalence"] == "not_established"


@pytest.mark.parametrize("fixture", fixtures.FIXTURES, ids=lambda f: f.domain)
def test_public_facade_only_same_main_settings_no_reasoning_replay(fixture):
    ctx, calls = fake_context(fixture)
    report = run(fixture, ctx=ctx, task="crystallize_validation")
    assert len(calls) == 2
    assert report["status"] == "completed"
    assert report["comparison"]["same_declared_main_settings"]
    assert report["comparison"]["both_pass_observable_checks"]
    assert report["comparison"]["effective_reasoning_verification"] == "not_exposed_by_current_public_facade"
    assert not report["comparison"]["verified_live_job_savings"]
    assert report["cost_summary"]["actual_billed_usd"] is None
    assert report["expected_settings"]["reasoning"] == {"effort": "max"}
    assert calls[0]["task"] == calls[1]["task"] == "crystallize_validation"
    for call in calls:
        assert set(call) == {"messages", "temperature", "max_tokens", "timeout", "purpose", "task"}
        assert call["temperature"] == 0 and call["max_tokens"] == 1024
        assert [m["role"] for m in call["messages"]] == ["system", "user"]
        assert "mutated by fake" not in call["messages"][-1]["content"]
        assert "FROZEN_OBSERVATION" in call["messages"][-1]["content"]
        assert "reasoning_content" not in call and "tools" not in call
    assert fixtures.FIXTURES[0].observation != "mutated by fake"


def test_no_run_flag_never_imports_context_factory(tmp_path):
    output = tmp_path / "plan.json"
    assert validation.main(["--context-factory", "does.not.exist:do_not_import", "--report", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["live_calls"] == 0 and report["execution"] == "not_run"
    assert report["provider_coverage"]["live_tested"] == []
    assert len(report["fixtures"]) == 6
    assert all(f["reference_self_check"]["ok"] for f in report["fixtures"])


@pytest.mark.parametrize("change", [
    {"provider": "other"}, {"model": "other-model"}, {"reasoning": {"effort": "low"}},
])
def test_confounded_settings_rejected_before_any_call(change):
    fixture = fixtures.FIXTURES[0]
    ctx, calls = fake_context(fixture)
    budget = validation.Budget(2, 0.1)
    report = run(ctx=ctx, snapshot=lambda: {**SETTINGS, **change}, budget=budget)
    assert report["status"] == "stopped" and calls == [] and budget.committed_usd == 0
    assert not report["comparison"]["cost_comparison_eligible"]
    assert not report["comparison"]["same_declared_main_settings"]


def test_reasoning_change_during_call_rejects_pair_without_second_call():
    fixture = fixtures.FIXTURES[0]
    ctx, calls = fake_context(fixture)
    snapshots = iter([SETTINGS, {**SETTINGS, "reasoning": {"effort": "low"}}])
    report = run(ctx=ctx, snapshot=lambda: next(snapshots))
    assert report["status"] == "stopped" and len(calls) == 1
    assert report["comparison"]["host_estimate_reduction_pct"] is None


@pytest.mark.parametrize("identity", [{"provider": "openrouter"}, {"model": "fallback-model"}, {"model": None}])
def test_served_route_change_or_unknown_attribution_never_becomes_savings(identity):
    ctx, calls = fake_context(fixtures.FIXTURES[0], response_changes=identity)
    report = run(ctx=ctx)
    assert report["status"] == "stopped" and len(calls) == 1
    assert not report["comparison"]["same_declared_main_settings"]
    assert not report["comparison"]["cost_comparison_eligible"]


def test_asserted_settings_remain_unverified_and_excluded_from_cost_claim():
    report = run(snapshot=lambda: {**SETTINGS, "source": "operator_assertion"})
    assert report["status"] == "completed"
    assert not report["comparison"]["configuration_snapshots_verified"]
    assert not report["comparison"]["cost_comparison_eligible"]


def test_unknown_cost_stops_by_default_but_explicit_reservation_mode_is_honest():
    usage = SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150,
                            cache_read_tokens=0, cache_write_tokens=0, cost_usd=None)
    ctx, calls = fake_context(fixtures.FIXTURES[0], usage=usage)
    strict = run(ctx=ctx)
    assert len(calls) == 1 and strict["status"] == "stopped"
    ctx, calls = fake_context(fixtures.FIXTURES[0], usage=usage)
    report = run(ctx=ctx, budget=validation.Budget(2, 0.1, accept_estimated_cap=True))
    assert len(calls) == 2 and report["status"] == "completed"
    assert report["unknown_cost_continuation_authorized"]
    assert report["cost_summary"]["unknown_cost_calls"] == 2
    assert report["cost_summary"]["actual_billed_usd"] is None
    assert report["cost_summary"]["complete_host_estimate_usd"] is None
    assert report["comparison"]["host_estimate_reduction_pct"] is None


def test_usage_absent_default_zero_and_reported_zero_are_distinct():
    unknown = validation.normalize_usage(None)
    defaults = validation.normalize_usage(SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0,
                                                          cache_read_tokens=0, cache_write_tokens=0, cost_usd=None))
    zero = validation.normalize_usage({"input_tokens": 10, "output_tokens": 2, "cost_usd": 0.0})
    assert unknown["status"] == "unknown"
    assert defaults["status"] == "unknown" and defaults["ambiguous_default_zero_tokens"]
    assert zero["status"] == "reported" and zero["cost_status"] == "host_estimate_zero"
    assert zero["host_estimate_usd"] == 0.0 and zero["actual_billed_usd"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, "0.01", 10**400])
def test_invalid_usage_is_not_fabricated_as_zero_or_actual(bad):
    usage = validation.normalize_usage({"input_tokens": 1, "output_tokens": 2, "cost_usd": bad})
    assert usage["status"] == "invalid" and usage["host_estimate_usd"] is None
    assert usage["actual_billed_usd"] is None


def test_error_with_reported_zero_cost_stays_an_error():
    error = RuntimeError("Bearer secret-that-must-not-appear-in-report")
    error.usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    ctx, calls = fake_context(fixtures.FIXTURES[0], error=error)
    report = run(ctx=ctx)
    assert len(calls) == 1 and report["calls"][0]["status"] == "error"
    assert report["cost_summary"]["error_calls"] == 1
    assert report["cost_summary"]["known_zero_estimate_calls"] == 1
    assert not report["comparison"]["cost_comparison_eligible"]
    assert "secret-that" not in json.dumps(report)


def test_error_usage_cost_above_reservation_stops_and_keeps_overrun():
    ctx, calls = fake_context(fixtures.FIXTURES[0], usage={"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.5})
    budget = validation.Budget(2, 0.1)
    report = run(ctx=ctx, budget=budget)
    assert len(calls) == 1 and report["status"] == "stopped"
    assert budget.committed_usd == pytest.approx(0.5)


def test_budget_reserves_every_attempt_no_refunds_for_zero_or_errors():
    budget = validation.Budget(0.2, 0.1)
    ctx, calls = fake_context(fixtures.FIXTURES[0], usage={"input_tokens": 10, "output_tokens": 3, "cost_usd": 0.0})
    run(ctx=ctx, budget=budget)
    assert budget.committed_usd == pytest.approx(0.2)
    report = run(ctx=ctx, budget=budget)
    assert report["calls"] == [] and len(calls) == 2


@pytest.mark.parametrize("limit,reservation", [(2.01, 0.1), (float("nan"), 0.1), (2, 0), (2, -1), (2, float("inf"))])
def test_invalid_or_over_authorized_budget_is_rejected(limit, reservation):
    with pytest.raises(validation.ValidationError):
        validation.Budget(limit, reservation)


def test_cache_difference_is_a_confound_not_a_savings_claim():
    fixture = fixtures.FIXTURES[0]
    ctx, calls = fake_context(fixture)
    complete = ctx.llm.complete

    def changed_cache(**kwargs):
        result = complete(**kwargs)
        result.usage.cache_read_tokens = 30 if len(calls) == 2 else 0
        return result

    ctx.llm.complete = changed_cache
    report = run(ctx=ctx)
    assert not report["comparison"]["cost_comparison_eligible"]
    assert report["comparison"]["host_estimate_reduction_pct"] is None


def test_lost_evidence_rejected_before_completion():
    ctx, calls = fake_context(fixtures.FIXTURES[0])
    with pytest.raises(validation.ValidationError, match="lost required"):
        validation.run_fixture(ctx, lambda: SETTINGS, fixtures.FIXTURES[0], expected_settings=SETTINGS,
            allowed_providers={"nous"}, budget=validation.Budget(2, 0.1), cleaner=lambda *_: "missing")
    assert calls == []


@pytest.mark.parametrize("allowed", [set(), {"*"}, {"openrouter"}])
def test_explicit_provider_allowlist_cannot_be_bypassed(allowed):
    ctx, calls = fake_context(fixtures.FIXTURES[0])
    with pytest.raises(validation.ValidationError):
        validation.run_fixture(ctx, lambda: SETTINGS, fixtures.FIXTURES[0], expected_settings=SETTINGS,
            allowed_providers=allowed, budget=validation.Budget(2, 0.1), cleaner=prerun.clean_result)
    assert calls == []


def test_fixture_failure_and_action_claim_are_visible_without_replay():
    cron = next(f for f in fixtures.FIXTURES if f.domain == "cron")
    value = {**cron.reference, "action_performed": True}
    assert not fixtures.check_answer(cron, json.dumps(value))["ok"]
    research = fixtures.FIXTURES[0]
    assert not fixtures.check_answer(research, json.dumps({**research.reference, "opening_hour": "09:00"}))["ok"]


def test_generated_code_is_interpreted_with_calls_and_imports_rejected(tmp_path):
    fixture = next(f for f in fixtures.FIXTURES if f.domain == "coding")
    marker = tmp_path / "must-not-exist"
    malicious = f"def clamp(value, lower, upper):\n    __import__('pathlib').Path({str(marker)!r}).write_text('bad')\n    return value"
    assert not fixtures.check_answer(fixture, json.dumps({"code": malicious}))["ok"]
    assert not marker.exists()
    assert not fixtures.check_answer(fixture, json.dumps({"code": "def clamp(value, lower, upper):\n    return lower"}))["ok"]


@pytest.mark.parametrize("a_rate,b_rate", [("4/5", "3/5"), ("20/25", "15/25"), (0.8, 0.6), ("-4/-5", "3/5")])
def test_analysis_accepts_mathematically_correct_bounded_fraction_strings_and_numbers(a_rate, b_rate):
    fixture = next(f for f in fixtures.FIXTURES if f.domain == "analysis")
    result = fixtures.check_answer(fixture, json.dumps({**fixture.reference, "a_rate": a_rate, "b_rate": b_rate}))
    assert result["ok"] and result["checker_revision"] == "2-analysis-rational-rates"


@pytest.mark.parametrize("invalid", ["3/5", "4/0", "0/0", "4/NaN", "NaN", "Infinity", "4/5 + 0", "__import__('os')", "1" * 13 + "/5", "0.8", True, False, None, float("nan"), float("inf")])
def test_analysis_rejects_wrong_unbounded_nonfinite_or_boolean_rates(invalid):
    fixture = next(f for f in fixtures.FIXTURES if f.domain == "analysis")
    assert not fixtures.check_answer(fixture, json.dumps({**fixture.reference, "a_rate": invalid}))["ok"]


def _factory_file(tmp_path, fixture, *, sleep_seconds=0):
    factory = tmp_path / "factory.py"
    factory.write_text(
        "import copy, json, os, time\nfrom types import SimpleNamespace\n"
        "def make_context(*, home, settings):\n"
        "    assert os.environ['HOME'] == str(home)\n"
        "    assert os.environ['HERMES_HOME'] == str(home / '.hermes')\n"
        "    assert 'CRYSTALLIZE_VALIDATION_SENTINEL' not in os.environ\n"
        "    def complete(**kwargs):\n"
        f"        time.sleep({sleep_seconds!r})\n"
        f"        return SimpleNamespace(text={json.dumps(fixture.reference)!r}, provider=settings['provider'], model=settings['model'], "
        "usage=SimpleNamespace(input_tokens=20, output_tokens=10, total_tokens=30, cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.01))\n"
        "    return {'ctx': SimpleNamespace(llm=SimpleNamespace(complete=complete)), 'snapshot': lambda: copy.deepcopy(settings)}\n"
    )
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(SETTINGS))
    return factory, settings


def test_cli_fake_worker_isolates_home_strips_auth_environment_and_never_touches_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYSTALLIZE_VALIDATION_SENTINEL", "not-a-real-secret")
    fixture = fixtures.FIXTURES[0]
    factory, settings = _factory_file(tmp_path, fixture)
    output = tmp_path / "report.json"
    before = os.environ["HERMES_HOME"]
    status = validation.main(["--run", "--context-factory", str(factory) + ":make_context", "--settings", str(settings),
        "--fixture", fixture.id, "--allowed-providers", "nous", "--reserve-per-call-usd", "0.1", "--report", str(output)])
    assert status == 0
    report = json.loads(output.read_text())
    assert report["live_calls"] == 2
    assert os.environ["HERMES_HOME"] == before
    assert report["budget"]["committed_usd"] == pytest.approx(0.2)
    assert report["cost_summary"]["actual_billed_usd"] is None


def test_cli_kills_isolated_worker_at_deadline_and_keeps_unknown_attempts(tmp_path):
    fixture = fixtures.FIXTURES[0]
    factory, settings = _factory_file(tmp_path, fixture, sleep_seconds=10)
    output = tmp_path / "timeout.json"
    status = validation.main(["--run", "--context-factory", str(factory) + ":make_context", "--settings", str(settings),
        "--fixture", fixture.id, "--allowed-providers", "nous", "--reserve-per-call-usd", "0.1",
        "--max-task-seconds", "0.7", "--call-timeout", "0.1", "--report", str(output)])
    report = json.loads(output.read_text())
    assert status == 1 and report["fixtures"][0]["status"] == "deadline_exceeded"
    assert "comparison" not in report["fixtures"][0]
    assert report["cost_summary"]["actual_billed_usd"] is None
    assert report["cost_summary"]["complete_host_estimate_usd"] in (None, 0)


@pytest.mark.parametrize("statement,returncode,error_type", [
    ("raise SystemExit('private-error-text')", 1, "SystemExit"),
    ("os._exit(7)", 7, "WorkerProcessExit"),
])
def test_worker_system_exit_and_native_exit_keep_reservations_without_error_text(tmp_path, statement, returncode, error_type):
    fixture = fixtures.FIXTURES[0]
    factory, settings = _factory_file(tmp_path, fixture)
    factory.write_text(factory.read_text().replace("        time.sleep(0)", "        " + statement))
    output = tmp_path / "worker-exit.json"
    status = validation.main(["--run", "--context-factory", str(factory) + ":make_context", "--settings", str(settings),
        "--fixture", fixture.id, "--allowed-providers", "nous", "--reserve-per-call-usd", "0.1",
        "--already-reserved-usd", "0.1", "--report", str(output)])
    report = json.loads(output.read_text())
    worker = report["fixtures"][0]
    assert status == 1 and worker["status"] == "worker_failed"
    assert worker["worker_returncode"] == returncode and worker["error_type"] == error_type
    assert report["budget"]["committed_usd"] == pytest.approx(0.2)
    assert report["cost_summary"]["unknown_cost_calls"] == 1
    assert "private-error-text" not in output.read_text()


def test_catalog_manifest_matches_literal_capabilities_and_rejects_creep():
    manifest = catalog.read_manifest((ROOT / "plugin.yaml").read_text())
    analysis = catalog.analyze_sources({"__init__.py": (ROOT / "__init__.py").read_text()})
    catalog.validate_capabilities(manifest, analysis)
    bad = {**manifest, "provides_tools": manifest["provides_tools"] + ["undeclared_tool"]}
    with pytest.raises(catalog.BundleError, match="differs"):
        catalog.validate_capabilities(bad, analysis)
    with pytest.raises(catalog.BundleError, match="Dynamic"):
        catalog.analyze_sources({"__init__.py": "def register(ctx):\n    ctx.register_tool(name=some_name)\n"})


@pytest.mark.parametrize("pin", ["HEAD", "main", "d72142a", "0" * 40, "f" * 40])
def test_catalog_rejects_fake_unresolved_or_non_full_pins(pin):
    with pytest.raises(catalog.BundleError):
        catalog.GitTree(ROOT, pin)


def test_catalog_bundle_pins_real_existing_commit_and_never_claims_admission(tmp_path):
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
    report = catalog.generate(ROOT, sha, tmp_path)
    entry = (tmp_path / "crystallize.yaml").read_text()
    assert 'sha: "' + sha + '"' in entry and len(sha) == 40
    assert report["literal_capabilities_match_manifest"]
    assert report["catalog_ready"] is False and report["publication_verified"] is False
    assert report["upstream_catalog_pr"] is None and report["maintainer_approval"] == "not_obtained"
    assert report["hermes_plugins_validate"]["status"] == "not_run_by_generator"
    audit = report["source_analysis"]["public_api_audit"]
    assert audit["ok"] == (not audit["findings"])
    assert report["technical_static_ready"] == (audit["ok"] and report["technical_checks"]["reviewable_payloads"])
    assert report["technical_ready_for_submission"] is False  # no exact-pin verification/screenshots supplied
    submission = (tmp_path / "submission.md").read_text().replace("\n", " ")
    assert "only a maintainer-merged upstream catalog entry establishes listing" in submission


def test_catalog_disclosures_do_not_claim_unpinned_file_handoffs():
    entry = {"sha": "a" * 40, "repo": "https://github.com/owner/repo", "version": "0.3.0", "requires_hermes": ">=0.22.7"}
    readiness = {"pinned_features": {"delegated_file_handoffs": False, "sdk_gap_document": False},
                 "source_analysis": {"core_imports_requiring_public_api_review": []}}
    assert "Explicit delegated file handoffs" not in catalog.submission_text(entry, readiness)
    readiness["pinned_features"]["delegated_file_handoffs"] = True
    assert "Explicit delegated file handoffs" in catalog.submission_text(entry, readiness)
