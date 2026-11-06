"""Record every tool call so Crystallize can line up runs later.

Hooks are observers: anything that goes wrong here is logged and swallowed,
so recording can never break a Hermes turn.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable

from . import store

logger = logging.getLogger(__name__)

# Our own tools are never part of a routine we learn from.
_IGNORED_TOOLS = {"crystal_list", "crystal_run"}
_MAX_ARGS_CHARS = 8000


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def make_post_tool_call(ctx: Any) -> Callable[..., None]:
    def on_post_tool_call(
        tool_name: str = "",
        args: Any = None,
        result: Any = None,
        task_id: str = "",
        duration_ms: int | None = None,
        session_id: str = "",
        tool_call_id: str = "",
        status: str | None = None,
        **_: Any,
    ) -> None:
        try:
            if not tool_name or tool_name in _IGNORED_TOOLS:
                return
            if not ctx.get_config("capture_enabled", default=True):
                return

            preview_chars = int(ctx.get_config("result_preview_chars", default=2000) or 0)
            result_text = _as_text(result)
            args_text = _as_text(args or {})[:_MAX_ARGS_CHARS]

            run = ctx.current_cron_execution()
            if run is None and not store.chat_enabled(session_id):
                return  # Crystallize is off for this chat
            execution_id = getattr(run, "execution_id", None) if run else None
            scheduled = None
            if run is not None:
                # Manual runs (`hermes cron run`) have no scheduled_instant; we skip them when learning.
                scheduled = 1 if getattr(run, "scheduled_instant", None) else 0
            store.add_trace({
                "ts": store.now(),
                "session_id": session_id or None,
                "task_id": task_id or None,
                "tool_call_id": tool_call_id or None,
                "tool_name": tool_name,
                "args_json": args_text,
                "result_sha256": hashlib.sha256(result_text.encode("utf-8", "replace")).hexdigest(),
                "result_len": len(result_text),
                "result_preview": result_text[:preview_chars] if preview_chars > 0 else None,
                "status": status,
                "duration_ms": duration_ms,
                "cron_job_id": getattr(run, "job_id", None),
                "cron_job_name": getattr(run, "job_name", None),
                "cron_execution_id": str(execution_id) if execution_id is not None else None,
                "cron_scheduled": scheduled,
            })
        except Exception:  # noqa: BLE001 — an observer must never break the turn
            logger.debug("crystallize: failed to record tool call", exc_info=True)

    return on_post_tool_call


def make_transform_tool_result(ctx: Any) -> Callable[..., Any]:
    """Clean a tool's result before the AI reads it, for cron jobs in a Crystallize trial
    or keeping its settings. Every later AI call re-reads it, so this saves on all of them."""
    from . import prerun, tuning

    def transform(tool_name: str = "", result: Any = None, **_: Any) -> Any:
        try:
            run = ctx.current_cron_execution()
            name = getattr(run, "job_name", None) if run else None
            if not name or tool_name not in prerun.CLEANED_TOOLS or not tuning.cleans_results(name):
                return None
            cleaned = prerun.clean_result(tool_name, result)
            if cleaned is None:
                return None
            store.add_cleaned(name, len(result) - len(cleaned))
            return cleaned
        except Exception:  # noqa: BLE001 — a hook must never break the run
            logger.debug("crystallize: cleaning a tool result failed", exc_info=True)
            return None

    return transform


RUN_MARKER = "__run__"  # stands for a cron run that made no tool calls


def make_on_session_end(ctx: Any) -> Callable[..., None]:
    """After a scheduled cron run: make sure the run is on record (a live
    routine's run may make no tool calls at all), then test the job's routine
    if it's being tested."""
    from . import lifecycle, tools, tuning

    def on_session_end(session_id: str = "", **_: Any) -> None:
        try:
            run = ctx.current_cron_execution()
            if run is None or not getattr(run, "scheduled_instant", None):
                return
            execution_id = str(run.execution_id)
            if not store.execution_traces(execution_id):
                store.add_trace({
                    "ts": store.now(), "session_id": session_id or None, "tool_name": RUN_MARKER,
                    "args_json": "{}", "cron_job_id": getattr(run, "job_id", None),
                    "cron_job_name": getattr(run, "job_name", None),
                    "cron_execution_id": execution_id, "cron_scheduled": 1,
                })
            lifecycle.test_after(execution_id, run.job_id, tools.dispatch)
            tuning.refresh()
        except Exception:  # noqa: BLE001 — an observer must never break the run
            logger.debug("crystallize: after-run bookkeeping failed", exc_info=True)

    return on_session_end
