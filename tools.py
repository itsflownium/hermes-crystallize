"""Tool handlers. Handlers return JSON strings and never raise."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4
from typing import Any, Optional

from . import dispatch_support, lifecycle, runner, store

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
    dispatch_support.set_context(ctx)


def dispatch(name: str, args: dict, **caller_context: Any) -> str:
    """Use only a verified public host dispatch; the current SDK fails closed."""
    context = dispatch_support.require(_ctx)
    identity = {**_caller.get(), **{k: v for k, v in caller_context.items() if k in _CALLER_FIELDS}}
    # Nested calls need their own identity, rather than overwriting the outer
    # crystal_run receipt or causing tools to share an approval/cache slot.
    identity["tool_call_id"] = f"crystal-{uuid4().hex}"
    return context.dispatch_tool(name, args, **identity)


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
    if not dispatch_support.support(_ctx)["guarded_dispatch_available"]:
        try:
            return json.dumps(dispatch_support.routine_plan(routine), ensure_ascii=False)
        except (ValueError, TypeError) as exc:
            return json.dumps({"ok": False, "completed": False, "outputs": [], "routine": routine["name"],
                               "error": f"The saved recipe cannot supply safe guidance: {exc}. Do the task yourself."})
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
    from . import handoffs

    try:
        identity = _identity(kwargs)
        if _ctx is None:
            return json.dumps({"error": "Crystallize isn't fully loaded yet. Do this yourself."})
        args = args or {}
        if not isinstance(args.get("subtask", False), bool):
            raise ValueError("subtask must be true or false.")
        return handoffs.execute(_ctx, args.get("input"), str(identity.get("session_id") or ""),
            str(identity.get("turn_id") or ""), input_files=args.get("input_files"), save_to=args.get("save_to"),
            expected_output_tokens=args.get("expected_output_tokens"), subtask=args.get("subtask", False))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("crystallize: crystal_cheap failed: %s", exc)
        return json.dumps({"error": "The cheap model couldn't be reached. Do this yourself."})
