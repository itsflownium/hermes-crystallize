"""Offline execution-boundary regressions for Chat helpers and original recovery."""

import json
import os
import sqlite3
import sys
import threading
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

import pytest

from crystallize import chat, chat_policy, chat_savings as cs, overview, rent, store, tools


BIG = json.dumps({"output": "\n".join(f"record {i}: value {i * 7}" for i in range(1500)), "exit_code": 0})
PICKED = {"provider": "p", "model": "cheap"}
PRICES = {"cheap": {"input": 1e-7, "output": 2e-7},
          "main": {"input": 1e-6, "output": 8e-6}}


class Helper:
    def __init__(self, text="Record 123: value 861.", usage=None, error=None, provider="p", model="cheap"):
        self.calls = []
        self.text, self.error = text, error
        self.usage = usage if usage is not None else SimpleNamespace(input_tokens=1000, output_tokens=100)
        self.provider, self.model = provider, model
        self.llm = SimpleNamespace(complete=self.complete)

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text, usage=self.usage, provider=self.provider, model=self.model)


@pytest.fixture
def engine(monkeypatch, tmp_path):
    originals = tmp_path / "originals"
    originals.mkdir()
    monkeypatch.setattr(cs, "_full_dir", lambda: originals)
    monkeypatch.setattr(cs, "chosen_model", lambda: dict(PICKED))
    monkeypatch.setattr(cs, "_main_model", lambda sid: {"model": "main", "provider": "p"})
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda model, provider: PRICES.get(model))
    monkeypatch.setattr(cs, "_pauses", {})
    monkeypatch.setattr(cs, "_seen", {})
    monkeypatch.setattr(cs, "_filled", {})
    store.set_chat_enabled("s1", True)
    chat_policy.set_policy("s1", "*", "summary")
    return originals


@pytest.mark.parametrize("session_id", ["", "s2"])
def test_all_helper_boundaries_require_opt_in(engine, session_id):
    helper = Helper()
    result_id = cs._keep_full("private original", "s1")
    assert "off" in json.loads(cs.ask_cheap(helper, "write", session_id))["error"]
    assert "off" in json.loads(cs.ask_full(helper, result_id, "question", session_id))["error"]
    assert cs.shrink(helper, session_id, "terminal", BIG) is None
    assert cs._call(helper, [], "test", session_id=session_id)[0] is None
    assert not helper.calls and not store.chat_events(session_id)


def test_runtime_session_cannot_be_spoofed_by_tool_arguments(engine, monkeypatch):
    helper = Helper()
    monkeypatch.setattr(tools, "_ctx", helper)
    response = tools.crystal_cheap({"input": "write", "session_id": "s1"}, session_id="s2")
    assert "off" in json.loads(response)["error"] and not helper.calls


def test_middleware_identity_supplies_turn_omitted_by_host_handler(engine, monkeypatch):
    helper = Helper()
    monkeypatch.setattr(tools, "_ctx", helper)
    with tools.caller_context(session_id="s1", turn_id="host-turn", enabled_toolsets=["web"]):
        assert json.loads(tools.crystal_cheap({"input": "write"}, session_id="s1"))["output"]
    assert store.chat_turn_savings("s1")[0]["turn_id"] == "host-turn"
    assert tools._caller.get() == {}


def test_exact_recovery_works_while_off_but_only_for_owner(engine, monkeypatch):
    helper = Helper()
    monkeypatch.setattr(tools, "_ctx", helper)
    result_id = cs._keep_full("private original", "s1")
    store.set_chat_enabled("s1", False)
    args = {"result_id": result_id}
    assert json.loads(tools.crystal_full_result(args, session_id="s1"))["result"] == "private original"
    assert "error" in json.loads(tools.crystal_full_result(args, session_id="s2"))
    assert "off" in json.loads(tools.crystal_full_result({**args, "question": "q"}, session_id="s1"))["error"]
    assert not helper.calls


def test_context_rent_uses_owned_originals_and_falls_back_after_expiry(engine, monkeypatch):
    rows = [{"role": "tool", "tool_call_id": "c1", "content": BIG}]
    monkeypatch.setattr(rent, "_read_chat", lambda sid: (rows, ""))
    store.set_chat_enabled("s1", False)
    assert rent.evict("s1", "c1")["ok"]
    result_id = store.evictions("s1")["c1"]
    assert json.loads(cs.read_full(result_id, session_id="s1"))["result"] == BIG[:cs.MAX_FULL_READ]
    request = {"messages": [{"role": "tool", "tool_call_id": "c1", "content": BIG}]}
    assert result_id in rent.apply(request, "s1")["messages"][0]["content"]
    monkeypatch.setattr(cs.time, "time", lambda: 10**12)
    assert rent.apply(request, "s1") is None
    assert request["messages"][0]["content"] == BIG and not store.evictions("s1")


def test_originals_expire_on_read_without_another_write(engine, monkeypatch):
    result_id = cs._keep_full("short text", "s1")
    path = cs._session_dir("s1") / f"{result_id}.json"
    expiry = json.loads(path.read_text())["expires_at"]
    monkeypatch.setattr(cs.time, "time", lambda: expiry + 1)
    assert "expired" in json.loads(cs.read_full(result_id, session_id="s1"))["error"]
    assert not path.exists()
    assert "expired" in cs.fill_placeholders(f"{{{{crystal:{result_id}}}}}", session_id="s1")


def test_original_publish_is_atomic_private_and_immutable(engine, monkeypatch):
    replace = os.replace
    published = []

    def observe(source, destination):
        path = Path(destination)
        assert not path.exists()
        assert json.loads(Path(source).read_text())["text"] == BIG
        published.append(path)
        replace(source, destination)

    monkeypatch.setattr(cs.os, "replace", observe)
    first = cs._keep_full(BIG, "s1")
    second = cs._keep_full(BIG, "s1")
    assert first != second and len(published) == 2
    assert published[0].stat().st_mode & 0o777 == 0o600
    assert not list(engine.rglob(".original-*"))
    assert "error" in json.loads(cs.read_full(first, session_id="s2"))


def test_legacy_original_requires_trusted_eviction_ownership(engine):
    result_id = "0123456789abcdef"
    (engine / f"{result_id}.txt").write_text("legacy private text")
    assert "error" in json.loads(cs.read_full(result_id, session_id="s1"))
    store.add_eviction("s1", "call", result_id)
    assert json.loads(cs.read_full(result_id, session_id="s1"))["result"] == "legacy private text"
    assert "error" in json.loads(cs.read_full(result_id, session_id="s2"))


def test_policy_focus_reaches_helper_and_exact_mode_never_cleans(engine, monkeypatch):
    chat_policy.set_policy("s1", "terminal", "summary", "Find the failed release and its version")
    helper = Helper()
    assert json.loads(cs.shrink(helper, "s1", "terminal", BIG, turn_id="turn1"))["summary"]
    assert "Find the failed release" in helper.calls[0][0][1]["content"]
    chat_policy.set_policy("s1", "terminal", "exact")
    monkeypatch.setattr(cs.prerun, "clean_result", lambda *args: pytest.fail("exact policy cleaned a result"))
    assert cs.shrink(Helper(), "s1", "terminal", BIG) is None


def test_clean_policy_never_calls_helper(engine, monkeypatch):
    chat_policy.set_policy("s1", "terminal", "clean")
    helper = Helper()
    monkeypatch.setattr(cs.prerun, "clean_result", lambda tool, text: json.dumps({"output": "short"}))
    response = json.loads(cs.shrink(helper, "s1", "terminal", BIG))
    assert response["full_result_id"] and not helper.calls
    assert store.list_chat_savings("s1")[0]["helper_calls"] == 0


@pytest.mark.parametrize("tool,text", [("read_file", BIG), ("terminal", json.dumps({"output": BIG, "exit_code": 1})),
                                      ("terminal", json.dumps({"error": BIG}))])
def test_files_and_failures_are_exact_before_cleaning(engine, monkeypatch, tool, text):
    monkeypatch.setattr(cs.prerun, "clean_result", lambda *args: pytest.fail("failure/file was cleaned"))
    assert cs.shrink(Helper(), "s1", tool, text) is None


def test_numeric_and_timestamp_changes_cannot_be_deduplicated(engine):
    first = json.dumps({"output": "price=100.10; timestamp=2026-11-06T01:00:00Z\n" + "facts " * 300})
    changed = first.replace("100.10", "100.20").replace("01:00:00", "02:00:00")
    assert cs._repeat_note("s1", "terminal", first, first) is None
    assert cs._repeat_note("s1", "terminal", changed, changed) is None
    assert json.loads(cs._repeat_note("s1", "terminal", first, first))["same_as_earlier_result"]


def test_rejected_and_failed_summaries_remain_in_accounting(engine):
    helper = Helper(text="y" * len(BIG))
    cs.shrink(helper, "s1", "terminal", BIG, turn_id="t1")
    cs.shrink(Helper(error=RuntimeError("request failed")), "s1", "terminal", BIG + " ", turn_id="t2")
    row = store.list_chat_savings("s1")[0]
    assert row["helper_calls"] == 2 and row["rejected_calls"] == 1 and row["failed_calls"] == 1
    assert row["known_cheap_cost"] == pytest.approx(0.00012)
    assert row["cheap_cost"] is None and row["unknown_cost_calls"] == 1
    assert {row["turn_id"] for row in store.chat_turn_savings("s1")} == {"t1", "t2"}


def test_questions_are_counted_and_inputs_are_never_silently_truncated(engine):
    helper = Helper()
    result_id = cs._keep_full("value 123", "s1")
    assert json.loads(cs.ask_full(helper, result_id, "value?", "s1", "turn1"))["answer"]
    assert store.chat_savings_breakdown("s1")[0]["kind"] == "question"
    too_big = "x" * (cs.MAX_INPUT_CHARS + 1)
    assert "error" in json.loads(cs.ask_cheap(helper, too_big, "s1"))
    result_id = cs._keep_full(too_big, "s1")
    assert "error" in json.loads(cs.ask_full(helper, result_id, "q", "s1"))
    assert len(helper.calls) == 1


def test_unknown_prices_and_explicit_host_zero_are_distinct(engine, monkeypatch):
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda *args: None)
    assert cs._usage_cost(SimpleNamespace(input_tokens=50, output_tokens=10), PICKED) == (60, None)
    assert cs._usage_cost(SimpleNamespace(input_tokens=50, output_tokens=10, cost_usd=0.0), PICKED) == (60, 0.0)
    cs.ask_cheap(Helper(), "write", "s1", "turn1")
    assert store.list_chat_savings("s1")[0]["cheap_cost"] is None
    assert store.list_chat_savings("s1")[0]["unknown_cost_calls"] == 1


def test_timeout_stays_unknown_until_late_usage_is_available(engine, monkeypatch):
    release, resolved = threading.Event(), threading.Event()
    helper = Helper()
    original_complete = helper.complete
    helper.llm.complete = lambda *args, **kwargs: (release.wait(2), original_complete(*args, **kwargs))[1]
    monkeypatch.setattr(cs, "CONDENSE_SECONDS", 0.02)
    update = store.update_chat_event

    def observed_update(event_id, **fields):
        update(event_id, **fields)
        if fields.get("status") == "timeout_finished":
            resolved.set()

    monkeypatch.setattr(store, "update_chat_event", observed_update)
    answer, _ = cs._call(helper, [], "slow", session_id="s1", picked=PICKED)
    assert answer is None
    row = store.list_chat_savings("s1")[0]
    assert row["cheap_cost"] is None and row["failed_calls"] == 1
    release.set()
    assert resolved.wait(2)
    row = store.list_chat_savings("s1")[0]
    assert row["cheap_cost"] == pytest.approx(0.00012) and row["helper_calls"] == 1


def test_worker_preserves_contextvars(engine):
    profile = ContextVar("test-active-profile", default="launch")
    helper = Helper()
    observed = []
    complete = helper.complete
    helper.llm.complete = lambda *args, **kwargs: (observed.append(profile.get()), complete(*args, **kwargs))[1]
    token = profile.set("routed")
    try:
        cs.ask_cheap(helper, "write", "s1")
    finally:
        profile.reset(token)
    assert observed == ["routed"]


def test_routed_generation_benefit_requires_used_owned_placeholder(engine):
    answer = json.loads(cs.ask_cheap(Helper(text="long table " * 300), "write", "s1", "t1"))
    assert store.list_chat_savings("s1")[0]["output_chars"] == 0
    assert "unavailable" in cs.fill_placeholders(answer["placeholder"], session_id="s2", turn_id="t1")
    assert cs.fill_placeholders(answer["placeholder"], session_id="s1", turn_id="t1") == answer["output"]
    first = store.list_chat_savings("s1")[0]["output_chars"]
    cs.fill_placeholders(answer["placeholder"], session_id="s1", turn_id="t1")
    assert first > 0 and store.list_chat_savings("s1")[0]["output_chars"] == first


def test_unknown_helper_cost_makes_all_net_cards_unknown(engine, monkeypatch):
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda *args: None)
    cs.ask_cheap(Helper(), "write", "s1", "t1")
    monkeypatch.setattr(cs, "status_for", lambda sid: {"enabled": True})
    monkeypatch.setattr(cs, "_chat_title", lambda sid: None)
    monkeypatch.setattr(chat, "analyse", lambda sid: {})
    monkeypatch.setattr(overview, "_chat_spent", lambda sid: None)
    report = cs.summary("s1")
    assert report["this_chat"]["net_once"] is None
    assert report["turns"][0]["cheap_cost"] is None


def test_routing_learns_expensive_or_slow_helpers(engine):
    assert cs._routing_pays(PICKED, "s1")
    store.add_chat_event("s1", kind="routed", helper_call=True, cheap_cost=2.0,
                         output_tokens=100, duration_ms=1000, **PICKED)
    assert not cs._routing_pays(PICKED, "s1")
    for _ in range(3):
        store.add_chat_event("s2", kind="routed", helper_call=True, cheap_cost=0.001,
                             output_tokens=1000, duration_ms=30000, **PICKED)
    assert cs._routing_pays(PICKED, "s2")  # output-sized handoffs can exceed the 20s summary deadline
    for _ in range(3):
        store.add_chat_event("s3", kind="routed", helper_call=True, cheap_cost=0.001,
                             output_tokens=1000, duration_ms=180000, **PICKED)
    assert not cs._routing_pays(PICKED, "s3")


def test_legacy_totals_migrate_once_and_ambiguous_zero_is_unknown(isolated_plugin_storage):
    conn = isolated_plugin_storage
    conn.execute("INSERT INTO chat_savings VALUES ('old', 1000, 2, 100, 0, 1)")
    store._migrate_chat_savings(conn)
    store._migrate_chat_savings(conn)
    row = store.list_chat_savings("old")[0]
    assert row["chars"] == 1000 and row["results"] == 2
    assert row["cheap_cost"] is None and len(store.chat_events("old")) == 1


def test_plugin_connections_follow_active_profile(monkeypatch, tmp_path):
    from crystallize import history

    active = ContextVar("test-home", default=tmp_path / "profile1")
    connections = []

    def plugin_db(name):
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        connections.append(conn)
        return conn

    monkeypatch.setitem(sys.modules, "plugins", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "plugins.plugin_storage", SimpleNamespace(plugin_db=plugin_db))
    monkeypatch.setattr(history, "_hermes_home", lambda: active.get())
    monkeypatch.setattr(store, "_conn", None)
    monkeypatch.setattr(store, "_connections", {})
    try:
        store.set_chat_enabled("same-id", True)
        token = active.set(tmp_path / "profile2")
        assert not store.chat_enabled("same-id")
        store.set_chat_enabled("same-id", False)
        active.reset(token)
        assert store.chat_enabled("same-id") and len(connections) == 2
    finally:
        for conn in connections:
            conn.close()


def test_late_accounting_is_pinned_and_tolerates_orderly_store_close(monkeypatch):
    first = sqlite3.connect(":memory:")
    second = sqlite3.connect(":memory:")
    first.executescript(store._SCHEMA)
    second.executescript(store._SCHEMA)
    monkeypatch.setattr(store, "_conn", first)
    event = store.add_chat_event("profile1", kind="summary", helper_call=True, cheap_cost=None)
    update = store.chat_event_updater(event)
    monkeypatch.setattr(store, "_conn", second)
    replacement = store.add_chat_event("profile2", kind="summary", helper_call=True, cheap_cost=None)
    assert event == replacement  # local SQLite ids may be reused in another store
    update(status="rejected", cheap_cost=0.01)
    assert first.execute("SELECT status FROM chat_saving_events").fetchone()[0] == "rejected"
    assert second.execute("SELECT status FROM chat_saving_events").fetchone()[0] == "accepted"
    with store._lock:
        first.close()
    update(status="timeout_finished", cheap_cost=0.02)  # no write is retargeted to profile2
    assert second.execute("SELECT cheap_cost FROM chat_saving_events").fetchone()[0] is None
    with store._lock:
        second.close()


def test_nested_execution_is_pending_guidance_until_public_host_guards_are_available(monkeypatch):
    monkeypatch.setattr(tools, "_ctx", SimpleNamespace(dispatch_tool=lambda *args, **kw: pytest.fail("raw SDK dispatch used")))
    routine = {"id": "r", "source_ref": "job", "status": "live", "name": "routine",
               "steps": {"steps": [{"tool": "web_extract", "args": {"url": "https://example.com"}}]},
               "meta": {"config_snapshot": {"fingerprint": "unchanged"}}}
    monkeypatch.setattr(store, "get_routine", lambda rid: routine)
    monkeypatch.setattr(tools.lifecycle, "check_drift", lambda saved: True)
    monkeypatch.setattr(tools.lifecycle, "live_result", lambda *args: pytest.fail("guidance was reported as an execution"))
    monkeypatch.setattr(tools.runner, "run", lambda *args: pytest.fail("unsupported nested replay ran"))
    result = json.loads(tools.crystal_run({"routine_id": "r"}, session_id="s1", task_id="task", turn_id="turn", user_task="read"))
    assert not result["ok"] and result["status"] == "pending_host_tools" and not result["completed"]
    assert result["outputs"] == [] and result["execution_validation"] == "not_run"
    assert result["host_tool_plan"]["actions"][0]["arguments"] == {"url": "https://example.com"}
    assert tools._caller.get() == {}


def test_direct_routine_call_invalidates_drift_without_dispatching(monkeypatch):
    routine = {"id": "r", "source_ref": "job", "status": "live", "name": "routine", "steps": {},
               "meta": {"config_snapshot": {"fingerprint": "old", "configuration": {"prompt": "old target"}}}}
    invalidated = []
    monkeypatch.setattr(store, "get_routine", lambda rid: routine)
    monkeypatch.setattr(tools.lifecycle, "_bound_job", lambda job_id: {"id": "job", "prompt": "new target"})
    monkeypatch.setattr(tools.lifecycle, "configuration_fingerprint",
                        lambda job, recipe: {"fingerprint": "new", "configuration": {"prompt": job["prompt"]}})
    monkeypatch.setattr(tools.lifecycle, "invalidate", lambda *args, **kw: invalidated.append((args, kw)))
    monkeypatch.setattr(tools.runner, "run", lambda *args: pytest.fail("drifted routine ran steps"))
    monkeypatch.setattr(tools, "dispatch", lambda *args: pytest.fail("drifted routine dispatched a tool"))
    monkeypatch.setattr(tools.lifecycle, "live_result", lambda *args: pytest.fail("drift became a live-run result"))
    result = json.loads(tools.crystal_run({"routine_id": "r"}, session_id="s1"))
    assert not result["ok"] and result["failed_step"] == 0 and result["outputs"] == []
    assert "tested again" in result["reason"] and "Do the task yourself" in result["error"]
    assert invalidated[0][0] == ("r", "configuration changed: prompt")


def test_tool_listing_does_not_expose_rollback_prompts(monkeypatch):
    monkeypatch.setattr(store, "list_routines", lambda: [{"id": "r", "name": "routine", "status": "live", "steps": 1,
                                                          "meta": {"original_prompt": "private", "own_script": "private/path"}}])
    response = tools.crystal_list()
    assert "private" not in response and json.loads(response)["routines"][0]["id"] == "r"


def test_model_pair_is_saved_in_one_profile_scoped_operation(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cs.modelinfo, "_config", lambda: {"auxiliary": {cs.TASK: {"temperature": 0.1}}})
    monkeypatch.setattr(cs.lifecycle, "_hermes_cmd", lambda: ["hermes"])
    monkeypatch.setattr(cs.lifecycle, "hermes_env", lambda: {"HERMES_HOME": str(tmp_path)})
    monkeypatch.setattr(cs.subprocess, "run", lambda cmd, **kw: (calls.append((cmd, kw)), SimpleNamespace(returncode=0))[1])
    cs.set_model("p", "cheap")
    assert len(calls) == 1 and calls[0][1]["env"]["HERMES_HOME"] == str(tmp_path)
    assert json.loads(calls[0][0][-1]) == {"provider": "p", "model": "cheap", "temperature": 0.1}
