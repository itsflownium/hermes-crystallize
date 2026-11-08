"""Context Rent: cost per item, and removing a result only from what's sent to the AI."""

import copy
import json

import pytest

from crystallize import chat_savings, modelinfo, rent, store

BIG = "x" * 4000


def _row(role, content="", call_id=None, calls=None, tool=None):
    return {"role": role, "content": content, "tool_call_id": call_id, "tool_calls": calls, "tool_name": tool}


def _call(call_id, url):
    return json.dumps([{"id": call_id, "function": {"name": "web_extract", "arguments": json.dumps({"url": url})}}])


CHAT = [
    _row("user", "hello"),
    _row("assistant", "", calls=_call("c1", "https://e.com/old")),
    _row("tool", BIG, "c1", tool="web_extract"),
    _row("assistant", "ok"),
    _row("assistant", "", calls=_call("c2", "https://e.com/new")),
    _row("tool", BIG * 2, "c2", tool="web_extract"),
    _row("tool", "tiny", "c3", tool="terminal"),
]


@pytest.fixture
def chat(monkeypatch):
    removed = {}
    monkeypatch.setattr(rent, "_read_chat", lambda sid: (CHAT, "system prompt"))
    monkeypatch.setattr(chat_savings, "_main_model", lambda sid: {"model": "m", "provider": "p"})
    monkeypatch.setattr(modelinfo, "token_prices", lambda m, p: {"input": 1e-5, "output": 2e-5, "cache_read": 1e-6, "cache_write": 1.25e-5})
    monkeypatch.setattr(store, "evictions", lambda sid: removed)
    monkeypatch.setattr(store, "eviction_rows", lambda sid: {k: {"full_id": v, "content_sha256": None} for k, v in removed.items()})
    monkeypatch.setattr(store, "add_eviction", lambda sid, key, full, digest=None: removed.__setitem__(key, full))
    monkeypatch.setattr(store, "remove_eviction", lambda sid, key: removed.pop(key, None))
    monkeypatch.setattr(chat_savings, "_keep_full", lambda text, session_id: "abc123")
    monkeypatch.setattr(chat_savings, "_load_full", lambda result_id, session_id: BIG if result_id == "abc123" else None)
    return removed


def test_each_big_result_shows_its_rent_and_what_it_asked_for(chat):
    out = rent.report("s")
    assert [i["id"] for i in out["items"]] == ["c2", "c1"]
    new, old = out["items"]
    assert new["tokens"] == 2000 and new["what"] == "https://e.com/new"
    assert new["per_call_usd"] == pytest.approx(2000 * 1e-6) and new["paid_usd"] == 0
    assert old["paid_usd"] == pytest.approx(1000 * 1e-6 * 2)  # two AI calls have re-read it


def test_payback_counts_the_broken_cache(chat):
    new, old = rent.report("s")["items"]
    assert new["payback_calls"] == 1            # almost nothing after it to re-send
    assert old["payback_calls"] > new["payback_calls"]  # the newer result after it gets re-sent uncached


def test_small_results_and_messages_are_grouped(chat):
    groups = {g["name"] for g in rent.report("s")["groups"]}
    assert {"Instructions", "Your messages", "AI replies", "Small tool results"} <= groups


def test_no_prices_still_shows_sizes(chat, monkeypatch):
    monkeypatch.setattr(modelinfo, "token_prices", lambda m, p: None)
    out = rent.report("s")
    assert out["items"][0]["tokens"] == 2000 and out["items"][0]["per_call_usd"] is None
    assert out["items"][0]["payback_calls"] is None and not out["priced"]


def test_removing_changes_the_totals_and_can_be_undone(chat):
    full = rent.report("s")["tokens"]
    assert rent.evict("s", "c2")["ok"]
    out = rent.report("s")
    assert out["tokens"] == full - 2000 and out["removed_tokens"] == 2000 and out["items"][0]["removed"]
    rent.restore("s", "c2")
    assert rent.report("s")["tokens"] == full


def test_cant_remove_what_isnt_in_the_chat(chat):
    assert not rent.evict("s", "nope")["ok"] and not chat


def test_openai_style_request_keeps_its_shape_and_the_original(chat):
    chat["c1"] = "abc123"
    request = {"model": "m", "messages": [{"role": "assistant", "tool_calls": [{"id": "c1"}]},
                                          {"role": "tool", "tool_call_id": "c1", "content": BIG},
                                          {"role": "tool", "tool_call_id": "c2", "content": "keep"}]}
    original = copy.deepcopy(request)
    out = rent.apply(request, "s")
    assert request == original
    assert "abc123" in out["messages"][1]["content"] and BIG not in out["messages"][1]["content"]
    assert out["messages"][0]["tool_calls"] == [{"id": "c1"}] and out["messages"][2]["content"] == "keep"


def test_other_routes_are_handled(chat):
    chat["c1"] = "abc123"
    anthropic = {"messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": BIG}]}]}
    assert "abc123" in rent.apply(anthropic, "s")["messages"][0]["content"][0]["content"]
    codex = {"input": [{"type": "function_call_output", "call_id": "c1|fc_9", "output": BIG}]}
    assert "abc123" in rent.apply(codex, "s")["input"][0]["output"]
    bedrock = {"messages": [{"role": "user", "content": [{"toolResult": {"toolUseId": "c1", "content": [{"text": BIG}]}}]}]}
    assert "abc123" in rent.apply(bedrock, "s")["messages"][0]["content"][0]["toolResult"]["content"][0]["text"]


def test_nothing_removed_changes_nothing(chat):
    assert rent.apply({"messages": [{"role": "tool", "tool_call_id": "c1", "content": BIG}]}, "s") is None
    chat["other"] = "abc123"
    assert rent.apply({"messages": [{"role": "tool", "tool_call_id": "c1", "content": BIG}]}, "s") is None


def _long_chat(calls=8, big=BIG):
    rows = [_row("user", "start"), _row("assistant", "", calls=json.dumps([{"id": "old", "function": {"name": "terminal", "arguments": "{}"}}])),
            _row("tool", big, "old", tool="terminal")]
    for i in range(calls):
        rows += [_row("user", f"question {i}"), _row("assistant", "short answer")]
    return rows


@pytest.fixture
def auto(chat, monkeypatch):
    monkeypatch.setattr(rent, "_read_chat", lambda sid: (_long_chat(), "system"))
    monkeypatch.setattr(store, "chat_enabled", lambda sid: True)
    monkeypatch.setattr(store, "add_chat_event", lambda *a, **k: 1)
    saved = {}
    monkeypatch.setattr(store, "get_setting", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr(store, "set_setting", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(store, "_chat_ids", lambda sid: (sid,))
    return chat


def test_an_old_large_result_is_removed_once_it_pays(auto):
    assert rent.auto_evict("s")["payback_calls"] >= 0
    assert list(auto) == ["old"]


def test_nothing_is_removed_without_a_known_price(auto, monkeypatch):
    monkeypatch.setattr(modelinfo, "token_prices", lambda m, p: None)
    assert rent.auto_evict("s") is None and not auto


def test_a_short_chat_keeps_its_results(auto, monkeypatch):
    # Removing it would resend this long reply, and the chat hasn't gone on long enough to repay that.
    rows = _long_chat(calls=1) + [_row("user", "more"), _row("assistant", "y" * 400_000)]
    monkeypatch.setattr(rent, "_read_chat", lambda sid: (rows, "system"))
    assert rent.auto_evict("s") is None and not auto


def test_this_messages_result_is_never_removed(auto, monkeypatch):
    rows = _long_chat() + [_row("user", "new"), _row("tool", BIG, "fresh", tool="terminal")]
    monkeypatch.setattr(rent, "_read_chat", lambda sid: (rows, "system"))
    rent.auto_evict("s")
    assert "fresh" not in auto


def test_read_backs_turn_removal_off_for_that_chat(auto):
    rent.auto_evict("s")
    auto["other"] = "abc123"
    rent.note_read_back("s", "abc123")
    rent.note_read_back("s", "abc123")
    assert rent.auto_state("s")["off"]
    auto.clear()
    assert rent.auto_evict("s") is None and not auto


def test_a_chat_that_keeps_reading_things_back_stops_removing(auto):
    state = {"removed": 2, "read_backs": 0, "off": False}
    store.set_setting(rent._auto_key("s"), state)
    good = rent.auto_plan("s")["read_back_chance"]
    store.set_setting(rent._auto_key("s"), {**state, "read_backs": 1})
    assert rent.auto_plan("s")["read_back_chance"] > good


def test_a_small_old_result_in_a_long_chat_isnt_worth_a_read_back_risk(auto, monkeypatch):
    rows = _long_chat(calls=2, big="x" * 400) + [_row("user", "more"), _row("assistant", "y" * 20_000)]
    monkeypatch.setattr(rent, "_read_chat", lambda sid: (rows, "system"))
    assert rent.auto_plan("s")["items"] == []
