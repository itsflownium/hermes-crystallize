"""Analyse one chat: what it cost and which steps Hermes kept repeating.

Reads the chat's tool calls from Hermes's own message history (read-only), so it
works on any chat, including ones from before Crystallize was installed.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from . import analyze, history

logger = logging.getLogger(__name__)

MAX_SEQ = 6        # longest repeated sequence we look for
MIN_TIMES = 2      # a sequence must happen at least this often
MIN_TIMES_SINGLE = 3  # a single step must repeat this often to count


def _tool_calls(session_id: str) -> list[dict[str, Any]]:
    """Tool calls in this chat, in order: [{'tool': name, 'args': parsed}]."""
    conn = history._open_readonly(history._hermes_home() / "state.db")
    if conn is None:
        return []
    try:
        conn.execute("BEGIN")
        cols = history._columns(conn, "messages")
        if not {"session_id", "role", "tool_calls"} <= cols:
            return []
        order = "timestamp, rowid" if "timestamp" in cols else "rowid"
        chain, uncertainty = history._session_chain(conn, session_id)
        ids = [row["id"] for row in chain] if chain and not uncertainty else [session_id]
        rows = []
        seen_ids: set[str] = set()
        select = ", ".join(["tool_calls", *(key for key in ("message_uid", "tool_call_uids") if key in cols)])
        original = " AND COALESCE(_compressed_summary, 0) = 0" if "_compressed_summary" in cols else ""
        for sid in ids:
            for row in conn.execute(
                    f"SELECT {select} FROM messages WHERE session_id = ? AND role = 'assistant' "
                    f"AND tool_calls IS NOT NULL {original} ORDER BY {order}", (sid,)):
                uid = row["message_uid"] if "message_uid" in row.keys() else None
                if uid and uid in seen_ids:
                    continue
                if uid:
                    seen_ids.add(uid)
                rows.append((sid, row))
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: reading chat messages failed", exc_info=True)
        return []
    finally:
        conn.close()

    calls: list[dict[str, Any]] = []
    seen_calls: set[str] = set()
    for _, row in rows:
        raw = row["tool_calls"]
        try:
            items = json.loads(raw) if raw else []
        except ValueError:
            continue
        try:
            uids = json.loads(row["tool_call_uids"] or "{}") if "tool_call_uids" in row.keys() else {}
        except (ValueError, TypeError):
            uids = {}
        uids = uids if isinstance(uids, dict) else {}
        occurrences: dict[str, int] = {}
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            # Compression can carry verbatim tail messages forward. Hermes'
            # durable call UIDs identify those copies. Provider call IDs can be
            # reused on later responses, so they are never identity by themselves.
            call_id = item.get("id")
            if isinstance(call_id, str) and call_id:
                occurrence = occurrences.get(call_id, 0)
                occurrences[call_id] = occurrence + 1
                uid = uids.get(call_id)
                if isinstance(uid, list):
                    uid = uid[occurrence] if occurrence < len(uid) else None
                if isinstance(uid, str) and uid and uid in seen_calls:
                    continue
                if isinstance(uid, str) and uid:
                    seen_calls.add(uid)
            fn = (item or {}).get("function") or {}
            if not isinstance(fn, dict):
                continue
            name = fn.get("name") or item.get("name")
            if not name or name in ("crystal_run", "crystal_list"):
                continue
            args = fn.get("arguments", item.get("arguments", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    pass
            calls.append({"tool": name, "args": args})
    return calls


def _signature(call: dict[str, Any]) -> str:
    blanked, _ = analyze._blank(analyze._canon(call["args"]))
    return f"{call['tool']}|{blanked}"


def _short(args: Any, limit: int = 80) -> str:
    text = " ".join(analyze._canon(args).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def find_repeats(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repeated runs of steps, longest first. Each step is used by at most one finding."""
    sigs = [_signature(c) for c in calls]
    used = [False] * len(sigs)
    found: list[dict[str, Any]] = []

    for length in range(min(MAX_SEQ, len(sigs) // 2 or 1), 0, -1):
        need = MIN_TIMES if length > 1 else MIN_TIMES_SINGLE
        groups: dict[tuple[str, ...], list[int]] = {}
        for start in range(len(sigs) - length + 1):
            if any(used[start:start + length]):
                continue
            groups.setdefault(tuple(sigs[start:start + length]), []).append(start)

        for key, starts in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            picked, last_end = [], -1
            for s in starts:  # non-overlapping, still unused
                if s >= last_end and not any(used[s:s + length]):
                    picked.append(s)
                    last_end = s + length
            if len(picked) < need:
                continue
            for s in picked:
                for i in range(s, s + length):
                    used[i] = True
            first = picked[0]
            steps = [{"tool": calls[i]["tool"], "args": _short(calls[i]["args"])}
                     for i in range(first, first + length)]
            found.append({
                "steps": steps,
                "length": length,
                "times": len(picked),
                "calls_saved": (len(picked) - 1) * length,
                "has_side_effects": any(
                    analyze._is_side_effect(calls[i]["tool"], calls[i]["args"])
                    for i in range(first, first + length)),
            })

    found.sort(key=lambda f: f["calls_saved"], reverse=True)
    return found


MIN_STREAK = 4      # same tool this many times in a row counts as a pattern
MIN_PATTERN_TIMES = 3


def find_patterns(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same KIND of step repeated with changing inputs (tool names only).

    These aren't exact repeats, so nothing is promised: they're where Hermes is
    looping by hand and a single saved routine might replace many calls.
    """
    names = [c["tool"] for c in calls]
    out: list[dict[str, Any]] = []

    # Streaks: one tool many times in a row.
    i = 0
    while i < len(names):
        j = i
        while j < len(names) and names[j] == names[i]:
            j += 1
        if j - i >= MIN_STREAK:
            out.append({"kind": "streak", "tools": [names[i]], "times": j - i,
                        "example": _short(calls[i]["args"])})
        i = j

    # Short tool sequences (2–3 steps) that recur.
    seen: set[tuple[str, ...]] = set()
    for length in (3, 2):
        counts: dict[tuple[str, ...], int] = {}
        for s in range(len(names) - length + 1):
            key = tuple(names[s:s + length])
            if len(set(key)) == 1:  # covered by streaks
                continue
            counts[key] = counts.get(key, 0) + 1
        for key, n in counts.items():
            if n >= MIN_PATTERN_TIMES and not any(set(key) <= set(k) for k in seen):
                seen.add(key)
                out.append({"kind": "sequence", "tools": list(key), "times": n, "example": ""})

    out.sort(key=lambda p: p["times"] * len(p["tools"]), reverse=True)
    return out[:5]


def analyse(session_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not session_id:
        return None
    calls = _tool_calls(session_id)
    info = history._session_costs([session_id]).get(session_id) or {}
    repeats = find_repeats(calls)
    return {
        "session_id": session_id,
        "spent_usd": info.get("actual_cost_usd") if info.get("actual_cost_usd") is not None else info.get("estimated_cost_usd"),
        "model": info.get("model"),
        "provider": info.get("billing_provider"),
        "reasoning_effort": info.get("reasoning_effort"),
        "session_ids": info.get("session_ids") or [session_id],
        "attribution_evidence": info.get("attribution_evidence") or {},
        "api_calls": info.get("api_call_count"),
        "tool_calls": len(calls),
        "repeats": repeats[:5],
        "patterns": find_patterns(calls),
        "calls_saveable": sum(r["calls_saved"] for r in repeats),
    }
