"""Panel routes that had no tests: they reach the feature, and bad input gets a friendly error."""

from __future__ import annotations

import asyncio
import importlib

import pytest

from crystallize import auction, brake, rent, starter_script, store, tuning
from crystallize.dashboard import plugin_api as api


@pytest.fixture(autouse=True)
def same_package(monkeypatch):
    """The panel normally finds the plugin by its installed name."""
    monkeypatch.setattr(api, "_module", lambda name: importlib.import_module("crystallize." + name))
    monkeypatch.setattr(api, "_tuning", lambda: tuning)


def _call(route, *args, **kwargs):
    return asyncio.run(route(*args, **kwargs))


def test_brake_routes_read_and_change_the_setting(monkeypatch):
    monkeypatch.setattr(tuning, "_all_jobs", lambda: [])
    assert _call(api.brake_setting, {"on": True})["ok"] and brake.enabled()
    assert _call(api.brake_summary)["on"] is True
    assert _call(api.brake_setting, {"on": False})["ok"] and not brake.enabled()


def test_brake_route_refuses_a_non_boolean():
    assert _call(api.brake_setting, {"on": "yes"})["ok"] is False


def test_rent_routes_report_remove_and_restore(monkeypatch):
    big = "x" * 5000
    monkeypatch.setattr(rent.modelinfo, "token_prices", lambda m, p: None)
    rows = [{"role": "tool", "content": big, "tool_call_id": "c1", "tool_calls": None, "tool_name": "terminal"}]
    monkeypatch.setattr(rent, "_read_chat", lambda sid: (rows, ""))
    monkeypatch.setattr(rent.chat_savings, "_keep_full", lambda text, sid: "0123456789abcdef")
    monkeypatch.setattr(rent.chat_savings, "_main_model", lambda sid: {"model": "m", "provider": "p"})
    assert _call(api.chat_rent, "s1")["items"][0]["id"] == "c1"
    assert _call(api.chat_evict, {"session_id": "s1", "call_id": "c1"})["ok"]
    assert store.evictions("s1") == {"c1": "0123456789abcdef"}
    assert _call(api.chat_restore, {"session_id": "s1", "call_id": "c1"})["ok"]
    assert store.evictions("s1") == {}


def test_rent_route_refuses_a_bad_session():
    assert _call(api.chat_evict, {"session_id": "../x y", "call_id": "c1"})["ok"] is False


def test_clearing_an_auction_needs_confirmation():
    with store._lock:
        conn = store._db()
        conn.executescript(auction._SCHEMA)
        conn.execute("INSERT INTO auction_profile_locks VALUES (?, ?, ?)", (auction._profile_key(), "a1", 1))
        conn.commit()
    assert _call(api.auction_reconcile, {"confirmed": False})["ok"] is False
    assert auction.locked()
    assert _call(api.auction_reconcile, {"confirmed": True})["cleared"]


def test_draft_route_reaches_the_drafter(monkeypatch):
    monkeypatch.setattr(starter_script, "draft", lambda job: {"ok": True, "source_script": f"crystallize-drafts/{job}.py"})
    assert _call(api.native_cron_draft, {"job_id": "j1"})["source_script"] == "crystallize-drafts/j1.py"


def test_a_failed_draft_is_a_friendly_error(monkeypatch):
    monkeypatch.setattr(starter_script, "draft", lambda job: (_ for _ in ()).throw(RuntimeError("boom")))
    out = _call(api.native_cron_draft, {"job_id": "j1"})
    assert out["ok"] is False and "boom" not in out["message"]
