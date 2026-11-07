"""Wire-only re-read projections: first reads, cache economics and exact recovery."""
import copy
import json
from types import SimpleNamespace

import pytest

from crystallize import capture, chat_savings as chat, handoffs, modelinfo, rent, reread, store

TEXT = "opening context\n" + "unique body text with complete source material\n" * 400 + "closing context\n"
USAGE = {"input_tokens": 50_000, "output_tokens": 100, "cache_read_tokens": 0, "cache_write_tokens": 0}
PRICES = {"input": 1e-5, "output": 2e-5, "cache_read": 1e-6, "cache_write": 1.25e-5}


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    store.set_chat_enabled("s", True)
    monkeypatch.setattr(chat, "_main_model", lambda sid: {"model": "main", "provider": "provider"})
    monkeypatch.setattr(chat, "_full_dir", lambda: tmp_path / "originals")
    monkeypatch.setattr(modelinfo, "token_prices", lambda model, provider: {**PRICES, "cache_read": PRICES["input"], "cache_write": PRICES["input"]})
    reread.configure("s", enabled=True, writes=True, results=True)
    return "s"


def _record(usage=None):
    handoffs.note_usage("s", USAGE if usage is None else usage)


def _baseline(container="messages"):
    request = {"model": "main", container: [{"role": "user", "content": "existing user context"}]}
    assert reread.rewrite(request, "s") is None
    _record()
    return request


def _chat_write(text=TEXT, result=None, name="write_file", field="content"):
    return [
        {"role": "assistant", "content": None, "reasoning_content": "exact reasoning", "tool_calls": [
            {"id": "write-1", "function": {"name": name, "arguments": json.dumps({"path": "/tmp/exact.txt", field: text})}}]},
        {"role": "tool", "tool_call_id": "write-1", "content": json.dumps({"success": True, "verified": True}) if result is None else result},
    ]


def _new_request(items, container="messages", **extra):
    request = _baseline(container)
    request[container].extend(copy.deepcopy(items))
    request.update(extra)
    return request


def _replay(request):
    assert reread.rewrite(request, "s") is None  # complete first read
    assert reread.rewrite(request, "s") is None  # same wire request/retry, no usage confirmation
    _record()
    return reread.rewrite(request, "s")


def test_defaults_and_configuration_require_explicit_booleans():
    assert reread.policy("new") == {"enabled": False, "writes": False, "results": False,
                                     "batching": False, "reasoning_unchanged": True}
    assert reread.configure("new", enabled=True)["writes"] is False
    for sid, settings in [("", {"enabled": True}), ("s", {"writes": 1}), ("s", {"unknown": True})]:
        with pytest.raises(ValueError):
            reread.configure(sid, **settings)


def test_initial_old_prefix_never_changes_and_first_read_is_complete(enabled):
    request = {"messages": [{"role": "user", "content": "all existing context"}, *_chat_write()]}
    before = copy.deepcopy(request)
    for _ in range(3):
        assert reread.rewrite(request, "s") is None
        _record()
    assert request == before


def test_completed_write_replays_are_immutable_and_recoverable(enabled):
    request = _new_request(_chat_write(), reasoning_effort="high", reasoning={"summary": "detailed"},
                           extra_body={"reasoning": {"effort": "high"}}, tools=[{"name": "write_file"}])
    before = copy.deepcopy(request)
    out = _replay(request)
    assert request == before
    args = json.loads(out["messages"][1]["tool_calls"][0]["function"]["arguments"])
    assert args["path"] == "/tmp/exact.txt" and len(args["content"]) < len(TEXT)
    assert "completed tool input excerpt" in args["content"]
    assert out["messages"][1]["reasoning_content"] == "exact reasoning"
    assert out["messages"][2] == request["messages"][2]
    for key in ("model", "tools", "reasoning", "reasoning_effort", "extra_body"):
        assert out[key] == before[key]
    report = reread.summary("s")
    assert report["active_projections"] == 1 and report["estimated_avoided_input_tokens"] > 0
    assert chat._load_full(report["recovery"][0]["result_id"], "s") == TEXT
    assert chat._load_full(report["recovery"][0]["result_id"], "another-session") is None
    frozen = copy.deepcopy(out)
    out["messages"][1]["tool_calls"][0]["function"]["arguments"] = "mutated caller copy"
    assert reread.rewrite(request, "s") == frozen
    _record()
    assert reread.rewrite(request, "s") == frozen


@pytest.mark.parametrize("result", [json.dumps({"success": False}), json.dumps({"error": "denied"}),
                                   json.dumps({"status": "pending"}), "write attempted", "", None])
def test_only_proven_successful_completed_writes_are_reduced(enabled, result):
    messages = _chat_write(result=result)
    if result is None:
        messages.pop()  # a tool call without a result is not completed
    request = _new_request(messages)
    assert _replay(request) is None


@pytest.mark.parametrize("name,field", [("write_file", "content"), ("patch", "new_string"),
                                       ("patch", "old_string"), ("patch", "patch")])
def test_supported_historical_write_fields(enabled, name, field):
    request = _new_request(_chat_write(name=name, field=field))
    out = _replay(request)
    assert len(json.loads(out["messages"][1]["tool_calls"][0]["function"]["arguments"])[field]) < len(TEXT)


def test_expired_original_restores_exact_message_and_does_not_make_new_placeholder(enabled, monkeypatch):
    request = _new_request(_chat_write())
    assert _replay(request)
    monkeypatch.setattr(chat, "_load_full", lambda *args: None)
    assert reread.rewrite(request, "s") is None
    assert reread.summary("s")["expired_projections"] == 1
    monkeypatch.setattr(chat, "_keep_full", lambda *args: pytest.fail("expired projection was regenerated"))
    _record()
    assert reread.rewrite(request, "s") is None


def test_off_and_subfeature_toggle_restore_exact_history(enabled):
    request = _new_request(_chat_write())
    projected = _replay(request)
    reread.configure("s", writes=False)
    assert reread.rewrite(request, "s") is None
    reread.configure("s", writes=True)
    assert reread.rewrite(request, "s") == projected
    reread.configure("s", enabled=False)
    assert reread.rewrite(request, "s") is None


def test_result_name_is_resolved_from_call_and_structured_metadata_stays_exact(enabled):
    request = _new_request([
        {"role": "assistant", "tool_calls": [{"id": "search", "function": {"name": "web_extract", "arguments": '{"url":"https://example.test"}'}}]},
        {"role": "tool", "tool_call_id": "search", "content": json.dumps({"output": TEXT, "next": "cursor-42", "count": 71,
                                                                              "url": "https://example.test", "status": "success"})},
    ])
    out = _replay(request)
    result = json.loads(out["messages"][2]["content"])
    assert result["next"] == "cursor-42" and result["count"] == 71 and result["status"] == "success"
    assert result["url"] == "https://example.test" and "tool result excerpt" in result["output"]
    assert "opening context" in result["output"] and "closing context" in result["output"]


@pytest.mark.parametrize("name,text", [("read_file", TEXT), ("read_document", TEXT), ("terminal", "small exact result"),
                                     ("terminal", json.dumps({"output": TEXT, "exit_code": 1})),
                                     ("terminal", TEXT + "\nError: failed\n"),
                                     ("terminal", json.dumps({"output": TEXT, "warning": "partial result"})),
                                     ("terminal", json.dumps({"records": [{"name": TEXT, "price": 15.25}]})),
                                     ("unknown_domain_tool", TEXT)])
def test_files_failures_small_results_unknown_tools_and_records_stay_exact(enabled, name, text):
    request = _new_request([{"role": "tool", "name": name, "tool_call_id": "r", "content": text}])
    assert _replay(request) is None


def test_user_system_assistant_context_is_never_condensed(enabled):
    request = _new_request([{"role": "user", "content": TEXT}, {"role": "system", "content": TEXT},
                            {"role": "assistant", "content": TEXT}])
    assert _replay(request) is None


def test_changed_same_id_result_gets_its_own_complete_first_read(enabled):
    request = _new_request([{"role": "tool", "name": "terminal", "tool_call_id": "r", "content": TEXT}])
    assert _replay(request)
    request["messages"][1]["content"] += "new changed tail"
    assert reread.rewrite(request, "s") is None
    _record()
    assert "new changed tail" in reread.rewrite(request, "s")["messages"][1]["content"]


def test_identical_duplicate_items_do_not_share_mutable_projection_state(enabled):
    result = {"role": "tool", "name": "terminal", "content": TEXT}
    request = _new_request([result, result])
    out = _replay(request)
    assert out["messages"][1] is not out["messages"][2]
    out["messages"][1]["content"] = "caller change"
    assert "caller change" not in reread.rewrite(request, "s")["messages"][2]["content"]


@pytest.mark.parametrize("usage", [{"input_tokens": 50_000},
                                   {"input_tokens": 50_000, "cache_read_tokens": None, "cache_write_tokens": 0}])
def test_missing_cache_evidence_freezes_exact_decision(enabled, usage):
    request = _new_request(_chat_write())
    assert reread.rewrite(request, "s") is None
    _record(usage)
    assert reread.rewrite(request, "s") is None
    _record()
    assert reread.rewrite(request, "s") is None


def test_unknown_prices_and_free_input_do_not_invent_savings(enabled, monkeypatch):
    request = _new_request(_chat_write())
    assert reread.rewrite(request, "s") is None
    _record()
    monkeypatch.setattr(modelinfo, "token_prices", lambda *args: None)
    assert reread.rewrite(request, "s") is None
    assert reread.summary("s")["projected_messages"] == 0


def test_cache_break_even_includes_the_cached_suffix_and_does_not_assume_future_calls(enabled, monkeypatch):
    monkeypatch.setattr(modelinfo, "token_prices", lambda *args: dict(PRICES))
    request = _new_request([*_chat_write(), {"role": "assistant", "content": TEXT * 8}])
    assert reread.rewrite(request, "s") is None
    _record({**USAGE, "input_tokens": 50_000, "cache_read_tokens": 49_000})
    assert reread.rewrite(request, "s") is None
    rows = next(iter(store.get_setting(reread._key("s"))["streams"].values()))["rows"].values()
    estimates = [row["estimate"] for row in rows if row.get("estimate")]
    assert estimates and estimates[0]["payback_calls"] > 1 and not estimates[0]["worthwhile"]
    _record()
    assert reread.rewrite(request, "s") is None  # no opportunistic old-prefix rewrite later


def test_batch_note_is_added_once_to_new_user_text_and_survives_append_and_reorder(enabled):
    reread.configure("s", writes=False, results=False, batching=True)
    request = _baseline()
    request["messages"].append({"role": "user", "content": "new user task"})
    out = reread.rewrite(request, "s")
    assert out["messages"][0] == request["messages"][0]
    assert out["messages"][1]["content"].count("batch independent reads") == 1
    request["messages"].insert(0, {"role": "system", "content": "new host prefix"})
    request["messages"].append({"role": "user", "content": "another task"})
    later = reread.rewrite(request, "s")
    assert later["messages"][2] == out["messages"][1]
    assert later["messages"][3] == request["messages"][3]
    assert sum(str(item).count("batch independent reads") for item in later["messages"]) == 1
    assert all("batch independent reads" not in str(item) for item in request["messages"])


def test_batch_note_never_changes_user_tool_results_or_reasoning(enabled):
    reread.configure("s", writes=False, results=False, batching=True)
    request = _new_request([{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": TEXT}]}])
    assert reread.rewrite(request, "s") is None


def _native(provider):
    args = {"path": "/tmp/exact.txt", "content": TEXT}
    success = json.dumps({"success": True})
    if provider == "responses":
        return "input", [{"type": "reasoning", "id": "r", "encrypted_content": "signed reasoning"},
                         {"type": "function_call", "call_id": "c|item", "name": "write_file", "arguments": json.dumps(args)},
                         {"type": "function_call_output", "call_id": "c", "output": success}], [2, "arguments"]
    if provider == "anthropic":
        return "messages", [{"role": "assistant", "content": [{"type": "thinking", "thinking": "keep", "signature": "sig"},
                                                                  {"type": "tool_use", "id": "c", "name": "write_file", "input": args}]},
                            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c", "is_error": False, "content": success}]}], [1, "content", 1, "input"]
    if provider == "bedrock":
        return "messages", [{"role": "assistant", "content": [{"reasoningContent": {"reasoningText": {"text": "keep", "signature": "sig"}}},
                                                                  {"toolUse": {"toolUseId": "c", "name": "write_file", "input": args}}]},
                            {"role": "user", "content": [{"toolResult": {"toolUseId": "c", "status": "success", "content": [{"text": success}]}}]}], [1, "content", 1, "toolUse", "input"]
    return "contents", [{"role": "model", "parts": [{"text": "keep reasoning", "thought": True},
                                                         {"functionCall": {"name": "write_file", "args": args}}]},
                        {"role": "user", "parts": [{"functionResponse": {"name": "write_file", "response": {"success": True}}}]}], [1, "parts", 1, "functionCall", "args"]


@pytest.mark.parametrize("provider", ["responses", "anthropic", "bedrock", "gemini"])
def test_native_provider_writes_preserve_full_reasoning_and_shapes(enabled, provider):
    container, items, args_path = _native(provider)
    request = _new_request(items, container)
    before = copy.deepcopy(request)
    out = _replay(request)
    args = reread._get(out[container], args_path)
    args = json.loads(args) if isinstance(args, str) else args
    assert len(args["content"]) < len(TEXT) and args["path"] == "/tmp/exact.txt"
    assert request == before
    if provider == "responses":
        assert out[container][1] == request[container][1]
    else:
        field = "parts" if provider == "gemini" else "content"
        assert out[container][1][field][0] == before[container][1][field][0]


def test_signed_tool_call_blocks_are_kept_whole(enabled):
    container, items, _ = _native("gemini")
    items[0]["parts"][1]["thoughtSignature"] = "signed call"
    request = _new_request(items, container)
    assert _replay(request) is None


@pytest.mark.parametrize("provider", ["responses", "anthropic", "bedrock", "gemini"])
def test_native_provider_results_preserve_metadata_and_signatures(enabled, provider):
    container, items, _ = _native(provider)
    # Use a text-result tool and keep provider result envelopes native.
    if provider == "responses":
        items[1]["name"] = "terminal"
        items[2]["output"] = TEXT
    elif provider == "anthropic":
        items[0]["content"][1]["name"] = "terminal"
        items[1]["content"][0]["content"] = [{"type": "text", "text": TEXT}, {"type": "image", "source": {"data": "exact image"}}]
    elif provider == "bedrock":
        items[0]["content"][1]["toolUse"]["name"] = "terminal"
        items[1]["content"][0]["toolResult"]["content"] = [{"text": TEXT}]
    else:
        items[0]["parts"][1]["functionCall"]["name"] = "terminal"
        items[1]["parts"][0]["functionResponse"] = {"name": "terminal", "response": {"output": TEXT, "count": 88}}
    request = _new_request(items, container)
    before = copy.deepcopy(request)
    out = _replay(request)
    assert out and len(json.dumps(out)) < len(json.dumps(request))
    assert request == before
    if provider == "anthropic":
        assert out[container][2]["content"][0]["content"][1] == items[1]["content"][0]["content"][1]
    elif provider == "gemini":
        assert out[container][2]["parts"][0]["functionResponse"]["response"]["count"] == 88


def test_model_switch_starts_exact_new_baseline(enabled):
    request = _new_request(_chat_write())
    assert _replay(request)
    request["model"] = "another-main"
    assert reread.rewrite(request, "s") is None
    _record()
    assert reread.rewrite(request, "s") is None


def test_disabled_chat_and_unsupported_request_shapes_are_unchanged(enabled):
    assert reread.rewrite({"input": "plain string"}, "s") is None
    assert reread.rewrite({"messages": [{"role": "assistant", "content": object()}]}, "s") is None
    store.set_chat_enabled("s", False)
    assert reread.rewrite({"messages": _chat_write()}, "s") is None


def test_capture_layers_rent_then_reread_and_preserves_earlier_success_on_failure(monkeypatch):
    calls = []
    request = {"messages": []}
    after_rent = {"messages": [], "rent": True}
    monkeypatch.setattr(rent, "apply", lambda incoming, sid: (calls.append(("rent", incoming)), after_rent)[1])
    def fail(incoming, sid):
        calls.append(("reread", incoming))
        raise RuntimeError("projection unavailable")
    monkeypatch.setattr(reread, "rewrite", fail)
    hook = capture.make_llm_request(SimpleNamespace(current_cron_execution=lambda: None))
    assert hook(request, "s") == {"request": after_rent}
    assert calls == [("rent", request), ("reread", after_rent)]


def test_capture_rent_failure_still_allows_replay_savings(monkeypatch):
    def fail(*args):
        raise RuntimeError("rent unavailable")
    monkeypatch.setattr(rent, "apply", fail)
    changed = {"messages": [], "reread": True}
    monkeypatch.setattr(reread, "rewrite", lambda request, sid: changed)
    hook = capture.make_llm_request(SimpleNamespace(current_cron_execution=lambda: None))
    assert hook({"messages": []}, "s") == {"request": changed}


def test_capture_argument_hook_preserves_runtime_identity_and_blocks_unrecoverable_args(monkeypatch):
    seen = []
    monkeypatch.setattr(handoffs, "expand_args", lambda *args: (seen.append(args), {"content": "full"})[1])
    hook = capture.make_pre_tool_call(SimpleNamespace(current_cron_execution=lambda: None))
    assert hook(session_id="s", args={"content": "placeholder"}, turn_id="t", tool_name="write_file") == {
        "action": "modify", "args": {"content": "full"}}
    assert seen == [({"content": "placeholder"}, "s", "t", "write_file")]
    def unavailable(*args):
        raise ValueError("expired")
    monkeypatch.setattr(handoffs, "expand_args", unavailable)
    assert hook(session_id="s", args={}) == {"action": "block", "message": "expired"}


def test_write_that_finishes_after_first_observation_becomes_eligible_after_success(enabled):
    request = _new_request(_chat_write()[:1])
    assert reread.rewrite(request, "s") is None
    _record()
    assert reread.rewrite(request, "s") is None
    request["messages"].append(_chat_write()[1])
    out = reread.rewrite(request, "s")
    assert out is not None
    assert out["messages"][2] == request["messages"][2]  # complete first result read


def test_duplicate_conflicting_outcomes_do_not_prove_success(enabled):
    items = _chat_write()
    items.append({**items[-1], "content": '{"error":"the final write failed"}'})
    request = _new_request(items)
    assert _replay(request) is None


def test_incomplete_unknown_tool_shapes_do_not_disable_valid_projections(enabled):
    items = [{"role": "assistant", "tool_calls": [{"id": "missing", "function": {"name": "other"}}]},
             {"role": "tool", "tool_call_id": "missing"}, *_chat_write()]
    request = _new_request(items)
    assert _replay(request) is not None


def test_metadata_cap_does_not_remove_existing_frozen_projection(enabled, monkeypatch):
    request = _new_request(_chat_write())
    frozen = _replay(request)
    monkeypatch.setattr(reread, "MAX_MESSAGES", 3)
    request["messages"].extend({"role": "user", "content": f"message {i}"} for i in range(10))
    out = reread.rewrite(request, "s")
    assert out["messages"][:3] == frozen["messages"]


def test_zero_observed_cache_does_not_assume_new_history_cannot_be_cached(enabled, monkeypatch):
    monkeypatch.setattr(modelinfo, "token_prices", lambda *args: dict(PRICES))
    request = _new_request([*_chat_write(), {"role": "assistant", "content": TEXT * 8}])
    assert reread.rewrite(request, "s") is None
    _record()  # first read was uncached; its text can be cached by the next call
    assert reread.rewrite(request, "s") is None
    rows = next(iter(store.get_setting(reread._key("s"))["streams"].values()))["rows"].values()
    estimate = next(row["estimate"] for row in rows if row.get("estimate"))
    assert estimate["cache_read_tokens"] == 0
    assert estimate["potential_cached_tokens"] == USAGE["input_tokens"]
    assert estimate["estimated_cache_rebuild_usd"] > estimate["estimated_per_replay_usd"]


def test_large_recent_write_can_repay_discounted_cache_on_first_replay(enabled, monkeypatch):
    monkeypatch.setattr(modelinfo, "token_prices", lambda *args: dict(PRICES))
    request = _new_request(_chat_write(text=TEXT * 20))
    assert reread.rewrite(request, "s") is None
    _record({**USAGE, "input_tokens": 1_000, "prompt_tokens": 95_000, "cache_read_tokens": 94_000})
    assert reread.rewrite(request, "s") is not None
    rows = next(iter(store.get_setting(reread._key("s"))["streams"].values()))["rows"].values()
    estimate = next(row["estimate"] for row in rows if row.get("estimate"))
    assert estimate["worthwhile"] and estimate["first_replay_net_usd"] > 0
    assert estimate["payback_calls"] <= 1


@pytest.mark.parametrize("metadata", [{"signature": "sig"}, {"thoughtSignature": "sig"},
                                       {"citations": [{"start_index": 2, "end_index": 55}]},
                                       {"annotations": [{"start_index": 2, "end_index": 55}]}])
def test_signed_and_cited_result_text_blocks_stay_exact(enabled, metadata):
    request = _new_request([{"role": "tool", "name": "terminal", "tool_call_id": "r",
                             "content": [{"type": "text", "text": TEXT, **metadata}]}])
    assert _replay(request) is None


def test_capture_prunes_only_cron_requests_and_keeps_successful_prior_layers(monkeypatch):
    from crystallize import adaptive
    request = {"messages": [], "tools": [{"name": "keep"}, {"name": "optional"}]}
    monkeypatch.setattr(rent, "apply", lambda *args: None)
    monkeypatch.setattr(reread, "rewrite", lambda *args: None)
    seen = []
    def prune(incoming, job_ref):
        seen.append((incoming, job_ref))
        return {**incoming, "tools": incoming["tools"][:1]}
    monkeypatch.setattr(adaptive, "prune_request", prune)
    chat_hook = capture.make_llm_request(SimpleNamespace(current_cron_execution=lambda: None))
    assert chat_hook(request, "s") is None and not seen
    cron_hook = capture.make_llm_request(SimpleNamespace(current_cron_execution=lambda: SimpleNamespace(job_id="j", job_name="name")))
    assert cron_hook(request, "s")["request"]["tools"] == request["tools"][:1]
    assert seen == [(request, "j")]
    def fail(*args):
        raise RuntimeError("unknown pruning evidence")
    monkeypatch.setattr(adaptive, "prune_request", fail)
    assert cron_hook(request, "s") is None
    assert len(request["tools"]) == 2


def test_capture_real_usage_observation_remains_chat_only(monkeypatch):
    from crystallize import brake
    seen = []
    monkeypatch.setattr(handoffs, "note_usage", lambda *args: seen.append(("chat", args)))
    monkeypatch.setattr(brake, "enabled", lambda: True)
    monkeypatch.setattr(brake, "note_usage", lambda *args: seen.append(("cron", args)))
    monkeypatch.setattr(capture.store, "chat_enabled", lambda sid: True)
    capture.make_post_api_request(SimpleNamespace(current_cron_execution=lambda: None))("s", USAGE)
    capture.make_post_api_request(SimpleNamespace(current_cron_execution=lambda: object()))("s", USAGE)
    assert seen == [("chat", ("s", USAGE)), ("cron", ("s", USAGE))]


def test_cron_brake_preempts_argument_expansion(monkeypatch):
    from crystallize import brake
    stopped = {"action": "block", "message": "token brake"}
    monkeypatch.setattr(brake, "check", lambda *args: stopped)
    monkeypatch.setattr(handoffs, "expand_args", lambda *args: pytest.fail("braked tool arguments were expanded"))
    hook = capture.make_pre_tool_call(SimpleNamespace(current_cron_execution=lambda: SimpleNamespace(job_name="job")))
    assert hook("s", {}) == stopped


@pytest.mark.parametrize("tool", ["terminal", "*"])
def test_exact_result_policy_always_overrides_general_reread_opt_in(enabled, tool):
    from crystallize import chat_policy
    chat_policy.set_policy("s", tool, "exact")
    request = _new_request([{"role": "tool", "name": "terminal", "tool_call_id": "r", "content": TEXT}])
    assert _replay(request) is None


def test_changing_tool_policy_to_exact_restores_already_projected_result(enabled):
    from crystallize import chat_policy
    request = _new_request([{"role": "tool", "name": "terminal", "tool_call_id": "r", "content": TEXT}])
    assert _replay(request) is not None
    chat_policy.set_policy("s", "terminal", "exact")
    assert reread.rewrite(request, "s") is None
    assert reread.summary("s")["active_projections"] == 0


def test_exact_result_policy_does_not_disable_successful_write_argument_projection(enabled):
    from crystallize import chat_policy
    chat_policy.set_policy("s", "*", "exact")
    request = _new_request(_chat_write())
    assert _replay(request) is not None


def test_summary_counts_only_projections_in_current_wire_request(enabled):
    request = _new_request(_chat_write())
    assert _replay(request) is not None
    assert reread.summary("s")["active_projections"] == 1
    assert reread.rewrite({"model": "another", "messages": request["messages"]}, "s") is None
    assert reread.summary("s")["active_projections"] == 0
    assert reread.summary("s")["projected_messages"] == 1
    assert reread.rewrite({"model": "main", "messages": request["messages"][:1]}, "s") is None
    assert reread.summary("s")["active_projections"] == 0
