"""Crystallize: turn Hermes's repeated work into proven, cheap step lists."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import capture, chat_savings, cli, schemas, store, tools

logger = logging.getLogger(__name__)


def _status_text() -> str:
    stats = store.trace_stats()
    lines = [
        f"Crystallize Cron is recording. {stats['tool_calls']} tool calls recorded so far."
    ]
    if stats["cron_jobs"]:
        lines.append("Scheduled cron runs recorded:")
        for job in stats["cron_jobs"]:
            name = job["job_name"] or job["job_id"]
            lines.append(f"  - {name}: {job['runs']} runs, {job['tool_calls']} tool calls")
    else:
        lines.append("No scheduled cron runs recorded yet.")
    routines = store.list_routines()
    lines.append(f"Saved routines: {len(routines)}")
    return "\n".join(lines)


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="crystal_list", toolset="crystallize",
        schema=schemas.CRYSTAL_LIST, handler=tools.crystal_list,
    )
    ctx.register_tool(
        name="crystal_run", toolset="crystallize",
        schema=schemas.CRYSTAL_RUN, handler=tools.crystal_run,
    )

    tools.set_context(ctx)
    ctx.register_hook("post_tool_call", capture.make_post_tool_call(ctx))
    ctx.register_hook("on_session_end", capture.make_on_session_end(ctx))
    ctx.register_hook("transform_tool_result", capture.make_transform_tool_result(ctx))

    ctx.register_tool(
        name="crystal_full_result", toolset="crystallize",
        schema=schemas.CRYSTAL_FULL_RESULT, handler=tools.crystal_full_result,
    )
    try:
        ctx.register_auxiliary_task(
            chat_savings.TASK, display_name="Crystallize Chat",
            description="Condenses big tool results in chats where Crystallize Chat is on.",
        )
    except Exception:  # noqa: BLE001 — chat condensing stays off; cleaning still works
        logger.warning("crystallize: couldn't register the chat model slot", exc_info=True)

    def _chat_command(raw_args: str = "", *, session_id: str = "", **_: Any) -> str:
        sub = (raw_args or "").strip().split(" ", 1)[0].lower()
        if sub == "on":
            state = chat_savings.status_for(session_id)
            if not state["can_start"]:
                return state["why_not"]
            store.set_chat_enabled(session_id, True)
            picked = chat_savings.chosen_model()
            which = (f"Big tool results will be condensed by {picked['model']}." if picked["model"] else
                     "Tool results will be cleaned. Pick a cheap model on the Crystallize Chat page to condense big ones too.")
            return f"Crystallize Chat is on. {which} Send /crystallize-chat off to stop."
        if sub == "off":
            if not session_id:
                return "Send this inside a chat to turn Crystallize Chat off."
            store.set_chat_enabled(session_id, False)
            return "Crystallize Chat is off for this chat."
        if sub in ("", "status"):
            state = chat_savings.status_for(session_id)
            picked = chat_savings.chosen_model()
            return (f"Crystallize Chat is {'on' if state['enabled'] else 'off'} for this chat. "
                    f"Cheap model: {picked['model'] or 'none picked'}.")
        return "Usage: /crystallize-chat [on|off|status]"

    def _cron_command(raw_args: str = "", **_: Any) -> str:
        try:
            return _status_text()
        except Exception as exc:  # noqa: BLE001
            logger.warning("crystallize: status failed: %s", exc)
            return "Crystallize Cron couldn't read its data. Check `hermes logs` for details."

    ctx.register_command(
        "crystallize-chat", handler=_chat_command,
        description="Turn Crystallize Chat on or off for this chat. Available after your first prompt",
        args_hint="[on|off|status]",
    )
    ctx.register_command(
        "crystallize-cron", handler=_cron_command,
        description="Show what Crystallize Cron has recorded for your scheduled jobs",
    )

    cli.register(ctx)

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists():
                ctx.register_skill(child.name, child / "SKILL.md")
