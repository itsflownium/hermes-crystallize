"""Outcome requirements use current evidence rather than answer length."""

import json

import pytest

from crystallize import contracts, store, tuning
from crystallize.history import Run, Step


def source(url="https://example.com/feed", output=None, **kw):
    return {"tool": "web_extract", "args": {"urls": [url]}, "status": "ok",
            "output": output if output is not None else {"items": [{"title": "current", "url": url}]}, **kw}


def test_contracts_are_explicit_versioned_and_bound_to_job_id():
    spec = contracts.set("job-1", {"required_urls": ["https://example.com/feed"], "minimum_items": 2})
    assert spec["version"] == 1
    assert contracts.get("job-1") == spec
    assert contracts.get("same display name") is None
    assert contracts.evaluate(None, answer="anything")["configured"] is False
    contracts.set("job-1", None)
    assert contracts.get("job-1") is None


@pytest.mark.parametrize("spec", [
    {"version": 2}, {"required_fileds": ["title"]}, {"minimum_items": True},
    {"required_fields": "url"}, {"preserve_errors": "true"}, {"item_path": "items"},
    {"required_sources": [{"tool": "web_extract"}]},
    {"required_actions": [{"tool": "send_message", "target": "ops", "min_count": 0}]},
])
def test_invalid_or_misspelled_requirements_do_not_silently_pass(spec):
    with pytest.raises(contracts.ContractError):
        contracts.validate(spec)


def test_current_required_source_cannot_be_replaced_by_same_sized_wrong_source():
    spec = {"required_sources": [{"tool": "web_extract", "target": "https://example.com/feed"}]}
    answer = [{"title": "current", "url": "https://example.com/feed"}]
    assert contracts.evaluate(spec, answer=answer, sources=[source()])["ok"]
    report = contracts.evaluate(spec, answer=answer, sources=[source("https://example.org/feed")])
    assert not report["ok"] and "source" in report["why"]
    assert not contracts.evaluate(spec, answer=answer)["ok"]
    assert not contracts.evaluate(spec, answer=answer, sources=[source(truncated=True)])["ok"]
    preview = {"tool_name": "web_extract", "args_json": '{"urls":["https://example.com/feed"]}',
               "result_preview": '{"items": []}', "result_len": 9000, "status": "ok"}
    assert not contracts.evaluate(spec, answer=answer, sources=[preview])["ok"]


def test_urls_must_be_in_the_answer_and_current_evidence():
    spec = {"required_urls": ["https://example.com/feed"]}
    assert contracts.evaluate(spec, answer="[current](https://example.com/feed)", sources=[source()])["ok"]
    assert not contracts.evaluate(spec, answer="https://example.com/feed")["ok"]
    assert not contracts.evaluate(spec, answer="https://example.org/feed", sources=[source()])["ok"]


def test_fields_and_items_are_checked_structurally_including_every_item():
    spec = {"required_fields": ["items.*.title", "items.*.url"], "minimum_items": 2}
    good = {"items": [{"title": "a", "url": "u"}, {"title": "b", "url": "v"}]}
    assert contracts.evaluate(spec, answer=json.dumps(good))["ok"]
    bad = {"items": [{"title": "a", "url": "u"}, {"title": "b", "uri": "v"}]}
    assert len(json.dumps(good)) == len(json.dumps(bad))
    assert not contracts.evaluate(spec, answer=bad)["ok"]
    assert not contracts.evaluate(spec, answer={"items": []})["ok"]
    assert contracts.evaluate({"minimum_items": 2}, answer="1. a\n   - nested\n2. b")["ok"]
    assert not contracts.evaluate({"minimum_items": 3}, answer="1. a\n   - nested\n2. b")["ok"]
    assert contracts.evaluate({"minimum_items": 2, "item_path": "data.rows"}, answer={"data": {"rows": [1, 2]}})["ok"]


def test_error_and_status_values_cannot_disappear_behind_success_wording():
    spec = {"preserve_errors": True, "preserve_status": True}
    current = [source(output={"results": [{"status": 503, "error": "feed temporarily unavailable"}], "exit_code": 7})]
    assert not contracts.evaluate(spec, answer="Everything healthy. " * 10, sources=current)["ok"]
    assert contracts.evaluate(spec, answer="503: feed temporarily unavailable (exit_code 7)", sources=current)["ok"]
    assert not contracts.evaluate(spec, answer="503: feed temporarily unavailable (exit_code 7)")["ok"]
    assert "semantic equivalence" in contracts.evaluate(spec, answer="", sources=current)["limitations"][0]


def test_required_action_needs_the_recipient_count_and_success_evidence():
    spec = {"required_actions": [{"tool": "send_message", "target": "ops", "target_field": "recipient", "min_count": 2}]}
    right = {"tool_name": "send_message", "args_json": '{"recipient":"ops","text":"hello"}', "status": "ok"}
    wrong = {**right, "args_json": '{"recipient":"dev","text":"ops"}'}
    assert contracts.evaluate(spec, answer="sent", actions=[right, right])["ok"]
    for actions in ([right], [right, wrong], [right, {**right, "status": "error"}], [right, {**right, "status": None}]):
        report = contracts.evaluate(spec, answer="sent to ops twice", actions=actions)
        assert not report["ok"] and "successful actions" in report["why"]
    # A mention of a target in the message body cannot substitute for its recipient.
    assert not contracts.evaluate({"required_actions": [{"tool": "send_message", "target": "ops"}]},
                                  answer="sent", actions=[wrong])["ok"]
    nested_decoy = {**right, "args_json": '{"recipient":"dev","body":{"to":"ops"}}'}
    assert not contracts.evaluate({"required_actions": [{"tool": "send_message", "target": "ops"}]},
                                  answer="sent", actions=[nested_decoy])["ok"]


def run(sid, cost, answer_sources=None):
    return Run(execution_id=sid, job_id="j", job_name="job", session_id=sid, scheduled=True, started_ts=1,
               session_started=1, finished=True, outcome="completed", cost_usd=cost, steps=answer_sources or [])


def test_trial_rejects_equal_length_wrong_answer_and_wrong_action_target():
    before = [run("b1", .02), run("b2", .02)]
    trial = [run("t1", .001), run("t2", .001)]
    good = '{"items":[{"title":"x","url":"u"}]}'
    bad = '{"items":[{"title":"x","uri":"u"}]}'
    assert len(good) == len(bad)
    answers = {"b1": good, "b2": good, "t1": bad, "t2": bad}
    assert tuning.evaluate(before, trial, answers)["ok"]  # old structural heuristics alone
    report = tuning.evaluate(before, trial, answers, contract={"required_fields": ["items.*.url"]})
    assert not report["ok"] and "outcome contract" in report["why"]
    wrong = Step("send_message", {"recipient": "dev", "text": "ops"}, None, None, 5, "ok", 1)
    for r in trial:
        r.steps = [wrong]
    report = tuning.evaluate(before, trial, {k: good for k in answers},
                             contract={"required_actions": [{"tool": "send_message", "target": "ops"}]})
    assert not report["ok"] and "ops" in report["why"]


def test_kept_settings_are_reverted_when_a_configured_requirement_stops(monkeypatch):
    contracts.set("j", {"required_fields": ["items.*.url"]})
    t = {"job_id": "j", "job_name": "job", "status": "kept", "finished_at": .5, "result": {"ok": True}}
    current = run("new", .001)
    monkeypatch.setattr(tuning, "_finished_scheduled", lambda job: [current])
    monkeypatch.setattr(tuning.history, "final_answers", lambda ids: {"new": '{"items":[{"uri":"x"}]}'})
    restored = []
    monkeypatch.setattr(tuning, "_restore", lambda data: restored.append(data["job_id"]))
    tuning._guard_kept(t)
    assert restored == ["j"] and t["status"] == "reverted"
    assert t["kept_at"] == .5 and "outcome contract" in t["result"]["why"]
    assert store.get_tuning("job")["result"]["contract_checks"][0]["execution_id"] == "new"


def test_silent_trial_runs_cannot_hide_a_required_outcome(monkeypatch):
    contracts.set("j", {"minimum_items": 2})
    t = {"job_id": "j", "job_name": "job", "status": "trial", "started_at": 0}
    baseline = [run("b1", .02), run("b2", .02)]
    trial = [run("t1", .001), run("silent", .001), run("t2", .001)]
    answer = "1. first item\n2. second item"
    monkeypatch.setattr(tuning, "_trial_runs", lambda data: (baseline, trial))
    monkeypatch.setattr(tuning.history, "final_answers", lambda ids: {**{sid: answer for sid in ids}, "silent": "[SILENT]"})
    monkeypatch.setattr(tuning.lifecycle, "cron_job", lambda ref: {"id": "j", "name": "job"})
    restored = []
    monkeypatch.setattr(tuning, "_restore", lambda data: restored.append(True))
    tuning._decide(t)
    assert restored and t["status"] == "reverted"
    assert "outcome contract" in t["result"]["why"]
    assert any(check["execution_id"] == "silent" and not check["ok"] for check in t["result"]["contract_checks"])
