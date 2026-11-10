"""A live Crystallize line in the CLI and TUI, posted through Hermes's public ``ctx.post_message``.

In the TUI the line sits in the status area and is replaced in place after each turn; the classic
CLI prints it after the turn. Desktop has the full panel and messaging chats would receive a real
message, so neither gets this line. It never raises and never touches the AI's answer.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

SETTING = "chat_notices"
SURFACES = frozenset({"cli", "tui"})
_HINT_AFTER_TURNS = 2

_lock = threading.Lock()
_handles: dict[str, Any] = {}
_last_text: dict[str, str] = {}
_turns: dict[str, int] = {}


def enabled() -> bool:
    from . import store
    return store.get_setting(SETTING, True) is not False


def set_enabled(on: bool) -> str:
    from . import store
    store.set_setting(SETTING, bool(on))
    return ("The Crystallize line will show after each turn in the CLI and TUI." if on
            else "The Crystallize line is off. /crystallize still shows everything.")


def text_for(session_id: str, turn: int, compact: bool = False) -> Optional[str]:
    """The line for this chat, or None when there is nothing worth showing.

    ``compact`` is for the TUI status line, which shows only about twenty characters, so the
    number that matters comes first.
    """
    from . import chat_commands, chat_savings, rent
    state = chat_savings.status_for(session_id)
    if not state.get("enabled"):
        if turn == _HINT_AFTER_TURNS and state.get("can_start", True):
            return ("◆ Crystallize off · /crystallize chat on" if compact else
                    "◆ Crystallize is off for this chat · /crystallize chat on cuts what each AI call re-reads")
        return None
    try:
        report = rent.report(session_id)
    except Exception:  # noqa: BLE001 — a chat with no readable history still gets a line
        report = {}
    removed = report.get("removed_tokens") or 0
    saving = report.get("saving_per_call_usd")
    worth = [item for item in report.get("items") or [] if item.get("worth_it") and not item.get("removed")]
    spent = chat_savings.summary(session_id).get("chat_spent_usd")
    if compact:
        if removed:
            return f"◆ −{chat_commands._tokens(removed)} tok/call · /crystallize"
        if worth:
            return f"◆ {len(worth)} to trim · /crystallize rent"
        if report.get("tokens"):
            return f"◆ {chat_commands._tokens(report['tokens'])} re-read/call · /crystallize"
        return "◆ Crystallize on · /crystallize"
    parts = ["◆ Crystallize on"]
    if removed:
        parts.append(f"{chat_commands._tokens(removed)} tokens no longer re-sent"
                     + (f" (~{chat_commands._money(saving)}/call)" if saving else ""))
    elif report.get("tokens"):
        parts.append(f"each call re-reads {chat_commands._tokens(report['tokens'])} tokens")
    if worth:
        parts.append(f"{len(worth)} worth removing")
    if spent is not None:
        parts.append(f"spent {chat_commands._money(spent)}")
    parts.append("/crystallize")
    return " · ".join(parts)


def _surface(ctx: Any, platform: str) -> str:
    try:
        surface = ctx.session_surface() if hasattr(ctx, "session_surface") else ""
    except Exception:  # noqa: BLE001
        surface = ""
    return str(surface or platform or "").lower()


def make_post_llm_call(ctx: Any):
    def hook(session_id: str = "", platform: str = "", **_: Any) -> None:
        try:
            if not session_id or not enabled() or not hasattr(ctx, "post_message"):
                return
            surface = _surface(ctx, platform)
            if surface not in SURFACES:
                return
            with _lock:
                turn = _turns[session_id] = _turns.get(session_id, 0) + 1
            text = text_for(session_id, turn, compact=surface == "tui")
            if not text or _last_text.get(session_id) == text:
                return
            handle = _handles.get(session_id)
            result = handle.edit(text) if handle is not None else None
            if handle is None:
                handle = ctx.post_message(text)
            if getattr(handle, "ok", False) or (isinstance(result, dict) and result.get("ok")):
                with _lock:
                    _handles[session_id] = handle
                    _last_text[session_id] = text
        except Exception:  # noqa: BLE001 — the line is a convenience; the turn is already done
            logger.debug("crystallize: live line skipped", exc_info=True)
    return hook


def forget(session_id: str) -> None:
    with _lock:
        _handles.pop(session_id, None)
        _last_text.pop(session_id, None)
        _turns.pop(session_id, None)
