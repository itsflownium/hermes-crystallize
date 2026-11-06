"""Anonymous same-input review persists its order and commits votes before reveal."""

import copy
import importlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from crystallize import blind, experiments as ex, store, tools


@pytest.fixture
def record():
    calls = []
    for index, side in enumerate(("original", "candidate")):
        answer = "The observed value is 19.75." if index == 0 else "Observed value: 19.75."
        calls.append({"call_id": "private-call-" + side, "side": side, "sample_index": 0,
                      "status": "completed", "complete": True, "output": answer,
                      "output_sha256": ex._hash(answer), "input_sha256": "f" * 64,
                      "source_execution_id": "private-execution",
                      "requested": {"model": "hidden-request-" + side, "provider": "hidden-request-provider"},
                      "answered_by": {"model": "hidden-served-" + side, "provider": "hidden-served-provider"},
                      "cost": {"actual_usd": 0.0315 if index == 0 else 0.0012, "provenance": "host_actual"},
                      "validation": {"ok": True, "configured": True, "checks": [{"detail": "hidden-check-evidence"}]},
                      "completion_check": {"ok": True, "finish_reasons": ["stop"]}, "duration_ms": 197 + index})
    value = {"id": "comparison-1", "job_id": "private-job", "created_at": 1.0,
             "scope": ex._SCOPE, "status": "completed", "sample_count": 1, "calls": calls,
             "experiment_kind": "model_switch", "experiment_label": "Model-switch answer experiment",
             "efficiency_comparison": False, "contract_configured": True,
             "route_context": {"candidate_configured_route": {"model": "hidden-route-context"}},
             "contract": {"required_fields": ["hidden-contract-field"]},
             "quality_evidence": {"all_outputs_complete": True, "all_contract_checks_passed": True},
             "pairs": [{"sample_index": 0, "same_input": True, "both_completed": True,
                        "diff": "hidden-original-to-candidate-diff", "actual_answer_cost_difference_usd": 0.0303}],
             "limitations": ["Answer-generation experiment only."]}
    ex._save(value)
    return value


def test_presentation_allowlist_hides_mapping_identity_costs_and_checks(record, monkeypatch):
    monkeypatch.setattr(blind.secrets, "randbelow", lambda size: 1)
    result = blind.present(record["id"])
    assert set(result) == {"id", "status", "scope", "samples", "choice", "can_choose", "choice_eligible",
                           "revealed", "choice_recorded_blind", "notice"}
    assert result["can_choose"] and not result["revealed"] and result["choice"] is None
    assert result["samples"][0]["A"]["answer"] == record["calls"][1]["output"]
    assert result["samples"][0]["B"]["answer"] == record["calls"][0]["output"]
    for sample in result["samples"]:
        assert set(sample) == {"sample_index", "A", "B"}
        assert set(sample["A"]) == set(sample["B"]) == {"answer", "status"}
    exported = json.dumps(result)
    for hidden in ("hidden-", "private-", "original", "candidate", "actual_usd", "answered_by", "requested", "0.0315", "0.0012", "validation", "duration_ms"):
        assert hidden not in exported


def test_randomized_order_is_stable_across_module_reload(record, monkeypatch):
    draws = []

    def draw(size):
        draws.append(size)
        return 1

    monkeypatch.setattr(blind.secrets, "randbelow", draw)
    first = blind.present(record["id"])
    assert draws == [2]
    importlib.reload(blind)
    assert blind.present(record["id"]) == first
    assert draws == [2]


def test_randomized_order_and_decision_survive_database_reopen(record, monkeypatch, tmp_path, isolated_plugin_storage):
    monkeypatch.setattr(blind.secrets, "randbelow", lambda size: 1)
    original = blind.present(record["id"])
    blind.choose(record["id"], "tie")
    path = tmp_path / "persistent-review.db"
    saved = sqlite3.connect(path, check_same_thread=False)
    isolated_plugin_storage.backup(saved)
    saved.close()
    reopened = sqlite3.connect(path, check_same_thread=False)
    monkeypatch.setattr(store, "_conn", reopened)
    try:
        result = blind.present(record["id"])
        assert result["samples"] == original["samples"]
        assert result["revealed"] and result["choice"] == "tie" and result["choice_recorded_blind"]
        assert result["reveal"]["mapping"] == {"A": "candidate", "B": "original"}
    finally:
        monkeypatch.setattr(store, "_conn", isolated_plugin_storage)
        reopened.close()


@pytest.mark.parametrize("choice", ["A", "B", "tie", "neither"])
def test_preference_is_committed_before_any_reveal_is_returned(record, choice, monkeypatch):
    present = blind._presentation
    checkpoints = []

    def checked(saved, state):
        if state["revealed_at"] is not None:
            row = store._db().execute("SELECT choice,chosen_at,revealed_at FROM blind_reviews WHERE comparison_id=?", (record["id"],)).fetchone()
            assert row[0] == choice and row[1] <= row[2]
            checkpoints.append(row[0])
        return present(saved, state)

    monkeypatch.setattr(blind, "_presentation", checked)
    result = blind.choose(record["id"], choice)
    assert checkpoints == [choice]
    assert result["choice"] == choice and result["revealed"] and result["choice_recorded_blind"]
    assert not result["can_choose"] and not result["choice_eligible"]
    assert result["reveal_reason"] == "choice"
    assert result["reveal"]["quality_evidence"]["human_review"]["blinded"] is True
    assert result["reveal"]["quality_evidence"]["semantic_quality_proven"] is False
    for label in ("A", "B"):
        call = result["reveal"]["samples"][0][label]
        assert call["answered_by"]["model"].startswith("hidden-served-")
        assert call["cost"]["actual_usd"] is not None and call["validation"]["ok"]
        assert call["side"] == result["reveal"]["mapping"][label]
    assert blind.present(record["id"])["choice"] == choice


def test_reveal_without_vote_is_explicit_and_later_choice_cannot_be_blind(record):
    result = blind.reveal(record["id"])
    assert result["revealed"] and result["choice"] is None
    assert result["reveal_reason"] == "explicit_without_vote"
    assert not result["choice_recorded_blind"] and not result["can_choose"] and not result["choice_eligible"]
    assert result["reveal"]["quality_evidence"]["human_review"] == {
        "choice": None, "blinded": False, "chosen_at": None, "reveal_reason": "explicit_without_vote"}
    with pytest.raises(ex.ComparisonError, match="already revealed without a vote"):
        blind.choose(record["id"], "A")
    assert blind.reveal(record["id"]) == result
    assert blind.present(record["id"]) == result


def test_vote_retry_is_idempotent_and_cannot_be_changed_after_reveal(record):
    first = blind.choose(record["id"], "B")
    assert blind.choose(record["id"], "B") == first
    with pytest.raises(ex.ComparisonError, match="cannot be changed"):
        blind.choose(record["id"], "A")
    assert blind.reveal(record["id"]) == first


@pytest.mark.parametrize("choice", ["a", "original", "candidate", "Tie", "", None, {}, True])
def test_invalid_vote_cannot_reveal_or_record_a_choice(record, choice):
    with pytest.raises(ex.ComparisonError, match="Choose A, B, tie or neither"):
        blind.choose(record["id"], choice)
    result = blind.present(record["id"])
    assert not result["revealed"] and result["choice"] is None and result["can_choose"]


@pytest.mark.parametrize("change", ["failed", "pending", "complete_false", "empty", "truncated_text", "completion_failed", "hash_mismatch", "different_input", "duplicate"])
def test_failed_or_incomplete_pairs_are_not_accepted_for_a_vote(record, change):
    call = record["calls"][0]
    if change in {"failed", "pending"}:
        call["status"] = change
        call["error"] = "hidden-provider-error-and-cost-0.51"
    elif change == "complete_false":
        call["complete"] = False
    elif change == "empty":
        call["output"] = ""
    elif change == "truncated_text":
        call["output"] = "Output truncated: more omitted"
        call["output_sha256"] = ex._hash(call["output"])
    elif change == "completion_failed":
        call["completion_check"]["ok"] = False
    elif change == "hash_mismatch":
        call["output_sha256"] = "0" * 64
    elif change == "different_input":
        call["input_sha256"] = "e" * 64
    elif change == "duplicate":
        record["calls"].append(copy.deepcopy(call))
    ex._save(record)
    result = blind.present(record["id"])
    assert not result["can_choose"] and not result["revealed"]
    assert "hidden-" not in json.dumps(result)
    with pytest.raises(ex.ComparisonError, match="Only complete answers"):
        blind.choose(record["id"], "A")
    assert not blind.present(record["id"])["revealed"]


def test_oversized_output_is_omitted_instead_of_silently_truncated(record, monkeypatch):
    monkeypatch.setattr(ex, "MAX_OUTPUT_CHARS", 8)
    result = blind.present(record["id"])
    assert not result["can_choose"]
    assert all(result["samples"][0][label]["answer"] is None for label in ("A", "B"))


def test_read_vote_reveal_and_registration_never_generate_answers_or_dispatch(record, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A blind review made an implicit model or tool call")

    ex.set_context(SimpleNamespace(llm=SimpleNamespace(complete=forbidden)))
    monkeypatch.setattr(ex, "compare", forbidden)
    monkeypatch.setattr(tools, "dispatch", forbidden)
    try:
        blind.present(record["id"])
        blind.choose(record["id"], "neither")
        blind.reveal(record["id"])
        other = copy.deepcopy(record)
        other["id"] = "auction:opaque:0:1"
        blind.register(other["id"], other)
        blind.present(other["id"])
    finally:
        ex.set_context(None)


def test_concurrent_conflicting_votes_persist_exactly_one_blind_choice(record):
    def vote(choice):
        try:
            return blind.choose(record["id"], choice)["choice"]
        except ex.ComparisonError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(vote, ("A", "B")))
    assert results.count("rejected") == 1
    saved = blind.present(record["id"])
    assert saved["choice"] in {"A", "B"} and saved["choice_recorded_blind"]
    assert saved["choice"] in results


@pytest.mark.parametrize("domain", ["cron", "chat", "reread", "rhythm", "auction"])
def test_generic_already_generated_comparisons_keep_domain_independent_blind_evidence(record, domain):
    other = copy.deepcopy(record)
    other["id"] = domain + ":opaque:0:1"
    other["domain"] = domain
    assert blind.register(other["id"], other) == {"id": other["id"], "registered": True}
    result = blind.present(other["id"])
    assert result["can_choose"] and not result["revealed"] and "domain" not in result
    result = blind.choose(other["id"], "neither")
    assert result["reveal"]["quality_evidence"]["human_review"]["choice"] == "neither"
    assert result["reveal"]["efficiency_savings_claimed"] is False


def test_generic_registration_is_immutable_and_not_aliasable_to_saved_comparisons(record):
    other = copy.deepcopy(record)
    other["id"] = "auction:opaque:0:1"
    assert blind.register(other["id"], other)["registered"]
    assert blind.register(other["id"], other)["registered"]
    blind.choose(other["id"], "A")
    other["calls"][0]["output"] = "changed after vote"
    other["calls"][0]["output_sha256"] = ex._hash(other["calls"][0]["output"])
    with pytest.raises(ex.ComparisonError, match="immutable"):
        blind.register(other["id"], other)
    assert "changed after vote" not in json.dumps(blind.present(other["id"]))
    with pytest.raises(ex.ComparisonError, match="already belongs"):
        blind.register(record["id"], record)


def test_registration_rejects_mismatched_input_before_persistence(record):
    record["id"] = "auction:opaque:0:1"
    record["calls"][0]["input_sha256"] = "e" * 64
    with pytest.raises(ex.ComparisonError, match="identical frozen input"):
        blind.register(record["id"], record)
    with pytest.raises(LookupError):
        blind.present(record["id"])


def test_multiple_samples_share_one_persisted_display_mapping(record, monkeypatch):
    monkeypatch.setattr(blind.secrets, "randbelow", lambda size: 1)
    record["sample_count"] = 2
    additional = copy.deepcopy(record["calls"])
    for call in additional:
        call["sample_index"] = 1
        call["input_sha256"] = "a" * 64
        call["output"] = "Second sample " + call["side"]
        call["output_sha256"] = ex._hash(call["output"])
    record["calls"].extend(additional)
    ex._save(record)
    result = blind.present(record["id"])
    assert result["can_choose"] and len(result["samples"]) == 2
    assert result["samples"][0]["A"]["answer"] == record["calls"][1]["output"]
    assert result["samples"][1]["A"]["answer"] == "Second sample candidate"
    assert blind.choose(record["id"], "tie")["reveal"]["mapping"]["A"] == "candidate"


def test_unknown_comparison_and_invalid_id_do_not_create_review_rows():
    with pytest.raises(LookupError, match="does not exist"):
        blind.present("missing")
    with pytest.raises(ex.ComparisonError, match="valid comparison ID"):
        blind.present("../secret")
    assert store._db().execute("SELECT COUNT(*) FROM blind_reviews").fetchone()[0] == 0
