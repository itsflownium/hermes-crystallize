"""Crystallize: turn Hermes's repeated work into repeatable step lists."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import capture, chat_commands, chat_savings, cli, live_notice, experiments, fetch_cache, output_delivery, schemas, store, tools

logger = logging.getLogger(__name__)


def _status_text() -> str:
    stats = store.trace_stats()
    lines = [
        f"Crystallize records cron jobs, and any chat you turn it on for. {stats['tool_calls']} tool calls recorded so far."
    ]
    if stats["cron_jobs"]:
        lines.append("Scheduled cron runs recorded:")
        totals: dict[str, list[int]] = {}
        for job in stats["cron_jobs"]:  # a re-created job has a new id but the same name
            entry = totals.setdefault(job["job_name"] or job["job_id"], [0, 0])
            entry[0] += job["runs"]
            entry[1] += job["tool_calls"]
        for name, (runs, calls) in totals.items():
            lines.append(f"  - {name}: {runs} runs, {calls} tool calls")
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
    experiments.set_context(ctx)
    ctx.register_middleware("llm_request", capture.make_llm_request(ctx))
    ctx.register_middleware("tool_execution", fetch_cache.make_tool_execution(ctx))
    ctx.register_hook("post_tool_call", capture.make_post_tool_call(ctx))
    ctx.register_hook("on_session_end", capture.make_on_session_end(ctx))
    ctx.register_hook("transform_tool_result", capture.make_transform_tool_result(ctx))
    ctx.register_hook("pre_llm_call", capture.make_pre_llm_call(ctx))
    ctx.register_hook("post_api_request", capture.make_post_api_request(ctx))
    ctx.register_hook("pre_tool_call", capture.make_pre_tool_call(ctx))
    ctx.register_hook("post_llm_call", live_notice.make_post_llm_call(ctx))
    ctx.register_hook("transform_llm_output", capture.make_transform_llm_output(ctx),
                      **output_delivery.registration_options(ctx))

    ctx.register_tool(
        name="crystal_full_result", toolset="crystallize",
        schema=schemas.CRYSTAL_FULL_RESULT, handler=tools.crystal_full_result,
    )
    ctx.register_tool(
        name="crystal_cheap", toolset="crystallize",
        schema=schemas.CRYSTAL_CHEAP, handler=tools.crystal_cheap,
    )
    try:
        ctx.register_auxiliary_task(
            chat_savings.TASK, display_name="Crystallize Chat",
            description="Condenses big tool results in chats where Crystallize Chat is on.",
        )
    except Exception:  # noqa: BLE001 — chat condensing stays off; cleaning still works
        logger.warning("crystallize: couldn't register the chat model slot", exc_info=True)

    def _crystallize_command(raw_args: str = "", *, session_id: str = "", **_: Any) -> str:
        return chat_commands.handle(raw_args, session_id)

    ctx.register_command(
        "crystallize", handler=_crystallize_command,
        description="See what Crystallize saved, what this chat re-reads, your cron jobs, Runaway Brake and prices",
        args_hint="[chat on|off | rent [remove|restore N] | jobs | brake [on|off] | prices | line on|off | help]",
    )

    cli.register(ctx)
    # Register an optional backend choice; never change the user's active backend.
    from . import delegation, host_api
    delegation.register(ctx)
    host_api.set_context(ctx)

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists():
                ctx.register_skill(child.name, child / "SKILL.md")
