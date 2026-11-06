"""Conservative local checks before and after an optional paid text summary.

These checks preserve observable literals, not factual or semantic equivalence.
Uncertain/oversized structures stay exact. No model or host tool is called here.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

MAX_NODES = 10_000
MAX_DEPTH = 20
MAX_LITERALS = 256
_BAD_STATUS = {"error", "failed", "blocked", "denied", "cancelled", "canceled", "timeout",
               "timed_out", "incomplete", "partial", "pending", "running", "queued", "interrupted"}
_TRANSPORT_NUMBERS = {"exit_code"}
_LITERALS = re.compile(
    r'https?://[^\s<>"\\]+|\b\d{4}-\d{2}-\d{2}(?:T[0-9:.+Z-]+)?\b|'
    r'(?<!\w)[$€£¥]\s?\d[\d,]*(?:\.\d+)?|(?<!\w)-?\d+(?:\.\d+)?\s?%|'
    r'\b[A-Z][A-Z0-9]{1,15}-\d[\w.-]*|'
    r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b')
_LABELED_ID = re.compile(
    r'\b(?:incident|ticket|issue|order|invoice|customer|account|request|trace|job|task)[ _-]?'
    r'(?:id|number|#)\s*["\']?\s*[:=#]\s*["\']?([A-Za-z0-9][A-Za-z0-9_.:/-]*)', re.I)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON keys cannot establish preservation.")
        result[key] = value
    return result


def _parse(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def failed_result(text: str) -> bool:
    """Recognize structured failures, including nested/mixed-source results."""
    count = 0
    def walk(value: Any, depth: int = 0) -> bool:
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            return True
        if isinstance(value, str):
            stripped = value.lstrip()
            if stripped.startswith(("{", "[")):
                try:
                    return walk(_parse(stripped), depth + 1)
                except (ValueError, RecursionError):
                    return True
            return bool(re.match(r"(?:error\s*:|fatal\s*:|traceback\s*\(|permission denied\b)", stripped, re.I))
        if isinstance(value, dict):
            if value.get("error") or value.get("errors") or value.get("isError") is True or value.get("is_error") is True:
                return True
            if (value.get("success") is False or value.get("ok") is False or value.get("complete") is False
                    or value.get("truncated") is True or value.get("incomplete") is True):
                return True
            code = value.get("exit_code")
            if isinstance(code, (int, float)) and code != 0:
                return True
            for key in ("status", "status_code", "http_status"):
                status = value.get(key)
                if isinstance(status, str) and (status.strip().lower() in _BAD_STATUS
                        or status.strip().isdigit() and int(status) >= 400):
                    return True
                if isinstance(status, (int, float)) and not isinstance(status, bool) and status >= 400:
                    return True
            return any(walk(child, depth + 1) for child in value.values())
        return isinstance(value, list) and any(walk(child, depth + 1) for child in value)
    try:
        return walk(text)
    except (ValueError, RecursionError):
        return True


def required_literals(text: str) -> set[str] | None:
    """URLs/dates/IDs/amounts plus structured numeric values and explicit IDs.

    Bare numbers in unstructured prose are not classified as important facts.
    The caller retains the exact original; this is not a semantic guarantee.
    """
    literals = {match.group().rstrip(".,;)]}") for match in _LITERALS.finditer(text)}
    literals.update(match[1].rstrip(".,;)]}") for match in _LABELED_ID.finditer(text))
    count = 0
    def walk(value: Any, key: str = "", depth: int = 0) -> None:
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH or len(literals) > MAX_LITERALS:
            raise ValueError("Literal checks exceeded their bound.")
        if isinstance(value, dict):
            for name, child in value.items():
                walk(child, str(name).lower(), depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, key, depth + 1)
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and key not in _TRANSPORT_NUMBERS:
            if not math.isfinite(value):
                raise ValueError("Nonfinite value")
            literals.add(str(value))
        elif isinstance(value, str):
            if key == "id" or key.endswith(("_id", "_uuid")):
                if value:
                    literals.add(value)
            if value.lstrip().startswith(("{", "[")):
                walk(_parse(value), "", depth + 1)
    try:
        if text.lstrip().startswith(("{", "[")):
            walk(_parse(text))
    except (ValueError, RecursionError, OverflowError):
        return None
    return literals if len(literals) <= MAX_LITERALS else None


def preserves(summary: str, literals: set[str]) -> bool:
    # Boundary checks prevent an identifier/value being "preserved" as a
    # substring of a different value such as 503 in 15030 or INC-42 in INC-420.
    return all(re.search(r"(?<![\w.-])" + re.escape(value) + r"(?![\w/%?&#=+:-]|\.\w)", summary) is not None
               for value in literals)
