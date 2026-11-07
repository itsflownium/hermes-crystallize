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
