"""What each cron AI call actually asked for.

Hermes doesn't always save a cron session's reasoning setting, and a saved setting isn't what was
sent. The request itself is direct evidence, for any provider. A comparison only counts when both
sides sent the same settings, so thinking less can never show up as a saving.
"""

from __future__ import annotations

import json
from typing import Any, Optional

UNSET = "unset"     # the request named no reasoning, so the provider chose; still comparable
MIXED = "mixed"     # calls in one run differed, so the run doesn't establish one setting

_REASONING_FIELDS = ("reasoning_effort", "reasoning", "thinking", "thinking_config", "thinkingConfig", "effort")
_OTHER_FIELDS = ("temperature", "top_p", "service_tier", "verbosity", "seed")
_NESTED = ("extra_body", "generationConfig", "generation_config", "output_config",
           "additionalModelRequestFields", "inferenceConfig", "text")


def _collect(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    reasoning: dict[str, Any] = {}
    other: dict[str, Any] = {}
    layers = [("", request)] + [(name + ".", request[name]) for name in _NESTED if isinstance(request.get(name), dict)]
    for prefix, layer in layers:
        for key in _REASONING_FIELDS:
            if layer.get(key) is not None:
                reasoning[prefix + key] = layer[key]
        for key in _OTHER_FIELDS:
            if layer.get(key) is not None:
                other[prefix + key] = layer[key]
    return reasoning, other


def _effort(reasoning: dict[str, Any]) -> str:
    if not reasoning:
        return UNSET
    for value in reasoning.values():
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        if isinstance(value, dict):
            if value.get("enabled") is False or value.get("type") == "disabled":
                return "none"
            for key in ("effort", "level", "thinkingLevel"):
                if isinstance(value.get(key), str) and value[key].strip():
                    return value[key].strip().lower()
            for key in ("budget_tokens", "max_tokens", "thinkingBudget"):
                if value.get(key) is not None:
                    return f"budget:{value[key]}"
    return "configured"


def settings_of(request: Any) -> Optional[dict[str, str]]:
    """The reasoning level and a fingerprint of the settings that change what a call costs.

    max_tokens is left out because Hermes can size it per call from the remaining context."""
    if not isinstance(request, dict):
        return None
    reasoning, other = _collect(request)
    try:
        fingerprint = json.dumps({"reasoning": reasoning, "other": other}, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return None
    return {"effort": _effort(reasoning), "fingerprint": fingerprint}


def record(execution_id: Any, request: Any) -> None:
    """Remember what this run sent, keeping only whether its calls agreed."""
    from . import store

    found = settings_of(request)
    if execution_id is None or found is None:
        return
    key = "run_request:" + str(execution_id)
    saved = store.get_setting(key)
    if saved == found or (isinstance(saved, dict) and saved.get("effort") == MIXED):
        return
    store.set_setting(key, found if saved is None else {"effort": MIXED, "fingerprint": MIXED})


def for_run(execution_id: Any) -> Optional[dict[str, str]]:
    from . import store

    saved = store.get_setting("run_request:" + str(execution_id)) if execution_id is not None else None
    return saved if isinstance(saved, dict) and saved.get("effort") else None
