"""Token estimates that learn from real usage instead of assuming a fixed rule.

Tokens follow words and symbols, not characters, so removing whitespace saves very little even when
it removes thousands of characters. Each model's real prompt counts then calibrate the estimate.
"""

from __future__ import annotations

import json
import math
import re
import threading
from typing import Any, Optional

FALLBACK_CHARS_PER_TOKEN = 4    # used only until a model's own usage has been seen
MIN_SAMPLE_TOKENS = 1000        # below this, overhead dominates and the ratio says little
MAX_SAMPLED_CHARS = 2_000_000
_MAX_PENDING = 256
_LONG_RUN_CHARS = 6             # a long unbroken run, such as base64, is several tokens
_LEXEME = re.compile(r"\w+|[^\w\s]")

_lock = threading.Lock()
_pending: dict[str, tuple[int, int]] = {}


def lexemes(text: Any) -> int:
    """Words and symbols, weighted for long runs. Whitespace alone counts for nothing."""
    if not isinstance(text, str) or not text:
        return 0
    return sum(max(1, math.ceil(len(found) / _LONG_RUN_CHARS)) for found in _LEXEME.findall(text))


def _key(model: Optional[str]) -> str:
    return "token_ratio:" + str(model or "")


def _ratios(model: Optional[str]) -> Optional[dict[str, float]]:
    from . import store

    saved = store.get_setting(_key(model)) if model else None
    if not isinstance(saved, dict) or not saved.get("lexemes") or not saved.get("chars") or not saved.get("tokens"):
        return None
    return saved


def tokens(text: Any, model: Optional[str] = None) -> int:
    """About how many tokens this text is, for this model."""
    count = lexemes(text)
    saved = _ratios(model)
    return math.ceil(count * saved["tokens"] / saved["lexemes"]) if saved else count


def chars_per_token(model: Optional[str] = None) -> float:
    saved = _ratios(model)
    return saved["chars"] / saved["tokens"] if saved else FALLBACK_CHARS_PER_TOKEN


def calibrated(model: Optional[str]) -> bool:
    return _ratios(model) is not None


def note_request(session_id: str, request: Any) -> None:
    """Size up what's about to be sent, to pair with the prompt count that comes back."""
    if not session_id or not isinstance(request, dict):
        return
    content = {key: request[key] for key in ("messages", "input", "system", "instructions", "tools") if key in request}
    try:
        text = json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return
    if len(text) > MAX_SAMPLED_CHARS:
        return
    with _lock:
        if len(_pending) >= _MAX_PENDING:
            _pending.clear()
        _pending[session_id] = (lexemes(text), len(text))


def note_usage(session_id: str, model: Optional[str], usage: Any) -> None:
    """Learn this model's ratio from one real call, weighted by size."""
    from . import store

    with _lock:
        sent = _pending.pop(session_id, None)
    if not sent or not model or not isinstance(usage, dict):
        return
    prompt = usage.get("prompt_tokens")
    if isinstance(prompt, bool) or not isinstance(prompt, int) or prompt < MIN_SAMPLE_TOKENS:
        return
    saved = store.get_setting(_key(model))
    saved = saved if isinstance(saved, dict) else {"lexemes": 0, "chars": 0, "tokens": 0, "calls": 0}
    store.set_setting(_key(model), {"lexemes": saved["lexemes"] + sent[0], "chars": saved["chars"] + sent[1],
                                    "tokens": saved["tokens"] + prompt, "calls": saved["calls"] + 1})
