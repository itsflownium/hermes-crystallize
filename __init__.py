"""Crystallize: turn Hermes's repeated work into proven, cheap step lists."""

from __future__ import annotations

import logging
from typing import Any

from . import capture, cli, schemas, store, tools

logger = logging.getLogger(__name__)


def _status_text() -> str:
    stats = store.trace_stats()
    lines = [
        f"Crystallize is recording. {stats['tool_calls']} tool calls "
        f"across {stats['sessions']} chats so far."
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

    ctx.register_hook("post_tool_call", capture.make_post_tool_call(ctx))
    ctx.register_hook("on_session_end", capture.on_session_end)

    def _crystallize_command(raw_args: str = "", **_: Any) -> str:
        sub = (raw_args or "").strip().split(" ", 1)[0].lower()
        if sub in ("", "status"):
            try:
                return _status_text()
            except Exception as exc:  # noqa: BLE001
                logger.warning("crystallize: status failed: %s", exc)
                return "Crystallize couldn't read its data. Check `hermes logs` for details."
        return "Usage: /crystallize [status]"

    ctx.register_command(
        "crystallize", handler=_crystallize_command,
        description="Show what Crystallize has recorded", args_hint="[status]",
    )

    cli.register(ctx)
