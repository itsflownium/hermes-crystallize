"""Tool handlers. Handlers return JSON strings and never raise."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4
from typing import Any, Optional

from . import lifecycle, runner, store

logger = logging.getLogger(__name__)

_ctx: Optional[Any] = None
_caller: ContextVar[dict[str, Any]] = ContextVar("crystallize_tool_caller", default={})
_CALLER_FIELDS = {"task_id", "session_id", "turn_id", "api_request_id", "user_task", "enabled_tools",
                  "enabled_toolsets", "disabled_toolsets"}


@contextmanager
def caller_context(**identity: Any):
    """Bind trusted host middleware identity while it invokes a tool handler."""
    token = _caller.set({**_caller.get(), **{k: v for k, v in identity.items() if k in _CALLER_FIELDS and v is not None}})
    try:
        yield
    finally:
        _caller.reset(token)


def _identity(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {**_caller.get(), **{k: v for k, v in kwargs.items() if k in _CALLER_FIELDS and v is not None}}


def set_context(ctx: Any) -> None:
    global _ctx
    _ctx = ctx


_builtins_loaded = False


def _ensure_builtin_tools() -> None:
    """Load Hermes's built-in tools when running outside an agent.

    Inside a chat or cron run the registry is already full. A bare terminal
    command (`hermes crystal test`) starts with an empty registry, so we ask
    Hermes to discover its built-ins once. Feature-detected: if the function
    moves, the test just reports the missing tool.
    """
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    try:
        from tools.registry import discover_builtin_tools

        discover_builtin_tools()
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: couldn't load built-in tools", exc_info=True)


def dispatch(name: str, args: dict, **caller_context: Any) -> str:
    """Run a nested tool through Hermes's ordinary guard/middleware pipeline.

    The plugin SDK's dispatch_tool is a raw registry call in supported Hermes
    versions. Do not silently fall back to it if the host pipeline moves.
    """
    if _ctx is None:
        raise RuntimeError("Crystallize isn't fully loaded yet.")
    try:
        from model_tools import handle_function_call
    except (ImportError, AttributeError) as exc:
        raise RuntimeError("This Hermes version cannot safely dispatch routine steps. Do the task yourself.") from exc
    context = {**_caller.get(), **{k: v for k, v in caller_context.items() if k in _CALLER_FIELDS}}
    # Nested calls need their own identity, rather than overwriting the outer
    # crystal_run receipt or causing tools to share an approval/cache slot.
    context["tool_call_id"] = f"crystal-{uuid4().hex}"
    if not _builtins_loaded:
        _ensure_builtin_tools()
    return handle_function_call(name, args, **context)


def crystal_list(args: dict[str, Any] | None = None, **_: Any) -> str:
    try:
        public = ("id", "name", "source", "status", "steps", "updated_at")
        return json.dumps({"routines": [{k: row[k] for k in public if k in row}
                                        for row in store.list_routines()]}, default=str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("crystallize: crystal_list failed: %s", exc)
        return json.dumps({"error": "Couldn't read saved routines."})


def crystal_run(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
    routine_id = str((args or {}).get("routine_id") or "").strip()
    if not routine_id:
        return json.dumps({"ok": False, "error": "routine_id is required."})
    try:
        routine = store.get_routine(routine_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("crystallize: crystal_run lookup failed: %s", exc)
        return json.dumps({"ok": False, "error": "Couldn't read saved routines. Do the task yourself."})
    if routine is None:
        return json.dumps({"ok": False, "error": f"No routine with id {routine_id!r}. Do the task yourself."})
    if routine["status"] != "live":
        return json.dumps({"ok": False, "error": f"Routine {routine_id!r} is {routine['status']}, not live. "
                                                 "Do the task yourself."})
    try:
        current = lifecycle.check_drift(routine)
    except Exception:  # noqa: BLE001 — never execute a recipe whose binding cannot be verified
        logger.warning("crystallize: crystal_run configuration check failed", exc_info=True)
        current = False
    if not current:
        reason = "This job's configuration changed or could not be verified, so the routine must be tested again."
        return json.dumps({"ok": False, "failed_step": 0, "reason": reason, "outputs": [],
                           "error": reason + " Do the task yourself.", "routine": routine["name"]})
    token = _caller.set(_identity(kwargs))
    try:
        try:
            result = runner.run(routine["steps"], dispatch)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "outputs": []}
    finally:
        _caller.reset(token)
    try:
        lifecycle.live_result(routine_id, bool(result.get("ok")), result.get("reason", ""))
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: live_result bookkeeping failed", exc_info=True)
    for out in result.get("outputs", []):
        out.pop("started", None)
        out.pop("args", None)
        text = out.get("output") or ""
        if len(text) > runner.MAX_OUTPUT_CHARS:
            out["output"] = text[:runner.MAX_OUTPUT_CHARS] + f"\n… (cut, {len(text)} characters in total)"
    if not result.get("ok"):
        result["error"] = f"Step {result.get('failed_step')} failed: {result.get('reason')}. Do the task yourself."
    result["routine"] = routine["name"]
    return json.dumps(result, default=str)


def crystal_full_result(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
    from . import chat_savings

    try:
        identity = _identity(kwargs)
        question = str((args or {}).get("question") or "").strip()
        session_id = str(identity.get("session_id") or "")
        if question:
            if _ctx is None:
                return json.dumps({"error": "Crystallize isn't fully loaded yet. Read the exact result instead."})
            return chat_savings.ask_full(_ctx, str((args or {}).get("result_id") or ""), question,
                                          session_id=session_id, turn_id=str(identity.get("turn_id") or ""))
        return chat_savings.read_full(str((args or {}).get("result_id") or ""), (args or {}).get("offset") or 0,
                                       session_id=session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("crystallize: crystal_full_result failed: %s", exc)
        return json.dumps({"error": "Couldn't read that result."})


def crystal_cheap(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
    from . import chat_savings

    try:
        identity = _identity(kwargs)
        if _ctx is None:
            return json.dumps({"error": "Crystallize isn't fully loaded yet. Do this yourself."})
        return chat_savings.ask_cheap(_ctx, str((args or {}).get("input") or ""),
                                      str(identity.get("session_id") or ""), str(identity.get("turn_id") or ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("crystallize: crystal_cheap failed: %s", exc)
        return json.dumps({"error": "The cheap model couldn't be reached. Do this yourself."})
