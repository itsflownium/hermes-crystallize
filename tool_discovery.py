"""Explicit, profile-wide tool deferral through Hermes's supported config."""
from __future__ import annotations

import hashlib
import json
import subprocess

from . import chat, lifecycle, modelinfo

PROTECTED = frozenset({"clarify", "tool_search", "tool_describe", "tool_call", "crystal_full_result"})


def preview(session_id: str = "") -> dict:
    config = modelinfo._config()
    current = ((config.get("tools") or {}).get("tool_search") or {}).get("defer")
    used = {c["tool"] for c in chat._tool_calls(session_id)} if session_id else set()
    try:
        from tools.registry import discover_builtin_tools, registry
        discover_builtin_tools()
        names = set(registry.get_tool_to_toolset_map())
    except (ImportError, AttributeError):
        names = set()
    return {"current": current, "config_fingerprint": _fingerprint(current),
            "suggested_unused": sorted(names - used - PROTECTED), "used_in_this_chat": sorted(used),
            "scope": "profile-wide; affects tools built for new sessions",
            "notice": "Never-used tools may still be needed later. Only explicit selections are deferred through Hermes tool_search; clarify stays direct."}


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def configure(names: list[str], *, confirmed: bool = False, expected_config_fingerprint: str = "") -> dict:
    if confirmed is not True or not isinstance(names, list) or len(names) > 100 or any(
            not isinstance(n, str) or not n or not n.replace("_", "").isalnum() for n in names):
        raise ValueError("Confirm an explicit list of at most 100 tool names for profile-wide deferral.")
    if PROTECTED.intersection(names):
        raise ValueError("clarify, recovery and tool-discovery bridge tools must stay directly available.")
    report = preview()
    if not expected_config_fingerprint or report["config_fingerprint"] != expected_config_fingerprint:
        raise ValueError("Tool deferral settings changed. Preview the current config before saving.")
    if set(names) - set(report["suggested_unused"]):
        raise ValueError("One of those tools is not registered in this host; no settings changed.")
    # Merge with the effective host defaults as returned by public config loading.
    value = sorted((set(report["current"] or []) | set(names)) - PROTECTED)
    proc = subprocess.run([*lifecycle._hermes_cmd(), "config", "set", "--force", "tools.tool_search.defer", json.dumps(value)],
                          capture_output=True, text=True, timeout=60, env=lifecycle.hermes_env())
    if proc.returncode:
        raise ValueError("Hermes could not save tool deferral; inspect the host config command diagnostics.")
    return {"ok": True, "deferred": value, "scope": report["scope"], "notice": report["notice"]}
