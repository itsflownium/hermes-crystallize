"""Tool handlers. Handlers return JSON strings and never raise."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from . import lifecycle, runner, store

logger = logging.getLogger(__name__)

_ctx: Optional[Any] = None


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


def dispatch(name: str, args: dict) -> str:
    """Run a tool through Hermes's registry (approvals, redaction and budgets apply)."""
    if _ctx is None:
        raise RuntimeError("Crystallize isn't fully loaded yet.")
    result = _ctx.dispatch_tool(name, args)
    if isinstance(result, str) and "Unknown tool" in result and not _builtins_loaded:
        _ensure_builtin_tools()
        result = _ctx.dispatch_tool(name, args)
    return result


def crystal_list(args: dict[str, Any] | None = None, **_: Any) -> str:
    try:
        return json.dumps({"routines": store.list_routines()}, default=str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("crystallize: crystal_list failed: %s", exc)
        return json.dumps({"error": "Couldn't read saved routines."})


def crystal_run(args: dict[str, Any] | None = None, **_: Any) -> str:
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
        result = runner.run(routine["steps"], dispatch)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "outputs": []}
    try:
        lifecycle.live_result(routine_id, bool(result.get("ok")), result.get("reason", ""))
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: live_result bookkeeping failed", exc_info=True)
    for out in result.get("outputs", []):
        text = out.get("output") or ""
        if len(text) > runner.MAX_OUTPUT_CHARS:
            out["output"] = text[:runner.MAX_OUTPUT_CHARS] + f"\n… (cut, {len(text)} characters in total)"
    if not result.get("ok"):
        result["error"] = f"Step {result.get('failed_step')} failed: {result.get('reason')}. Do the task yourself."
    result["routine"] = routine["name"]
    return json.dumps(result, default=str)
