"""Offline capability-preservation and learned-budget regressions for Cron."""

from copy import deepcopy
import hashlib
import json
import time

import pytest

from crystallize import adaptive, history, lifecycle, prerun, store, tuning
from crystallize.history import Run, Step


ANSWER = "Full current report with every requested action and source https://example.invalid/current"


def _run(index, cost=1.0, text='{"status":"ok","value":12,"elapsed":1.25}', tools=("read_primary",)):
    steps = [Step(tool, {}, text, hashlib.sha256(text.encode()).hexdigest(), len(text), "ok", 10) for tool in tools]
    return Run(str(index), "job-id", "job", str(index), True, float(index), steps=steps, outcome="completed",
               cost_usd=cost, model="host-main-model", provider="host-provider", reasoning_effort="high",
               session_started=float(index), finished=True)


def _evaluate(before, trial, **kwargs):
    return tuning.evaluate(before, trial, {run.session_id: ANSWER for run in before + trial}, **kwargs)


@pytest.mark.parametrize("field,value", [("model", "different"), ("provider", "different"),
                                           ("reasoning_effort", "low"), ("provider", None),
                                           ("reasoning_effort", None)])
def test_cheaper_capability_or_unknown_identity_cannot_earn_savings(field, value):
    before, trial = [_run(1), _run(2)], [_run(3, .01), _run(4, .01)]
    setattr(trial[-1], field, value)
    result = _evaluate(before, trial)
    assert not result["ok"] and result["kind"] == "identity"
    assert result["before"] is None and result["after"] is None


def test_same_effort_with_a_different_reasoning_budget_is_not_comparable():
    before, trial = [_run(1), _run(2)], [_run(3, .01), _run(4, .01)]
    for run in before + trial:
        run.reasoning_config_fingerprint = '{"effort":"high","max_tokens":20000}'
    trial[-1].reasoning_config_fingerprint = '{"effort":"high","max_tokens":1000}'
    assert _evaluate(before, trial)["kind"] == "identity"


def test_contracts_and_actions_are_still_checked_before_identity_rejection():
    before, trial = [_run(1), _run(2)], [_run(3, .01), _run(4, .01)]
    trial[0].provider = None
    result = _evaluate(before, trial, required=["send_message"])
    assert result["kind"] == "quality" and "send_message" in result["why"]
    result = _evaluate(before, trial, contract={"required_urls": ["https://missing.invalid/required"]})
    assert result["kind"] == "quality" and result["contract_checks"]


def test_verdict_threshold_depends_on_observed_spread_and_sample_count():
    sparse = adaptive.savings_evidence([1, 1], [.9, .9])
    numerous = adaptive.savings_evidence([1] * 24, [.9] * 24)
    variable = adaptive.savings_evidence([.5, 1.5] * 12, [.9] * 24)
    assert not sparse["ok"] and numerous["ok"] and not variable["ok"]
    assert sparse["required_drop"] > numerous["required_drop"]
    assert variable["required_drop"] > numerous["required_drop"]
    assert not sparse["statistical_confidence_claimed"]


def test_one_observation_and_nonfinite_measurements_do_not_prove_savings():
    assert not adaptive.savings_evidence([1], [.01]).get("ok")
    assert not adaptive.savings_evidence([1, float("nan")], [.01, .01]).get("ok")


def test_close_sparse_trial_records_uncertainty_until_more_samples_resolve_it(monkeypatch):
    baseline, trial = [_run(1), _run(2)], [_run(3, .7), _run(4, .7)]
    tune = {"job_id": "job-id", "job_name": "job", "status": "trial", "started_at": 0, "policy_version": 2}
    monkeypatch.setattr(tuning, "_trial_runs", lambda data: (baseline, trial))
    monkeypatch.setattr(history, "final_answers", lambda ids: {sid: ANSWER for sid in ids})
    monkeypatch.setattr(lifecycle, "_bound_job", lambda ref: {"id": "job-id", "state": "scheduled"})
    monkeypatch.setattr(tuning, "_save", lambda *args: None)
    tuning._decide(tune)
    assert tune["status"] == "trial" and tune["pending_result"]["saving_evidence"]["trial_count"] == 2
    assert "gathering more comparable evidence" in tuning.status_text(tune)
    trial.extend(_run(index, .7) for index in range(5, 9))
    tuning._decide(tune)
    assert tune["status"] == "kept" and "pending_result" not in tune
    assert tune["result"]["saving_evidence"]["trial_count"] == 6


def test_cli_level_and_saved_opt_in_cannot_change_reasoning(monkeypatch):
    monkeypatch.setattr(store, "get_setting", lambda *args: True)
    edits = []
    monkeypatch.setattr(lifecycle, "_edit_job", lambda *args: edits.append(args))
    assert tuning.lowers_reasoning() is False
    with pytest.raises(lifecycle.LifecycleError, match="preserves"):
        tuning.start("job-id", "low")
    with pytest.raises(lifecycle.LifecycleError, match="preserves"):
        tuning._apply_stage("job-id", {"level": "low"})
    assert not edits


def test_new_trial_preserves_host_model_reasoning_and_toolsets(monkeypatch):
    job = {"id": "job-id", "name": "job", "model": "host-main-model", "provider": "host-provider",
           "reasoning_effort": "high", "enabled_toolsets": ["all-host-tools"], "prompt": "Do every action."}
    original = deepcopy(job)
    edits, saved = [], {}
    monkeypatch.setattr(tuning, "_job_or_error", lambda ref: job)
    monkeypatch.setattr(lifecycle, "_tuning_for_job", lambda found: None)
    monkeypatch.setattr(tuning, "_split", lambda *args: ([_run(1), _run(2), _run(3)], []))
    monkeypatch.setattr(tuning, "_snapshot", lambda *args: {"fingerprint": "same"})
    monkeypatch.setattr(tuning, "_save", lambda name, data: saved.update(deepcopy(data)))
    monkeypatch.setattr(store, "get_setting", lambda *args: True)
    monkeypatch.setattr(lifecycle, "set_toolsets", lambda *args: pytest.fail("host toolsets changed"))
    monkeypatch.setattr(lifecycle, "_edit_job", lambda *args: edits.append(args))
    result = tuning.start("job-id")
    assert result["reasoning_preserved"] and result["level"] is None and not result["new_only"]
    assert result["schema_pruning"] and result["presentation_budgets"]["ready"]
    assert all(args[1] == "--prompt" for args in edits)
    assert job == original and saved["status"] == "trial"


@pytest.mark.parametrize("current,restored", [("low", "high"), ("ultra", "ultra")])
def test_legacy_migration_restores_only_our_changes_and_requires_fresh_baseline(monkeypatch, current, restored):
    job = {"id": "job-id", "name": "renamed", "reasoning_effort": current,
           "enabled_toolsets": ["web"], "prompt": "User's newer prompt." + tuning.LEGACY_EFFICIENCY_HINT}
    tune = {"job_id": "job-id", "job_name": "job", "status": "kept", "level": "low", "previous": "high",
            "toolsets": ["web"], "previous_toolsets": None, "tools_changed": True,
            "hint": True, "hint_added": True, "new_only": True, "stages": [{"level": "medium"}], "finished_at": 10}
    monkeypatch.setattr(lifecycle, "_bound_job", lambda ref: job)
    monkeypatch.setattr(tuning, "_save", lambda *args: None)
    monkeypatch.setattr(tuning, "_set_new_only", lambda *args: True)
    def edit(ref, flag, value):
        job[{"--prompt": "prompt", "--reasoning-effort": "reasoning_effort"}[flag]] = value
    monkeypatch.setattr(lifecycle, "_edit_job", edit)
    monkeypatch.setattr(lifecycle, "set_toolsets", lambda ref, value: job.update(enabled_toolsets=value))
    assert tuning._migrate_legacy(tune)
    assert job["reasoning_effort"] == restored and job["prompt"] == "User's newer prompt."
    assert job["enabled_toolsets"] is None and tune["status"] == "revalidation"
    assert tune["level"] is None and tune["stages"] == [] and not tune["schema_pruning"]
    assert not tune["result"]["ok"] and tune["legacy_settings"]["level"] == "low"
    assert tune["kept_at"] == 10 and tune["trial_finished_at"] == 10


def test_legacy_restore_failure_keeps_recovery_data_for_retry(monkeypatch):
    tune = {"job_id": "job-id", "job_name": "job", "status": "trial", "level": "low", "previous": "high"}
    monkeypatch.setattr(tuning, "_restore", lambda data: (_ for _ in ()).throw(lifecycle.LifecycleError("offline")))
    monkeypatch.setattr(tuning, "_save", lambda *args: None)
    assert tuning._migrate_legacy(tune) and tune["needs_restore"] and tune["level"] == "low"


def test_new_quality_preserving_hint_is_not_misclassified_as_a_legacy_campaign():
    campaign = {"status": "trial", "hint": True, "clean": True, "schema_pruning": True, "policy_version": 2}
    assert not tuning._legacy_campaign(campaign)
    assert tuning._legacy_campaign({**campaign, "policy_version": 1})
    assert "verification" in tuning.EFFICIENCY_HINT and "once each" not in tuning.EFFICIENCY_HINT


def test_legacy_disabled_reasoning_restores_the_disabled_value(monkeypatch):
    job = {"id": "job-id", "reasoning_effort": "low"}
    calls = []
    monkeypatch.setattr(lifecycle, "_bound_job", lambda ref: job)
    monkeypatch.setattr(lifecycle, "_edit_job", lambda *args: calls.append(args))
    tuning._restore({"job_id": "job-id", "level": "low", "previous": False})
    assert calls == [("job-id", "--reasoning-effort", "none")]


def test_budgets_require_complete_comparable_evidence_and_adapt_to_this_job():
    small = [_run(i, text="small") for i in range(1, 4)]
    large = [_run(i, text="large " * 1000) for i in range(1, 4)]
    first, second = adaptive.presentation_budgets(small), adaptive.presentation_budgets(large)
    assert first["ready"] and first["complete_results"]
    assert first["by_tool"]["read_primary"]["chars"] < second["by_tool"]["read_primary"]["chars"]
    small[-1].steps[0].result_len += 1
    assert not adaptive.presentation_budgets(small)["ready"]
    large[-1].reasoning_effort = "medium"
    assert not adaptive.presentation_budgets(large)["ready"]


def test_over_budget_presentation_keeps_the_full_current_original():
    original = {"output": "PASS test one 1.25s\n" * 5000 + "TAIL changed: HTTP 503 at 23:59 price 1.75", "exit_code": 0}
    result = prerun.step_block({}, {"output": json.dumps(original)}, limit=20)
    assert "complete output follows" in result
    assert json.loads(result.split("\n", 1)[1]) == original


def test_oversized_or_incomplete_artifacts_hand_back_the_whole_task(monkeypatch, tmp_path):
    monkeypatch.setattr(lifecycle, "replay_support", lambda: {"guarded_dispatch_available": True, "automatic_nested_tools": True})
    artifact = tmp_path / "large.txt"
    artifact.write_text("complete output" * 100)
    monkeypatch.setattr(prerun, "MAX_FILE_BYTES", 10)
    with pytest.raises(ValueError, match="complete-result"):
        prerun.step_block({"args": {"path": str(artifact)}}, {"output": "written", "started": time.time()})
    routine = {"id": "routine", "name": "job", "source_ref": "job-id", "status": "live", "meta": {},
               "steps": {"steps": [{"tool": "read_primary", "args": {}}]}}
    monkeypatch.setattr(store, "get_routine", lambda ref: routine)
    monkeypatch.setattr(lifecycle, "check_drift", lambda ref: True)
    monkeypatch.setattr(lifecycle, "live_result", lambda *args: None)
    monkeypatch.setattr(prerun.runner, "run", lambda *args: {"ok": True, "outputs": [
        {"step": 1, "output": "preview", "artifacts": [{"name": "omitted", "truncated": True}]}]})
    text, code = prerun.run("routine", lambda *args: None)
    assert code == 0 and "Do the whole task yourself" in text


def test_json_compaction_and_fingerprints_preserve_precision_and_duplicate_fields():
    first = '{ "number": 1.000000000000000000001, "status": 200, "status": 503, "text": "a  b\u000a c" }'.replace("\n", "\\n")
    second = first.replace("0001,", "0002,")
    compact = prerun.clean_text(first)
    assert "1.000000000000000000001" in compact and compact.count('"status"') == 2
    assert prerun.fingerprint(first) != prerun.fingerprint(second)
    assert prerun.fingerprint('  {"number": 12,"status":503}') == prerun.fingerprint('{"status":503, "number":12}')
    assert prerun.fingerprint('12') != prerun.fingerprint('"12"')


def _schema(name, read=None):
    schema = {"type": "function", "function": {"name": name, "description": "Complete host description",
                                               "parameters": {"type": "object", "properties": {"all": {"type": "string"}}}}}
    if read is not None:
        schema["annotations"] = {"readOnlyHint": read}
    return schema


@pytest.fixture
def pruning(monkeypatch):
    job = {"id": "job-id", "name": "job"}
    tune = {"job_id": "job-id", "job_name": "job", "status": "trial", "schema_pruning": True,
            "baseline_until": 10, "config_snapshot": {"fingerprint": "same"}}
    runs = [_run(i, tools=("read_primary", "send_email")) for i in range(1, 4)]
    request = {"model": "host-main-model", "provider": "host-provider", "reasoning_effort": "high",
               "reasoning": {"enabled": True, "max_tokens": 30000}, "messages": [{"role": "user", "content": "Run the task."}],
               "tools": [_schema("read_primary", True), _schema("send_email", False), _schema("read_unused", True),
                         _schema("unknown_capability"), _schema("tool_describe", True)]}
    monkeypatch.setattr(lifecycle, "cron_job", lambda ref: job if ref in {"job-id", "job"} else None)
    monkeypatch.setattr(lifecycle, "_tuning_for_job", lambda found: tune)
    monkeypatch.setattr(lifecycle, "configuration_fingerprint", lambda *args: {"fingerprint": "same"})
    monkeypatch.setattr(store, "routine_for_job", lambda ref: None)
    monkeypatch.setattr(history, "load_runs", lambda ref: runs)
    return request, tune, runs


def test_schema_pruning_keeps_actions_unknowns_discovery_and_full_host_parameters(pruning):
    request, tune, runs = pruning
    original = deepcopy(request)
    result = adaptive.prune_request(request, "job-id")
    assert [tool["function"]["name"] for tool in result["tools"]] == ["read_primary", "send_email", "unknown_capability", "tool_describe"]
    assert {key: value for key, value in result.items() if key != "tools"} == {key: value for key, value in request.items() if key != "tools"}
    assert result["tools"][0] == request["tools"][0] and request == original


def test_conflicting_read_annotation_cannot_hide_an_action_tool(pruning):
    request, tune, runs = pruning
    action = _schema("send_notification", True)
    request["tools"].append(action)
    result = adaptive.prune_request(request, "job-id")
    assert action in result["tools"]


def test_host_top_level_schema_shape_and_provider_reasoning_fields_are_preserved(pruning):
    request, tune, runs = pruning
    request["tools"] = [{**schema["function"], "annotations": schema.get("annotations", {}),
                         "input_schema": schema["function"]["parameters"]} for schema in request["tools"]]
    request["thinking"] = {"type": "enabled", "budget_tokens": 30000}
    result = adaptive.prune_request(request, "job-id")
    assert result["thinking"] == request["thinking"]
    assert result["tools"][0] == request["tools"][0]


@pytest.mark.parametrize("uncertainty", ["off", "legacy_reasoning", "incomplete", "new_tool", "tool_error", "drift", "varying", "no_discovery", "explicit_tool"])
def test_uncertainty_restores_original_host_definitions(pruning, uncertainty):
    request, tune, runs = pruning
    if uncertainty == "off":
        tune["schema_pruning"] = False
    elif uncertainty == "legacy_reasoning":
        tune["level"] = "low"
    elif uncertainty == "incomplete":
        runs[0].steps[0].result_len += 5
    elif uncertainty == "new_tool":
        request["messages"].append({"role": "assistant", "tool_calls": [{"function": {"name": "tool_describe"}}]})
    elif uncertainty == "tool_error":
        request["messages"].append({"role": "tool", "content": '{"error":"different source needed"}'})
    elif uncertainty == "drift":
        tune["config_snapshot"] = {"fingerprint": "changed"}
    elif uncertainty == "varying":
        runs[0].steps = runs[0].steps[:1]
    elif uncertainty == "no_discovery":
        request["tools"] = request["tools"][:-1]
    elif uncertainty == "explicit_tool":
        request["messages"][0]["content"] = "Use read_unused for the additional source."
    original = deepcopy(request)
    assert adaptive.prune_request(request, "job-id") is None
    assert request == original
