"""Offline API checks: no Hermes process, connected provider or model call."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from crystallize.dashboard import plugin_api as api


def call(fn, *args, **kwargs):
    return asyncio.run(fn(*args, **kwargs))


def test_mutations_run_on_workers_and_strict_boolean_validation(monkeypatch):
    observed = []
    main = threading.get_ident()
    def enable(session_id, on):
        observed.append((threading.get_ident(), session_id, on))
        return {"ok": True}
    monkeypatch.setattr(api, "_chat", lambda: SimpleNamespace(set_enabled=enable))
    assert call(api.chat_toggle, {"session_id": "chat-a", "on": True})["ok"]
    assert observed == [(observed[0][0], "chat-a", True)]
    assert observed[0][0] != main
    assert call(api.chat_toggle, {"session_id": "chat-a", "on": "false"})["code"] == "invalid_request"
    assert len(observed) == 1


def test_slow_mutation_does_not_block_unrelated_server_work(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def undo(job):
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(api, "_tuning", lambda: SimpleNamespace(undo=undo))
    async def scenario():
        action = asyncio.create_task(api.tune_job({"job": "job-a", "undo": True}))
        await asyncio.wait_for(asyncio.to_thread(entered.wait), 1)
        # This callback must execute while the synchronous job edit is blocked.
        await asyncio.sleep(0)
        assert not action.done()
        release.set()
        assert (await action)["ok"]
    asyncio.run(scenario())


def test_invalid_mode_never_reaches_lifecycle(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_lifecycle", lambda: SimpleNamespace(set_mode=lambda *a: calls.append(a)))
    assert call(api.mode_routine, {"routine_id": "routine-a", "mode": "garbage"})["code"] == "invalid_request"
    assert not calls


def test_error_codes_separate_invalid_missing_blocked_and_internal(monkeypatch):
    class LifecycleError(RuntimeError):
        pass
    def failing(error):
        def fail(*args):
            raise error
        return fail
    for error, code in [(ValueError("bad"), "invalid_request"), (LookupError("gone"), "not_found"),
                        (LifecycleError("needs evidence"), "action_blocked"), (RuntimeError("private-token"), "internal_error")]:
        monkeypatch.setattr(api, "_tuning", lambda: SimpleNamespace(undo=failing(error)))
        result = call(api.tune_job, {"job": "job-a", "undo": True})
        assert result["code"] == code
        assert "private-token" not in str(result)


def test_chat_policy_route_is_session_scoped_and_never_toggles_consent(monkeypatch):
    from crystallize import chat_policy, store
    monkeypatch.setattr(api, "_module", lambda name: chat_policy if name == "chat_policy" else None)
    result = call(api.set_chat_policy, {"session_id": "chat-a", "tool": "terminal", "mode": "summary", "focus": "Keep line numbers"})
    assert result["ok"] and result["policy"]["tools"] == {"terminal": "summary"}
    assert call(api.chat_policy, "chat-b")["tools"] == {}
    assert not store.chat_enabled("chat-a")
    assert call(api.set_chat_policy, {"session_id": "../other", "tool": "terminal", "mode": "summary"})["code"] == "invalid_request"


def test_readiness_and_receipt_gets_never_start_comparison(monkeypatch):
    calls = []
    modules = {
        "experiments": SimpleNamespace(preview=lambda job: {"available": True, "job_id": job},
                                        comparisons=lambda job, limit=1: [],
                                        compare=lambda *a, **kw: calls.append(kw)),
        "receipts": SimpleNamespace(receipt=lambda job: {"job_id": job, "net_payback_usd": None}),
    }
    monkeypatch.setattr(api, "_module", modules.__getitem__)
    assert call(api.compare_preview, "job-a")["available"]
    assert call(api.receipt, "job-a")["net_payback_usd"] is None
    assert not calls


def test_comparison_requires_explicit_action_and_preserves_failed_call_receipt(monkeypatch):
    calls = []
    def compare(job, **kwargs):
        calls.append((job, kwargs, threading.get_ident()))
        return {"id": "pair-a", "status": "failed", "calls": [{"status": "failed", "cost": None}]}
    modules = {
        "experiments": SimpleNamespace(compare=compare),
        "blind": SimpleNamespace(present=lambda ident: {"id": ident, "revealed": False,
                                  "samples": [{"A": {"status": "failed", "answer": ""}, "B": {"status": "failed", "answer": ""}}]}),
    }
    monkeypatch.setattr(api, "_module", modules.__getitem__)
    assert call(api.compare_job, {"job_id": "job-a", "confirmed": False})["code"] == "invalid_request"
    assert not calls
    result = call(api.compare_job, {"job_id": "job-a", "confirmed": True, "execution_id": "run-a"})
    assert not result["ok"] and result["code"] == "comparison_failed"
    assert result["comparison"]["samples"][0]["A"]["status"] == "failed"
    assert result["comparison"]["revealed"] is False
    assert "cost" not in str(result["comparison"])
    assert calls[0][:2] == ("job-a", {"execution_id": "run-a", "confirmed": True})
    assert calls[0][2] != threading.get_ident()


def test_unavailable_comparison_is_distinct_from_bad_policy(monkeypatch):
    class ComparisonError(ValueError):
        pass
    def compare(*a, **kw):
        raise ComparisonError("No registered Hermes model interface.")
    monkeypatch.setattr(api, "_module", lambda name: SimpleNamespace(compare=compare))
    result = call(api.compare_job, {"job_id": "job-a", "confirmed": True})
    assert result["code"] == "action_blocked"


def test_contracts_require_stable_existing_job_id_and_bounded_payload(monkeypatch):
    saved = []
    monkeypatch.setattr(api, "_lifecycle", lambda: SimpleNamespace(cron_job=lambda job: {"id": "job-a"} if job in {"job-a", "Display name"} else None))
    monkeypatch.setattr(api, "_module", lambda name: SimpleNamespace(set=lambda *args: saved.append(args) or args[1]))
    assert call(api.set_contract, {"job_id": "unknown", "contract": {}})["code"] == "not_found"
    assert call(api.set_contract, {"job_id": "Display name", "contract": {}})["code"] == "invalid_request"
    assert call(api.set_contract, {"job_id": "job-a", "contract": {"required_fields": ["x" * 32000]}})["code"] == "invalid_request"
    spec = {"required_fields": ["items.*.url"], "minimum_items": 2}
    result = call(api.set_contract, {"job_id": "job-a", "contract": spec})
    assert result["ok"] and saved == [("job-a", spec)]


def test_context_rent_mutations_are_validated_and_off_loop(monkeypatch):
    observed = []
    def evict(session_id, call_id):
        observed.append((session_id, call_id, threading.get_ident()))
        return {"ok": True}
    monkeypatch.setattr(api, "_module", lambda name: SimpleNamespace(evict=evict))
    assert call(api.chat_evict, {"session_id": "chat-a", "id": "call-a"})["ok"]
    assert observed[0] == ("chat-a", "call-a", observed[0][2])
    assert observed[0][2] != threading.get_ident()
    assert call(api.chat_evict, {"session_id": "chat-a", "id": "../call-a"})["code"] == "invalid_request"
