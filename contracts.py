"""Opt-in, deterministic outcome checks for a cron job.

A contract is keyed by the immutable Hermes job ID. It describes observable
requirements, not semantic equivalence. Checks use the current run's sources and
successful action records; a previous answer is never evidence for this run.
Fields are JSON dotted paths (``items.*.url`` checks every item). ``minimum_items``
counts a JSON list, an ``items``/``results``/``entries`` list, or top-level Markdown
list items. Set ``item_path`` when the output has another or an ambiguous shape.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import re
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from . import store

VERSION = 1
_KEY = "cron_contract:"
_MISSING = object()
_RULES = {"version", "required_sources", "required_urls", "required_fields", "minimum_items",
          "item_path", "preserve_errors", "preserve_status", "required_actions"}
_TARGET_KEYS = {"target", "recipient", "recipients", "to", "channel", "channel_id", "chat_id",
                "email", "room", "room_id", "path", "file_path", "url", "urls", "href", "resource"}
_SUCCESS = {"ok", "success", "succeeded", "completed"}
_ERROR_KEYS = {"error", "errors", "exception", "failure", "failures"}
_STATUS_KEYS = {"status", "status_code", "http_status", "http_code", "exit_code", "code"}
_URL = re.compile(r"https?://[^\s<>\"'`\[\]{}]+")


class ContractError(ValueError):
    """A malformed or unsupported outcome contract."""


def _strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ContractError(f"{name} must be a list of nonempty strings.")
    return list(dict.fromkeys(v.strip() for v in value))


def _requirements(value: Any, name: str, *, actions: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ContractError(f"{name} must be a list of tool/target objects.")
    out = []
    for rule in value:
        allowed = {"tool", "target", "target_field", "min_count"} if actions else {"tool", "target", "target_field"}
        if not isinstance(rule, dict) or builtins.set(rule) - allowed:
            raise ContractError(f"Each {name} entry must contain tool, target and optional target_field" + ("/min_count." if actions else "."))
        for field in ("tool", "target"):
            if not isinstance(rule.get(field), str) or not rule[field].strip():
                raise ContractError(f"Each {name} entry needs a nonempty {field}.")
        item = {"tool": rule["tool"].strip(), "target": rule["target"].strip()}
        if "target_field" in rule:
            if not isinstance(rule["target_field"], str) or not rule["target_field"].strip():
                raise ContractError("target_field must be a JSON dotted path.")
            item["target_field"] = rule["target_field"].strip()
        if actions:
            count = rule.get("min_count", 1)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ContractError("An action's min_count must be a positive integer.")
            item["min_count"] = count
        out.append(item)
    return out


def validate(spec: Any) -> dict[str, Any]:
    """Return a normalized v1 specification or raise ContractError.

    Unknown keys are rejected so a misspelled requirement cannot silently pass.
    ``None`` clears a contract through :func:`set`; it is not a specification.
    """
    if not isinstance(spec, dict) or builtins.set(spec) - _RULES:
        unknown = sorted(builtins.set(spec) - _RULES) if isinstance(spec, dict) else []
        raise ContractError("Expected an outcome contract object." + (f" Unknown fields: {', '.join(unknown)}." if unknown else ""))
    version = spec.get("version", VERSION)
    if isinstance(version, bool) or version != VERSION:
        raise ContractError(f"Only contract version {VERSION} is supported.")
    out: dict[str, Any] = {"version": VERSION}
    for name in ("required_urls", "required_fields"):
        if name in spec:
            out[name] = _strings(spec[name], name)
    for url in out.get("required_urls", []):
        if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
            raise ContractError("required_urls entries must be complete HTTP(S) URLs.")
    for name in ("required_sources", "required_actions"):
        if name in spec:
            out[name] = _requirements(spec[name], name, actions=name == "required_actions")
    if "minimum_items" in spec:
        count = spec["minimum_items"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ContractError("minimum_items must be a nonnegative integer.")
        out["minimum_items"] = count
    if "item_path" in spec:
        if not isinstance(spec["item_path"], str) or not spec["item_path"].strip():
            raise ContractError("item_path must be a JSON dotted path.")
        if "minimum_items" not in spec:
            raise ContractError("item_path requires minimum_items.")
        out["item_path"] = spec["item_path"].strip()
    for name in ("preserve_errors", "preserve_status"):
        if name in spec:
            if not isinstance(spec[name], bool):
                raise ContractError(f"{name} must be true or false.")
            out[name] = spec[name]
    return out


def get(job_id: str) -> Optional[dict[str, Any]]:
    """Read a job's contract. Callers resolve a display name to a job ID first."""
    value = store.get_setting(_KEY + str(job_id))
    return validate(value) if value is not None else None


def set(job_id: str, spec: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Save or clear an explicit contract, without editing the cron job."""
    if not isinstance(job_id, str) or not job_id.strip():
        raise ContractError("A stable job ID is required.")
    normalized = validate(spec) if spec is not None else None
    from . import native_cron
    native_cron.invalidate(job_id)
    store.set_setting(_KEY + job_id, normalized)
    return normalized


def fingerprint(spec: Optional[dict[str, Any]]) -> str:
    value = validate(spec) if spec is not None else None
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _parse(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("```json") and stripped.endswith("```"):
            stripped = stripped[7:-3].strip()
        try:
            return json.loads(stripped)
        except ValueError:
            return value
    return value


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _values(data: Any, path: str) -> list[Any]:
    parts = re.sub(r"\[(\d+|\*)\]", r".\1", path).split(".")
    values = [data]
    for part in parts:
        following = []
        for value in values:
            if part == "*" and isinstance(value, (list, dict)):
                following.extend(value.values() if isinstance(value, dict) else value)
            elif isinstance(value, dict) and part in value:
                following.append(value[part])
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                following.append(value[int(part)])
            else:
                following.append(_MISSING)
        values = following
    return values


def _walk(data: Any):
    if isinstance(data, dict):
        for key, value in data.items():
            yield str(key), value
            yield from _walk(value)
    elif isinstance(data, list):
        for value in data:
            yield from _walk(value)


def _scalars(data: Any):
    if isinstance(data, dict):
        for value in data.values():
            yield from _scalars(value)
    elif isinstance(data, list):
        for value in data:
            yield from _scalars(value)
    elif data is not None:
        yield str(data)


def _url(value: str) -> str:
    try:
        p = urlsplit(value.rstrip(".,);"))
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, p.fragment))
    except ValueError:
        return value


def _urls(value: Any) -> builtins.set[str]:
    return {_url(m.group()) for m in _URL.finditer(_text(value))}


def _equal_target(value: Any, target: str) -> bool:
    for item in _scalars(value):
        if item == target or (target.startswith(("https://", "http://")) and _url(target) in _urls(item)):
            return True
    return False


def observations(sources: Optional[list[Any]]) -> list[dict[str, Any]]:
    """Adapt recorded Steps, traces or frozen runner outputs to current evidence."""
    out = []
    for source in sources or []:
        if not isinstance(source, dict):
            source = getattr(source, "__dict__", {})
        raw = source.get("output", source.get("result", source.get("result_preview")))
        preview_only = "output" not in source and "result" not in source and "result_preview" in source
        length = source.get("result_len")
        incomplete_preview = preview_only and isinstance(length, int) and (raw is None or length > len(_text(raw)))
        out.append({"tool": source.get("tool", source.get("tool_name")),
                    "args": _parse(source.get("args", source.get("args_json", {}))),
                    "output": _parse(raw),
                    "status": source.get("status"),
                    "truncated": bool(source.get("truncated") or source.get("result_truncated") or incomplete_preview)})
    return out


def _matches(source: dict[str, Any], rule: dict[str, Any], *, action: bool = False) -> bool:
    if source.get("tool") != rule["tool"]:
        return False
    if rule.get("target_field"):
        targets = _values(source.get("args"), rule["target_field"])
    elif action:
        args = source.get("args")
        targets = [v for k, v in args.items() if str(k).lower() in _TARGET_KEYS] if isinstance(args, dict) else []
    else:
        targets = [source.get("args")]
    return any(v is not _MISSING and _equal_target(v, rule["target"]) for v in targets)


def _success(source: dict[str, Any]) -> bool:
    # A tool having been called is not evidence that a send/change succeeded.
    status = source.get("status")
    data = source.get("output")
    if isinstance(data, dict):
        if data.get("error") or data.get("success") is False or data.get("ok") is False:
            return False
        if isinstance(data.get("exit_code"), int) and data["exit_code"] != 0:
            return False
    return status is True or str(status).lower() in _SUCCESS or (
        status is None and isinstance(data, dict) and (data.get("success") is True or data.get("ok") is True))


def _item_count(answer: Any, path: Optional[str]) -> Optional[int]:
    if path:
        values = _values(answer, path)
        return len(values[0]) if len(values) == 1 and isinstance(values[0], list) else None
    if isinstance(answer, list):
        return len(answer)
    if isinstance(answer, dict):
        lists = [answer[k] for k in ("items", "results", "entries") if isinstance(answer.get(k), list)]
        return len(lists[0]) if len(lists) == 1 else None
    if isinstance(answer, str):
        lines = [m.group(1) for m in re.finditer(r"^([ \t]*)(?:[-*+] |\d+[.)] )\S", answer, re.MULTILINE)]
        if not lines:
            return 0
        indent = min(len(s.expandtabs(4)) for s in lines)
        return sum(len(s.expandtabs(4)) == indent for s in lines)
    return None


def _preserved(value: Any, text: str) -> bool:
    token = str(value)
    return bool(re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", text, re.IGNORECASE))


def evaluate(spec: Optional[dict[str, Any]], *, answer: Any, sources: Optional[list[Any]] = None,
             actions: Optional[list[Any]] = None, status: Optional[str] = None) -> dict[str, Any]:
    """Check explicit requirements against current evidence; never calls an AI/tool.

    Source requirements verify collection from a tool and target. URL rules also
    require the URL in both current evidence and the answer. Required actions match
    a successful tool+target/count; writing "sent" in the answer is insufficient.
    Error/status checks preserve literal structured values, not their meaning.
    """
    limitations = ["Deterministic requirements are checked; factual correctness and semantic equivalence are not proven."]
    if spec is None:
        return {"ok": True, "configured": False, "why": "no outcome contract configured", "checks": [],
                "failures": [], "version": VERSION, "fingerprint": fingerprint(None), "limitations": limitations}
    spec = validate(spec)
    data, text = _parse(answer), _text(answer)
    current = observations(sources)
    recorded_actions = observations(actions if actions is not None else sources)
    checks: list[dict[str, Any]] = []

    def check(rule: str, ok: bool, detail: str) -> None:
        checks.append({"rule": rule, "ok": bool(ok), "detail": detail})

    for rule in spec.get("required_sources", []):
        matching = [s for s in current if _matches(s, rule)]
        check("required_source", bool(matching) and all(s["output"] is not None and not s["truncated"] for s in matching),
              f"source {rule['tool']} → {rule['target']} must have current, complete evidence")
    for url in spec.get("required_urls", []):
        present = _url(url) in _urls(answer)
        evidenced = any(s["output"] is not None and not s["truncated"] and
                        _url(url) in _urls({"args": s["args"], "output": s["output"]}) for s in current)
        check("required_url", present and evidenced, f"URL {url} must appear in the answer and current sources")
    for path in spec.get("required_fields", []):
        values = _values(data, path)
        check("required_field", bool(values) and all(v is not _MISSING and v is not None and v != "" for v in values),
              f"required JSON field {path}")
    if "minimum_items" in spec:
        count = _item_count(data, spec.get("item_path"))
        check("minimum_items", count is not None and count >= spec["minimum_items"],
              f"at least {spec['minimum_items']} items required (observed {count if count is not None else 'unknown'})")
    for name, keys in (("preserve_errors", _ERROR_KEYS), ("preserve_status", _STATUS_KEYS)):
        if not spec.get(name):
            continue
        complete = bool(current) and all(s["output"] is not None and not s["truncated"] for s in current)
        check(name, complete, f"{name} requires complete current source evidence")
        for s in current:
            for key, value in _walk(s["output"]):
                if (key.lower() in keys and value is not None and value != "" and value is not False and value != []
                        and (name != "preserve_errors" or bool(value))):
                    check(name, _preserved(_text(value) if isinstance(value, (dict, list)) else value, text),
                          f"preserve {s['tool'] or 'source'} {key}={_text(value)}")
            if name == "preserve_errors" and s.get("status") and str(s["status"]).lower() in {"failed", "error", "failure"}:
                check(name, _preserved(s["status"], text), f"preserve source failure status {s['status']}")
        if name == "preserve_status" and status is not None:
            check(name, _preserved(status, text), f"preserve run status {status}")
    for rule in spec.get("required_actions", []):
        count = sum(_matches(s, rule, action=True) and _success(s) for s in recorded_actions)
        check("required_action", count >= rule["min_count"],
              f"{rule['tool']} → {rule['target']} needs {rule['min_count']} successful actions (observed {count})")
    failures = [c["detail"] for c in checks if not c["ok"]]
    return {"ok": not failures, "configured": True, "why": failures[0] if failures else "configured outcome checks passed",
            "failures": failures, "checks": checks, "version": VERSION, "fingerprint": fingerprint(spec), "limitations": limitations}

