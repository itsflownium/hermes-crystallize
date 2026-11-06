"""Per-chat tool-result controls, scoped to Hermes's active plugin store.

Saving a policy never enables Crystallize Chat. The caller still checks the chat's
opt-in before transforming results or calling a helper model.
"""

from __future__ import annotations

import hashlib
import re
import threading
from typing import Any

from . import store

MODES = ("exact", "clean", "summary")
DEFAULT_MODE = "clean"
MAX_FOCUS = 2000
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,255}\Z")
_TOOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_lock = threading.RLock()


class PolicyError(ValueError):
    """A policy request has an invalid session, tool, mode, or task focus."""


def _session(session_id: str) -> str:
    if not isinstance(session_id, str) or not _SESSION.fullmatch(session_id):
        raise PolicyError("Choose a valid chat before saving its controls.")
    return session_id


def _tool(tool: str) -> str:
    if tool != "*" and (not isinstance(tool, str) or not _TOOL.fullmatch(tool)):
        raise PolicyError("Tool names must be 1–128 letters, numbers, dots, colons, slashes, underscores or hyphens.")
    return tool


def _key(session_id: str) -> str:
    # Store already follows the active Hermes profile. Hashing keeps keys bounded
    # and avoids putting chat identifiers in settings/debug listings.
    return "chat_policy:" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _read(session_id: str) -> dict[str, Any]:
    value = store.get_setting(_key(session_id), {})
    value = value if isinstance(value, dict) else {}
    mode = value.get("default_mode")
    focus = value.get("focus")
    tools = value.get("tools")
    # A corrupt or future-version setting must not turn helpers on accidentally.
    return {
        "default_mode": mode if mode in MODES else DEFAULT_MODE,
        "focus": focus[:MAX_FOCUS] if isinstance(focus, str) else "",
        "tools": {name: mode for name, mode in tools.items()
                  if isinstance(name, str) and _TOOL.fullmatch(name) and mode in MODES}
                 if isinstance(tools, dict) else {},
    }


def summary(session_id: str | None) -> dict[str, Any]:
    """Describe explicit controls without changing consent or running a model."""
    if not session_id:
        return {"session_id": None, "default_mode": DEFAULT_MODE,
                "focus": "", "tools": {}, "modes": list(MODES)}
    session_id = _session(session_id)
    with _lock:
        policy = _read(session_id)
    return {"session_id": session_id, **policy, "modes": list(MODES)}


def policy_for(session_id: str, tool: str) -> dict[str, str]:
    """Return the tool's mode and the chat's task focus.

    The integration treats files and failed results as exact even when summary
    mode is selected. Invalid hook identifiers fail closed, without model calls.
    """
    try:
        session_id, tool = _session(session_id), _tool(tool)
    except PolicyError:
        return {"mode": "exact", "focus": ""}
    with _lock:
        policy = _read(session_id)
    return {"mode": policy["tools"].get(tool, policy["default_mode"]), "focus": policy["focus"]}


def set_policy(session_id: str, tool: str, mode: str, focus: str | None = None) -> dict[str, Any]:
    """Save a tool override (``*`` changes the default) and optional chat focus."""
    session_id, tool = _session(session_id), _tool(tool)
    if not isinstance(mode, str) or mode not in MODES:
        raise PolicyError("Choose exact, clean or summary for tool results.")
    if focus is not None and (not isinstance(focus, str) or len(focus) > MAX_FOCUS or "\x00" in focus):
        raise PolicyError(f"Task focus must be text of at most {MAX_FOCUS} characters.")
    with _lock:
        policy = _read(session_id)
        if tool == "*":
            policy["default_mode"] = mode
        else:
            if tool not in policy["tools"] and len(policy["tools"]) >= 100:
                raise PolicyError("This chat already has 100 tool controls. Change the default instead.")
            policy["tools"][tool] = mode
        if focus is not None:
            policy["focus"] = focus.strip()
        store.set_setting(_key(session_id), policy)
        return {"session_id": session_id, **policy, "modes": list(MODES)}
