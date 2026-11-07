"""Context Rent: what a chat pays to keep re-reading, and a way to stop paying for a result.

Every AI call re-sends the whole chat, so each old tool result charges "rent" on every call
after it. This shows that per item. Removing one changes only what's sent to the AI: the saved
chat stays as it was, and the AI can still read the original. Removing also breaks the provider's
prompt cache from that point once, so each item shows how many calls it takes to pay that back.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Optional

from . import chat_savings, estimate, history, modelinfo, store

CHARS_PER_TOKEN = 4         # Hermes's own rough rule, good enough to compare items
TOP_ITEMS = 12
_NOTE = ("[Crystallize: removed from this chat to save money. If you need it, read the original with crystal_full_result, "
         "result_id {id}, or run the tool again.]")
MIN_REMOVABLE_CHARS = len(_NOTE) * 3    # below this the note saves too little to be worth it


def _tokens(text: Any, model: Optional[str] = None) -> int:
    return math.ceil(len(text or "") / estimate.chars_per_token(model))


def _key(call_id: Any) -> str:
    """Some routes send "call_id|item_id", while the saved chat has only the first part."""
    return str(call_id or "").split("|")[0]


def _digest(text: Any) -> str:
    """Providers may reuse a call id, so the text tells two results apart."""
    if isinstance(text, list):
        text = "".join(str(block.get("text", "")) if isinstance(block, dict) else str(block) for block in text)
    return hashlib.sha256(str(text or "").encode("utf-8", "replace")).hexdigest()


def _read_chat(session_id: str) -> tuple[list[Any], str]:
    conn = history._open_readonly(history._hermes_home() / "state.db")
    if conn is None:
        return [], ""
    try:
        rows = conn.execute("SELECT role, content, tool_call_id, tool_calls, tool_name FROM messages WHERE session_id = ? "
                            "AND COALESCE(active, 1) = 1 AND COALESCE(compacted, 0) = 0 ORDER BY id", (session_id,)).fetchall()
        found = conn.execute("SELECT system_prompt FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return list(rows), (found["system_prompt"] or "") if found else ""
    finally:
        conn.close()


def _call_labels(rows: list[Any]) -> dict[str, str]:
    """What each tool was asked to do, from the call that started it."""
    labels: dict[str, str] = {}
    for row in rows:
        try:
            calls = json.loads(row["tool_calls"] or "[]")
        except ValueError:
            continue
        for call in calls if isinstance(calls, list) else []:
            function = call.get("function") or {}
            try:
                args = json.loads(function.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            first = next((str(v) for v in args.values() if isinstance(v, (str, list))), "") if isinstance(args, dict) else ""
            labels[_key(call.get("id") or call.get("call_id"))] = re.sub(r"\s+", " ", first)[:80]
    return labels


def report(session_id: Optional[str]) -> dict[str, Any]:
    """What this chat re-reads on every AI call, largest first."""
    if not session_id:
        return {"items": [], "groups": [], "calls": 0}
    rows, system_prompt = _read_chat(session_id)
    main = chat_savings._main_model(session_id)
    prices = modelinfo.token_prices(main.get("model"), main.get("provider"))
    removed = store.evictions(session_id)
    reread = prices["cache_read"] if prices else None
    # Text after a removed item is re-sent uncached once, at the price of writing the cache.
    extra = max(0.0, prices["cache_write"] - prices["cache_read"]) if prices else None

    model = main.get("model")
    sizes = [_tokens(r["content"], model) + _tokens(r["tool_calls"], model) for r in rows]
    is_call = [r["role"] == "assistant" for r in rows]
    calls = sum(is_call)
    labels = _call_labels(rows)
    groups = {"Instructions": _tokens(system_prompt, model), "Your messages": 0, "AI replies": 0, "Small tool results": 0}
    items = []
    for i, row in enumerate(rows):
        key = _key(row["tool_call_id"])
        if row["role"] != "tool" or not key or len(row["content"] or "") < MIN_REMOVABLE_CHARS:
            name = {"user": "Your messages", "assistant": "AI replies", "tool": "Small tool results"}.get(row["role"])
            if name:
                groups[name] += sizes[i]
            continue
        size = sizes[i]
        later_calls = sum(is_call[i + 1:])
        after = sum(sizes[i + 1:])
        gain = size * reread if prices else None
        once = after * extra if prices else None
        payback = (math.ceil(once / gain) if gain else None) if prices else None
        items.append({"id": key, "tool": row["tool_name"] or "tool", "what": labels.get(key, ""), "tokens": size,
                      "per_call_usd": gain, "paid_usd": gain * later_calls if prices else None,
                      "payback_calls": payback, "removed": key in removed,
                      # A chat that has gone this long is likely to go about as long again.
                      "worth_it": payback is not None and payback <= calls})
    items.sort(key=lambda it: -it["tokens"])
    for item in items[TOP_ITEMS:]:
        if not item["removed"]:
            groups["Small tool results"] += item["tokens"]
    shown = items[:TOP_ITEMS] + [it for it in items[TOP_ITEMS:] if it["removed"]]
    kept = sum(s for s in groups.values()) + sum(it["tokens"] for it in shown if not it["removed"])
    gone = sum(it["tokens"] for it in shown if it["removed"])
    return {"items": shown, "groups": [{"name": k, "tokens": v} for k, v in groups.items() if v], "calls": calls,
            "tokens": kept, "removed_tokens": gone, "model": main.get("model"), "priced": bool(prices),
            "per_call_usd": kept * reread if prices else None,
            "saving_per_call_usd": gone * reread if prices else None}


def evict(session_id: str, call_id: str) -> dict[str, Any]:
    """Stop sending one tool result to the AI. The saved chat doesn't change."""
    key = _key(call_id)
    rows, _ = _read_chat(session_id) if session_id and key else ([], "")
    row = next((r for r in rows if r["role"] == "tool" and _key(r["tool_call_id"]) == key), None)
    if row is None:
        return {"ok": False, "message": "That result isn't in this chat any more."}
    store.add_eviction(session_id, key, chat_savings._keep_full(row["content"] or "", session_id), _digest(row["content"]))
    return {"ok": True, "message": "Removed. Your next message sends the chat without it."}


def restore(session_id: str, call_id: str) -> dict[str, Any]:
    store.remove_eviction(session_id, _key(call_id))
    return {"ok": True, "message": "Put back. Your next message sends it again."}


def _call_counts(request: dict[str, Any]) -> dict[str, int]:
    """How many results in this request use each call id, across every request shape."""
    counts: dict[str, int] = {}

    def add(value: Any) -> None:
        key = _key(value)
        if key:
            counts[key] = counts.get(key, 0) + 1

    for message in request.get("messages") if isinstance(request.get("messages"), list) else []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool":
            add(message.get("tool_call_id"))
        for block in message.get("content") if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                add(block.get("tool_use_id"))
            elif isinstance(block, dict) and isinstance(block.get("toolResult"), dict):
                add(block["toolResult"].get("toolUseId"))
    for item in request.get("input") if isinstance(request.get("input"), list) else []:
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            add(item.get("call_id"))
    return counts


def apply(request: Any, session_id: str) -> Optional[dict[str, Any]]:
    """The request with removed results swapped for a short note, or None if nothing changes.
    Copies only what it changes, because Hermes keeps using the original messages."""
    if not session_id or not isinstance(request, dict):
        return None
    rows = store.eviction_rows(session_id)
    removed = {key: row["full_id"] for key, row in rows.items()}
    if not removed:
        return None
    # Expiry or a missing original must never turn a saved result into an
    # unrecoverable placeholder. Keep sending its exact text until re-evicted.
    unavailable = [key for key, result_id in removed.items() if chat_savings._load_full(result_id, session_id) is None]
    for key in unavailable:
        store.remove_eviction(session_id, key)
    removed = {key: value for key, value in removed.items() if key not in unavailable}
    if not removed:
        return None
    note = lambda key: _NOTE.format(id=removed[key])  # noqa: E731
    changed = False
    seen = _call_counts(request)

    def is_removed(key: str, content: Any) -> bool:
        """When a call id appears once it's the removed result. When it repeats, only the
        text that was removed goes, so a later result with the same id is never lost."""
        if key not in removed:
            return False
        digest = rows[key].get("content_sha256")
        return seen.get(key, 0) <= 1 or not digest or _digest(content) == digest

    def swap_block(block: Any) -> Any:
        nonlocal changed
        if not isinstance(block, dict):
            return block
        if block.get("type") == "tool_result" and is_removed(_key(block.get("tool_use_id")), block.get("content")):  # Anthropic
            changed = True
            return {**block, "content": note(_key(block["tool_use_id"]))}
        inner = block.get("toolResult")  # Bedrock
        if isinstance(inner, dict) and is_removed(_key(inner.get("toolUseId")), inner.get("content")):
            changed = True
            return {**block, "toolResult": {**inner, "content": [{"text": note(_key(inner["toolUseId"]))}]}}
        return block

    def swap_message(message: Any) -> Any:
        nonlocal changed
        if not isinstance(message, dict):
            return message
        if message.get("role") == "tool" and is_removed(_key(message.get("tool_call_id")), message.get("content")):  # OpenAI-style
            changed = True
            return {**message, "content": note(_key(message["tool_call_id"]))}
        if isinstance(message.get("content"), list):
            blocks = [swap_block(b) for b in message["content"]]
            if any(new is not old for new, old in zip(blocks, message["content"])):
                return {**message, "content": blocks}
        return message

    def swap_item(item: Any) -> Any:
        nonlocal changed
        if isinstance(item, dict) and item.get("type") == "function_call_output" and is_removed(_key(item.get("call_id")), item.get("output")):  # Codex
            changed = True
            return {**item, "output": note(_key(item["call_id"]))}
        return item

    out = dict(request)
    if isinstance(request.get("messages"), list):
        out["messages"] = [swap_message(m) for m in request["messages"]]
    if isinstance(request.get("input"), list):
        out["input"] = [swap_item(i) for i in request["input"]]
    return out if changed else None
