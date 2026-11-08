"""Record every tool call so Crystallize can line up runs later.

Recording hooks are optional observers. Recovery hooks also protect transformed
text: unavailable placeholders must not reach a tool as literal arguments.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Optional

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
            # A job's id, because two jobs can share a name.
            name = (getattr(run, "job_id", None) or getattr(run, "job_name", None)) if run else None
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


def _run_key(run: Any, session_id: str) -> str:
    execution_id = getattr(run, "execution_id", None)
    return str(execution_id) if execution_id is not None else session_id


def _own_tool_chars() -> int:
    from . import schemas

    return sum(len(json.dumps(schema)) for schema in (schemas.CRYSTAL_LIST, schemas.CRYSTAL_RUN,
                                                      schemas.CRYSTAL_FULL_RESULT, schemas.CRYSTAL_CHEAP))


def make_post_api_request(ctx: Any) -> Callable[..., None]:
    """Observe real Chat usage and keep Cron token totals for Runaway Brake."""
    from . import brake

    def after_call(session_id: str = "", usage: Any = None, model: Any = None, **_: Any) -> None:
        try:
            from . import estimate, handoffs
            estimate.note_usage(session_id, model, usage)
            run = ctx.current_cron_execution()
            if run is None:
                if store.chat_enabled(session_id):
                    handoffs.note_usage(session_id, usage)
                    # Crystallize's own tools are sent with this call, so they count against its savings.
                    store.add_chat_event(session_id, kind="own_tools", results=0, added_input_chars=_own_tool_chars())
            elif brake.enabled():
                # Compression gives a run a new session id, but it's still one run.
                brake.note_usage(_run_key(run, session_id), usage)
        except Exception:  # noqa: BLE001 — a hook must never break the run
            logger.debug("crystallize: counting tokens failed", exc_info=True)

    return after_call


def make_pre_tool_call(ctx: Any) -> Callable[..., Any]:
    """Check the Cron brake and recover owned text before host tool checks."""
    from . import brake

    def before_tool(session_id: str = "", args: Any = None, tool_name: str = "", turn_id: str = "", **_: Any) -> Any:
        try:
            run = ctx.current_cron_execution()
            stopped = (brake.check(getattr(run, "job_name", None), session_id, _run_key(run, session_id))
                       if run is not None else None)
            if stopped:
                return stopped
        except Exception:  # noqa: BLE001 — a hook must never break the run
            logger.debug("crystallize: Runaway Brake check failed", exc_info=True)
        # Recovery changes tool inputs, so it is not an optional observer. An
        # unrelated Cron observation failure must not bypass placeholder checks.
        from . import handoffs
        try:
            changed = handoffs.expand_args(args, session_id, turn_id, tool_name)
            return {"action": "modify", "args": changed} if changed is not None else None
        except ValueError as exc:
            return {"action": "block", "message": str(exc)}
        except Exception:  # noqa: BLE001 — never execute an unrecovered placeholder
            logger.debug("crystallize: tool placeholder recovery failed", exc_info=True)
            return {"action": "block", "message": "Crystallize could not recover the retained text. "
                    "Recover the original in its owning chat before retrying this tool."}

    return before_tool


OWN_TOOLS = frozenset({"crystal_list", "crystal_run", "crystal_full_result", "crystal_cheap"})


def _tool_name(tool: Any) -> Optional[str]:
    """A tool definition's name, in each provider's request shape."""
    if not isinstance(tool, dict):
        return None
    for found in (tool.get("name"), (tool.get("function") or {}).get("name") if isinstance(tool.get("function"), dict) else None,
                  (tool.get("toolSpec") or {}).get("name") if isinstance(tool.get("toolSpec"), dict) else None):
        if isinstance(found, str):
            return found
    return None


def drop_own_tools(request: Any) -> Optional[dict[str, Any]]:
    """Leave Crystallize's own tools out where they can't be used, so they cost nothing there.

    Every tool definition is re-sent on every AI call, and these four add about 800 tokens."""
    if not isinstance(request, dict):
        return None
    changed = dict(request)
    found = False
    if isinstance(request.get("tools"), list):
        kept = [tool for tool in request["tools"] if _tool_name(tool) not in OWN_TOOLS]
        if len(kept) != len(request["tools"]):
            changed["tools"], found = kept, True
            if not kept:
                changed.pop("tools")
                changed.pop("tool_choice", None)
    config = request.get("toolConfig")
    if isinstance(config, dict) and isinstance(config.get("tools"), list):
        kept = [tool for tool in config["tools"] if _tool_name(tool) not in OWN_TOOLS]
        if len(kept) != len(config["tools"]):
            changed["toolConfig"], found = {**config, "tools": kept}, True
    return changed if found else None


def make_llm_request(ctx: Any) -> Callable[..., Any]:
    """Apply independent, opt-in wire savings. Every layer fails open on its own."""
    from . import rent, reread

    def rewrite(request: Any = None, session_id: str = "", **_: Any) -> Any:
        current = request
        try:  # observed before any change, and never allowed to affect the call
            from . import estimate, request_settings
            run = ctx.current_cron_execution()
            if run is not None:
                request_settings.record(getattr(run, "execution_id", None), request)
            elif store.get_setting(request_settings.RECORD_CHATS, False):  # on only during a paired test
                request_settings.record("chat:" + session_id, request)
            if run is not None or store.chat_enabled(session_id):
                estimate.note_request(session_id, request)
        except Exception:  # noqa: BLE001
            logger.debug("crystallize: recording the request failed", exc_info=True)
        try:
            if ctx.current_cron_execution() is not None or not store.chat_enabled(session_id):
                changed = drop_own_tools(current)
                if isinstance(changed, dict):
                    current = changed
        except Exception:  # noqa: BLE001 — leaving the tools in is always safe
            logger.debug("crystallize: leaving out unused tools failed", exc_info=True)
        try:
            changed = rent.apply(current, session_id)
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
            try:
                from . import rent
                # Before this message's first AI call, so a removal only resends the chat once.
                rent.auto_evict(kwargs.get("session_id") or "")
            except Exception:  # noqa: BLE001 — a saving must never stop a turn
                logger.debug("crystallize: automatic removal failed", exc_info=True)
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
            if isinstance(response_text, str) and chat_savings._PLACEHOLDER.search(response_text):
                return ("[Crystallize could not recover the retained text for this reply. "
                        "Use crystal_full_result in the owning chat to recover the original before retrying.]")
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
                run = ctx.current_cron_execution()
                brake.forget(_run_key(run, session_id) if run is not None else session_id)
            except Exception:  # noqa: BLE001
                logger.debug("crystallize: brake cleanup failed", exc_info=True)
            try:
                chat_savings.cleanup_session(session_id)
            except Exception:  # noqa: BLE001
                logger.debug("crystallize: chat cleanup failed", exc_info=True)

    return on_session_end
