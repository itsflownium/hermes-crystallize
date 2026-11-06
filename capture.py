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
            # Replay inputs are data, not a display preview. Slicing serialized JSON
            # can both hide changed tails and make the next dispatch use wrong args.
            args_text = _as_text(args if args is not None else {})

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
                "result_full": result_text if run is not None else None,
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
    from . import chat_savings, prerun, tuning

    def transform(tool_name: str = "", result: Any = None, session_id: str = "", turn_id: str = "", **_: Any) -> Any:
        try:
            run = ctx.current_cron_execution()
            if run is None:
                if not session_id or not store.chat_enabled(session_id):
                    return None
                return chat_savings.shrink(ctx, session_id, tool_name, result, turn_id=turn_id)
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


def make_post_api_request(ctx: Any) -> Callable[..., None]:
    """Observe real Chat usage and keep Cron token totals for Runaway Brake."""
    from . import brake

    def after_call(session_id: str = "", usage: Any = None, **_: Any) -> None:
        try:
            from . import handoffs
            if ctx.current_cron_execution() is None:
                handoffs.note_usage(session_id, usage)
            if ctx.current_cron_execution() is not None and brake.enabled():
                brake.note_usage(session_id, usage)
        except Exception:  # noqa: BLE001 — a hook must never break the run
            logger.debug("crystallize: counting tokens failed", exc_info=True)

    return after_call


def make_pre_tool_call(ctx: Any) -> Callable[..., Any]:
    """Check the Cron brake and recover owned text before host tool checks."""
    from . import brake

    def before_tool(session_id: str = "", args: Any = None, tool_name: str = "", turn_id: str = "", **_: Any) -> Any:
        try:
            run = ctx.current_cron_execution()
            stopped = brake.check(getattr(run, "job_name", None), session_id) if run is not None else None
            if stopped:
                return stopped
            from . import handoffs
            try:
                changed = handoffs.expand_args(args, session_id, turn_id, tool_name)
                return {"action": "modify", "args": changed} if changed is not None else None
            except ValueError as exc:
                return {"action": "block", "message": str(exc)}
        except Exception:  # noqa: BLE001 — a hook must never break the run
            logger.debug("crystallize: Runaway Brake check failed", exc_info=True)
            return None

    return before_tool


def make_llm_request(ctx: Any) -> Callable[..., Any]:
    """Apply independent, opt-in wire savings. Every layer fails open on its own."""
    from . import rent, reread

    def rewrite(request: Any = None, session_id: str = "", **_: Any) -> Any:
        current = request
        try:
            changed = rent.apply(request, session_id)
            if isinstance(changed, dict):
                current = changed
        except Exception:  # noqa: BLE001 — a failed saving must never stop the AI call
            logger.debug("crystallize: leaving out removed results failed", exc_info=True)
        try:
            changed = reread.rewrite(current, session_id)
            if isinstance(changed, dict):
                current = changed
        except Exception:  # noqa: BLE001 — retain a successful earlier projection
            logger.debug("crystallize: re-read projection failed", exc_info=True)
        try:
            run = ctx.current_cron_execution()
            if run is not None:
                from . import adaptive
                job_ref = getattr(run, "job_id", None) or getattr(run, "job_name", None)
                if job_ref:
                    changed = adaptive.prune_request(current, job_ref)
                    if isinstance(changed, dict):
                        current = changed
        except Exception:  # noqa: BLE001 — keep all tools on uncertain pruning evidence
            logger.debug("crystallize: optional cron schema pruning failed", exc_info=True)
        return {"request": current} if current is not request else None

    return rewrite


def make_pre_llm_call(ctx: Any) -> Callable[..., Any]:
    """Tell the main model it can route routine work, in chats where Crystallize Chat is on."""
    from . import chat_savings

    def pre_llm_call(**kwargs: Any) -> Any:
        try:
            if ctx.current_cron_execution() is not None:
                return None
            return chat_savings.routing_context(**kwargs)
        except Exception:  # noqa: BLE001 — a hook must never break a turn
            logger.debug("crystallize: routing note failed", exc_info=True)
            return None

    return pre_llm_call


def make_transform_llm_output(ctx: Any) -> Callable[..., Any]:
    """Fill in the cheap model's text, so the main model doesn't pay to retype it."""
    from . import chat_savings

    def transform(response_text: str = "", session_id: str = "", turn_id: str = "", **_: Any) -> Any:
        try:
            return chat_savings.fill_placeholders(response_text, session_id=session_id, turn_id=turn_id)
        except Exception:  # noqa: BLE001 — a hook must never break a turn
            logger.debug("crystallize: filling placeholders failed", exc_info=True)
            return None

    return transform


RUN_MARKER = "__run__"  # stands for a cron run that made no tool calls


def make_on_session_end(ctx: Any) -> Callable[..., None]:
    """After a scheduled cron run: make sure the run is on record (a live
    routine's run may make no tool calls at all), then test the job's routine
    if it's being tested."""
    from . import brake, chat_savings, lifecycle, tools, tuning

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
        finally:
            # This hook also runs for ordinary/manual sessions and bookkeeping errors.
            # Only this profile/session's active in-memory observations are removed.
            try:
                brake.forget(session_id)
            except Exception:  # noqa: BLE001
                logger.debug("crystallize: brake cleanup failed", exc_info=True)
            try:
                chat_savings.cleanup_session(session_id)
            except Exception:  # noqa: BLE001
                logger.debug("crystallize: chat cleanup failed", exc_info=True)

    return on_session_end
