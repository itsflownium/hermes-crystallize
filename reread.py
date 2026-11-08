"""Opt-in savings on repeated wire history, with exact, session-owned recovery.

The saved conversation, model, reasoning and signed reasoning blocks are never
changed. Existing history is a baseline. New text gets one complete API read;
only its first successful replay may select a smaller, immutable projection.
Unknown prices/usage, uncertain tool outcomes and unsupported shapes stay exact.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
from typing import Any

from . import chat_policy, chat_savings as chat, handoffs, modelinfo, store

MAX_MESSAGES = 2000
MAX_STREAMS = 8
MIN_TEXT_CHARS = 8000
MIN_SAVED_CHARS = 1000
_WRITE_FIELDS = {"write_file": ("content",), "patch": ("old_string", "new_string", "patch")}
_SETTINGS = frozenset({"enabled", "writes", "results", "batching"})
_BATCH_NOTE = ("\n\n[Crystallize: batch independent reads when the available tools support it. "
               "Preserve dependencies, approvals and the complete context needed for each read.]")
_NOTE = ("\n[Crystallize: {kind} excerpt; the middle is omitted. Recover the exact original "
         "with crystal_full_result, result_id {id}, before relying on an omitted detail.]\n")
_FAILURE = re.compile(r"(?im)^\s*(?:traceback \(most recent call last\)|(?:fatal |syntax |type |value )?error\b|warn(?:ing)?\b)")
_LOCK = threading.RLock()


def policy(session_id: str) -> dict[str, Any]:
    ids = store._chat_ids(session_id) if session_id else ("",)
    value = next((found for found in (store.get_setting("reread:" + sid) for sid in reversed(ids)) if found), {})
    value = value if isinstance(value, dict) else {}
    return {**{key: value.get(key) is True for key in ("enabled", "writes", "results", "batching")},
            "reasoning_unchanged": True}


def configure(session_id: str, **settings: Any) -> dict[str, Any]:
    if (not isinstance(session_id, str) or not session_id.strip()
            or set(settings) - _SETTINGS or any(type(value) is not bool for value in settings.values())):
        raise ValueError("Re-read settings must be explicit booleans for one chat.")
    with _LOCK:
        value = {**policy(session_id), **settings}
        store.set_setting("reread:" + session_id, {key: value[key] for key in _SETTINGS})
    return value


def _key(session_id: str) -> str:
    return "reread_projection:" + str(session_id or "")


def _json(value: Any) -> str:
    # Do not fingerprint object reprs: they may change between otherwise equal calls.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    return (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0 else None)


def _usage(session_id: str) -> dict[str, Any]:
    getter = getattr(handoffs, "main_usage", None)
    value = getter(session_id) if callable(getter) else store.get_setting(handoffs.usage_key(session_id))
    return value if isinstance(value, dict) else {}


def _call_id(value: Any) -> str:
    return str(value or "").split("|", 1)[0]


def _name(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _get(value: Any, path: list[Any]) -> Any:
    for component in path:
        value = value[component]
    return value


def _put(value: Any, path: list[Any], replacement: Any) -> None:
    parent = _get(value, path[:-1])
    parent[path[-1]] = copy.deepcopy(replacement)


def _has_failure(value: Any, depth: int = 0) -> bool:
    if depth > 20:
        return True
    if isinstance(value, str):
        if _FAILURE.search(value):
            return True
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return False
        return _has_failure(decoded, depth + 1) if isinstance(decoded, (dict, list)) else False
    if isinstance(value, list):
        return any(_has_failure(part, depth + 1) for part in value)
    if not isinstance(value, dict):
        return False
    if (value.get("is_error") is True or value.get("isError") is True or value.get("error")
            or value.get("error_message") or value.get("errorMessage") or value.get("truncated") is True
            or value.get("success") is False
            or value.get("ok") is False or value.get("warning") or value.get("warnings") or value.get("_warning")):
        return True
    status = value.get("status")
    if isinstance(status, str) and status.lower() in {"error", "failed", "blocked", "cancelled", "canceled", "denied", "incomplete", "pending", "running", "warning", "partial", "partial_success", "timeout", "timed_out"}:
        return True
    for field in ("exit_code", "http_status", "status_code"):
        code = _number(value.get(field))
        if code is not None and (code != 0 if field == "exit_code" else code >= 400):
            return True
    # Keep structured envelopes and nested failures exact; never hide a failed source.
    return any(_has_failure(part, depth + 1) for part in value.values() if isinstance(part, (dict, list, str)))


def _successful(value: Any, envelope: dict[str, Any]) -> bool:
    if _has_failure(envelope) or _has_failure(value):
        return False
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return False
    evidence = [envelope]
    if isinstance(value, list):
        for block in value:
            if not isinstance(block, dict) or not isinstance(block.get("text"), str):
                continue
            try:
                decoded = json.loads(block["text"])
            except ValueError:
                continue
            if isinstance(decoded, dict):
                evidence.append(decoded)
    if isinstance(value, dict):
        evidence.append(value)
    # A tool result existing is not evidence that its write succeeded.
    return any(part.get("success") is True or part.get("ok") is True or part.get("verified") is True
               or str(part.get("status", "")).lower() in {"success", "succeeded", "completed", "ok"}
               for part in evidence)


def _text_sites(value: Any, path: list[Any]) -> list[tuple[list[Any], str]]:
    """Text only. Images, citations, tool metadata and reasoning blocks stay intact."""
    if isinstance(value, str):
        return [(path, value)]
    sites = []
    if isinstance(value, list):
        for index, block in enumerate(value):
            if not isinstance(block, dict):
                continue
            if (any(key in block for key in ("signature", "thoughtSignature", "thought_signature", "encrypted_content"))
                    or block.get("thought") is True or block.get("citations") or block.get("annotations")):
                continue  # signed text and citation offsets depend on the complete text
            if block.get("type") in {"text", "input_text", "output_text"} and isinstance(block.get("text"), str):
                sites.append((path + [index, "text"], block["text"]))
            elif set(block) == {"text"} and isinstance(block["text"], str):
                sites.append((path + [index, "text"], block["text"]))
    return sites


def _signed(item: dict, path: list[Any]) -> bool:
    value: Any = item
    for component in [*path, None]:
        if isinstance(value, dict) and (
                any(key in value for key in ("signature", "thoughtSignature", "thought_signature", "reasoning_signature", "encrypted_content"))
                or value.get("thought") is True or value.get("type") in {"thinking", "reasoning", "redacted_thinking"}):
            return True
        if component is not None:
            try:
                value = value[component]
            except (KeyError, TypeError, IndexError):
                return True  # unsupported/incomplete wire site remains exact
    return False


def _wire_sites(items: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Enumerate provider-native tool calls/results without normalizing their wire shapes."""
    calls, results = [], []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if item.get("role") == "assistant" and isinstance(item.get("tool_calls"), list):
            for offset, call in enumerate(item["tool_calls"]):
                if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                    continue
                function = call["function"]
                calls.append({"index": index, "id": _call_id(call.get("id")), "name": _name(function.get("name")),
                              "path": ["tool_calls", offset, "function", "arguments"], "args": function.get("arguments")})
        if item.get("role") == "tool":
            results.append({"index": index, "id": _call_id(item.get("tool_call_id")),
                            "name": _name(item.get("name") or item.get("tool_name")), "value": item.get("content"),
                            "path": ["content"], "envelope": item})
        if item.get("type") == "function_call":  # OpenAI Responses / Codex
            calls.append({"index": index, "id": _call_id(item.get("call_id")), "name": _name(item.get("name")),
                          "path": ["arguments"], "args": item.get("arguments")})
        elif item.get("type") == "function_call_output":
            results.append({"index": index, "id": _call_id(item.get("call_id")), "name": _name(item.get("name")),
                            "value": item.get("output"), "path": ["output"], "envelope": item})
        content = item.get("content")
        for offset, block in enumerate(content if isinstance(content, list) else []):
            if not isinstance(block, dict):
                continue
            base = ["content", offset]
            if block.get("type") == "tool_use" and item.get("role") == "assistant":  # Anthropic
                calls.append({"index": index, "id": _call_id(block.get("id")), "name": _name(block.get("name")),
                              "path": base + ["input"], "args": block.get("input")})
            elif block.get("type") == "tool_result":
                results.append({"index": index, "id": _call_id(block.get("tool_use_id")), "name": "",
                                "value": block.get("content"), "path": base + ["content"], "envelope": block})
            elif isinstance(block.get("toolUse"), dict) and item.get("role") == "assistant":  # Bedrock
                tool = block["toolUse"]
                calls.append({"index": index, "id": _call_id(tool.get("toolUseId")), "name": _name(tool.get("name")),
                              "path": base + ["toolUse", "input"], "args": tool.get("input")})
            elif isinstance(block.get("toolResult"), dict):
                tool = block["toolResult"]
                results.append({"index": index, "id": _call_id(tool.get("toolUseId")), "name": "",
                                "value": tool.get("content"), "path": base + ["toolResult", "content"], "envelope": tool})
        parts = item.get("parts")
        for offset, block in enumerate(parts if isinstance(parts, list) else []):
            if not isinstance(block, dict):
                continue
            base = ["parts", offset]
            if isinstance(block.get("functionCall"), dict) and item.get("role") in {"model", "assistant"}:  # Gemini
                tool = block["functionCall"]
                calls.append({"index": index, "id": _call_id(tool.get("id")), "name": _name(tool.get("name")),
                              "path": base + ["functionCall", "args"], "args": tool.get("args")})
            elif isinstance(block.get("functionResponse"), dict):
                tool = block["functionResponse"]
                results.append({"index": index, "id": _call_id(tool.get("id")), "name": _name(tool.get("name")),
                                "value": tool.get("response"), "path": base + ["functionResponse", "response"], "envelope": tool})
    # Native Gemini may omit ids. Match by name only when a single prior call is unambiguous.
    paired: set[int] = set()
    for result in results:
        matched = [call for call in calls if (result["id"] or id(call) not in paired)
                   and call["index"] < result["index"]
                   and (not result["name"] or result["name"] == call["name"]) and (
            result["id"] and call["id"] == result["id"] or
            not result["id"] and result["name"] and call["name"] == result["name"])]
        if len(matched) == 1:
            result["call"] = matched[0]
            result["name"] = result["name"] or matched[0]["name"]
            paired.add(id(matched[0]))
    calls = [call for call in calls if not _signed(items[call["index"]], call["path"])]
    for result in results:
        if result.get("call") not in calls:
            result.pop("call", None)
    results = [result for result in results if not _signed(items[result["index"]], result["path"])]
    return calls, results


def _excerpt(text: str, kind: str, keep: Any) -> str:
    if len(text) < MIN_TEXT_CHARS:
        return text
    # This is explicitly an excerpt, never a claimed semantic summary. Both ends
    # and the surrounding structured metadata remain visible; full text is recoverable.
    edge = min(2000, max(1200, len(text) // 8))
    preview = text[:edge] + _NOTE.format(kind=kind, id="0" * 16) + text[-edge:]
    if len(text) - len(preview) < MIN_SAVED_CHARS:
        return text
    return text[:edge] + _NOTE.format(kind=kind, id=keep(text)) + text[-edge:]


def _result_edits(value: Any, path: list[Any], keep: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            structured = json.loads(value)
        except ValueError:
            structured = None
        if isinstance(structured, (dict, list)):
            # Preserve arrays, records, control metadata and exact numeric values.
            # Only an explicit large text body in an object is eligible.
            if not isinstance(structured, dict):
                return []
            changed = copy.deepcopy(structured)
            for field in ("output", "content", "text", "result"):
                if isinstance(changed.get(field), str):
                    changed[field] = _excerpt(changed[field], "tool result", keep)
            return [{"path": path, "value": _json(changed)}] if changed != structured else []
        replacement = _excerpt(value, "tool result", keep)
        return [{"path": path, "value": replacement}] if replacement != value else []
    if isinstance(value, dict):
        edits = []
        for field in ("output", "content", "text", "result"):
            if isinstance(value.get(field), str):
                replacement = _excerpt(value[field], "tool result", keep)
                if replacement != value[field]:
                    edits.append({"path": path + [field], "value": replacement})
        return edits
    edits = []
    for field_path, text in _text_sites(value, path):
        edits.extend(_result_edits(text, field_path, keep))
    return edits


def _edits(index: int, calls: list[dict], results: list[dict], prefs: dict, keep: Any,
           session_id: str) -> tuple[str, list[dict]]:
    edits = []
    if prefs["writes"]:
        for call in calls:
            if call["index"] != index or call["name"] not in _WRITE_FIELDS:
                continue
            outcomes = [result for result in results if result.get("call") is call]
            if len(outcomes) != 1 or not _successful(outcomes[0]["value"], outcomes[0]["envelope"]):
                continue
            args = call["args"]
            try:
                decoded = json.loads(args) if isinstance(args, str) else args
            except (ValueError, TypeError):
                continue
            if not isinstance(decoded, dict):
                continue
            changed = copy.deepcopy(decoded)
            for field in _WRITE_FIELDS[call["name"]]:
                if isinstance(changed.get(field), str):
                    changed[field] = _excerpt(changed[field], "completed tool input", keep)
            if changed != decoded:
                edits.append({"path": call["path"], "value": _json(changed) if isinstance(args, str) else changed})
        if edits:
            return "writes", edits
    if prefs["results"]:
        for result in results:
            if (result["index"] == index and result["name"] in chat.CONDENSED_TOOLS
                    and chat_policy.policy_for(session_id, result["name"])["mode"] != "exact"
                    and not _has_failure(result["envelope"]) and not _has_failure(result["value"])):
                edits.extend(_result_edits(result["value"], result["path"], keep))
        if edits:
            return "results", edits
    return "", []


def _result_policy_allows(row: dict, session_id: str) -> bool:
    return row.get("kind") != "results" or all(
        chat_policy.policy_for(session_id, tool)["mode"] != "exact" for tool in row.get("tools", []))


def _apply(item: dict, edits: list[dict]) -> dict:
    changed = copy.deepcopy(item)
    for edit in edits:
        _put(changed, edit["path"], edit["value"])
    return changed


def _awaiting_write(index: int, calls: list[dict], results: list[dict], prefs: dict) -> bool:
    """An incomplete write becomes eligible only after its outcome is present."""
    if not prefs["writes"]:
        return False
    return any(call["index"] == index and call["name"] in _WRITE_FIELDS
               and not any(result.get("call") is call for result in results) for call in calls)


def _estimate(request: dict, items: list, index: int, candidate: dict, usage: dict, prices: Any) -> dict | None:
    if not isinstance(prices, dict):
        return None
    rates = {key: _number(prices.get(key)) for key in ("input", "cache_read", "cache_write")}
    tokens = {key: _number(usage.get(key)) for key in ("input_tokens", "cache_read_tokens", "cache_write_tokens")}
    if (any(value is None for value in (*rates.values(), *tokens.values()))
            or not tokens["input_tokens"] or tokens["cache_read_tokens"] + tokens["cache_write_tokens"] > tokens["input_tokens"]):
        return None
    saved_chars = len(_json(items[index])) - len(_json(candidate))
    if saved_chars < MIN_SAVED_CHARS:
        return None
    # Sizes are estimates. Prices and cached-token evidence come from this actual
    # route's last successful call. Never forecast an invented number of future reads.
    scale = min(1.0, tokens["input_tokens"] / max(1, len(_json(request)) / chat.CHARS_PER_TOKEN))
    saved = math.floor(saved_chars / chat.CHARS_PER_TOKEN * scale)
    if saved <= 0:
        return None
    before = sum(len(_json(item)) for item in items[:index]) / chat.CHARS_PER_TOKEN * scale
    observed_cached = tokens["cache_read_tokens"] + tokens["cache_write_tokens"]
    # A zero cache hit on the first read does not prove the provider failed to
    # populate its implicit cache. Bound that newly cached prefix by the real
    # input count whenever the route advertises a cached-input discount.
    cached = (tokens["input_tokens"] if rates["cache_read"] < rates["input"] or rates["cache_write"] > rates["input"]
              else observed_cached)
    # Assume the removed text had the cheapest observed rate and that every
    # surviving cached token after its position needs rebuilding. This deliberately
    # overstates the penalty when tools/system text precedes the conversation.
    rate = min(rates["input"], rates["cache_read"]) if cached else rates["input"]
    saving = saved * rate
    affected = max(0.0, cached - before - saved)
    penalty = affected * max(0.0, max(rates["input"], rates["cache_write"]) - rates["cache_read"])
    payback = math.ceil(penalty / saving) if saving > 0 else None
    return {"estimated_tokens_saved": saved, "estimated_per_replay_usd": saving,
            "estimated_cache_rebuild_usd": penalty, "payback_calls": payback,
            "first_replay_net_usd": saving - penalty, "usage_calls": usage.get("calls"),
            "input_tokens": tokens["input_tokens"], "cache_read_tokens": tokens["cache_read_tokens"],
            "cache_write_tokens": tokens["cache_write_tokens"], "potential_cached_tokens": cached,
            "cache_basis": "observed input bounds a potentially populated implicit cache",
            "worthwhile": saving > penalty}


def _batch_edit(item: dict) -> list[dict]:
    if item.get("role") != "user":
        return []
    blocks = item.get("content") if "content" in item else item.get("parts")
    if isinstance(blocks, list) and any(isinstance(block, dict) and (
            block.get("type") == "tool_result" or "toolResult" in block or "functionResponse" in block) for block in blocks):
        return []
    field = "content" if "content" in item else "parts"
    sites = _text_sites(blocks, [field])
    if not sites:
        return []
    path, text = sites[-1]
    return [{"path": path, "value": text + _BATCH_NOTE}]


def rewrite(request: Any, session_id: str) -> dict | None:
    """Project opted-in replays only; return a fresh request or None.

    Existing history is never retroactively shortened. A new item is decided
    once, after real API usage confirms its complete first read. A retry before
    that confirmation therefore sends the same full text. Choices survive restarts.
    """
    if not session_id or not store.chat_enabled(session_id) or not isinstance(request, dict):
        return None
    prefs = policy(session_id)
    if not prefs["enabled"]:
        return None
    containers = [(key, request[key]) for key in ("messages", "input", "contents") if isinstance(request.get(key), list)]
    if not containers:
        return None
    try:
        _json(request)
    except (ValueError, TypeError):
        return None
    with _LOCK:
        main = chat._main_model(session_id)
        route = {"model": request.get("model") or main.get("model"),
                 "provider": request.get("provider") or main.get("provider"), "base_url": request.get("base_url")}
        prices = modelinfo.token_prices(route["model"], route["provider"])
        usage = _usage(session_id)
        epoch = _number(usage.get("calls")) or 0
        state = store.get_setting(_key(session_id))
        if not isinstance(state, dict) or state.get("version") != 2 or not isinstance(state.get("streams"), dict):
            state = {"version": 2, "streams": {}}
        # Isolate adapters/models: a different route begins with an exact baseline.
        stream_key = _digest({**route, "containers": [key for key, _ in containers]})
        if stream_key not in state["streams"] and len(state["streams"]) >= MAX_STREAMS:
            state["active_stream"] = None
            store.set_setting(_key(session_id), state)
            return None
        first = stream_key not in state["streams"]
        stream = state["streams"].setdefault(stream_key, {"rows": {}, "batch_key": None})
        if not isinstance(stream, dict) or not isinstance(stream.get("rows"), dict):
            return None
        rows = stream["rows"]
        state["active_stream"] = stream_key
        present_keys = []
        out = copy.deepcopy(request)
        changed = False
        for container, items in containers:
            calls, results = _wire_sites(items)
            occurrences: dict[str, int] = {}
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                digest = _digest(item)
                occurrence = occurrences.get(digest, 0)
                occurrences[digest] = occurrence + 1
                key = f"{container}:{digest}:{occurrence}"
                row = rows.get(key)
                if row is not None and (not isinstance(row, dict) or not isinstance(row.get("edits"), list)
                                        or not isinstance(row.get("originals"), list)):
                    continue
                if row is None:
                    if len(rows) >= MAX_MESSAGES:
                        continue  # bounded metadata; never evict a frozen decision
                    row = {"first_epoch": epoch, "decided": first, "edits": [], "originals": []}
                    rows[key] = row
                    # Guidance belongs to one new user text, never the system prefix.
                    if not first and prefs["batching"] and stream["batch_key"] is None:
                        edits = _batch_edit(item)
                        if edits:
                            row.update(decided=True, kind="batching", edits=edits)
                            stream["batch_key"] = key
                elif not row["decided"] and epoch > row["first_epoch"]:
                    kind, preview = _edits(index, calls, results, prefs, lambda text: "0" * 16, session_id)
                    row["decided"] = bool(preview) or not _awaiting_write(index, calls, results, prefs)
                    if preview:
                        estimate = _estimate(request, items, index, _apply(item, preview), usage, prices)
                        row["estimate"] = estimate
                        if estimate and estimate["worthwhile"]:
                            originals = []
                            def keep(text: str) -> str:
                                result_id = chat._keep_full(text, session_id)
                                originals.append({"id": result_id, "chars": len(text)})
                                return result_id
                            kind, edits = _edits(index, calls, results, prefs, keep, session_id)
                            candidate = _apply(item, edits)
                            row.update(kind=kind, edits=edits, originals=originals,
                                       saved_chars=len(_json(item)) - len(_json(candidate)),
                                       tools=sorted({result["name"] for result in results if result["index"] == index
                                                     and result["name"] in chat.CONDENSED_TOOLS
                                                     and chat_policy.policy_for(session_id, result["name"])["mode"] != "exact"}))
                present_keys.append(key)
                if (not row["edits"] or not prefs.get(row.get("kind"), False) or row.get("expired")
                        or not _result_policy_allows(row, session_id)):
                    continue
                if any(chat._load_full(original["id"], session_id) is None for original in row["originals"]):
                    row["expired"] = True
                    continue
                out[container][index] = _apply(item, row["edits"])
                changed = True
        stream["present_keys"] = present_keys
        store.set_setting(_key(session_id), state)
        return out if changed else None


def summary(session_id: str) -> dict[str, Any]:
    """Inspectable projections and recovery references; amounts are estimates, never billed savings."""
    prefs = policy(session_id)
    state = store.get_setting(_key(session_id)) or {}
    streams = state.get("streams", {}) if isinstance(state, dict) else {}
    streams = streams if isinstance(streams, dict) else {}
    projections, active, expired, recovery = [], [], [], []
    for stream_key, stream in streams.items():
        if not isinstance(stream, dict) or not isinstance(stream.get("rows"), dict):
            continue
        for key, row in stream["rows"].items():
            if not isinstance(row, dict):
                continue
            if not row.get("edits") or row.get("kind") == "batching":
                continue
            projections.append(row)
            available = not row.get("expired")
            for original in row.get("originals", []):
                exists = chat._load_full(original["id"], session_id) is not None
                recovery.append({"result_id": original["id"], "chars": original["chars"], "available": exists,
                                 "kind": row["kind"], "message_fingerprint": key.split(":")[1]})
                available = available and exists
            if not available:
                expired.append(row)
            elif (prefs["enabled"] and prefs.get(row["kind"], False) and _result_policy_allows(row, session_id)
                  and stream_key == state.get("active_stream") and key in stream.get("present_keys", [])):
                active.append(row)
    return {"policy": prefs, "projected_messages": len(projections), "active_projections": len(active),
            "expired_projections": len(expired), "reduced_chars": sum(row.get("saved_chars", 0) for row in active),
            "estimated_avoided_input_tokens": sum((row.get("estimate") or {}).get("estimated_tokens_saved", 0) for row in active),
            "estimate_scope": "per replay request; token sizes estimated, usage and cache evidence observed",
            "observed_requests": _usage(session_id).get("calls", 0), "recovery": recovery,
            "reasoning_unchanged": True}
