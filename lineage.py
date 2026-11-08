"""Which saved sessions are the same chat.

Context compression gives a chat a new session id. Opt-in, retained originals and removed
results belong to the chat, so they follow its verified compression lineage. Forks, delegated
work and anything ambiguous stay separate: `history._session_chain` fails closed on those.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from . import history

_TTL_SECONDS = 5.0      # checked before every tool and request, so one lookup is reused briefly
_MAX_ENTRIES = 512

_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}


def chat_ids(session_id: Optional[str]) -> tuple[str, ...]:
    """This session and its verified compression relatives, oldest first."""
    if not session_id:
        return ()
    key = (str(history._hermes_home()), session_id)
    now = time.monotonic()
    with _lock:
        found = _cache.get(key)
        if found and now - found[0] < _TTL_SECONDS:
            return found[1]
    ids = (session_id,)
    try:
        conn = history._open_readonly(history._hermes_home() / "state.db")
        if conn is not None:
            try:
                chain, _ = history._session_chain(conn, session_id)
            finally:
                conn.close()
            verified = tuple(row["id"] for row in chain)
            if session_id in verified:
                ids = verified
    except Exception:  # noqa: BLE001 — an unreadable history means only the exact session is known
        ids = (session_id,)
    with _lock:
        if len(_cache) >= _MAX_ENTRIES:
            _cache.clear()
        _cache[key] = (now, ids)
    return ids


def forget() -> None:
    with _lock:
        _cache.clear()
