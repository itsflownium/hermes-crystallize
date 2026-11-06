"""A public method alone must never authorize guard-bypassing nested execution."""

import ast
import builtins
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from crystallize import capture, chat_savings as cs, dispatch_support, handoffs, store, tools


@pytest.fixture
def world(monkeypatch, tmp_path):
    originals = tmp_path / "originals"
    originals.mkdir()
    monkeypatch.setattr(cs, "_full_dir", lambda: originals)
    monkeypatch.setattr(cs, "chosen_model", lambda: {"model": "configured-helper", "provider": "public-provider"})
    monkeypatch.setattr(cs, "_main_model", lambda sid: {"model": "configured-main", "provider": "public-provider"})
    # Unknown prices do not disable explicitly opted-in, self-contained text work.
    monkeypatch.setattr(cs.modelinfo, "token_prices", lambda *a: None)
    monkeypatch.setattr(dispatch_support, "_ctx", None)
    monkeypatch.setattr(tools, "_ctx", None)
    store.set_chat_enabled("owner", True)
    return originals


class PublicContext:
    def __init__(self, text="  retained complete output ☀\n" * 100, error=None):
        self.calls = []
        self.text, self.error = text, error
        self.llm = SimpleNamespace(complete=self.complete)

    def complete(self, messages, **settings):
        self.calls.append((messages, settings))
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text, model="configured-helper", provider="public-provider",
            usage={"input_tokens": 100, "output_tokens": 150, "cost_usd": 0.003}, finish_reason="stop")

    def dispatch_tool(self, *args, **kwargs):
        pytest.fail("The current SDK's unverified public dispatch must not be invoked.")

    def current_cron_execution(self):
        return None

    def get_config(self, *args):
        pytest.fail("A configuration setting is not evidence that SDK guards exist.")


def test_support_is_read_only_and_does_not_treat_method_presence_or_settings_as_guard_evidence(world):
    ctx = PublicContext()
    ctx.guarded_dispatch_available = True
    ctx.capabilities = {"guarded_dispatch": True, "approval": True}
    result = dispatch_support.support(ctx)
    assert result["public_dispatch_method_present"] and result["text_helpers_available"]
    assert result["public_contract_claims_host_guards"] and not result["public_contract_verified"]
    assert result["guarded_dispatch_available"] is False and result["automatic_nested_tools"] is False
    assert result["owned_placeholders_available"] and result["input_files_mode"] == "normal_host_tool_plan"
    assert result["save_to_mode"] == "normal_host_tool_plan" and not result["raw_registry_fallback_enabled"]
    assert not ctx.calls


def test_support_handles_a_missing_or_unavailable_public_facade_without_executing_it(world):
    class Missing:
        @property
        def llm(self):
            raise RuntimeError("public facade unavailable")

    result = dispatch_support.support(Missing())
    assert not result["text_helpers_available"] and not result["guarded_dispatch_available"]
    assert dispatch_support.support()["context_bound"] is False


def test_dispatch_fails_closed_without_private_imports_or_registry_execution(world, monkeypatch):
    ctx = PublicContext()
    tools.set_context(ctx)
    real_import = builtins.__import__

    def no_private(name, *args, **kwargs):
        if name == "model_tools" or name == "tools.registry":
            pytest.fail("A nested dispatch attempted a private core import or raw registry fallback.")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_private)
    with tools.caller_context(session_id="owner", task_id="host-task", turn_id="host-turn"):
        with pytest.raises(dispatch_support.UnsupportedDispatch, match="ordinary tools"):
            tools.dispatch("read_file", {"path": "notes.txt"})
    assert tools._caller.get() == {} and not ctx.calls
    assert dispatch_support.support()["context_bound"] is True


def test_unsupported_input_files_return_normal_read_plan_before_any_paid_call(world):
    ctx = PublicContext()
    result = json.loads(handoffs.execute(ctx, "Summarize both complete sources.", "owner", "read",
        input_files=["first.txt", "second.txt"], save_to="report.txt", expected_output_tokens=900, subtask=True))
    assert result["status"] == "pending_host_tools" and result["helper_called"] is False
    assert result["written"] is False and not result["completed"] and not ctx.calls
    plan = result["host_tool_plan"]
    assert not plan["automatically_executed"] and plan["execution"] == "normal_host_tools_only"
    assert plan["actions"] == [
        {"tool": "read_file", "arguments": {"path": "first.txt", "offset": 1, "limit": 1000}},
        {"tool": "read_file", "arguments": {"path": "second.txt", "offset": 1, "limit": 1000}},
    ]
    resume = plan["continuation"]
    assert resume["requires_complete_source_text_in_input"]
    assert resume["arguments"] == {"input": "Summarize both complete sources.", "save_to": "report.txt",
                                    "expected_output_tokens": 900, "subtask": True}
    accounting = store.list_chat_savings("owner")[0]
    assert accounting["helper_calls"] == 0 and accounting["cheap_cost"] == 0
    assert accounting["output_chars"] == 0 and accounting["added_input_chars"] > 0


def test_direct_input_reader_is_gated_even_when_it_is_not_called_through_execute(world):
    with pytest.raises(dispatch_support.UnsupportedDispatch):
        handoffs.read_inputs(["notes.txt"], "owner", "turn", 5000)
    assert handoffs.read_inputs([], "owner", "turn", 5000) == ""


def test_self_contained_public_text_helper_still_works_with_unknown_prices(world):
    ctx = PublicContext()
    result = json.loads(handoffs.execute(ctx, "Translate this supplied source text: bonjour.", "owner", "text",
                                        subtask=True, expected_output_tokens=900))
    assert result["output"] == ctx.text and len(ctx.calls) == 1
    assert result["completion"]["limit_requested_not_guaranteed"]
    assert set(ctx.calls[0][1]) == {"task", "timeout", "max_tokens", "purpose"}
    assert store.list_chat_savings("owner")[0]["cheap_cost"] == 0.003


def test_save_to_retains_answer_and_returns_one_regular_write_without_paid_rerun(world, monkeypatch):
    ctx = PublicContext()
    result = json.loads(handoffs.execute(ctx, "Draft the complete report from supplied text.", "owner", "paid",
                                        save_to="report.txt", expected_output_tokens=900))
    assert len(ctx.calls) == 1 and result["status"] == "pending_host_tools"
    assert result["written"] is False and result["write_attempted"] is False and not result["completed"]
    assert "output" not in result and result["preview"] == ctx.text[:500]
    assert cs._load_full(result["full_result_id"], "owner") == ctx.text
    assert cs._load_full(result["full_result_id"], "other") is None
    action = result["host_tool_plan"]["actions"][0]
    assert action == {"tool": "write_file", "arguments": {"path": "report.txt", "content": result["placeholder"]}}
    first = store.list_chat_savings("owner")[0]
    assert first["helper_calls"] == 1 and first["cheap_cost"] == 0.003 and first["output_chars"] == 0

    # The ordinary host pipeline calls the documented hook before its guard.
    hook = capture.make_pre_tool_call(ctx)
    changed = hook(tool_name="write_file", args=action["arguments"], session_id="owner", turn_id="normal-write")
    assert changed == {"action": "modify", "args": {"path": "report.txt", "content": ctx.text}}
    seen_by_guard = changed["args"]
    assert seen_by_guard["content"] == ctx.text
    blocked_result = {"error": "ordinary host guard blocked the write"}
    assert blocked_result["error"] and result["written"] is False  # no fabricated completion
    assert len(ctx.calls) == 1  # permission handling never invokes the paid helper again
    credited = store.list_chat_savings("owner")[0]["output_chars"]
    assert credited == len(ctx.text) - len(result["placeholder"])
    cs.fill_placeholders(result["placeholder"], "owner", "later")
    handoffs.expand_args(action["arguments"], "owner", "retry", "write_file")
    assert store.list_chat_savings("owner")[0]["output_chars"] == credited


def test_pending_save_placeholder_is_blocked_in_another_chat_and_in_command_or_path_fields(world):
    ctx = PublicContext()
    result = json.loads(handoffs.execute(ctx, "Draft a report.", "owner", save_to="report.txt"))
    marker = result["placeholder"]
    hook = capture.make_pre_tool_call(ctx)
    assert hook(tool_name="write_file", args={"path": "report.txt", "content": marker}, session_id="other")["action"] == "block"
    assert hook(tool_name="terminal", args={"command": marker}, session_id="owner")["action"] == "block"
    assert hook(tool_name="write_file", args={"path": marker, "content": "safe"}, session_id="owner")["action"] == "block"
    assert store.list_chat_savings("owner")[0]["output_chars"] == 0


def test_failed_or_cutoff_helpers_never_emit_a_write_plan_and_keep_their_costs(world):
    ctx = PublicContext(error=RuntimeError("provider error"))
    failed = json.loads(handoffs.execute(ctx, "Draft the report.", "owner", "failed", save_to="report.txt"))
    assert "error" in failed and "host_tool_plan" not in failed and len(ctx.calls) == 1
    assert store.list_chat_savings("owner")[0]["unknown_cost_calls"] == 1
    ctx.error = None
    complete = ctx.complete

    def cut_off(*a, **kw):
        answer = complete(*a, **kw)
        answer.finish_reason = "length"
        return answer

    ctx.llm.complete = cut_off
    rejected = json.loads(handoffs.execute(ctx, "Draft the report.", "owner", "rejected", save_to="report.txt"))
    assert rejected["completion"]["cut_off"] and "host_tool_plan" not in rejected
    row = store.list_chat_savings("owner")[0]
    assert row["known_cheap_cost"] == 0.003 and row["rejected_calls"] == 1 and row["helper_calls"] == 2
    assert row["cheap_cost"] is None and row["output_chars"] == 0


def live_routine(**changes):
    row = {"id": "routine", "name": "Read the current record", "status": "live", "source_ref": "job",
           "meta": {}, "steps": {"steps": [{"tool": "web_extract", "args": {"url": "https://example.test"},
                                           "check": {"min_len": 10}}]}}
    row.update(changes)
    return row


def test_crystal_run_returns_validated_guidance_without_nested_execution_or_live_success(world, monkeypatch):
    ctx = PublicContext()
    tools.set_context(ctx)
    routine = live_routine()
    monkeypatch.setattr(store, "get_routine", lambda rid: routine)
    monkeypatch.setattr(tools.lifecycle, "check_drift", lambda saved: True)
    monkeypatch.setattr(tools.lifecycle, "live_result", lambda *a, **kw: pytest.fail("guidance is not an execution result"))
    monkeypatch.setattr(tools.runner, "run", lambda *a, **kw: pytest.fail("unsupported nested replay executed"))
    result = json.loads(tools.crystal_run({"routine_id": "routine"}, session_id="owner", task_id="task"))
    assert result["status"] == "pending_host_tools" and result["outputs"] == []
    assert not result["ok"] and not result["completed"] and result["execution_validation"] == "not_run"
    assert result["host_tool_plan"]["actions"][0]["tool"] == "web_extract"
    assert not result["host_tool_plan"]["automatically_executed"] and not ctx.calls


def test_guidance_keeps_runtime_bindings_unresolved_and_does_not_create_artifacts(world):
    recipe = {"steps": [
        {"id": "first", "tool": "web_extract", "args": {"url": "https://example.test/lookup"}},
        {"tool": "web_extract", "args": {"url": {"$url": {"template": "https://example.test/{id}",
            "values": {"id": {"$result": {"step": "first", "path": ["id"]}}}}}}},
    ]}
    result = dispatch_support.routine_plan(live_routine(steps=recipe))
    second = result["host_tool_plan"]["actions"][1]
    assert second["requires_fresh_binding"] and second["arguments"] == recipe["steps"][1]["args"]
    second["arguments"]["url"]["$url"]["template"] = "changed by caller"
    assert recipe["steps"][1]["args"]["url"]["$url"]["template"] == "https://example.test/{id}"


@pytest.mark.parametrize("recipe", [
    None,
    {"steps": [{"tool": "write_file", "args": {"path": "x", "content": "write"}}]},
    {"steps": [{"tool": "terminal", "args": {"command": "rm -rf task"}}]},
    {"steps": [{"tool": "web_extract", "args": {"url": {"$result": {"step": 2}}}}]},
])
def test_unsafe_or_invalid_recipe_is_not_laundered_into_an_approved_action_plan(world, monkeypatch, recipe):
    monkeypatch.setattr(store, "get_routine", lambda rid: live_routine(steps=recipe))
    monkeypatch.setattr(tools.lifecycle, "check_drift", lambda saved: True)
    result = json.loads(tools.crystal_run({"routine_id": "routine"}, session_id="owner"))
    assert "error" in result and "host_tool_plan" not in result and not result["completed"]


def test_dispatch_production_modules_have_no_private_core_execution_imports():
    root = Path(__file__).resolve().parents[1]
    for name in ("tools.py", "dispatch_support.py", "handoffs.py"):
        tree = ast.parse((root / name).read_text())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and not node.level]
        assert "model_tools" not in imports and "tools.registry" not in imports


def test_audited_public_sdk_dispatch_really_skips_normal_pretool_guard(monkeypatch):
    """Optional source-backed audit; isolated registry contains one harmless fake."""
    source_value = os.environ.get("CRYSTAL_HERMES_SOURCE")
    if not source_value:
        pytest.skip("Set CRYSTAL_HERMES_SOURCE to audit a specific local SDK checkout.")
    source = Path(source_value)
    method_tree = ast.parse((source / "hermes_cli/plugins.py").read_text())
    cls = next(n for n in method_tree.body if isinstance(n, ast.ClassDef) and n.name == "PluginContext")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "dispatch_tool")
    # Compile the exact public method body, without host startup/discovery.
    isolated_cls = ast.ClassDef(name="IsolatedContext", bases=[], keywords=[], body=[method], decorator_list=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[isolated_cls], type_ignores=[])),
                 "verbatim-public-sdk-dispatch", "exec"), namespace)
    monkeypatch.syspath_prepend(str(source))
    from tools.registry import ToolRegistry
    import sys
    import types
    from unittest.mock import patch

    registry = ToolRegistry()
    handler_calls, guard_calls = [], []

    def harmless(args, **kwargs):
        handler_calls.append(args)
        return json.dumps({"output": "isolated synthetic result"})

    registry.register(name="public_dispatch_test", toolset="probe",
                      schema={"name": "public_dispatch_test", "parameters": {"type": "object"}}, handler=harmless)
    ctx = namespace["IsolatedContext"]()
    ctx._manager = SimpleNamespace(_cli_ref=None, scope_key=None)
    stub = types.ModuleType("hermes_cli.plugins")

    def deny(*args, **kwargs):
        guard_calls.append(True)
        return "isolated host policy refused", None

    stub._dispatch_pre_tool_call_hooks = deny
    normal_tree = ast.parse((source / "model_tools.py").read_text())
    guard = next(n for n in normal_tree.body if isinstance(n, ast.FunctionDef) and n.name == "_pre_dispatch_guards")
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    guard_ns = {"tool_error": lambda message: json.dumps({"error": message})}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, guard], type_ignores=[])),
                 "verbatim-normal-host-guard", "exec"), guard_ns)
    with patch.dict(sys.modules, {"hermes_cli.plugins": stub}), patch("tools.registry.registry", registry):
        _, blocked = guard_ns["_pre_dispatch_guards"]("public_dispatch_test", {}, False,
            SimpleNamespace(hook_kwargs=lambda: {}), [])
        assert blocked and guard_calls == [True]
        ctx.dispatch_tool("public_dispatch_test", {})
    assert len(handler_calls) == 1 and guard_calls == [True]
