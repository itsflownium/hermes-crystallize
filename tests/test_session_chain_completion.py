"""Compression is one execution, while delegation/forks are separate work.

Fixtures mirror the read-only Hermes sessions/messages/session_model_usage schema.
No installed Hermes modules, provider requests, or live databases are involved.
"""

import json
import sqlite3

import pytest

from crystallize import chat, history, receipts, store


@pytest.fixture
def sessions(monkeypatch, tmp_path):
    home = tmp_path / "chain-home"
    home.mkdir()
    monkeypatch.setattr(history, "_hermes_home", lambda: home)
    db = sqlite3.connect(home / "state.db")
    db.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, source TEXT, parent_session_id TEXT, end_reason TEXT,
            model TEXT, billing_provider TEXT, model_config TEXT, started_at REAL, ended_at REAL,
            input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,
            cache_read_tokens INTEGER, cache_write_tokens INTEGER, api_call_count INTEGER,
            actual_cost_usd REAL, estimated_cost_usd REAL, cost_status TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT,
            timestamp REAL, _compressed_summary INTEGER DEFAULT 0, message_uid TEXT, tool_call_uids TEXT
        );
        CREATE TABLE session_model_usage (
            session_id TEXT, model TEXT, billing_provider TEXT, task TEXT,
            input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,
            cache_read_tokens INTEGER, cache_write_tokens INTEGER, api_call_count INTEGER,
            actual_cost_usd REAL, estimated_cost_usd REAL, cost_status TEXT
        );
    """)

    def add(sid, *, parent=None, reason="completed", model="any-model", provider="any-provider",
            effort="high", config=None, tokens=100, calls=1, cost=.1, source="cron", delta=True):
        config = config if config is not None else {"reasoning_config": {"effort": effort}}
        start = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, source, parent, reason, model, provider, json.dumps(config), start, start + 1,
                    tokens, 10, 5, 0, 0, calls, cost, cost, "actual"))
        if delta:
            db.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (sid, model, provider, "", tokens, 10, 5, 0, 0, calls, cost, cost, "actual"))
        db.commit()

    def answer(sid, text, *, summary=False, tools=None, uid=None):
        db.execute("INSERT INTO messages (session_id,role,content,tool_calls,timestamp,_compressed_summary,message_uid) "
                   "VALUES (?,'assistant',?,?,1,?,?)", (sid, text, json.dumps(tools) if tools else None, summary, uid))
        db.commit()

    yield db, add, answer
    db.close()


def test_full_answer_and_cost_follow_only_verified_continuation(sessions):
    _, add, answer = sessions
    add("root", reason="compression", cost=.1)
    add("tip", parent="root", tokens=200, cost=.2)
    add("delegate", parent="root", cost=99, config={"_delegate_from": "root"})
    add("branch", parent="root", cost=99, config={"_branched_from": "root"})
    add("reset", parent="root", cost=99, config={"_reset_from": "root"})
    add("tool", parent="root", cost=99, source="tool")
    answer("root", "Partial result")
    answer("tip", "Compressed context, not an answer", summary=True)
    text = "Complete original answer " + "detail " * 2000
    answer("tip", text)
    answer("delegate", "Unrelated delegated answer")

    assert history.final_answers(["root", "tip"]) == {"root": text, "tip": text}
    infos = history._session_costs(["root", "tip"])
    for sid in ("root", "tip"):
        assert infos[sid]["actual_cost_usd"] == pytest.approx(.3)
        assert infos[sid]["input_tokens"] == 300
        assert infos[sid]["session_ids"] == ["root", "tip"]
        run = history.Run("e", "j", "job", sid, True, 0)
        history._apply_session(run, infos[sid])
        assert run.finished and run.api_calls == 2
        assert run.provider == "any-provider" and run.reasoning_effort == "high"
        assert history.comparison_identity(run) is not None


def test_snapshot_only_compression_cost_is_unknown_instead_of_summed(sessions):
    _, add, answer = sessions
    add("root", reason="compression", cost=.1, delta=False)
    add("tip", parent="root", cost=.2, delta=False)
    answer("tip", "Complete answer")
    info = history._session_costs(["root"])["root"]
    assert info["actual_cost_usd"] is None and info["input_tokens"] is None
    assert not info["usage_totals_complete"]
    assert [row["actual_cost_usd"] for row in info["attribution_evidence"]["session_snapshots"]] == [.1, .2]
    assert history.final_answers(["root"])["root"] == "Complete answer"


def test_inherited_cumulative_snapshot_cannot_be_counted_twice(sessions):
    db, add, _ = sessions
    add("root", reason="compression", cost=.1)
    add("tip", parent="root", cost=.3, tokens=300, calls=2)
    db.execute("UPDATE session_model_usage SET input_tokens=200,api_call_count=1,actual_cost_usd=.2,estimated_cost_usd=.2 WHERE session_id='tip'")
    db.commit()
    info = history._session_costs(["root"])["root"]
    assert info["actual_cost_usd"] is None and info["estimated_cost_usd"] is None
    assert not info["usage_totals_complete"]
    assert "inherited snapshots" in " ".join(info["attribution_evidence"]["uncertainty"])


def test_ambiguous_compression_siblings_do_not_select_a_recent_answer(sessions):
    _, add, answer = sessions
    add("root", reason="compression")
    add("child-a", parent="root")
    add("child-b", parent="root")
    answer("child-b", "A later row is not proof of continuation")
    assert history.final_answers(["root"]) == {}
    info = history._session_costs(["root"])["root"]
    assert info["actual_cost_usd"] is None
    assert not info["attribution_evidence"]["chain_verified"]


def test_parent_link_without_compression_does_not_change_the_answer(sessions):
    _, add, answer = sessions
    add("parent", reason="completed", cost=.1)
    add("child", parent="parent", cost=99)
    answer("parent", "Original parent answer")
    answer("child", "Delegated answer")
    assert history.final_answers(["parent"])["parent"] == "Original parent answer"
    assert history._session_costs(["parent"])["parent"]["actual_cost_usd"] == .1


def test_reasoning_budget_and_route_changes_make_comparison_unproven(sessions):
    _, add, _ = sessions
    add("root", reason="compression", config={"reasoning_config": {"effort": "high", "budget_tokens": 8000}})
    add("tip", parent="root", config={"reasoning_config": {"effort": "high", "budget_tokens": 2000}})
    info = history._session_costs(["root"])["root"]
    run = history.Run("e", "j", "job", "root", True, 0)
    history._apply_session(run, info)
    assert run.cost_usd == .2
    assert run.reasoning_effort is None
    assert history.comparison_identity(run) is None


def test_mixed_main_provider_is_not_misattributed_to_first_route(sessions):
    db, add, _ = sessions
    add("root")
    db.execute("INSERT INTO session_model_usage VALUES ('root','other-model','other-provider','',50,5,2,0,0,1,.1,.1,'actual')")
    db.commit()
    info = history._session_costs(["root"])["root"]
    assert info["model"] is None and info["billing_provider"] is None
    assert not info["identity_attribution_complete"]


def test_auxiliary_spend_is_visible_and_not_treated_as_actual_invoice_zero(sessions):
    db, add, _ = sessions
    add("root", reason="compression")
    add("tip", parent="root")
    db.execute("INSERT INTO session_model_usage VALUES ('root','aux-model','aux-provider','compression',50,5,2,0,0,1,0,.05,'estimated')")
    db.commit()
    info = history._session_costs(["root"])["root"]
    assert info["estimated_cost_usd"] == pytest.approx(.25)
    assert info["actual_cost_usd"] is None
    assert info["api_call_count"] == 3
    assert info["attribution_evidence"]["usage_breakdown"][-1]["task"] == "compression"
    assert not info["identity_attribution_complete"]


def test_chat_tools_deduplicate_inherited_calls_and_keep_original_repeats(sessions):
    _, add, answer = sessions
    add("root", reason="compression")
    add("tip", parent="root")
    call = {"id": "call-1", "function": {"name": "read_file", "arguments": '{"path":"a"}'}}
    answer("root", "", tools=[call], uid="occurrence-1")
    answer("root", "", tools=[call], uid="occurrence-2")  # A real repeated invocation.
    answer("tip", "", tools=[call], uid="occurrence-2")   # Verbatim inherited tail.
    answer("tip", "", tools=[{**call, "id": "call-2"}], uid="occurrence-3")
    assert len(chat._tool_calls("root")) == 3


def test_provider_call_id_reuse_is_not_mistaken_for_inherited_context(sessions):
    _, add, answer = sessions
    add("root", reason="compression")
    add("tip", parent="root")
    call = {"id": "provider-call-0", "function": {"name": "read_file", "arguments": "{}"}}
    answer("root", "", tools=[call], uid="message-a")
    answer("tip", "", tools=[call], uid="message-b")
    assert len(chat._tool_calls("root")) == 2


def test_comparable_runs_reject_unknown_and_reasoning_changes():
    def run(effort="high", provider="p", model="m"):
        return history.Run("e", "j", "job", "s", True, 0, model=model,
                           provider=provider, reasoning_effort=effort)
    assert history.comparable_runs([run()], [run()])
    assert not history.comparable_runs(run(), run("low"))
    assert not history.comparable_runs(run(), run(provider="other"))
    assert not history.comparable_runs(run(provider=None), run(provider=None))
    assert not history.comparable_runs([], [run()])


def test_same_chain_cannot_supply_cost_for_two_execution_bindings(sessions):
    _, add, _ = sessions
    add("root", reason="compression")
    add("tip", parent="root")
    for execution, sid in (("e1", "root"), ("e2", "tip")):
        store.add_trace({"ts": 1, "session_id": sid, "tool_name": "__run__", "cron_job_id": "j",
                         "cron_execution_id": execution, "cron_scheduled": 1})
    rows = history.load_runs("j")
    assert len(rows) == 2
    assert all(run.cost_usd is None and not run.usage_totals_complete for run in rows)
    # Re-reading valid session rows must not override a conflicting execution binding.
    receipt = receipts.receipt("j", runs=rows)
    assert receipt["recorded_spend"]["actual"]["total_usd"] is None
    assert all(not row["comparison_settings_verified"] for row in receipt["runs"])


def test_verbatim_inherited_fork_marker_stays_in_its_own_compression_chain(sessions):
    _, add, answer = sessions
    add("original", reason="compression")
    fork_config = {"_branched_from": "original", "reasoning_config": {"effort": "high"}}
    add("branch", parent="original", reason="compression", config=fork_config)
    add("branch-tip", parent="branch", config=fork_config)
    answer("branch-tip", "Branch continuation answer")
    assert history.final_answers(["branch"]) == {"branch": "Branch continuation answer"}
    assert history._session_costs(["branch"])["branch"]["session_ids"] == ["branch", "branch-tip"]


def test_job_filtered_view_still_rejects_foreign_execution_on_same_chain(sessions):
    _, add, _ = sessions
    add("root", reason="compression")
    add("tip", parent="root")
    for execution, sid, job in (("e1", "root", "job-a"), ("e2", "tip", "job-b")):
        store.add_trace({"ts": 1, "session_id": sid, "tool_name": "__run__", "cron_job_id": job,
                         "cron_execution_id": execution, "cron_scheduled": 1})
    [run] = history.load_runs("job-a")
    assert run.cost_usd is None and run.execution_binding == "conflicting_session_binding"


def test_default_zero_in_usage_ledger_is_not_a_reported_price(sessions):
    db, add, _ = sessions
    add("root", reason="compression")
    add("tip", parent="root")
    db.execute("UPDATE sessions SET actual_cost_usd=NULL,estimated_cost_usd=0,cost_status='unknown'")
    db.execute("UPDATE session_model_usage SET actual_cost_usd=0,estimated_cost_usd=0,cost_status=NULL")
    db.commit()
    info = history._session_costs(["root"])["root"]
    assert info["actual_cost_usd"] is None and info["estimated_cost_usd"] is None
