"""Public SDK dispatch readiness and non-executing host-tool plans.

The documented PluginContext.dispatch_tool contract promises normal host guards.
The audited SDK implementation dispatches directly through the registry instead.
Method presence, a version string, or a plugin setting cannot certify the missing
guards. Until a documented, verified public capability exists, nested execution
is disabled and the main agent receives ordinary host-tool guidance.
"""
from __future__ import annotations

import copy
from typing import Any

_ctx: Any = None

REASON = (
    "Automatic nested tool execution is unavailable: the current public SDK dispatch "
    "does not preserve the normal host guard pipeline. Have the main agent make the "
    "listed calls through its ordinary tools, where approvals, hooks, redaction and budgets apply."
)


class UnsupportedDispatch(RuntimeError):
    """No verified public nested-execution contract is available."""


def set_context(ctx: Any) -> None:
    global _ctx
    _ctx = ctx


def support(ctx: Any = None) -> dict[str, Any]:
    """Return capability facts without executing a tool or making provider calls."""
    context = ctx if ctx is not None else _ctx
    try:
        has_dispatch = callable(getattr(context, "dispatch_tool", None)) if context is not None else False
    except Exception:
        has_dispatch = False
    try:
        has_helper = callable(getattr(getattr(context, "llm", None), "complete", None)) if context is not None else False
    except Exception:
        has_helper = False
    return {
        "schema_version": 1,
        "context_bound": context is not None,
        "public_dispatch_method_present": has_dispatch,
        "public_contract_claims_host_guards": True,
        "public_contract_verified": False,
        "guarded_dispatch_available": False,
        "automatic_nested_tools": False,
        "text_helpers_available": has_helper,
        "owned_placeholders_available": True,
        "input_files_mode": "normal_host_tool_plan",
        "save_to_mode": "normal_host_tool_plan",
        "fallback": "normal_host_tool_plan",
        "contract": "unverified_public_dispatch",
        "reason": REASON,
        "raw_registry_fallback_enabled": False,
    }


def require(ctx: Any = None) -> Any:
    context = ctx if ctx is not None else _ctx
    if not support(context)["guarded_dispatch_available"]:
        raise UnsupportedDispatch(REASON)
    return context


def tool_plan(actions: list[dict[str, Any]], *, purpose: str, instructions: str,
              continuation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Describe calls for the ordinary main-agent loop; never execute or approve."""
    result = {
        "schema_version": 1,
        "execution": "normal_host_tools_only",
        "purpose": purpose,
        "actions": copy.deepcopy(actions),
        "instructions": instructions,
        "completed": False,
        "automatically_executed": False,
    }
    if continuation is not None:
        result["continuation"] = copy.deepcopy(continuation)
    return result


def routine_plan(routine: dict[str, Any]) -> dict[str, Any]:
    """Expose a validated recipe as fresh, checkable guidance, without replaying."""
    from . import analyze, parameters

    recipe = routine.get("steps")
    if not isinstance(recipe, dict):
        raise ValueError("The saved recipe must be an object.")
    parameters.validate(recipe)
    reason = analyze.replay_safety_error(recipe)
    if reason:
        raise ValueError(reason)
    actions = []
    for index, step in enumerate(recipe["steps"], 1):
        args = step["args"]
        actions.append({
            "step": index,
            "tool": step["tool"],
            "arguments": copy.deepcopy(args),
            "requires_fresh_binding": bool(parameters.contains(args) or step.get("fill_ins")),
            "fill_ins": list(step.get("fill_ins") or []),
            "check": copy.deepcopy(step.get("check") or {}),
        })
    return {
        "ok": False,
        "status": "pending_host_tools",
        "execution": "normal_host_tools_only",
        "completed": False,
        "outputs": [],
        "routine": routine["name"],
        "routine_id": routine["id"],
        "reason": REASON,
        "execution_validation": "not_run",
        "host_tool_plan": tool_plan(
            actions,
            purpose="Run the approved recipe through the main agent's ordinary tools.",
            instructions=(
                "These are instructions, not executed results. Do not call crystal_run again to execute them. "
                "Check that each argument still matches the current user task. Resolve dates, earlier-result "
                "bindings and run-local artifact locations from fresh evidence before calling dependent steps. "
                "Make each call through your normal host tools, respect approvals and inspect its actual result "
                "against the listed checks. If a binding, permission or result cannot be verified, finish the task "
                "yourself. Continue all remaining reasoning and user-requested actions after these observations."
            ),
        ),
    }
