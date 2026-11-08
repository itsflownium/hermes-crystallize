"""Regression tests for the runtime and savings bugs found in the full review."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from crystallize import (auction, brake, capture, chat_savings as cs, estimate, fetch_cache, handoffs, history, lifecycle,
                         lineage, overview, receipts, rent, request_settings, store, tuning)


@pytest.fixture(autouse=True)
def originals_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_full_dir", lambda: tmp_path / "originals")


def _compress(home, parent="old", child="new"):
    """A state database where `child` continues `parent` after context compression."""
    conn = sqlite3.connect(home / "state.db")
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT, end_reason TEXT, source TEXT, model_config TEXT)")
    conn.execute("INSERT INTO sessions VALUES (?, NULL, 'compression', 'cli', '{}')", (parent,))
    conn.execute("INSERT INTO sessions VALUES (?, ?, NULL, 'cli', '{}')", (child, parent))
    conn.commit()
    conn.close()
    lineage.forget()


@pytest.fixture
def compressed(tmp_path):
    _compress(tmp_path / "hermes")
    yield
    lineage.forget()


def test_compression_keeps_chat_on_and_originals_available(compressed):
    store.set_chat_enabled("old", True)
    result_id = cs._keep_full("the original text", "old")
    assert store.chat_enabled("new")
    assert cs._load_full(result_id, "new") == "the original text"
    assert "the original text" in cs.read_full(result_id, session_id="new")
    assert cs.fill_placeholders("{{crystal:" + result_id + "}}", session_id="new") == "the original text"
    assert handoffs.expand_args({"content": "{{crystal:" + result_id + "}}"}, "new", "t", "write_file") == {"content": "the original text"}


def test_turning_chat_off_after_compression_sticks(compressed):
    store.set_chat_enabled("old", True)
    store.set_chat_enabled("new", False)
    assert not store.chat_enabled("new")


def test_removed_results_stay_removed_after_compression(compressed):
    result_id = cs._keep_full("big", "old")
    store.add_eviction("old", "c1", result_id)
    out = rent.apply({"messages": [{"role": "tool", "tool_call_id": "c1", "content": "big"}]}, "new")
    assert result_id in out["messages"][0]["content"]
    store.remove_eviction("new", "c1")
    assert store.evictions("old") == {}


def test_chat_totals_span_compression(compressed):
    store.add_chat_event("old", chars=100)
    store.add_chat_event("new", chars=20)
    assert store.list_chat_savings("new")[0]["chars"] == 120


def test_a_forked_chat_does_not_inherit(tmp_path):
    conn = sqlite3.connect(tmp_path / "hermes" / "state.db")
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT, end_reason TEXT, source TEXT, model_config TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('a', NULL, NULL, 'cli', '{}')")
    conn.execute("INSERT INTO sessions VALUES ('b', 'a', NULL, 'cli', '{}')")
    conn.commit()
    conn.close()
    lineage.forget()
    store.set_chat_enabled("a", True)
    assert not store.chat_enabled("b")
    lineage.forget()


def test_jobs_with_the_same_name_keep_separate_tuning_records():
    store.save_tuning("same", {"job_name": "same", "job_id": "A", "status": "trial"})
    store.save_tuning("same", {"job_name": "same", "job_id": "B", "status": "kept"})
    assert lifecycle._tuning_for_job({"id": "A", "name": "same"})["status"] == "trial"
    assert lifecycle._tuning_for_job({"id": "B", "name": "same"})["status"] == "kept"
    assert store.get_tuning("same") is None  # a shared name can't pick one


def test_old_name_keyed_tuning_records_move_to_their_job_id(isolated_plugin_storage):
    conn = isolated_plugin_storage
    conn.execute("INSERT INTO tuning VALUES ('job', ?, 1)", (json.dumps({"job_name": "job", "job_id": "j1", "status": "kept"}),))
    store._migrate_tuning_keys(conn)
    assert [r[0] for r in conn.execute("SELECT job_name FROM tuning")] == ["j1"]


def test_one_failing_job_does_not_stop_the_others(monkeypatch):
    store.save_tuning("a", {"job_name": "a", "job_id": "a", "status": "trial", "started_at": 1})
    store.save_tuning("b", {"job_name": "b", "job_id": "b", "status": "trial", "started_at": 1})
    decided = []

    def decide(t):
        decided.append(t["job_id"])
        if t["job_id"] == "a":
            raise lifecycle.LifecycleError("Hermes couldn't update the job")

    for name, value in (("_migrate_legacy", lambda t: False), ("_configuration_drift", lambda t: False), ("_decide", decide)):
        monkeypatch.setattr(tuning, name, value)
    monkeypatch.setattr(lifecycle, "recover_promotions", lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    monkeypatch.setattr(lifecycle, "refresh_drift", lambda: None)
    tuning.refresh()
    assert sorted(decided) == ["a", "b"]


def test_a_hermes_edit_timeout_is_a_normal_failure(monkeypatch):
    import subprocess

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired("hermes", 60)

    monkeypatch.setattr(lifecycle.subprocess, "run", slow)
    monkeypatch.setattr(lifecycle, "_hermes_cmd", lambda: ["hermes"])
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle._edit_job("j", "--prompt", "x")


def test_brake_count_survives_a_new_session_id(monkeypatch):
    monkeypatch.setattr(brake, "enabled", lambda: True)
    monkeypatch.setattr(brake, "plan", lambda job: {"limit": 100, "normal": 10, "why": None})
    monkeypatch.setattr(store, "add_brake_stop", lambda *a: None)
    brake.note_usage("run-1", {"total_tokens": 500})
    try:
        assert brake.check("job", "session-after-compression", "run-1")["action"] == "block"
    finally:
        brake.forget("run-1")


@pytest.mark.parametrize("args,tool", [
    ({"command": "grep '{{crystal:' docs"}, "terminal"),
    ({"old_string": "{{crystal:", "new_string": "x"}, "patch"),
    ({"content": "Use {{crystal:id}} here"}, "write_file"),
])
def test_text_about_placeholders_is_not_blocked(args, tool):
    assert handoffs.expand_args(args, "s", "t", tool) is None
    blocked = capture.make_pre_tool_call(SimpleNamespace(current_cron_execution=lambda: None))
    assert blocked(session_id="s", args=args, tool_name=tool) is None


def test_an_interrupted_auction_can_be_cleared_only_on_purpose():
    with store._lock:
        conn = store._db()
        conn.executescript(auction._SCHEMA)
        conn.execute("INSERT INTO auction_profile_locks VALUES (?, ?, ?)", (auction._profile_key(), "a1", 1))
        conn.commit()
    assert auction.locked()["auction_id"] == "a1"
    with pytest.raises(auction.AuctionError):
        auction.reconcile()
    assert auction.reconcile(confirmed=True)["cleared"]
    assert auction.locked() is None


def test_a_reused_call_id_only_removes_the_text_that_was_removed():
    result_id = cs._keep_full("first", "s")
    store.add_eviction("s", "c1", result_id, rent._digest("first"))
    out = rent.apply({"messages": [{"role": "tool", "tool_call_id": "c1", "content": "first"},
                                   {"role": "tool", "tool_call_id": "c1", "content": "second"}]}, "s")
    assert result_id in out["messages"][0]["content"] and out["messages"][1]["content"] == "second"


def test_a_cache_hit_says_it_is_cached_and_keeps_json_valid():
    assert json.loads(fetch_cache.mark('{"a": 1}', 90)) == {fetch_cache.CACHE_MARKER: {"age_seconds": 90}, "a": 1}
    assert fetch_cache.mark("plain", 5).endswith("\nplain")
    assert fetch_cache.is_marked(fetch_cache.mark("plain", 5)) and not fetch_cache.is_marked("plain")


def test_a_cache_longer_than_the_schedule_is_refused(monkeypatch):
    monkeypatch.setattr(lifecycle, "cron_job", lambda ref: {"id": "j", "schedule": {"kind": "interval", "minutes": 30}})
    with pytest.raises(ValueError):
        fetch_cache.configure("j", 3600)
    assert fetch_cache.configure("j", 600)["ttl_seconds"] == 600


def test_a_helper_problem_pause_outlasts_the_turn():
    key = (cs._session_key("s"), "p", "m")
    cs._pauses[key] = cs.time.time() + 600
    try:
        cs.cleanup_session("s")
        assert key in cs._pauses
    finally:
        cs._pauses.pop(key, None)


def test_a_database_error_never_loses_a_paid_helper_answer(monkeypatch):
    store.set_chat_enabled("s", True)
    monkeypatch.setattr(store, "add_chat_event", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    answer = json.loads(handoffs.execute(SimpleNamespace(), "", "s", "t"))
    assert "error" in answer and "database" not in answer["error"]


def test_chats_with_crystallize_off_write_no_usage_rows(monkeypatch):
    hook = capture.make_post_api_request(SimpleNamespace(current_cron_execution=lambda: None))
    hook(session_id="off", usage={"prompt_tokens": 5000, "output_tokens": 1})
    assert store.get_setting(handoffs.usage_key("off")) is None


# ------------------------------------------------------------------ savings


@pytest.mark.parametrize("request_body,effort", [
    ({"reasoning_effort": "HIGH"}, "high"),
    ({"reasoning": {"effort": "low"}}, "low"),
    ({"extra_body": {"reasoning": {"effort": "max"}}}, "max"),
    ({"thinking": {"type": "enabled", "budget_tokens": 2000}}, "budget:2000"),
    ({"thinking": {"type": "disabled"}}, "none"),
    ({"generationConfig": {"thinkingConfig": {"thinkingBudget": 512}}}, "budget:512"),
    ({"messages": []}, request_settings.UNSET),
])
def test_reasoning_is_read_from_any_provider_shape(request_body, effort):
    assert request_settings.settings_of(request_body)["effort"] == effort


def test_a_run_that_changes_reasoning_midway_establishes_no_setting():
    request_settings.record("e1", {"reasoning_effort": "high"})
    request_settings.record("e1", {"reasoning_effort": "high"})
    assert request_settings.for_run("e1")["effort"] == "high"
    request_settings.record("e1", {"reasoning_effort": "low"})
    assert request_settings.for_run("e1")["effort"] == request_settings.MIXED


def test_a_recorded_request_makes_a_cron_run_comparable():
    request_settings.record("e1", {"reasoning_effort": "high"})
    run = history.Run(execution_id="e1", job_id="j", job_name="j", session_id="s", scheduled=True, started_ts=1, model="m")
    history._apply_session(run, {"billing_provider": "p", "model": "m", "model_config": None})
    assert history.comparison_identity(run)[:3] == ("m", "p", "high")


def test_the_request_hook_records_settings_without_changing_the_request():
    ctx = SimpleNamespace(current_cron_execution=lambda: SimpleNamespace(execution_id="e2", job_id="j", job_name="j"))
    body = {"messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "max"}
    assert capture.make_llm_request(ctx)(request=body, session_id="s") is None
    assert request_settings.for_run("e2")["effort"] == "max"


def _run(execution, cost, *, effort="high", outcome="completed", started=10.0, api=None, output=None, tokens=None):
    return SimpleNamespace(execution_id=execution, job_id="j", session_id=execution, scheduled=True, finished=True,
                           started_ts=started, outcome=outcome, cost_usd=cost, api_cost_usd=api, model="m", provider="p",
                           reasoning_effort=effort, reasoning_config_fingerprint=None, identity_attribution_complete=True,
                           usage_totals_complete=True, included=False, output_tokens=output, total_tokens=tokens)


def test_lower_reasoning_is_never_money_saved():
    before = [_run("a", 8.0), _run("b", 8.0)]
    assert overview._settings_match(before, [_run("c", 1.0, effort="low")]) == []
    assert len(overview._settings_match(before, [_run("d", 1.0)])) == 1


def test_identical_runs_show_no_saving(monkeypatch):
    mix = [_run("a", 10.0), _run("b", 1.0), _run("c", 1.0)]
    later = [_run("d", 10.0, started=20), _run("e", 1.0, started=20), _run("f", 1.0, started=20)]
    monkeypatch.setattr(history, "runs_for_job", lambda job, only_usable=False: mix)
    view = overview._saving_view("j", 15, later, "cost_usd")
    assert overview._saved_since(view["runs"], view["baseline"]) == pytest.approx(0)
    assert view["rate_per_run"] == pytest.approx(0)


def test_one_run_gives_no_weekly_rate(monkeypatch):
    monkeypatch.setattr(history, "runs_for_job", lambda job, only_usable=False: [_run("a", 10.0)])
    assert overview._saving_view("j", 15, [_run("b", 1.0, started=20)], "cost_usd")["rate_per_run"] is None


def test_a_run_with_no_outcome_is_not_credited_as_a_saving():
    assert history.net_saving(SimpleNamespace(outcome=None), 10.0, 4.0) == -4.0


def test_fewer_tokens_at_a_higher_price_is_not_a_saving():
    before, trial = [_run("a", None, api=0.048, output=500)], [_run("b", None, api=0.075, output=900)]
    assert "listed price" in tuning._tokens_mislead(before, trial)


def test_time_alone_is_not_a_saving(monkeypatch):
    monkeypatch.setattr(history, "comparable_runs", lambda a, b: True)
    fast = SimpleNamespace(cost_usd=None, api_cost_usd=None, total_tokens=None, duration_s=1.0)
    slow = SimpleNamespace(cost_usd=None, api_cost_usd=None, total_tokens=None, duration_s=9.0)
    assert tuning._measure([slow], [fast])[0] == "nothing"


def test_proof_needs_enough_runs_beyond_their_spread():
    assert not receipts._beyond_noise([0.01])
    assert not receipts._beyond_noise([1.0, -1.0, 1.0, -1.0, 1.0])
    assert receipts._beyond_noise([1.0, 1.1, 0.9, 1.0, 1.05])


def test_handoff_net_charges_the_main_model_for_typing_the_input(monkeypatch):
    store.add_chat_event("s", kind="routed", results=0, main_output_chars=48_000)
    prices = {"input": 1e-6, "output": 1.5e-5, "cache_read": 1e-7, "cache_write": 1e-6}
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda m, p: prices)
    monkeypatch.setattr(cs, "_main_model", lambda sid: {"model": "m", "provider": "p"})
    monkeypatch.setattr(cs, "status_for", lambda sid: {"enabled": False})
    monkeypatch.setattr(cs, "_chat_title", lambda sid: None)
    monkeypatch.setattr(overview, "_chat_spent", lambda sid: None)
    card = cs.summary("s")["this_chat"]
    assert card["estimated_main_typing_usd"] == pytest.approx(12_000 * 1.5e-5)
    assert card["net_once"] == pytest.approx(-12_000 * 1.5e-5)


def test_reading_an_original_back_is_charged():
    result_id = cs._keep_full("x" * 1000, "s")
    cs.read_full(result_id, session_id="s")
    assert store.list_chat_savings("s")[0]["added_input_chars"] > 1000


def test_whitespace_removal_is_not_counted_as_many_tokens():
    text = json.dumps({"key": ["value"] * 50}, indent=8)
    compact = json.dumps({"key": ["value"] * 50}, separators=(",", ":"))
    assert len(text) - len(compact) > 400
    assert estimate.tokens(text) - estimate.tokens(compact) == 0


def test_token_estimates_learn_from_real_usage():
    words = "alpha beta gamma " * 2000
    estimate.note_request("s", {"messages": [{"role": "user", "content": words}]})
    estimate.note_usage("s", "m", {"prompt_tokens": 12000})
    assert estimate.calibrated("m") and not estimate.calibrated("other")
    assert estimate.tokens("alpha beta", "m") == 4     # this model's real ratio is about 2 tokens a word
    assert estimate.chars_per_token("m") < estimate.FALLBACK_CHARS_PER_TOKEN + 1


def test_an_unpriced_helper_stays_off_unless_allowed(monkeypatch):
    store.set_setting(cs.UNPRICED_SETTING, False)
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda m, p: None)
    monkeypatch.setattr(cs, "_main_model", lambda sid: {"model": "main", "provider": "p"})
    picked = {"model": "helper", "provider": "p"}
    assert not cs._is_cheaper(picked, "s") and not cs._worth_condensing(50_000, picked, "s")
    store.set_setting(cs.UNPRICED_SETTING, True)
    assert cs._is_cheaper(picked, "s") and cs._worth_condensing(50_000, picked, "s")


def test_a_helper_inherits_the_main_reasoning(monkeypatch):
    sent = []
    monkeypatch.setattr(cs.modelinfo, "_config", lambda: {"agent": {"reasoning_effort": "high"}})
    monkeypatch.setattr(cs.lifecycle, "_hermes_cmd", lambda: ["hermes"])
    monkeypatch.setattr(cs.lifecycle, "hermes_env", lambda: {})
    monkeypatch.setattr(cs.subprocess, "run", lambda cmd, **k: sent.append(cmd) or SimpleNamespace(returncode=0, stderr="", stdout=""))
    cs.set_model("p", "m")
    assert json.loads(sent[0][-1])["reasoning_effort"] == "high"
