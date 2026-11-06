"""Opt-in chat controls use the current store and fail closed at hook boundaries."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from crystallize import chat_policy as policy, store


def test_policy_defaults_are_cleanup_only_and_saving_does_not_enable_chat():
    assert policy.summary(None)["default_mode"] == "clean"
    assert policy.policy_for("chat-a", "web_fetch") == {"mode": "clean", "focus": ""}
    policy.set_policy("chat-a", "web_fetch", "summary", "  Keep issue numbers and status.  ")
    assert not store.chat_enabled("chat-a")
    assert policy.policy_for("chat-a", "web_fetch") == {"mode": "summary", "focus": "Keep issue numbers and status."}
    assert policy.policy_for("chat-b", "web_fetch") == {"mode": "clean", "focus": ""}


def test_tool_override_survives_default_and_focus_changes():
    policy.set_policy("chat-a", "read_file", "exact")
    policy.set_policy("chat-a", "*", "summary", "Need exact status values")
    assert policy.policy_for("chat-a", "read_file")["mode"] == "exact"
    assert policy.policy_for("chat-a", "web_fetch")["mode"] == "summary"
    assert policy.summary("chat-a")["tools"] == {"read_file": "exact"}
    policy.set_policy("chat-a", "terminal", "clean", "")
    assert policy.summary("chat-a")["focus"] == ""


@pytest.mark.parametrize("args", [
    ("", "terminal", "summary"),
    ("../another-chat", "terminal", "summary"),
    ("chat\nother", "terminal", "summary"),
    ("c" * 257, "terminal", "clean"),
    ("chat", "bad tool", "summary"),
    ("chat", "t" * 129, "clean"),
    ("chat", None, "clean"),
    ("chat", "terminal", "anything"),
    ("chat", "terminal", True),
    ("chat", "terminal", "summary", "x" * 2001),
    ("chat", "terminal", "summary", {"focus": "bad"}),
    ("chat", "terminal", "summary", "bad\x00focus"),
])
def test_invalid_controls_never_persist(args):
    with pytest.raises(policy.PolicyError):
        policy.set_policy(*args)
    assert policy.summary("chat")["tools"] == {}


def test_bad_hook_identifiers_keep_results_exact():
    assert policy.policy_for(None, "terminal") == {"mode": "exact", "focus": ""}
    assert policy.policy_for("chat", "bad tool")["mode"] == "exact"


def test_corrupt_storage_cannot_opt_a_tool_into_summary():
    store.set_setting(policy._key("chat"), {"default_mode": "future-mode", "focus": 42,
                                           "tools": {"terminal": "future-mode", "bad tool": "summary"}})
    assert policy.policy_for("chat", "terminal") == {"mode": "clean", "focus": ""}
    assert policy.summary("chat")["tools"] == {}


def test_controls_follow_the_active_profile_store(monkeypatch):
    policy.set_policy("same-chat", "terminal", "exact", "profile one")
    other = sqlite3.connect(":memory:", check_same_thread=False)
    other.executescript(store._SCHEMA)
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(store, "_conn", other)
            assert policy.policy_for("same-chat", "terminal") == {"mode": "clean", "focus": ""}
            policy.set_policy("same-chat", "terminal", "summary", "profile two")
        assert policy.policy_for("same-chat", "terminal") == {"mode": "exact", "focus": "profile one"}
    finally:
        other.close()


def test_concurrent_different_tools_keep_every_override():
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: policy.set_policy("chat", f"tool_{n}", "exact"), range(30)))
    assert len(policy.summary("chat")["tools"]) == 30
