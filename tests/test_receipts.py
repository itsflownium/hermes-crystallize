"""Signed payback, failed spend, missing prices, and receipts after Undo."""
import json

import pytest

from crystallize import history, receipts as proof, store


@pytest.fixture
def world(monkeypatch):
    infos = {}
    monkeypatch.setattr(history, "_session_costs", lambda ids: {key: infos[key] for key in ids if key in infos})
    monkeypatch.setattr(history, "load_runs", lambda *a, **k: [])
    def run(eid, ts, cost=None, outcome="completed", *, api=None, actual=True, job_id="j1"):
        sid = "s" + eid
        if cost is not None:
            infos[sid] = {"actual_cost_usd" if actual else "estimated_cost_usd": cost}
        return history.Run(execution_id=eid, job_id=job_id, job_name="new job", session_id=sid,
                           scheduled=True, started_ts=ts, outcome=outcome, finished=True,
                           cost_usd=cost, api_cost_usd=api, input_tokens=123, output_tokens=23)
    return run, infos


def campaign(start=3, activated=4, ended=None):
    return proof.record_campaign("j1", {"kind": "tuning", "started_at": start,
                                        "activated_at": activated, "ended_at": ended, "status": "kept"})


def test_negative_savings_failures_and_all_overhead_remain_visible(world):
    run, _ = world
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("trial", 3, 4, "failed"),
            run("cost-overrun", 4, 12), run("failed-live", 5, 3, "failed")]
    campaign()
    proof.record_overhead("j1", kind="comparison", source_id="c1", actual_cost_usd=1, provenance="actual")
    proof.record_overhead("j1", kind="helper", source_id="h1", actual_cost_usd=2, status="rejected", provenance="actual")
    receipt = proof.receipt("j1", runs=rows)
    actual = receipt["cost_bases"]["actual"]
    assert actual["historical_savings_usd"] == -5  # -2 overrun, -3 failed spend
    assert actual["overhead_usd"] == 7  # 4 trial + 1 comparison + 2 rejected helper
    assert actual["net_payback_usd"] == receipt["net_payback_usd"] == -12
    assert actual["failed_live_run_count"] == 1 and actual["failed_trial_run_count"] == 1
    assert actual["estimated_break_even_remaining_runs"] is None
    assert receipt["recorded_spend"]["actual"]["total_usd"] == 39


def test_unknown_helper_price_never_becomes_free_or_complete_payback(world):
    run, _ = world
    campaign()
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("live", 4, 3)]
    proof.record_overhead("j1", kind="helper", source_id="unknown", status="failed")
    receipt = proof.receipt("j1", runs=rows)
    actual = receipt["cost_bases"]["actual"]
    assert actual["known_historical_savings_usd"] == 7
    assert actual["unknown_overhead_count"] == 1
    assert actual["net_payback_usd"] is None and receipt["net_payback_usd"] is None
    assert actual["estimated_break_even_remaining_runs"] is None
    assert any("Missing prices" in text for text in receipt["uncertainty"])


def test_actual_zero_has_priority_over_positive_estimate(world):
    run, infos = world
    rows = [run("b1", 1, 0.2), run("b2", 2, 0.2), run("free", 4, 0)]
    infos["sfree"]["estimated_cost_usd"] = 2.0
    rows[-1].cost_usd = 2.0  # An old adapter's fallback must not replace actual zero.
    campaign()
    receipt = proof.receipt("j1", runs=rows)
    assert receipt["cost_bases"]["actual"]["historical_savings_usd"] == pytest.approx(0.2)
    assert receipt["runs"][-1]["cost"]["actual_usd"] == 0.0
    assert receipt["runs"][-1]["cost"]["provenance"] == "session_actual"


def test_setup_payback_includes_trials_and_comparison_calls(world):
    run, _ = world
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("trial", 3, 30), run("live", 4, 5)]
    campaign()
    proof.record_overhead("j1", kind="comparison", source_id="c1", actual_cost_usd=5, provenance="actual")
    actual = proof.receipt("j1", runs=rows)["cost_bases"]["actual"]
    assert actual["net_payback_usd"] == -30
    assert actual["estimated_saving_per_future_run_usd"] == 5
    assert actual["estimated_break_even_remaining_runs"] == 6


def test_undo_and_disappearing_live_history_do_not_erase_receipt(world):
    run, _ = world
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("live", 4, 3)]
    tuning = {"job_id": "j1", "started_at": 3, "first_started_at": 3, "kept_at": 4,
              "trial_finished_at": 4, "finished_at": 4, "status": "kept"}
    store.save_tuning("new job", tuning)
    first = proof.receipt("j1", runs=rows)
    assert first["net_payback_usd"] == 7
    store.save_tuning("new job", {**tuning, "status": "undone", "undone_at": 5, "finished_at": 5})
    second = proof.receipt("j1", runs=[])
    assert second["net_payback_usd"] == first["net_payback_usd"]
    assert second["sample_count"] == 3
    assert second["campaigns"][0]["activated_at"] == 4
    assert second["campaigns"][0]["ended_at"] == 5
    # New original-cost runs after Undo do not earn optimized-run savings.
    third = proof.receipt("j1", runs=[run("after-undo", 6, 20)])
    assert third["net_payback_usd"] == 7


def test_retrial_preserves_previous_campaign_and_deduplicates_overlap(world):
    run, _ = world
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("live", 4, 3)]
    campaign()
    proof.record_campaign("j1", {"kind": "routine", "started_at": 3.5, "activated_at": 4})
    receipt = proof.receipt("j1", runs=rows)
    assert len(receipt["campaigns"]) == 2
    assert receipt["cost_bases"]["actual"]["live_run_count"] == 1
    assert receipt["net_payback_usd"] == 7


def test_api_equivalent_aggregate_values_are_qualified_without_repricing(world, monkeypatch):
    run, _ = world
    rows = [run("b1", 1, None, api=10), run("b2", 2, None, api=10), run("live", 4, None, api=2)]
    monkeypatch.setattr(history.modelinfo, "api_cost", lambda *a, **k: pytest.fail("aggregate was repriced"))
    campaign()
    receipt = proof.receipt("j1", runs=rows)
    assert receipt["cost_bases"]["api_equivalent"]["historical_savings_usd"] == 8
    assert receipt["net_payback_usd"] is None
    assert receipt["preferred_basis"] is None
    assert any("pricing tiers" in text for text in receipt["uncertainty"])


def test_estimated_basis_is_explicit_and_not_presented_as_actual(world):
    run, _ = world
    rows = [run("b1", 1, 10, actual=False), run("b2", 2, 10, actual=False), run("live", 4, 3, actual=False)]
    campaign()
    receipt = proof.receipt("j1", runs=rows)
    assert receipt["preferred_basis"] == "estimated"
    assert receipt["cost_bases"]["actual"]["historical_savings_usd"] is None
    assert receipt["cost_bases"]["estimated"]["historical_savings_usd"] == 7
    assert receipt["runs"][-1]["cost"]["provenance"] == "session_estimated"


def test_repeated_overhead_upserts_cannot_double_count(world):
    proof.record_overhead("j1", kind="comparison", source_id="same-call", status="pending")
    proof.record_overhead("j1", kind="comparison", source_id="same-call", estimated_cost_usd=0.04)
    receipt = proof.receipt("j1", runs=[])
    assert receipt["comparison_call_count"] == 1
    assert receipt["cost_bases"]["estimated"]["net_payback_usd"] == -0.04


def test_missing_baseline_price_does_not_produce_savings(world):
    run, _ = world
    rows = [run("b1", 1), run("live", 4, 3)]
    campaign()
    receipt = proof.receipt("j1", runs=rows)
    assert receipt["net_payback_usd"] is None
    assert receipt["cost_bases"]["actual"]["unknown_savings_run_count"] == 1


def test_unknown_outcome_does_not_earn_baseline_credit(world):
    run, _ = world
    campaign()
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("unknown-outcome", 4, 3, None)]
    actual = proof.receipt("j1", runs=rows)["cost_bases"]["actual"]
    assert actual["historical_savings_usd"] == -3
    assert actual["failed_live_run_count"] == 1


def test_foreign_job_same_name_cannot_supply_baseline_or_cost(world):
    run, _ = world
    campaign()
    rows = [run("foreign", 1, 99, job_id="other-job"), run("live", 4, 3)]
    receipt = proof.receipt("j1", runs=rows)
    assert receipt["sample_count"] == 1
    assert receipt["net_payback_usd"] is None


def test_auxiliary_ledger_includes_rejected_and_failed_calls(world):
    run, _ = world
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("live", 4, 3)]
    campaign()
    store.add_chat_event("slive", kind="summary", status="rejected", helper_call=True,
                         cheap_tokens=50, cheap_cost=1.5)
    store.add_chat_event("slive", kind="summary", status="failed", helper_call=True,
                         cheap_tokens=0, cheap_cost=None)
    receipt = proof.receipt("j1", runs=rows)
    assert receipt["helper_call_count"] == 2
    assert receipt["overhead_by_kind"]["helper"]["estimated"]["known_usd"] == 1.5
    assert receipt["cost_bases"]["estimated"]["net_payback_usd"] is None


def test_helpers_included_in_session_spend_are_not_subtracted_twice(world):
    run, _ = world
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("live", 4, 3)]
    campaign()
    proof.record_overhead("j1", kind="helper", source_id="already-counted", actual_cost_usd=1,
                          session_id="slive", included_in_session=True)
    assert proof.receipt("j1", runs=rows)["net_payback_usd"] == 7


def test_export_redaction_covers_nested_keys_headers_urls_and_private_keys():
    value = {"apiKey": "sensitive-a", "nested": [{"refresh_token": "sensitive-b"}],
             "header": "Authorization: Bearer sensitive-c", "url": "https://me:sensitive-d@example.com/?token=sensitive-e",
             "output": "password=sensitive-f\n-----BEGIN RSA PRIVATE KEY-----\nsensitive-g\n-----END RSA PRIVATE KEY-----",
             "input_tokens": 50}
    encoded = json.dumps(proof.redact_export(value))
    assert "sensitive-" not in encoded
    assert '"input_tokens": 50' in encoded


def test_record_limits_reject_instead_of_silently_truncating(world):
    with pytest.raises(ValueError, match="not silently truncated"):
        proof.record_overhead("j1", kind="other", source_id="large", details={"data": "x" * (proof.MAX_RECORD_BYTES + 1)})
    assert proof.receipt("j1", runs=[])["overhead"] == []


def test_bounded_export_retains_totals_and_explains_row_limit(world, monkeypatch):
    run, _ = world
    monkeypatch.setattr(proof, "MAX_EXPORT_ROWS", 2)
    campaign()
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("live1", 4, 3), run("live2", 5, 3)]
    receipt = proof.receipt("j1", runs=rows)
    assert receipt["sample_count"] == 4 and len(receipt["runs"]) == 2
    assert receipt["net_payback_usd"] == 14
    monkeypatch.setattr(proof, "MAX_RECEIPT_ROWS", 2)
    limited = proof.receipt("j1", runs=[])
    assert not limited["history_complete"] and limited["net_payback_usd"] is None


def test_subscription_incremental_zero_does_not_turn_into_api_equivalent_cash_spend(world):
    run, infos = world
    row = run("included", 1, None, api=5)
    infos["sincluded"] = {"cost_status": "included", "estimated_cost_usd": 5}
    receipt = proof.receipt("j1", runs=[row])
    assert receipt["recorded_spend"]["actual"]["total_usd"] == 0
    assert receipt["runs"][0]["cost"]["provenance"] == "subscription_incremental"
    assert receipt["recorded_spend"]["api_equivalent"]["total_usd"] == 5


def test_temporarily_unavailable_session_price_cannot_erase_durable_spend(world):
    run, infos = world
    row = run("recorded", 1, 5)
    proof.receipt("j1", runs=[row])
    infos.clear()
    row.cost_usd = None
    row.outcome = None
    later = proof.receipt("j1", runs=[row])
    assert later["runs"][0]["cost"]["actual_usd"] == 5
    assert later["runs"][0]["outcome"] == "completed"


def test_rejected_trial_has_no_live_activation_boundary(world):
    run, _ = world
    proof.snapshot_tuning({"job_id": "j1", "started_at": 3, "status": "reverted",
                           "trial_finished_at": 5, "finished_at": 5, "result": {"ok": False}})
    receipt = proof.receipt("j1", runs=[run("baseline", 1, 10), run("trial", 4, 3)])
    actual = receipt["cost_bases"]["actual"]
    assert actual["live_run_count"] == 0
    assert actual["trial_run_count"] == 1
    assert actual["net_payback_usd"] == -3


def test_each_routine_activation_survives_retirement_and_repromotion(world):
    run, _ = world
    first = {"id": "r1", "source_ref": "j1", "status": "live", "meta": {"created_at": 3, "promoted_at": 4}}
    proof.snapshot_routine(first)
    proof.snapshot_routine({**first, "status": "retired", "meta": {**first["meta"], "undone_at": 5}})
    proof.snapshot_routine({**first, "meta": {"created_at": 6, "promoted_at": 7, "first_promoted_at": 4, "undone_at": 5}})
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("live1", 4, 3), run("live2", 7, 2)]
    receipt = proof.receipt("j1", runs=rows)
    assert len(receipt["campaigns"]) == 2
    assert receipt["net_payback_usd"] == 15


def test_helper_list_price_is_api_equivalent_provenance_not_cash_estimate(world):
    run, _ = world
    store.add_chat_event("sbaseline", kind="summary", helper_call=True, cheap_cost=1.5,
                         cost_source="list_price_estimate")
    receipt = proof.receipt("j1", runs=[run("baseline", 1, 10)])
    event = receipt["overhead"][0]
    assert event["estimated_usd"] is None and event["actual_usd"] is None
    assert event["api_equivalent_usd"] == 1.5
    assert receipt["cost_bases"]["estimated"]["net_payback_usd"] is None


def test_completed_but_contract_failed_answer_earns_no_baseline_credit(world):
    run, _ = world
    campaign()
    proof.snapshot_tuning({"job_id": "j1", "started_at": 3, "status": "reverted", "kept_at": 4,
                           "reverted_at": 6, "result": {"ok": False,
                           "contract_checks": [{"execution_id": "bad-answer", "ok": False,
                                                 "fingerprint": "contract-v1", "why": "required URL is missing"}]}})
    receipt = proof.receipt("j1", runs=[run("b1", 1, 10), run("b2", 2, 10), run("bad-answer", 5, 3)])
    assert receipt["cost_bases"]["actual"]["historical_savings_usd"] == -3
    assert receipt["validation_failure_count"] == 1
    assert receipt["runs"][-1]["contract_ok"] is False
    # Replacing current tuning state cannot erase the failed execution record.
    proof.snapshot_tuning({"job_id": "j1", "started_at": 3, "kept_at": 4, "status": "undone", "undone_at": 6})
    assert proof.receipt("j1", runs=[])["cost_bases"]["actual"]["historical_savings_usd"] == -3


def test_new_trial_cost_is_overhead_when_older_kept_settings_overlap(world):
    run, _ = world
    campaign()
    proof.snapshot_tuning({"job_id": "j1", "campaign_id": "upgrade", "campaign_started_at": 6,
                           "first_started_at": 3, "started_at": 7, "status": "reverted", "reverted_at": 8})
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("old-live", 5, 3),
            run("upgrade-trial", 7, 2), run("old-settings-resumed", 9, 3)]
    receipt = proof.receipt("j1", runs=rows)
    actual = receipt["cost_bases"]["actual"]
    assert actual["live_run_count"] == 2 and actual["trial_run_count"] == 1
    assert actual["historical_savings_usd"] == 14
    assert actual["net_payback_usd"] == 12


@pytest.mark.parametrize("changed", ["model", "provider", "reasoning_effort", "reasoning_config_fingerprint"])
def test_confounded_settings_never_earn_efficiency_savings(world, changed):
    run, _ = world
    rows = [run("b1", 1, 10), run("b2", 2, 10), run("live", 4, 1)]
    for row in rows:
        row.model, row.provider, row.reasoning_effort = "m", "p", "high"
        row.reasoning_config_fingerprint = '{"effort":"high","budget_tokens":8000}'
    setattr(rows[-1], changed, {"model": "cheaper", "provider": "other", "reasoning_effort": "low",
                              "reasoning_config_fingerprint": '{"effort":"high","budget_tokens":2000}'}[changed])
    campaign()
    receipt = proof.receipt("j1", runs=rows)
    actual = receipt["cost_bases"]["actual"]
    assert actual["known_historical_savings_usd"] == 0
    assert actual["historical_savings_usd"] is None
    assert actual["net_payback_usd"] is None
    assert actual["qualified_efficiency_savings_usd"] is None
    assert not actual["efficiency_savings_proven"]
    assert actual["excluded_settings_comparison_count"] == 1
    assert actual["comparisons"][0]["status"] == "no_equivalent_baseline"


def test_receipt_matches_each_run_to_same_settings_baseline_cohort(world):
    run, _ = world
    rows = [run("expensive", 1, 100), run("equivalent", 2, 10), run("live", 4, 2)]
    for row in rows:
        row.model, row.provider, row.reasoning_effort = "m", "p", "high"
    rows[0].model = "other-model"
    campaign()
    actual = proof.receipt("j1", runs=rows)["cost_bases"]["actual"]
    assert actual["historical_savings_usd"] == 8
    assert actual["qualified_efficiency_savings_usd"] == 8
    assert actual["qualified_net_payback_usd"] == 8
    assert actual["efficiency_savings_proven"]
    assert actual["comparisons"][0]["baseline_execution_ids"] == ["equivalent"]


def test_legacy_receipt_math_exposes_unknown_settings_without_claiming_proof(world):
    run, _ = world
    campaign()
    actual = proof.receipt("j1", runs=[run("before", 1, 10), run("live", 4, 2)])["cost_bases"]["actual"]
    assert actual["net_payback_usd"] == 8  # Retained legacy counterfactual arithmetic.
    assert actual["qualified_efficiency_savings_usd"] is None
    assert not actual["efficiency_savings_proven"]
    assert actual["unproven_settings_comparison_count"] == 1


def test_host_reported_unknown_settings_cannot_fall_back_to_legacy_math(world):
    run, _ = world
    rows = [run("before", 1, 10), run("live", 4, 2)]
    for row in rows:
        row.identity_attribution_complete = False
    campaign()
    actual = proof.receipt("j1", runs=rows)["cost_bases"]["actual"]
    assert actual["net_payback_usd"] is None
    assert actual["excluded_settings_comparison_count"] == 1
    assert actual["comparisons"][0]["status"] == "unknown_settings"


def test_unknown_session_chain_cannot_reuse_partial_cached_cost(world):
    run, infos = world
    row = run("partial", 1, 5)
    infos[row.session_id]["usage_totals_complete"] = False
    receipt = proof.receipt("j1", runs=[row])
    assert receipt["recorded_spend"]["actual"]["total_usd"] is None
    assert not receipt["runs"][0]["cost"]["usage_totals_complete"]
