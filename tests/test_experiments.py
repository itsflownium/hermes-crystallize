"""Same captured input, bounded evidence, explicit calls, and no live dispatch."""

import copy
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from crystallize import chat_savings, contracts, experiments as ex, history, receipts, store, tools


@pytest.fixture
def world(monkeypatch):
    job = {"id": "j1", "name": "New weather job", "prompt": "Return the observed value and source URL.",
           "model": "original-model", "provider": "original-provider"}
    monkeypatch.setattr(ex.lifecycle, "cron_job", lambda key: job if key in {"j1", job["name"]} else None)
    monkeypatch.setattr(ex.modelinfo, "route", lambda value: {"provider": value.get("provider"), "model": value.get("model")})
    monkeypatch.setattr(chat_savings, "chosen_model", lambda: {"model": "candidate-model", "provider": "candidate-provider"})
    monkeypatch.setattr(ex.modelinfo, "api_cost", lambda *args, **kwargs: None)
    monkeypatch.setattr(history, "_session_costs", lambda ids: {})
    monkeypatch.setattr(tools, "dispatch", lambda *args, **kwargs: pytest.fail("comparison dispatched a live tool"))
    runs = []
    monkeypatch.setattr(history, "load_runs", lambda *args, **kwargs: list(runs))
    ex.set_context(None)

    def trace(eid="e1", text='{"value": 19.75, "url": "https://source.example/today"}', **changes):
        row = {"ts": float(len(runs) + 1), "session_id": "s" + eid, "tool_call_id": "call-" + eid,
               "tool_name": "read_file", "args_json": '{"path": "/tmp/observed.json"}',
               "result_sha256": hashlib.sha256(text.encode()).hexdigest(), "result_len": len(text),
               "result_preview": text, "status": "ok", "cron_job_id": "j1", "cron_job_name": job["name"],
               "cron_execution_id": eid, "cron_scheduled": 1}
        row.update(changes)
        store.add_trace(row)
        run = history.Run(execution_id=eid, job_id="j1", job_name=job["name"], session_id=row["session_id"],
                          started_ts=row["ts"], scheduled=True, outcome="completed", finished=True)
        runs.append(run)
        return row

    yield SimpleNamespace(job=job, runs=runs, trace=trace)
    ex.set_context(None)


def completion(calls, text='{"value":19.75,"url":"https://source.example/today"}', cost=0.01):
    def complete(messages, *, provider=None, model=None, temperature=None, max_tokens=None,
                 timeout=None, agent_id=None, profile=None, purpose=None, task=None):
        calls.append((copy.deepcopy(messages), {"provider": provider, "model": model, "purpose": purpose,
                                                "max_tokens": max_tokens, "timeout": timeout, "task": task}))
        # Mutating the caller-owned message must not alter the other side.
        messages[-1]["content"] = "changed by first fake call"
        served_model = model or ("candidate-model" if task else "original-model")
        served_provider = provider or ("candidate-provider" if task else "original-provider")
        return SimpleNamespace(text=text, model=served_model + "-canonical", provider=served_provider,
                               agent_id="default", usage=SimpleNamespace(input_tokens=31, output_tokens=12,
                                                                        total_tokens=43, cost_usd=cost))
    return complete


def test_same_frozen_data_public_facade_only_and_persisted_usage(world):
    world.trace()
    contracts.set("j1", {"required_fields": ["value", "url"], "required_urls": ["https://source.example/today"]})
    calls = []
    result = ex.compare("j1", completion=completion(calls), confirmed=True)
    assert len(calls) == 2
    assert calls[0][0][-1] == calls[1][0][-1]
    assert "19.75" in calls[0][0][-1]["content"]
    assert calls[0][1]["model"] is None and calls[0][1]["provider"] is None
    assert calls[1][1]["model"] is None and calls[1][1]["provider"] is None
    assert calls[0][1]["task"] is None and calls[1][1]["task"] == chat_savings.TASK
    assert result["route_context"]["job_configured_route"]["model"] == "original-model"
    assert result["pairs"][0]["same_input"] and result["pairs"][0]["both_pass_contract"]
    assert result["calls"][0]["cost"]["tokens"]["input_tokens"] == 31
    assert result["calls"][0]["cost"]["estimated_usd"] == 0.01
    assert result["calls"][0]["duration_ms"] >= 0
    assert result["calls"][0]["answered_by"]["model"] == "original-model-canonical"
    assert result["calls"][0]["input_sha256"] == result["calls"][1]["input_sha256"]
    assert ex.comparisons("j1")[0]["id"] == result["id"]
    receipt = receipts.receipt("j1")
    assert receipt["comparison_call_count"] == 2
    assert receipt["overhead_by_kind"]["comparison"]["estimated"]["total_usd"] == 0.02
    assert receipt["cost_bases"]["estimated"]["net_payback_usd"] == -0.02


def test_preview_capture_and_receipt_make_no_model_calls(world):
    world.trace()
    ex.set_context(SimpleNamespace(llm=SimpleNamespace(complete=lambda *a, **k: pytest.fail("implicit paid call"))))
    assert ex.preview("j1")["available"]
    assert ex.capture("j1")["complete"]
    assert receipts.receipt("j1")["sample_count"] == 1
    assert ex.comparisons("j1") == []


def test_explicit_action_required_even_with_injected_completion(world):
    world.trace()
    calls = []
    with pytest.raises(ex.ComparisonError, match="explicitly requested"):
        ex.compare("j1", completion=completion(calls))
    assert not calls
    assert ex.comparisons("j1") == []


@pytest.mark.parametrize("changes", [
    {"result_preview": "cut"},
    {"result_sha256": "0" * 64},
    {"result_len": None},
    {"args_json": '{"path":"cut'},
])
def test_incomplete_or_mismatched_evidence_rejects_before_calls(world, changes):
    world.trace(**changes)
    calls = []
    with pytest.raises(ex.ComparisonError):
        ex.compare("j1", completion=completion(calls), confirmed=True)
    assert not calls
    assert not ex.preview("j1")["available"]


def test_full_session_result_recovered_by_exact_call_and_hash(world):
    text = "full observed text " * 1500
    row = world.trace(text=text, result_preview=text[:2000])
    path = history._hermes_home() / "state.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT, tool_name TEXT)")
    conn.execute("INSERT INTO messages VALUES (?, 'tool', ?, ?, 'read_file')", (row["session_id"], text, row["tool_call_id"]))
    conn.commit()
    conn.close()
    captured = ex.capture("j1")
    assert captured["observations"][0]["output"] == text
    assert captured["observations"][0]["provenance"]["result"] == "session_tool_message"
    calls = []
    ex.compare("j1", completion=completion(calls), confirmed=True)
    assert text in calls[0][0][-1]["content"]


def test_full_result_from_wrong_session_or_call_is_never_substituted(world):
    text = "recorded" * 1000
    row = world.trace(text=text, result_preview="cut")
    conn = sqlite3.connect(history._hermes_home() / "state.db")
    conn.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT)")
    conn.execute("INSERT INTO messages VALUES ('other-session', 'tool', ?, ?)", (text, row["tool_call_id"]))
    conn.execute("INSERT INTO messages VALUES (?, 'tool', ?, 'other-call')", (row["session_id"], text))
    conn.commit()
    conn.close()
    with pytest.raises(ex.ComparisonError, match="truncated/mismatched"):
        ex.capture("j1")


def test_truncated_arguments_recovered_only_from_matching_session_tool_call(world):
    args = json.dumps({"path": "/tmp/" + "x" * 9000})
    row = world.trace(args_json=args[:8000])
    conn = sqlite3.connect(history._hermes_home() / "state.db")
    conn.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, tool_calls TEXT)")
    calls = [{"id": row["tool_call_id"], "function": {"name": "read_file", "arguments": args}}]
    conn.execute("INSERT INTO messages VALUES (?, 'assistant', '', ?)", (row["session_id"], json.dumps(calls)))
    conn.commit()
    conn.close()
    assert ex.capture("j1")["observations"][0]["args"] == json.loads(args)


def test_unfinished_or_failed_sample_is_not_presented_as_complete(world):
    world.trace()
    world.runs[0].outcome = "failed"
    with pytest.raises(ex.ComparisonError, match="completed execution"):
        ex.capture("j1")


def test_actions_are_excluded_and_contract_cannot_claim_they_were_performed(world):
    row = world.trace()
    store.add_trace({**row, "tool_call_id": "send", "tool_name": "send_message", "args_json": '{"recipient":"a@example.com"}'})
    contracts.set("j1", {"required_actions": [{"tool": "send_message", "target": "a@example.com"}]})
    calls = []
    result = ex.compare("j1", completion=completion(calls), confirmed=True)
    assert len(result["sources"][0]["excluded_non_read_calls"]) == 1
    assert "send_message" not in calls[0][0][-1]["content"]
    assert not result["pairs"][0]["both_pass_contract"]


def test_actual_source_failure_is_preserved_in_frozen_evidence(world):
    world.trace(text='{"error":"upstream missing", "status":503}', status="error")
    calls = []
    contracts.set("j1", {"preserve_errors": True})
    result = ex.compare("j1", completion=completion(calls, text="Everything is fine"), confirmed=True)
    assert "upstream missing" in calls[0][0][-1]["content"]
    assert not result["pairs"][0]["both_pass_contract"]


def test_multiple_samples_record_provenance_and_sample_count(world):
    world.trace("e1")
    world.trace("e2", text='{"value":91}')
    calls = []
    result = ex.compare("j1", sample_count=2, completion=completion(calls), confirmed=True)
    assert result["sample_count"] == 2 and len(calls) == 4
    assert calls[0][0][-1] == calls[1][0][-1]
    assert calls[2][0][-1] == calls[3][0][-1]
    assert result["sources"][0]["execution_id"] != result["sources"][1]["execution_id"]


def test_unsupported_reasoning_setting_rejected_before_paid_calls(world):
    world.trace()
    calls = []
    with pytest.raises(ex.ComparisonError, match="Unsupported public"):
        ex.compare("j1", candidate={"reasoning_effort": "low"}, completion=completion(calls), confirmed=True)
    assert not calls


def test_failed_call_is_durable_unknown_overhead_and_other_side_still_runs(world):
    world.trace()
    calls = []
    def flaky(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            raise RuntimeError("provider unavailable")
        return SimpleNamespace(text="observed", provider="p", model="m", usage=None)
    result = ex.compare("j1", completion=flaky, confirmed=True)
    assert len(calls) == 2 and result["status"] == "failed"
    assert result["calls"][0]["status"] == "failed"
    assert result["calls"][0]["cost"]["provenance"] == "unknown"
    receipt = receipts.receipt("j1")
    assert receipt["comparison_call_count"] == 2
    assert receipt["cost_bases"]["estimated"]["net_payback_usd"] is None
    assert receipt["cost_bases"]["estimated"]["unknown_overhead_count"] == 2


def test_exports_redact_secrets_after_both_models_receive_same_original_data(world):
    secret = "sk-0123456789abcdefghijklmnop"
    world.trace(text=json.dumps({"api_key": secret, "value": 19.75}))
    calls = []
    result = ex.compare("j1", completion=completion(calls, text="api_key=" + secret), confirmed=True)
    assert secret in calls[0][0][-1]["content"] and calls[0][0][-1] == calls[1][0][-1]
    assert secret not in json.dumps(result)
    assert secret not in json.dumps(ex.capture("j1"))
    assert secret not in json.dumps(ex.comparisons("j1"))


def test_oversized_input_rejected_without_silent_truncation(world, monkeypatch):
    world.trace()
    monkeypatch.setattr(ex, "MAX_CAPTURE_BYTES", 64)
    calls = []
    with pytest.raises(ex.ComparisonError, match="not silently truncated"):
        ex.compare("j1", completion=completion(calls), confirmed=True)
    assert not calls


def test_oversized_output_is_charged_and_not_validated_as_complete(world, monkeypatch):
    world.trace()
    monkeypatch.setattr(ex, "MAX_OUTPUT_CHARS", 30)
    result = ex.compare("j1", completion=completion([], text="x" * 50), confirmed=True)
    assert result["status"] == "failed"
    assert result["calls"][0]["cost"]["estimated_usd"] == 0.01
    assert "validation" not in result["calls"][0]
    assert "output" not in result["calls"][0]


def test_registered_context_is_used_without_private_dispatch(world):
    world.trace()
    calls = []
    ex.set_context(SimpleNamespace(llm=SimpleNamespace(complete=completion(calls))))
    # A freshly loaded panel module has no module-local context, but the
    # source-root registration remains the same explicitly bound context.
    ex._ctx = None
    assert ex.compare("j1", confirmed=True)["status"] == "completed"
    assert len(calls) == 2


@pytest.mark.parametrize("text", ['{"truncated":true,"output":"partial"}',
                                  '{"results":[1,2],"has_more":true}',
                                  "Output truncated: more data was omitted"])
def test_explicitly_incomplete_tool_data_is_not_mistaken_for_full_evidence(world, text):
    world.trace(text=text)
    with pytest.raises(ex.ComparisonError, match="explicitly reports incomplete"):
        ex.capture("j1")


def test_immutable_input_snapshot_survives_source_removal_and_redacts_exports(world):
    secret = "sk-0123456789abcdefghijklmnop"
    world.trace(text=json.dumps({"apiKey": secret, "value": 19.75}))
    result = ex.compare("j1", completion=completion([]), confirmed=True)
    with store._lock:
        store._db().execute("DELETE FROM traces")
        store._db().commit()
    snapshots = ex.comparison_inputs(result["id"])
    assert len(snapshots) == 1
    assert snapshots[0]["observations"][0]["output"]
    assert snapshots[0]["input_sha256"] == result["sources"][0]["input_sha256"]
    assert secret not in json.dumps(snapshots)


def test_identical_default_routes_require_a_meaningful_candidate(world, monkeypatch):
    world.trace()
    monkeypatch.setattr(chat_savings, "chosen_model", lambda: {"provider": None, "model": None})
    calls = []
    assert not ex.preview("j1")["available"]
    with pytest.raises(ex.ComparisonError, match="helper model"):
        ex.compare("j1", completion=completion(calls), confirmed=True)
    assert not calls
    assert ex.compare("j1", candidate={"prompt": "Return the observed value in JSON."},
                      completion=completion(calls), confirmed=True)["status"] == "completed"


def test_execution_session_disagreement_is_rejected(world):
    world.trace()
    world.runs[0].session_id = "another-job-session"
    with pytest.raises(ex.ComparisonError, match="session identity disagrees"):
        ex.capture("j1")


def test_explicit_overrides_remain_host_policy_gated_with_clear_failure(world):
    world.trace()
    def public_facade(messages, *, provider=None, model=None, **kwargs):
        if provider or model:
            raise PermissionError("Plugin cannot override the model (allow_model_override is false).")
        return SimpleNamespace(text="answer", model="host-selected", provider="host-provider", usage=None)
    result = ex.compare("j1", candidate={"model": "explicit-model"}, completion=public_facade, confirmed=True)
    assert result["calls"][0]["status"] == "completed"
    assert result["calls"][1]["status"] == "failed"
    assert "allow_model_override" in result["calls"][1]["error"]
    assert result["calls"][0]["answered_by"]["model"] == "host-selected"


def test_absent_host_usage_zeroes_are_not_fabricated_as_free_api_price(world, monkeypatch):
    world.trace()
    monkeypatch.setattr(ex.modelinfo, "api_cost", lambda *a, **k: pytest.fail("unknown zero usage was priced"))
    def complete(messages, **kwargs):
        return SimpleNamespace(text="answer", model="m", provider="p",
                               usage=SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0, cost_usd=None))
    result = ex.compare("j1", completion=complete, confirmed=True)
    assert all(c["cost"]["api_equivalent_usd"] is None for c in result["calls"])
    assert all(c["cost"]["provenance"] == "unknown" for c in result["calls"])


@pytest.mark.parametrize("metadata", [
    {"finish_reason": "length"},
    {"stop_reason": "max_output_tokens"},
    {"status": "incomplete"},
    {"raw_response": {"choices": [{"finish_reason": "length"}]}},
    {"raw": {"incomplete_details": {"reason": "max_output_tokens"}}},
    {"audit": {"truncated": True}},
    {"complete": False},
    {"error": {"message": "provider failed after producing partial output"}},
])
def test_incomplete_provider_responses_are_failed_paid_overhead(world, metadata):
    world.trace()
    calls = []
    base = completion(calls)

    def complete(messages, **kwargs):
        answer = base(messages, **kwargs)
        for key, value in metadata.items():
            setattr(answer, key, value)
        return answer

    result = ex.compare("j1", completion=complete, confirmed=True)
    assert len(calls) == 2
    assert result["status"] == "failed"
    assert not result["quality_evidence"]["all_outputs_complete"]
    for call in result["calls"]:
        assert call["status"] == "failed" and call["complete"] is False
        assert "output" not in call and "validation" not in call
        assert call["cost"]["estimated_usd"] == 0.01
        assert call["completion_check"]["ok"] is False
    assert receipts.receipt("j1")["comparison_call_count"] == 2


def test_output_token_ceiling_is_conservatively_incomplete_without_finish_reason(world):
    world.trace()
    calls = []
    base = completion(calls)

    def complete(messages, **kwargs):
        answer = base(messages, **kwargs)
        answer.usage.output_tokens = kwargs["max_tokens"]
        return answer

    result = ex.compare("j1", completion=complete, confirmed=True)
    assert result["status"] == "failed"
    assert all(call["completion_check"]["output_token_ceiling_reached"] for call in result["calls"])


@pytest.mark.parametrize("text", ["Output truncated: rest omitted", '{"truncated":true,"answer":"partial"}'])
def test_explicitly_truncated_answer_text_is_not_accepted(world, text):
    world.trace()
    result = ex.compare("j1", completion=completion([], text=text), confirmed=True)
    assert result["status"] == "failed"
    assert all("output" not in call and "validation" not in call for call in result["calls"])


def test_no_contract_is_unreviewed_quality_evidence(world):
    world.trace()
    result = ex.compare("j1", completion=completion([]), confirmed=True)
    assert not result["pairs"][0]["both_pass_contract"]
    assert not result["pairs"][0]["contract_configured"]
    assert result["quality_evidence"] == {
        "all_outputs_complete": True, "contract_configured": False,
        "all_contract_checks_passed": False, "semantic_quality_proven": False,
        "human_review": "blind_review_available",
        "limitation": "Task-specific deterministic checks and a human preference are evidence, not proof of factual correctness or live-job equivalence."}


def test_empty_contract_cannot_claim_outcome_requirements_passed(world):
    world.trace()
    contracts.set("j1", {})
    result = ex.compare("j1", completion=completion([]), confirmed=True)
    assert result["contract_configured"]
    assert not result["pairs"][0]["both_pass_contract"]
    assert not result["quality_evidence"]["all_contract_checks_passed"]
    assert not result["calls"][0]["validation"]["task_checks_configured"]
    assert "No task-specific" in result["calls"][0]["validation"]["why"]


def test_main_to_helper_is_a_model_switch_never_an_efficiency_saving(world):
    world.trace()
    preview = ex.preview("j1")
    result = ex.compare("j1", completion=completion([]), confirmed=True)
    for value in (preview, result, ex.comparison(result["id"])):
        assert value["experiment_kind"] == "model_switch"
        assert value["efficiency_comparison"] is False
        assert value["efficiency_savings_claimed"] is False
        assert value["reasoning_overridden"] is False
        assert "not efficiency savings" in value["cost_difference_scope"]


def test_same_route_prompt_experiment_keeps_reasoning_and_live_savings_unclaimed(world):
    world.trace()
    calls = []
    result = ex.compare("j1", candidate={"task": None, "prompt": "Answer more concisely using the frozen value and URL."},
                        completion=completion(calls), confirmed=True)
    assert result["experiment_kind"] == "same_route_efficiency"
    assert result["efficiency_comparison"] is True
    assert result["efficiency_savings_claimed"] is False
    assert result["reasoning_overridden"] is False
    assert all("reasoning" not in kwargs for _, kwargs in calls)


def test_observed_route_change_is_separate_even_with_same_requested_selector(world):
    world.trace()
    calls = []
    base = completion(calls)

    def complete(messages, **kwargs):
        answer = base(messages, **kwargs)
        answer.provider = "different-provider" if len(calls) == 2 else answer.provider
        return answer

    result = ex.compare("j1", candidate={"task": None, "prompt": "Answer concisely."},
                        completion=complete, confirmed=True)
    assert result["pairs"][0]["served_routes_identical"] is False
    assert result["experiment_kind"] == "model_switch" and not result["efficiency_comparison"]


@pytest.mark.parametrize("side", ["original", "candidate"])
def test_no_main_or_helper_reasoning_override_can_be_requested(world, side):
    world.trace()
    calls = []
    with pytest.raises(ex.ComparisonError, match="Unsupported public"):
        ex.compare("j1", **{side: {"reasoning_effort": "low"}}, completion=completion(calls), confirmed=True)
    assert not calls


@pytest.mark.parametrize("domain,payload,fields", [
    ("market report", {"asset": "ZQX", "price": 19.75, "currency": "AUD"}, ["asset", "price", "currency"]),
    ("build report", {"commit": "b17ad39", "passed": 17, "failed": 2}, ["commit", "passed", "failed"]),
    ("inventory", {"sku": "part-413", "available": 16, "warehouse": "west"}, ["sku", "available", "warehouse"]),
    ("research digest", {"title": "Unseen result", "sample_size": 137, "method": "measured"}, ["title", "sample_size", "method"]),
    ("travel schedule", {"train": "T23", "departure": "14:37", "platform": 6}, ["train", "departure", "platform"]),
    ("incident report", {"service": "queue-bridge", "status": "degraded", "incident": "INC-9"}, ["service", "status", "incident"]),
])
def test_quality_evidence_is_task_contract_based_across_unseen_domains(world, domain, payload, fields):
    world.job["prompt"] = "Report the observed " + domain + " as JSON, preserving exact values and evidence URL."
    payload["url"] = "https://evidence.example/" + domain.replace(" ", "-")
    text = json.dumps(payload)
    world.trace(text=text)
    contracts.set("j1", {"required_sources": [{"tool": "read_file", "target": "/tmp/observed.json"}],
                         "required_fields": fields + ["url"], "required_urls": [payload["url"]]})
    calls = []
    result = ex.compare("j1", completion=completion(calls, text=text), confirmed=True)
    assert calls[0][0][-1] == calls[1][0][-1]
    frozen = json.loads(calls[0][0][-1]["content"].split("\n", 1)[1])
    assert frozen["observations"][0]["output"] == text
    assert result["pairs"][0]["both_pass_contract"]
    assert result["quality_evidence"]["all_contract_checks_passed"]
    assert not result["quality_evidence"]["semantic_quality_proven"]
    assert {c["rule"] for c in result["calls"][0]["validation"]["checks"]} == {"required_source", "required_field", "required_url"}


def test_structurally_valid_wrong_value_never_claims_semantic_quality(world):
    world.trace(text='{"value":19.75,"url":"https://source.example/today"}')
    contracts.set("j1", {"required_fields": ["value", "url"], "required_urls": ["https://source.example/today"]})
    result = ex.compare("j1", completion=completion([], text='{"value":99.75,"url":"https://source.example/today"}'), confirmed=True)
    assert result["pairs"][0]["both_pass_contract"]
    assert result["quality_evidence"]["semantic_quality_proven"] is False
    assert "not proof" in result["quality_evidence"]["limitation"]


def test_single_diagnostic_reader_is_read_only_and_not_found_is_explicit(world):
    world.trace()
    result = ex.compare("j1", completion=completion([]), confirmed=True)
    assert ex.comparison(result["id"])["id"] == result["id"]
    with pytest.raises(LookupError, match="does not exist"):
        ex.comparison("not-a-comparison")


def test_explicit_comparison_flows_to_anonymous_vote_then_revealed_evidence(world, monkeypatch):
    from crystallize import blind

    world.trace()
    contracts.set("j1", {"required_fields": ["value", "url"]})
    monkeypatch.setattr(blind.secrets, "randbelow", lambda count: 1)
    result = ex.compare("j1", completion=completion([]), confirmed=True)
    anonymous = blind.present(result["id"])
    assert anonymous["can_choose"] and not anonymous["revealed"]
    assert "original-model" not in json.dumps(anonymous) and "candidate-model" not in json.dumps(anonymous)
    assert "cost" not in anonymous and "reveal" not in anonymous
    revealed = blind.choose(result["id"], "neither")
    assert revealed["reveal"]["samples"][0]["A"]["answered_by"]["model"] == "candidate-model-canonical"
    assert revealed["reveal"]["samples"][0]["A"]["cost"]["estimated_usd"] == 0.01
    assert revealed["reveal"]["samples"][0]["A"]["validation"]["task_checks_configured"]
    assert revealed["reveal"]["quality_evidence"]["human_review"]["choice"] == "neither"
    assert revealed["reveal"]["quality_evidence"]["semantic_quality_proven"] is False
