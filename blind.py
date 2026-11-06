"""Persisted anonymous A/B review of already-generated same-input answers.

This module never generates answers, dispatches actions or selects a model. Only
the private SQLite rows contain the random A/B mapping before an explicit choice
or reveal. A choice and its reveal are committed together before returning any
identity, price or validation metadata to the caller.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import time
from typing import Any

from . import experiments, receipts, store

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,255}\Z")
_CHOICES = {"A", "B", "tie", "neither"}
_SIDES = ("original", "candidate")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS blind_sources (
    comparison_id TEXT PRIMARY KEY, record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blind_reviews (
    comparison_id TEXT PRIMARY KEY, mapping_json TEXT NOT NULL,
    created_at REAL NOT NULL, choice TEXT, chosen_at REAL,
    revealed_at REAL, reveal_reason TEXT
);
"""
_NOTICE = "Review both anonymous answers, then choose A, B, tie or neither. Identity, costs and checks are shown only after a choice or an explicit reveal."
_QUALITY_LIMIT = "A human preference and deterministic task checks do not prove factual correctness, semantic equivalence or full live-job quality."


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise experiments.ComparisonError("Choose a valid comparison ID.")
    return value


def _setup(conn: Any) -> None:
    conn.executescript(experiments._SCHEMA)
    conn.executescript(_SCHEMA)


def _record(conn: Any, comparison_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT data_json FROM comparison_records WHERE id=?", (comparison_id,)).fetchone()
    if row is None:
        row = conn.execute("SELECT record_json FROM blind_sources WHERE comparison_id=?", (comparison_id,)).fetchone()
    if row is None:
        raise LookupError("That comparison does not exist.")
    return json.loads(row[0])


def _state(conn: Any, comparison_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT mapping_json,choice,chosen_at,revealed_at,reveal_reason FROM blind_reviews WHERE comparison_id=?",
                       (comparison_id,)).fetchone()
    if row is None:
        sides = _SIDES if secrets.randbelow(2) == 0 else tuple(reversed(_SIDES))
        # INSERT OR IGNORE also makes the first mapping stable across processes;
        # a later client cannot replace the winning mapping by reloading a panel.
        conn.execute("INSERT OR IGNORE INTO blind_reviews(comparison_id,mapping_json,created_at) VALUES (?,?,?)",
                     (comparison_id, experiments._canonical(dict(zip(("A", "B"), sides))), time.time()))
        conn.commit()
        row = conn.execute("SELECT mapping_json,choice,chosen_at,revealed_at,reveal_reason FROM blind_reviews WHERE comparison_id=?",
                           (comparison_id,)).fetchone()
    return {"mapping": json.loads(row[0]), "choice": row[1], "chosen_at": row[2],
            "revealed_at": row[3], "reveal_reason": row[4]}


def _sample_count(record: dict[str, Any]) -> int:
    value = record.get("sample_count", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= experiments.MAX_SAMPLES else 0


def _calls(record: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    calls: dict[tuple[int, str], dict[str, Any]] = {}
    duplicates = set()
    for call in record.get("calls", []):
        if not isinstance(call, dict):
            continue
        index, side = call.get("sample_index"), call.get("side")
        if isinstance(index, bool) or not isinstance(index, int) or side not in _SIDES:
            continue
        key = (index, side)
        if key in calls:
            duplicates.add(key)
        calls[key] = call
    return {key: call for key, call in calls.items() if key not in duplicates}


def _complete(call: dict[str, Any]) -> bool:
    answer = call.get("output")
    return (call.get("status") == "completed" and call.get("complete") is not False
            and isinstance(answer, str) and bool(answer.strip()) and len(answer) <= experiments.MAX_OUTPUT_CHARS
            and not experiments._incomplete_result(answer)
            and (not isinstance(call.get("completion_check"), dict) or call["completion_check"].get("ok") is True)
            and (not call.get("output_sha256") or call["output_sha256"] == experiments._hash(answer)))


def _eligible(record: dict[str, Any], calls: dict[tuple[int, str], dict[str, Any]]) -> bool:
    count = _sample_count(record)
    if record.get("status") != "completed" or record.get("scope") != experiments._SCOPE or not count:
        return False
    if len(record.get("calls", [])) != count * 2 or len(calls) != count * 2:
        return False
    for index in range(count):
        first, second = (calls.get((index, side), {}) for side in _SIDES)
        if not (_complete(first) and _complete(second)):
            return False
        digest = first.get("input_sha256")
        if not isinstance(digest, str) or not digest or digest != second.get("input_sha256"):
            return False
    return True


def _answer(call: dict[str, Any]) -> dict[str, Any]:
    complete = _complete(call)
    status = "completed" if complete else "pending" if call.get("status") == "pending" or not call else "failed"
    # Incomplete answer text and provider exceptions are deliberately omitted.
    # Errors frequently contain identity, routing and account information.
    return {"answer": call["output"] if complete else None, "status": status}


def _revealed_call(call: dict[str, Any]) -> dict[str, Any]:
    allowed = ("side", "status", "complete", "requested", "answered_by", "cost", "validation", "completion_check",
               "duration_ms", "source_execution_id", "input_sha256", "output_sha256", "error")
    return {key: copy.deepcopy(call[key]) for key in allowed if key in call}


def _presentation(record: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    calls = _calls(record)
    eligible = _eligible(record, calls)
    revealed = state["revealed_at"] is not None
    mapping = state["mapping"]
    samples = [{"sample_index": index,
                **{label: _answer(calls.get((index, side), {})) for label, side in mapping.items()}}
               for index in range(_sample_count(record))]
    status = record.get("status") if record.get("status") in {"completed", "running", "failed"} else "failed"
    result = {"id": record["id"], "status": status, "scope": experiments._SCOPE,
              "samples": samples, "choice": state["choice"], "can_choose": eligible and not revealed,
              "choice_eligible": eligible and not revealed, "revealed": revealed,
              "choice_recorded_blind": bool(state["choice"] is not None and state["reveal_reason"] == "choice"),
              "notice": _NOTICE if eligible else "A complete pair from identical frozen input is required before a preference can be recorded."}
    if revealed:
        quality = copy.deepcopy(record.get("quality_evidence") or {})
        quality.update(semantic_quality_proven=False,
                       human_review={"choice": state["choice"], "blinded": result["choice_recorded_blind"],
                                     "chosen_at": state["chosen_at"], "reveal_reason": state["reveal_reason"]},
                       limitation=_QUALITY_LIMIT)
        metadata = ("experiment_kind", "experiment_label", "efficiency_comparison", "cost_difference_scope",
                    "reasoning_policy", "contract_configured")
        result["reveal_reason"] = state["reveal_reason"]
        result["reveal"] = {**{key: copy.deepcopy(record[key]) for key in metadata if key in record},
                            "mapping": mapping, "revealed_at": state["revealed_at"],
                            "efficiency_savings_claimed": False, "reasoning_overridden": False,
                            "quality_evidence": quality, "limitations": copy.deepcopy(record.get("limitations") or []) + [_QUALITY_LIMIT],
                            "pairs": copy.deepcopy(record.get("pairs") or []),
                            "samples": [{"sample_index": index,
                                         **{label: _revealed_call(calls.get((index, side), {})) for label, side in mapping.items()}}
                                        for index in range(_sample_count(record))]}
    return receipts.redact_export(result)


def register(comparison_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """Register an immutable two-side snapshot from another experiment domain.

    The adapter uses the comparison call shape (``original``/``candidate`` sides,
    indexed samples and identical ``input_sha256`` values). It makes no model
    calls. Auction experiments can register each pair of already-generated
    answers with a distinct ID; a reload cannot replace an answer after a vote.
    """
    comparison_id = _identifier(comparison_id)
    if not isinstance(record, dict) or record.get("id", comparison_id) != comparison_id:
        raise experiments.ComparisonError("A blind source must be a matching comparison record.")
    saved = copy.deepcopy(record)
    saved["id"] = comparison_id
    saved.setdefault("sample_count", 1)
    saved.setdefault("scope", experiments._SCOPE)
    if saved.get("scope") != experiments._SCOPE or not _sample_count(saved) or not isinstance(saved.get("calls"), list):
        raise experiments.ComparisonError("A blind source needs bounded same-input answer samples.")
    calls = _calls(saved)
    if len(calls) != _sample_count(saved) * 2 or len(saved["calls"]) != len(calls):
        raise experiments.ComparisonError("A blind source needs exactly one original and candidate call per sample.")
    for index in range(_sample_count(saved)):
        first, second = (calls.get((index, side), {}) for side in _SIDES)
        digest = first.get("input_sha256")
        if not isinstance(digest, str) or not digest or digest != second.get("input_sha256"):
            raise experiments.ComparisonError("Both blind answers must use the identical frozen input.")
    encoded = experiments._bounded(saved, experiments.MAX_RECORD_BYTES, "Blind comparison source")
    with store._lock:
        conn = store._db()
        _setup(conn)
        if conn.execute("SELECT 1 FROM comparison_records WHERE id=?", (comparison_id,)).fetchone():
            raise experiments.ComparisonError("That comparison ID already belongs to a saved experiment.")
        conn.execute("INSERT OR IGNORE INTO blind_sources(comparison_id,record_json) VALUES (?,?)", (comparison_id, encoded))
        conn.commit()
        existing = conn.execute("SELECT record_json FROM blind_sources WHERE comparison_id=?", (comparison_id,)).fetchone()
        if existing[0] != encoded:
            raise experiments.ComparisonError("A registered blind comparison is immutable; use a new comparison ID.")
        _state(conn, comparison_id)
    return {"id": comparison_id, "registered": True}


def present(comparison_id: str) -> dict[str, Any]:
    """Return anonymous answers, or persisted revealed details after a decision.

    Reading never runs a model. The first read only persists the server's random
    display mapping; subsequent reads and module/process reloads retain it.
    """
    comparison_id = _identifier(comparison_id)
    with store._lock:
        conn = store._db()
        _setup(conn)
        record = _record(conn, comparison_id)
        state = _state(conn, comparison_id)
    return _presentation(record, state)


def choose(comparison_id: str, choice: str) -> dict[str, Any]:
    """Commit one blind preference, then reveal routes, costs and task checks."""
    comparison_id = _identifier(comparison_id)
    if not isinstance(choice, str) or choice not in _CHOICES:
        raise experiments.ComparisonError("Choose A, B, tie or neither.")
    with store._lock:
        conn = store._db()
        _setup(conn)
        record = _record(conn, comparison_id)
        state = _state(conn, comparison_id)
        if state["choice"] is not None:
            if state["choice"] != choice:
                raise experiments.ComparisonError("The blind preference is already recorded and cannot be changed after reveal.")
            return _presentation(record, state)
        if state["revealed_at"] is not None:
            raise experiments.ComparisonError("Identities were already revealed without a vote; a later preference cannot be recorded as blind.")
        if not _eligible(record, _calls(record)):
            raise experiments.ComparisonError("Only complete answers from identical frozen input can receive a blind preference.")
        now = time.time()
        conn.execute("UPDATE blind_reviews SET choice=?,chosen_at=?,revealed_at=?,reveal_reason='choice' "
                     "WHERE comparison_id=? AND choice IS NULL AND revealed_at IS NULL", (choice, now, now, comparison_id))
        conn.commit()
        state = _state(conn, comparison_id)
        if state["choice"] != choice:
            raise experiments.ComparisonError("This comparison was already revealed or received a different preference.")
    return _presentation(record, state)


def reveal(comparison_id: str) -> dict[str, Any]:
    """Explicitly reveal without voting; subsequent votes cannot count as blind."""
    comparison_id = _identifier(comparison_id)
    with store._lock:
        conn = store._db()
        _setup(conn)
        record = _record(conn, comparison_id)
        _state(conn, comparison_id)
        conn.execute("UPDATE blind_reviews SET revealed_at=?,reveal_reason='explicit_without_vote' "
                     "WHERE comparison_id=? AND revealed_at IS NULL", (time.time(), comparison_id))
        conn.commit()
        state = _state(conn, comparison_id)
    return _presentation(record, state)
