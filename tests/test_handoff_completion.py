"""Offline coverage for public, domain-neutral handoffs and guarded delivery."""

import json
from types import SimpleNamespace

import pytest

from crystallize import chat, chat_savings as cs, dispatch_support, handoffs, overview, schemas, store, tools


PICKED = {"provider": "provider-a", "model": "helper"}
PRICES = {
    "helper": {"input": 1e-7, "output": 2e-7, "cache_read": 1e-8, "cache_write": 2e-7},
    "main": {"input": 1e-6, "output": 8e-6, "cache_read": 1e-7, "cache_write": 2e-6},
    "fallback": {"input": 3e-6, "output": 1e-5},
}


class Helper:
    def __init__(self, text="Complete deliverable.\n", usage=None, error=None, **fields):
        self.calls = []
        self.error = error
        self.answer = SimpleNamespace(text=text, model="helper", provider="provider-a",
                                      usage=usage if usage is not None else {"input_tokens": 1000, "output_tokens": 100},
                                      **fields)
        self.llm = SimpleNamespace(complete=self.complete)

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def world(monkeypatch, tmp_path):
    originals = tmp_path / "originals"
    originals.mkdir()
    monkeypatch.setattr(cs, "_full_dir", lambda: originals)
    monkeypatch.setattr(cs, "chosen_model", lambda: dict(PICKED))
    monkeypatch.setattr(cs, "_main_model", lambda sid: {"model": "main", "provider": "provider-a"})
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda model, provider: PRICES.get(model))
    monkeypatch.setattr(cs, "_filled", {})
    monkeypatch.setattr(cs, "_pauses", {})
    store.set_chat_enabled("s1", True)
    return originals


@pytest.fixture
def guarded_test_io(monkeypatch):
    """Exercise dormant I/O validators with an explicitly isolated test dispatcher.

    This does not certify the SDK. Current-host fallback is covered separately
    without this fixture and must never execute nested tools.
    """
    real_support = dispatch_support.support
    monkeypatch.setattr(dispatch_support, "support", lambda ctx=None: {
        **real_support(ctx), "guarded_dispatch_available": True})


def attempts(session="s1"):
    return [row for row in store.chat_events(session) if row["helper_call"]]


@pytest.mark.parametrize("instruction", [
    "Translate the supplied garden notes into French: water the basil at dawn.",
    "Extract the dates from this event plan: setup 2026-11-06; doors 2026-11-07.",
    "Classify these feedback snippets by topic: shipping was slow; packaging was lovely.",
    "Draft a classroom handout explaining the supplied photosynthesis notes.",
    "Reformat the supplied accounting entries as a table without changing their values.",
    "Compute the totals from this small table and show the arithmetic: 3, 7, 11.",
])
def test_public_handoff_is_domain_neutral_and_preserves_reasoning(world, instruction):
    helper = Helper(finish_reason="stop")
    answer = json.loads(handoffs.execute(helper, instruction, "s1", "t1", expected_output_tokens=1200))
    assert answer["output"] == helper.answer.text
    assert helper.calls[0][0] == [{"role": "user", "content": instruction}]
    settings = helper.calls[0][1]
    assert settings == {"task": cs.TASK, "timeout": handoffs.timeout(PICKED, "unused", 1200),
                        "max_tokens": cs.MAX_ROUTED_TOKENS, "purpose": "routed task"}
    assert settings["timeout"] > cs.CONDENSE_SECONDS
    assert answer["completion"]["truncation_confidence"] == "stop_metadata"
    assert "reasoning" not in settings and "reasoning_effort" not in settings


def test_legacy_entry_uses_exactly_the_shared_path(world, monkeypatch):
    calls = []
    monkeypatch.setattr(handoffs, "execute", lambda *a, **kw: (calls.append((a, kw)), "shared")[1])
    helper = Helper()
    assert cs.ask_cheap(helper, "complete input", "s1", "t1", save_to="notes.txt") == "shared"
    assert calls == [((helper, "complete input", "s1", "t1"), {"save_to": "notes.txt"})]


@pytest.mark.parametrize("text,options", [
    (None, {}), ("  \n", {}), (True, {}), ("", {"input_files": ["source.txt"]}),
    ("input", {"expected_output_tokens": True}), ("input", {"expected_output_tokens": 0}),
    ("input", {"expected_output_tokens": 4001}), ("input", {"expected_output_tokens": 1.5}),
    ("input", {"input_files": "source.txt"}), ("input", {"input_files": ["x"] * 9}),
    ("input", {"save_to": ""}), ("input", {"save_to": "bad\x00path"}),
    ("input", {"subtask": "true"}),
])
def test_invalid_inputs_make_no_paid_call_or_file_operation(world, monkeypatch, text, options):
    helper = Helper()
    monkeypatch.setattr(tools, "dispatch", lambda *a, **kw: pytest.fail("invalid handoff touched a file"))
    assert "error" in json.loads(handoffs.execute(helper, text, "s1", **options))
    assert not helper.calls and not attempts()


def test_all_entry_points_require_the_owning_chat_to_be_enabled(world):
    helper = Helper()
    assert "off" in json.loads(handoffs.execute(helper, "write", "s2"))["error"]
    assert not helper.calls and not store.chat_events("s2")


def test_schema_and_injected_note_show_the_required_nonempty_call_shape():
    schema = schemas.CRYSTAL_CHEAP
    assert schema["parameters"]["required"] == ["input"]
    assert schema["parameters"]["properties"]["input"]["minLength"] == 1
    assert 'crystal_cheap({"input":' in schema["description"]
    assert 'crystal_cheap({"input":' in cs.ROUTING_NOTE
    assert "any subject" in cs.ROUTING_NOTE
    assert "input_files" in cs.ROUTING_NOTE and "nonempty" in cs.ROUTING_NOTE


def test_subtask_gets_evidence_rules_without_tools_or_reasoning_overrides(world):
    helper = Helper()
    answer = json.loads(handoffs.execute(helper, "Draft from these supplied notes.", "s1", subtask=True))
    assert answer["output"]
    system, user = helper.calls[0][0]
    assert system["role"] == "system" and "You have no tools" in system["content"]
    assert "missing evidence" in system["content"] and user["content"].startswith("Draft")
    assert set(helper.calls[0][1]) == {"task", "timeout", "max_tokens", "purpose"}


@pytest.mark.parametrize("metadata", [
    {"finish_reason": "length"},
    {"stop_reason": "max_tokens"},
    {"finish_reason": "MAX_TOKENS"},
    {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
    {"raw_response": {"choices": [{"finish_reason": "length"}]}},
    {"finish_reason": "stop", "raw_response": SimpleNamespace(choices=[SimpleNamespace(finish_reason="length")])},
    {"raw": {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}},
    {"raw_response": {"usage": {"completion_tokens": cs.MAX_ROUTED_TOKENS}}},
])
def test_cutoff_metadata_rejects_output_and_preserves_the_charge(world, monkeypatch, metadata):
    helper = Helper(**metadata)
    monkeypatch.setattr(tools, "dispatch", lambda *a, **kw: pytest.fail("cutoff output was written"))
    response = json.loads(handoffs.execute(helper, "Produce a complete deliverable.", "s1", save_to="report.txt"))
    assert response["written"] is False and response["completion"]["cut_off"]
    row = attempts()[0]
    assert row["status"] == "rejected" and row["cheap_cost"] == pytest.approx(0.00022)
    assert row["cost_source"] == "list_price_upper_bound_unknown_cache"
    assert not list(world.rglob("*.json"))


def test_public_usage_at_cap_rejects_without_stop_metadata(world):
    helper = Helper(usage={"input_tokens": 100, "output_tokens": cs.MAX_ROUTED_TOKENS})
    response = json.loads(cs.ask_cheap(helper, "Complete the task.", "s1"))
    assert response["completion"]["cut_off"] and attempts()[0]["status"] == "rejected"


def test_missing_finish_metadata_is_explicitly_unknown_and_uses_public_api(world):
    helper = Helper()
    response = json.loads(handoffs.execute(helper, "Complete the task.", "s1"))
    assert response["completion"]["truncation_confidence"] == "unknown"
    assert response["output"] == helper.answer.text
    assert not response["completion"]["finish_reasons"]
    assert response["completion"]["limit_requested_not_guaranteed"] is True
    assert response["completion"]["requested_output_limit"] == cs.MAX_ROUTED_TOKENS


def test_ignored_token_limit_cannot_return_an_unbounded_deliverable(world, monkeypatch):
    helper = Helper(text="x" * (cs.MAX_INPUT_CHARS + 1), usage={"input_tokens": 100, "cost_usd": 0.01})
    monkeypatch.setattr(tools, "dispatch", lambda *a, **kw: pytest.fail("oversize output was written"))
    result = json.loads(handoffs.execute(helper, "Produce a complete output.", "s1", save_to="report.txt"))
    assert result["written"] is False and "error" in result
    assert attempts()[0]["cheap_cost"] == 0.01 and attempts()[0]["status"] == "rejected"


def test_completion_filter_rejects_partial_safety_or_tool_call_responses(world):
    helper = Helper(finish_reason="content_filter")
    response = json.loads(handoffs.execute(helper, "Complete the task.", "s1"))
    assert response["completion"]["rejected"] and attempts()[0]["status"] == "rejected"


def test_saved_output_keeps_exact_bytes_and_returns_only_a_preview(world, guarded_test_io, monkeypatch):
    output = "  Heading ☀\n\n" + ("detail line\n" * 1000) + "\n  "
    helper = Helper(text=output, finish_reason="stop")
    writes = []

    def dispatch(name, args, **identity):
        writes.append((name, args, identity))
        return json.dumps({"bytes_written": len(args["content"].encode("utf-8")), "verified": True,
                           "resolved_path": "/workspace/report.txt", "content": output})

    monkeypatch.setattr(tools, "dispatch", dispatch)
    response = json.loads(handoffs.execute(helper, "Write the complete report.", "s1", "save", save_to="report.txt"))
    assert response["written"] and response["path"] == "/workspace/report.txt"
    assert writes == [("write_file", {"path": "report.txt", "content": output},
                       {"session_id": "s1", "turn_id": "save"})]
    assert "output" not in response and len(response["preview"]) == 500 and response["preview_truncated"]
    assert len(json.dumps(response)) < len(output) / 2
    assert cs._load_full(response["full_result_id"], "s1") == output
    credited = store.list_chat_savings("s1")[0]["output_chars"]
    assert credited == len(output)
    assert cs.fill_placeholders(response["placeholder"], "s1", "later") == output
    assert handoffs.expand_args({"body": response["placeholder"]}, "s1", "another", "send_message")["body"] == output
    cs._filled.clear()  # repeated reuse after ephemeral state disappears must not over-credit
    cs.fill_placeholders(response["placeholder"], "s1", "third")
    assert store.list_chat_savings("s1")[0]["output_chars"] == credited


@pytest.mark.parametrize("write_result", [
    {}, {"note": "approval required"}, {"status": "pending", "success": True},
    {"bytes_written": 1}, {"bytes_written": True}, {"bytes_written": 22, "verified": False},
    {"success": False, "bytes_written": 22}, {"error": "host policy blocked"},
])
def test_write_requires_affirmative_complete_success_without_losing_the_paid_answer(world, guarded_test_io, monkeypatch, write_result):
    helper = Helper(text="complete saved output\n")
    monkeypatch.setattr(tools, "dispatch", lambda *a, **kw: json.dumps(write_result))
    response = json.loads(handoffs.execute(helper, "Write the complete report.", "s1", save_to="report.txt"))
    assert response["written"] is False and "error" in response
    assert cs._load_full(response["full_result_id"], "s1") == helper.answer.text
    assert store.list_chat_savings("s1")[0]["output_chars"] == 0
    assert attempts()[0]["cheap_cost"] == pytest.approx(0.00022)
    assert attempts()[0]["cost_source"] == "list_price_upper_bound_unknown_cache"


def test_guarded_write_exception_returns_recoverable_output_and_counts_response(world, guarded_test_io, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("approval required")

    monkeypatch.setattr(tools, "dispatch", denied)
    helper = Helper()
    response = handoffs.execute(helper, "Write the report.", "s1", save_to="report.txt")
    data = json.loads(response)
    assert data["written"] is False and "approval required" in data["error"]
    assert data["placeholder"] and len(helper.calls) == 1
    assert store.list_chat_savings("s1")[0]["added_input_chars"] == len(response)


def test_input_files_page_through_host_guards_with_identity_and_strip_only_display_gutters(world, guarded_test_io, monkeypatch):
    helper = Helper()
    calls = []

    def dispatch(name, args, **identity):
        calls.append((name, dict(args), identity))
        if args["offset"] == 1:
            return json.dumps({"content": "1|first source line\n2|  keep this indent", "truncated": True,
                               "total_lines": 3, "file_size": 100})
        return json.dumps({"content": "3|2026|this is actual source data", "truncated": False,
                           "total_lines": 3, "file_size": 100})

    monkeypatch.setattr(tools, "dispatch", dispatch)
    response = json.loads(handoffs.execute(helper, "Extract facts from the complete file.", "s1", "t1", input_files=["notes.txt"]))
    assert response["output"]
    assert [call[1]["offset"] for call in calls] == [1, 3]
    assert all(call[0] == "read_file" and call[2] == {"session_id": "s1", "turn_id": "t1"} for call in calls)
    prompt = helper.calls[0][0][0]["content"]
    assert "first source line\n  keep this indent\n2026|this is actual source data" in prompt
    assert "<file path=" in prompt


@pytest.mark.parametrize("file_result", [
    {"success": True}, {"content": "partial", "truncated": True},
    {"content": "data", "status": "pending"}, {"content": "data", "success": False},
    {"content": "data", "truncated_lines": True}, {"content": "data", "is_binary": True},
    {"content": "data", "total_lines": 4, "truncated": False},
    {"content": "1|data", "truncated": True, "next_offset": 99, "total_lines": 3},
])
def test_incomplete_or_denied_input_is_never_sent_to_the_helper(world, guarded_test_io, monkeypatch, file_result):
    helper = Helper()
    monkeypatch.setattr(tools, "dispatch", lambda *a, **kw: json.dumps(file_result))
    response = json.loads(handoffs.execute(helper, "Use the complete input.", "s1", input_files=["notes.txt"]))
    assert "error" in response and not helper.calls and not attempts()


def test_input_file_budget_includes_source_wrappers_and_never_silently_truncates(world, guarded_test_io, monkeypatch):
    helper = Helper()
    monkeypatch.setattr(tools, "dispatch", lambda *a, **kw: json.dumps({"content": "x" * cs.MAX_INPUT_CHARS}))
    response = json.loads(handoffs.execute(helper, "Use this.", "s1", input_files=["notes.txt"]))
    assert "budget" in response["error"] and not helper.calls


def test_changed_file_between_pages_is_rejected(world, guarded_test_io, monkeypatch):
    helper = Helper()
    pages = iter([{"content": "1|first", "truncated": True, "total_lines": 2, "file_size": 10},
                  {"content": "2|second", "truncated": False, "total_lines": 2, "file_size": 11}])
    monkeypatch.setattr(tools, "dispatch", lambda *a, **kw: json.dumps(next(pages)))
    response = json.loads(handoffs.execute(helper, "Use complete input.", "s1", input_files=["notes.txt"]))
    assert "changed" in response["error"] and not helper.calls


def test_file_operations_become_normal_host_plans_without_raw_or_private_dispatch(world, monkeypatch):
    helper = Helper()
    monkeypatch.setattr(tools, "_ctx", SimpleNamespace(dispatch_tool=lambda *a, **kw: pytest.fail("raw SDK dispatch used")))
    with tools.caller_context(session_id="s1", task_id="host-task", user_task="Summarize my notes"):
        response = json.loads(handoffs.execute(helper, "Summarize the source.", "s1", "host-turn",
                                              input_files=["notes.txt"], save_to="summary.txt"))
    assert response["status"] == "pending_host_tools" and not response["written"]
    assert response["helper_called"] is False and not helper.calls
    assert response["host_tool_plan"]["actions"] == [
        {"tool": "read_file", "arguments": {"path": "notes.txt", "offset": 1, "limit": 1000}}]
    resume = response["host_tool_plan"]["continuation"]["arguments"]
    assert resume["input"] == "Summarize the source." and resume["save_to"] == "summary.txt"
    assert "input_files" not in resume and tools._caller.get() == {}


@pytest.mark.parametrize("field", sorted(handoffs.TEXT_FIELDS))
def test_text_placeholders_are_owned_and_credited_once_across_tools_and_final_answers(world, field):
    text = "Long reusable text. " * 100
    result_id = cs._keep_full(text, "s1")
    placeholder = "{{crystal:" + result_id + "}}"
    args = {field: "prefix " + placeholder}
    assert handoffs.expand_args(args, "s1", "t1", "write_file")[field] == "prefix " + text
    assert args[field] == "prefix " + placeholder
    assert cs.fill_placeholders(placeholder, "s1", "t2") == text
    handoffs.expand_args({field: placeholder}, "s1", "t3", "write_file")
    assert store.list_chat_savings("s1")[0]["output_chars"] == len(text) - len(placeholder)
    with pytest.raises(ValueError, match="another chat"):
        handoffs.expand_args({field: placeholder}, "s2", "t1", "write_file")


@pytest.mark.parametrize("make_args,tool_name", [
    (lambda p: {"path": p}, "write_file"),
    (lambda p: {"url": p}, "web_extract"),
    (lambda p: {"code": p}, "execute_code"),
    (lambda p: {"command": {"content": p}}, "terminal"),
    (lambda p: {"env": {"message": p}}, "terminal"),
    (lambda p: {"to": {"message": p}}, "send_message"),
    (lambda p: {"content": p}, "execute_code"),
    (lambda p: {p: "value"}, "write_file"),
])
def test_placeholders_never_expand_commands_paths_urls_recipients_or_keys(world, make_args, tool_name):
    result_id = cs._keep_full("reusable output " * 100, "s1")
    placeholder = "{{crystal:" + result_id + "}}"
    with pytest.raises(ValueError):
        handoffs.expand_args(make_args(placeholder), "s1", "t1", tool_name)
    assert not store.list_chat_savings("s1")


def test_argument_expansion_validates_every_placeholder_before_crediting_any(world):
    result_id = cs._keep_full("valid output " * 100, "s1")
    good = "{{crystal:" + result_id + "}}"
    with pytest.raises(ValueError, match="Malformed"):
        handoffs.expand_args({"content": good, "body": "{{crystal:bad}}"}, "s1", "t1", "write_file")
    assert not store.list_chat_savings("s1")


def test_argument_expansion_rejects_nested_placeholders_and_oversize_but_leaves_ordinary_args_alone(world, monkeypatch):
    nested_id = cs._keep_full("{{crystal:0000000000000000}}", "s1")
    with pytest.raises(ValueError, match="nested"):
        handoffs.expand_args({"content": "{{crystal:" + nested_id + "}}"}, "s1", "t1", "write_file")
    result_id = cs._keep_full("lots of output " * 100, "s1")
    monkeypatch.setattr(handoffs, "MAX_ARGUMENT_CHARS", 20)
    with pytest.raises(ValueError, match="safety cap"):
        handoffs.expand_args({"content": "{{crystal:" + result_id + "}}"}, "s1", "t1", "write_file")
    ordinary = {"content": {"nested": {}}}
    for _ in range(30):
        ordinary = {"body": ordinary}
    assert handoffs.expand_args(ordinary, "s1", "t1", "write_file") is None
    assert not store.list_chat_savings("s1")


def test_main_usage_is_real_normalized_and_never_invents_cache_metadata(world):
    handoffs.note_usage("s1", None)
    handoffs.note_usage("s1", {"input_tokens": True})
    handoffs.note_usage("s1", {"input_tokens": float("nan")})
    assert handoffs.main_usage("s1") == {}
    handoffs.note_usage("s1", {"prompt_tokens": 10000, "completion_tokens": 100,
                                "prompt_tokens_details": {"cached_tokens": 7000}})
    row = handoffs.main_usage("s1")
    assert row["input_tokens"] == 10000 and row["cache_read_tokens"] == 7000
    assert row["cache_write_tokens"] is None and row["calls"] == 1 and row["observed_at"] > 0
    handoffs.note_usage("s1", {"input_tokens": 50, "output_tokens": 0, "cache_read_tokens": 0,
                                "cache_write_tokens": 0})
    assert handoffs.main_usage("s1")["calls"] == 2
    assert handoffs.main_usage("s1")["cache_write_tokens"] == 0


def test_routing_and_net_account_for_actual_main_prefix_and_each_cache_rate(world, monkeypatch):
    handoffs.note_usage("s1", {"input_tokens": 2000, "prompt_tokens": 10000, "output_tokens": 100,
                                "cache_read_tokens": 7000, "cache_write_tokens": 1000})
    expected = 2000 * PRICES["main"]["input"] + 7000 * PRICES["main"]["cache_read"] + 1000 * PRICES["main"]["cache_write"]
    plan = cs._routing_plan(PICKED, "s1", input_tokens=30, expected_output_tokens=2000)
    assert plan["extra_main_read_usd"] == pytest.approx(expected)
    assert plan["cache_usage_observed"] and plan["worthwhile"]
    helper = Helper(text="reusable complete output " * 1000)
    response = json.loads(handoffs.execute(helper, "Produce the full deliverable.", "s1", "t1", expected_output_tokens=2000))
    cs.fill_placeholders(response["placeholder"], "s1", "t1")
    monkeypatch.setattr(cs, "status_for", lambda sid: {"enabled": True})
    monkeypatch.setattr(cs, "_chat_title", lambda sid: None)
    monkeypatch.setattr(chat, "analyse", lambda sid: {})
    monkeypatch.setattr(overview, "_chat_spent", lambda sid: None)
    report = cs.summary("s1")
    overall, turn = report["this_chat"], report["turns"][0]
    assert overall["estimated_extra_main_read_usd"] == pytest.approx(expected)
    assert turn["estimated_extra_main_read_usd"] == pytest.approx(expected)
    assert overall["net_once"] == pytest.approx(overall["once_at_main_price"] +
        overall["estimated_output_avoided_usd"] - overall["estimated_helper_read_usd"] -
        overall["estimated_main_typing_usd"] - overall["cheap_cost"] - expected)
    assert sum(row["extra_main_calls"] for row in report["breakdown"]) == 1


def test_unknown_prefix_cost_stays_unknown_in_net_accounting(world, monkeypatch):
    json.loads(handoffs.execute(Helper(), "Draft the complete deliverable.", "s1", "t1"))
    monkeypatch.setattr(cs, "status_for", lambda sid: {"enabled": True})
    monkeypatch.setattr(cs, "_chat_title", lambda sid: None)
    monkeypatch.setattr(chat, "analyse", lambda sid: {})
    monkeypatch.setattr(overview, "_chat_spent", lambda sid: None)
    row = cs.summary("s1")["this_chat"]
    assert row["unknown_prefix_cost_calls"] == 1 and row["net_once"] is None


def test_failed_calls_account_for_reported_usage_and_actual_billed_route(world):
    error = RuntimeError("host failed after billing")
    error.usage = {"prompt_tokens": 100, "completion_tokens": 20}
    error.model, error.provider = "fallback", "provider-b"
    helper = Helper(error=error)
    result = json.loads(handoffs.execute(helper, "Complete the task.", "s1", "failed"))
    assert "error" in result and len(helper.calls) == 1
    row = attempts()[0]
    assert row["cheap_tokens"] == 120 and row["input_tokens"] == 100 and row["output_tokens"] == 20
    assert row["cheap_cost"] == pytest.approx(100 * 3e-6 + 20 * 1e-5)
    assert row["status"] == "failed"
    assert cs._observations(PICKED, "s1", "routed")[0]["id"] == row["id"]


def test_rejected_host_fallback_is_charged_at_actual_prices_but_learned_as_route_failure(world):
    helper = Helper()
    helper.answer.model, helper.answer.provider = "fallback", "provider-b"
    response = json.loads(handoffs.execute(helper, "Complete the task.", "s1"))
    assert "error" in response and attempts()[0]["status"] == "rejected"
    assert attempts()[0]["cheap_cost"] == pytest.approx(1000 * 3e-6 + 100 * 1e-5)
    assert cs._observations(PICKED, "s1", "routed")


def test_unknown_failed_cost_is_not_a_free_call_and_host_zero_is_respected(world):
    helper = Helper(error=RuntimeError("unknown charge"))
    handoffs.execute(helper, "Complete the task.", "s1")
    row = store.list_chat_savings("s1")[0]
    assert row["unknown_cost_calls"] == 1 and row["cheap_cost"] is None
    assert cs._usage_cost({"input_tokens": 10, "output_tokens": 1, "cost_usd": 0.0}, PICKED) == (11, 0.0)
    assert cs._reported_cost({"cost_usd": False}) is None


def test_output_savings_need_delivery_and_survive_accounting_cache_reset(world):
    response = json.loads(handoffs.execute(Helper(text="long complete output " * 100), "Write this.", "s1"))
    assert store.list_chat_savings("s1")[0]["output_chars"] == 0
    assert "unavailable" in cs.fill_placeholders(response["placeholder"], "s2", "t1")
    cs.fill_placeholders(response["placeholder"], "s1", "t1")
    first = store.list_chat_savings("s1")[0]["output_chars"]
    cs.cleanup_session("s1")
    cs.fill_placeholders(response["placeholder"], "s1", "t2")
    assert store.list_chat_savings("s1")[0]["output_chars"] == first > 0


def test_output_scaled_timeout_is_separate_from_summary_and_learns_the_chosen_route(world):
    assert handoffs.timeout(PICKED, "s1", 1200) > cs.CONDENSE_SECONDS
    assert handoffs.timeout(PICKED, "s1", 3000) > handoffs.timeout(PICKED, "s1", 300)
    for _ in range(3):
        store.add_chat_event("s1", kind="routed", helper_call=True, cheap_cost=0.0002,
                             output_tokens=1000, duration_ms=30000, **PICKED)
    assert cs._routing_pays(PICKED, "s1")
    assert handoffs.timeout(PICKED, "s1", 1000) == pytest.approx(55)
    helper = Helper()
    cs._call(helper, [], "summary test", session_id="s1", picked=PICKED)
    assert helper.calls[0][1]["timeout"] == cs.CONDENSE_SECONDS


def test_routing_uses_workload_economics_rather_than_a_universal_input_price_gap(world, monkeypatch):
    prices = {"helper": {"input": 2e-6, "output": 2e-7}, "main": PRICES["main"]}
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda model, provider: prices.get(model))
    assert cs._is_cheaper(PICKED, "s1", "routed")
    assert cs._routing_plan(PICKED, "s1", input_tokens=10, expected_output_tokens=2000)["worthwhile"]
    assert "output" in json.loads(handoffs.execute(Helper(), "Draft the table.", "s1", expected_output_tokens=2000))


def test_model_alias_matching_does_not_accept_a_different_named_variant():
    assert cs._same_model("vendor/model-2026-11-06", "vendor/model")
    assert cs._same_model("vendor/model", "model")
    assert not cs._same_model("vendor/model-pro", "vendor/model")
    assert not cs._same_model("other/model", "vendor/model")
