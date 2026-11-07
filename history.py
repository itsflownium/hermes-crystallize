"""Turn recorded tool calls into runs, with cost and outcome attached.

Three read sources:
  - Crystallize's own traces (what each run called), from store.py
  - Hermes's cron run ledger (whether a run completed), read-only
  - Hermes's session database (cost, tokens, model), read-only

Hermes's databases are only ever opened read-only. If one is missing or its
columns change, we degrade to "unknown" instead of failing.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from . import modelinfo, store

logger = logging.getLogger(__name__)


@dataclass
class Step:
    tool_name: str
    args: Any
    result_preview: Optional[str]
    result_sha256: Optional[str]
    result_len: Optional[int]
    status: Optional[str]
    duration_ms: Optional[int]


@dataclass
class Run:
    execution_id: str
    job_id: Optional[str]
    job_name: Optional[str]
    session_id: Optional[str]
    scheduled: bool
    started_ts: float
    steps: list[Step] = field(default_factory=list)
    outcome: Optional[str] = None          # ledger status: completed, failed, ...
    cost_usd: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    included: bool = False                 # covered by a subscription, so cost_usd is 0
    api_cost_usd: Optional[float] = None   # when Hermes has no cost for it: at listed API prices
    session_started: Optional[float] = None
    finished: bool = False
    api_calls: Optional[int] = None
    duration_s: Optional[float] = None
    model: Optional[str] = None
    cost_provenance: str = "unknown"
    api_cost_provenance: str = "unknown"
    pricing_uncertain: bool = False
    provider: Optional[str] = None
    reasoning_effort: Optional[str] = None
    reasoning_config_fingerprint: Optional[str] = None
    session_ids: list[str] = field(default_factory=list)
    execution_binding: str = "unknown"
    identity_attribution_complete: Optional[bool] = None
    usage_totals_complete: bool = True
    attribution_evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> Optional[int]:
        parts = [self.input_tokens, self.output_tokens, self.cache_read_tokens, self.cache_write_tokens]
        known = [p for p in parts if p is not None]
        return sum(known) if known else None

    @property
    def usable(self) -> bool:
        """A run we can learn from: on schedule, finished cleanly, did something."""
        return self.scheduled and self.outcome in (None, "completed") and bool(self.steps)


# ---------------------------------------------------------------- Hermes paths

def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001 — outside Hermes (tests), fall back
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _open_readonly(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        logger.debug("crystallize: couldn't open %s read-only", path, exc_info=True)
        return None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _chunks(items: list[str], size: int = 500) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---------------------------------------------------------------- enrichment

def _ledger_outcomes(execution_ids: list[str]) -> dict[str, str]:
    if not execution_ids:
        return {}
    conn = _open_readonly(_hermes_home() / "cron" / "executions.db")
    if conn is None:
        return {}
    try:
        if not {"id", "status"} <= _columns(conn, "executions"):
            return {}
        out: dict[str, str] = {}
        for chunk in _chunks(execution_ids):
            q = f"SELECT id, status FROM executions WHERE id IN ({','.join('?' * len(chunk))})"
            for row in conn.execute(q, chunk):
                out[row["id"]] = row["status"]
        return out
    except sqlite3.Error:
        logger.debug("crystallize: reading cron ledger failed", exc_info=True)
        return {}
    finally:
        conn.close()


def running_job_ids() -> set[str]:
    """Cron jobs with a run in progress right now, from Hermes's run ledger."""
    conn = _open_readonly(_hermes_home() / "cron" / "executions.db")
    if conn is None:
        return set()
    try:
        return {r["job_id"] for r in conn.execute(
            "SELECT DISTINCT job_id FROM executions WHERE status IN ('claimed', 'running')")}
    except sqlite3.Error:
        logger.debug("crystallize: reading running jobs failed", exc_info=True)
        return set()
    finally:
        conn.close()


_SESSION_COLS = (
    "estimated_cost_usd", "actual_cost_usd", "input_tokens", "output_tokens",
    "api_call_count", "started_at", "ended_at", "model", "reasoning_tokens", "cache_read_tokens",
    "cost_status", "cache_write_tokens", "billing_provider", "model_config", "reasoning_effort",
    "parent_session_id", "end_reason", "source",
)

_USAGE_COUNTERS = ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens",
                   "cache_write_tokens", "api_call_count")
_CHAIN_CAP = 1000


def _model_config(value: Any) -> Optional[dict[str, Any]]:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    return value if isinstance(value, dict) else None


def _reasoning(info: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Only persisted choices are evidence; a missing value is not the provider default."""
    config = _model_config(info.get("model_config"))
    if config is None:
        return None, None
    value = config.get("reasoning_config", config.get("reasoning_effort", info.get("reasoning_effort")))
    if value is False:
        value = {"enabled": False}
    elif isinstance(value, str) and value.strip():
        value = {"effort": value.strip().lower()}
    if not isinstance(value, dict):
        return None, None
    effort = "none" if value.get("enabled") is False else value.get("effort")
    if not isinstance(effort, str) or not effort.strip():
        return None, None
    try:
        fingerprint = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        return None, None
    return effort.strip().lower(), fingerprint


def comparison_identity(run: Any) -> Optional[tuple[str, str, str, str]]:
    """A complete comparison key, including reasoning budgets/settings when recorded.

    None means that equivalence has not been established. In particular two missing
    provider or reasoning fields do not prove that settings stayed the same.
    """
    if getattr(run, "identity_attribution_complete", None) is False or not getattr(run, "usage_totals_complete", True):
        return None
    values = [getattr(run, key, None) for key in ("model", "provider", "reasoning_effort")]
    if any(not isinstance(value, str) or not value.strip() or value.strip().lower() in {"unknown", "auto"}
           for value in values):
        return None
    model, provider, effort = (value.strip() for value in values)
    fingerprint = getattr(run, "reasoning_config_fingerprint", None)
    return model, provider.lower(), effort.lower(), fingerprint or json.dumps({"effort": effort.lower()}, separators=(",", ":"))


def comparable_runs(left: Any, right: Any) -> bool:
    """Whether two runs or groups have the same known model, provider and reasoning."""
    groups = [([value] if isinstance(value, Run) else list(value)) for value in (left, right)]
    if not all(groups):
        return False
    keys = [comparison_identity(run) for group in groups for run in group]
    return keys[0] is not None and all(key == keys[0] for key in keys)


def _continuation(parent: dict[str, Any], child: dict[str, Any]) -> bool:
    """Hermes uses parent links for forks and delegation too, so check the boundary."""
    config = _model_config(child.get("model_config"))
    inherited = _model_config(parent.get("model_config")) or {}
    return (parent.get("end_reason") == "compression" and child.get("source") != "tool"
            and child.get("parent_session_id") == parent.get("id") and config is not None
            and not any(config.get(key) and (config[key] == parent["id"] or config[key] != inherited.get(key))
                        for key in ("_branched_from", "_delegate_from", "_reset_from")))


def _session_chain(conn: sqlite3.Connection, session_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Verified compression ancestors and continuation, failing closed on siblings/cycles."""
    have = _columns(conn, "sessions")
    if "id" not in have:
        return [], ["Session schema is unavailable."]
    cols = ["id", *(c for c in _SESSION_COLS if c in have)]

    def read(sid: str) -> Optional[dict[str, Any]]:
        row = conn.execute(f"SELECT {', '.join(cols)} FROM sessions WHERE id = ?", (sid,)).fetchone()
        return dict(row) if row else None

    row = read(session_id)
    if row is None:
        return [], ["The bound session is unavailable."]
    if not {"parent_session_id", "end_reason", "source", "model_config"} <= have:
        notes = ["Compression metadata is incomplete; only the exact segment is known."] if row.get("end_reason") == "compression" or row.get("parent_session_id") else []
        return [row], notes  # Never infer children from time or parent linkage alone.
    current, seen, uncertainty = row, {session_id}, []
    for _ in range(_CHAIN_CAP):
        parent = read(current.get("parent_session_id")) if current.get("parent_session_id") else None
        if not parent or not _continuation(parent, current):
            break
        if parent["id"] in seen:
            return [row], ["Compression lineage contains a cycle."]
        seen.add(parent["id"])
        current = parent
    else:
        return [row], ["Compression lineage exceeds the read limit."]
    chain, seen = [current], {current["id"]}
    for _ in range(_CHAIN_CAP):
        if current.get("end_reason") != "compression":
            break
        children = [dict(child) for child in conn.execute(
            f"SELECT {', '.join(cols)} FROM sessions WHERE parent_session_id = ?", (current["id"],))]
        candidates = [child for child in children if _continuation(current, child)]
        if len(candidates) != 1:
            uncertainty.append("Compression continuation is missing or ambiguous.")
            break
        current = candidates[0]
        if current["id"] in seen:
            uncertainty.append("Compression lineage contains a cycle.")
            break
        seen.add(current["id"])
        chain.append(current)
    else:
        uncertainty.append("Compression lineage exceeds the read limit.")
    if session_id not in {item["id"] for item in chain}:
        return [row], ["The requested session is outside the uniquely verified continuation."]
    return chain, uncertainty


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _session_usage(conn: sqlite3.Connection, ids: list[str]) -> list[dict[str, Any]]:
    have = _columns(conn, "session_model_usage")
    if not {"session_id", "model", "billing_provider", "task", "api_call_count"} <= have:
        return []
    cols = [c for c in ("session_id", "model", "billing_provider", "task", *_USAGE_COUNTERS,
                       "actual_cost_usd", "estimated_cost_usd", "cost_status") if c in have]
    cols = list(dict.fromkeys(cols))
    out = []
    for chunk in _chunks(ids):
        out.extend(dict(row) for row in conn.execute(
            f"SELECT {', '.join(cols)} FROM session_model_usage WHERE session_id IN ({','.join('?' * len(chunk))})", chunk))
    return out


def _delta_estimate(row: dict[str, Any]) -> Optional[float]:
    price = _finite_number(row.get("estimated_cost_usd"))
    status = row.get("cost_status")
    # The host ledger uses DEFAULT 0 for unpriced usage. Only a real status
    # establishes that zero as a known amount.
    return None if status == "unknown" or (price == 0 and not status) else price


def _session_info(conn: sqlite3.Connection, chain: list[dict[str, Any]], uncertainty: list[str]) -> dict[str, Any]:
    """Sum accounting deltas once; summary snapshots alone cannot prove chain totals."""
    info = dict(chain[-1])
    ids = [row["id"] for row in chain]
    usage = _session_usage(conn, ids)
    main = [row for row in usage if not row.get("task")]
    auxiliary = [row for row in usage if row.get("task")]
    notes = list(uncertainty)
    complete = not uncertainty
    if main:
        for row in chain:
            deltas = [item for item in main if item["session_id"] == row["id"]]
            if any(_finite_number(row.get(key)) is None or
                   any(_finite_number(item.get(key)) is None for item in deltas) or
                   sum(item.get(key) or 0 for item in deltas) != row[key]
                   for key in _USAGE_COUNTERS):
                complete = False
                notes.append("Session summary and usage deltas disagree; inherited or incomplete snapshots are not added.")
                break
    if len(chain) > 1:
        # sessions held cumulative snapshots in some hosts. The route ledger is
        # incremental; never add a parent snapshot to an inherited child total.
        covered = all(_finite_number(row.get("api_call_count")) is not None and
                      sum(item.get("api_call_count") or 0 for item in main if item["session_id"] == row["id"])
                      == row["api_call_count"] for row in chain)
        complete = complete and covered and bool(main)
        if not covered or not main:
            notes.append("Compression totals need complete per-session usage deltas; inherited snapshots are not added.")
        for key in (*_USAGE_COUNTERS, "actual_cost_usd", "estimated_cost_usd"):
            info[key] = None
        if complete:
            for key in _USAGE_COUNTERS:
                numbers = [_finite_number(row.get(key)) for row in usage]
                info[key] = int(sum(numbers)) if all(n is not None and n.is_integer() for n in numbers) else None
            # Actual session values are authoritative, including zero. Confirm
            # their delta provenance rather than reusing ledger DEFAULT 0 values.
            actuals = [_finite_number(row.get("actual_cost_usd")) for row in chain]
            actual_deltas_match = all(actual is not None and math.isclose(actual, sum(
                item.get("actual_cost_usd") or 0 for item in main if item["session_id"] == row["id"]), abs_tol=1e-12)
                for row, actual in zip(chain, actuals))
            if actual_deltas_match and not auxiliary:
                info["actual_cost_usd"] = sum(actuals)
            prices = [_delta_estimate(row) for row in usage]
            if all(price is not None for price in prices):
                info["estimated_cost_usd"] = sum(prices)
            info["cost_status"] = "included" if all(row.get("cost_status") == "included" for row in usage) else "estimated"
        else:
            info["cost_status"] = "unknown"
    elif auxiliary:
        # Auxiliary rows are explicitly outside sessions summary counters in Hermes.
        # Keep cash spend unknown when only a helper's list-price estimate exists.
        info["actual_cost_usd"] = None
        main_price = _delta_estimate(chain[0])
        extras = [_delta_estimate(row) for row in auxiliary]
        info["estimated_cost_usd"] = main_price + sum(extras) if main_price is not None and all(p is not None for p in extras) else None
        info["cost_status"] = "estimated" if info["estimated_cost_usd"] is not None else "unknown"
        for key in _USAGE_COUNTERS:
            base = _finite_number(chain[0].get(key))
            extra = [_finite_number(row.get(key)) for row in auxiliary]
            info[key] = int(base + sum(extra)) if base is not None and base.is_integer() and all(n is not None and n.is_integer() for n in extra) else None
    identities = {(row.get("model"), row.get("billing_provider")) for row in main}
    if not main:
        identities = {(row.get("model"), row.get("billing_provider")) for row in chain if row.get("api_call_count") != 0}
    if len(identities) == 1:
        info["model"], info["billing_provider"] = next(iter(identities))
    elif len(identities) > 1:
        info["model"] = info["billing_provider"] = None
        notes.append("Recorded usage spans different models or providers.")
    reasoning = {_reasoning(row) for row in chain if row.get("api_call_count") != 0}
    effort, fingerprint = next(iter(reasoning)) if len(reasoning) == 1 else (None, None)
    if len(reasoning) > 1:
        notes.append("Recorded reasoning settings differ across compression segments.")
    info["reasoning_effort"], info["reasoning_config_fingerprint"] = effort, fingerprint
    notes.append("Reasoning evidence is the persisted session configuration; per-request wire settings are not recorded." if effort else
                 "The reasoning setting is not established by the recorded session history.")
    if auxiliary:
        notes.append("Auxiliary usage has route and task evidence; its per-request reasoning settings and actual invoice cost are unavailable.")
    if not complete:
        for key in (*_USAGE_COUNTERS, "actual_cost_usd", "estimated_cost_usd"):
            info[key] = None
        info["cost_status"] = "unknown"
    info.update({"session_ids": ids, "usage_totals_complete": complete,
                 "identity_attribution_complete": bool(complete and info.get("model") and info.get("billing_provider") and effort and not auxiliary),
                 "started_at": chain[0].get("started_at"), "ended_at": chain[-1].get("ended_at"),
                 "attribution_evidence": {"session_ids": ids, "chain_verified": not uncertainty,
                    "usage_provenance": "per_route_usage_deltas" if len(chain) > 1 and complete else "session_summary",
                    "usage_totals_complete": complete, "reasoning_source": "persisted_session_configuration",
                    "reasoning_per_request_verified": False, "uncertainty": notes,
                    "usage_breakdown": usage, "session_snapshots": chain[:20],
                    "session_snapshots_truncated": len(chain) > 20}})
    return info


def _session_costs(session_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not session_ids:
        return {}
    conn = _open_readonly(_hermes_home() / "state.db")
    if conn is None:
        return {}
    try:
        conn.execute("BEGIN")
        out: dict[str, dict[str, Any]] = {}
        for sid in dict.fromkeys(session_ids):
            chain, uncertainty = _session_chain(conn, sid)
            if chain:
                out[sid] = _session_info(conn, chain, uncertainty)
        return out
    except sqlite3.Error:
        logger.debug("crystallize: reading session costs failed", exc_info=True)
        return {}
    finally:
        conn.close()


def final_answers(session_ids: list[str]) -> dict[str, str]:
    """Full original final text at the end of a verified compression continuation."""
    if not session_ids:
        return {}
    conn = _open_readonly(_hermes_home() / "state.db")
    if conn is None:
        return {}
    try:
        conn.execute("BEGIN")
        cols = _columns(conn, "messages")
        if not {"session_id", "role", "content"} <= cols:
            return {}
        out: dict[str, str] = {}
        filters = " AND COALESCE(_compressed_summary, 0) = 0" if "_compressed_summary" in cols else ""
        if "tool_calls" in cols:
            filters += " AND (tool_calls IS NULL OR tool_calls IN ('', '[]', 'null'))"
        for sid in dict.fromkeys(session_ids):
            chain, uncertainty = _session_chain(conn, sid)
            # An old transcript-only database still supports its exact session.
            ids = [row["id"] for row in chain] if chain else [sid]
            if uncertainty and chain:
                continue
            for current in reversed(ids):
                row = conn.execute(
                    "SELECT content FROM messages WHERE session_id = ? AND role = 'assistant' "
                    f"AND COALESCE(content, '') != '' {filters} ORDER BY rowid DESC LIMIT 1", (current,)).fetchone()
                if row and isinstance(row["content"], str):
                    out[sid] = row["content"]
                    break
        return out
    except sqlite3.Error:
        logger.debug("crystallize: reading final answers failed", exc_info=True)
        return {}
    finally:
        conn.close()


def net_saving(run: "Run", baseline: float, cost: float) -> float:
    """What one run saved compared with a normal one. A dearer run counts against the total,
    and a failed run delivered nothing, so all it cost is lost. So does a run with no recorded
    outcome, because nothing shows it delivered."""
    return baseline - cost if run.outcome in ("completed", "no_change", "skipped", "silent") else -cost


_QUIET_WORDS = {"[SILENT]", "SILENT", "NO_REPLY", "NO REPLY"}


def is_silent(text: Optional[str]) -> bool:
    """Hermes's own rule for "nothing to report": the marker alone, or on the first or last line."""
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    return bool(lines) and (lines[0].upper() in _QUIET_WORDS or lines[-1].upper() in _QUIET_WORDS)


def _parse_args(text: Optional[str]) -> Any:
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return text  # truncated or non-JSON args stay as text


def _apply_session(run: Run, info: Optional[dict[str, Any]]) -> None:
    """Fill in a run's cost, tokens and timing from its Hermes session."""
    if not info:
        return
    run.provider = info.get("billing_provider")
    effort, fingerprint = _reasoning(info)
    run.reasoning_effort = info.get("reasoning_effort", effort)
    run.reasoning_config_fingerprint = info.get("reasoning_config_fingerprint", fingerprint)
    # What the run actually sent beats what its session saved.
    from . import request_settings
    sent = request_settings.for_run(run.execution_id)
    if sent:
        mixed = sent["effort"] == request_settings.MIXED
        run.reasoning_effort = None if mixed else sent["effort"]
        run.reasoning_config_fingerprint = None if mixed else sent["fingerprint"]
    run.session_ids = list(info.get("session_ids") or ([run.session_id] if run.session_id else []))
    run.attribution_evidence = info.get("attribution_evidence") or {}
    run.usage_totals_complete = bool(info.get("usage_totals_complete", True))
    run.identity_attribution_complete = info.get("identity_attribution_complete")
    actual, estimated = info.get("actual_cost_usd"), info.get("estimated_cost_usd")
    run.cost_usd = actual if actual is not None else estimated
    run.cost_provenance = "actual" if actual is not None else "estimated" if estimated is not None else "unknown"
    if info.get("cost_status") == "unknown" and actual is None:
        run.cost_usd = None  # Hermes has no price for it. That isn't the same as free.
        run.cost_provenance = "unknown"
    run.input_tokens = info.get("input_tokens")
    run.output_tokens = info.get("output_tokens")
    run.reasoning_tokens = info.get("reasoning_tokens")
    run.cache_read_tokens = info.get("cache_read_tokens")
    run.cache_write_tokens = info.get("cache_write_tokens")
    run.session_started = info.get("started_at")
    run.finished = info.get("ended_at") is not None and info.get("end_reason") != "compression"
    run.included = info.get("cost_status") == "included"
    run.api_calls = info.get("api_call_count")
    if run.included and actual is None:
        run.cost_usd, run.cost_provenance = 0.0, "included"
    if run.included or run.cost_usd is None:
        breakdown = run.attribution_evidence.get("usage_breakdown") or []
        if breakdown and run.usage_totals_complete:
            prices = [modelinfo.api_cost(row.get("model"), row.get("billing_provider"), {
                "input": row.get("input_tokens"), "output": row.get("output_tokens"),
                "cache_read": row.get("cache_read_tokens"), "cache_write": row.get("cache_write_tokens"),
                "reasoning": row.get("reasoning_tokens")}) for row in breakdown]
            run.api_cost_usd = sum(prices) if all(price is not None for price in prices) else None
        elif run.usage_totals_complete:
            run.api_cost_usd = modelinfo.api_cost(info.get("model"), info.get("billing_provider"), {
                "input": run.input_tokens, "output": run.output_tokens, "cache_read": run.cache_read_tokens,
                "cache_write": run.cache_write_tokens, "reasoning": run.reasoning_tokens})
        else:
            run.api_cost_usd = None
        run.api_cost_provenance = "aggregate_usage_estimate" if run.api_cost_usd is not None else "unknown"
        # Context tiers and request fees are per request. Summed session buckets
        # cannot establish those prices; do not use them as automatic cost proof.
        run.pricing_uncertain = run.api_calls != 1
    run.model = info.get("model")
    if info.get("started_at") is not None and info.get("ended_at") is not None:
        run.duration_s = float(info["ended_at"]) - float(info["started_at"])


# ---------------------------------------------------------------- public API

def _unproven_binding(run: Run, reason: str) -> None:
    run.cost_usd = run.api_cost_usd = None
    for key in ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens", "cache_write_tokens", "api_calls"):
        setattr(run, key, None)
    run.cost_provenance = run.api_cost_provenance = "unknown"
    run.usage_totals_complete = False
    run.identity_attribution_complete = False
    run.execution_binding = "conflicting_session_binding"
    notes = list(run.attribution_evidence.get("uncertainty") or [])
    run.attribution_evidence = {**run.attribution_evidence, "execution_binding_verified": False,
                                "uncertainty": [*notes, reason]}


def _reject_shared_sessions(runs: list[Run], *, check_captured_owners: bool = False) -> None:
    """A compression segment cannot be charged to two scheduler executions."""
    owners: dict[str, list[Run]] = {}
    for run in runs:
        for sid in run.session_ids or ([run.session_id] if run.session_id else []):
            owners.setdefault(sid, []).append(run)
    conflicting = {id(run): run for group in owners.values()
                   if len({(run.job_id, run.execution_id) for run in group}) > 1 for run in group}
    if check_captured_owners and owners:
        # A job-filtered view must still notice another job claiming the same
        # segment. Query only the involved session IDs, not all historic tools.
        with store._lock:
            conn = store._db()
            for chunk in _chunks(list(owners)):
                rows = conn.execute(
                    "SELECT DISTINCT session_id,cron_job_id,cron_execution_id FROM traces "
                    f"WHERE session_id IN ({','.join('?' * len(chunk))}) AND cron_execution_id IS NOT NULL", chunk)
                for sid, job_id, execution_id in rows:
                    for run in owners[sid]:
                        if execution_id != run.execution_id or (job_id is not None and job_id != run.job_id):
                            conflicting[id(run)] = run
    for run in conflicting.values():
        _unproven_binding(run, "The same session chain is claimed by different job/execution bindings; spend is not counted twice.")


def load_runs(job: Optional[str] = None, only_usable: bool = False) -> list[Run]:
    """All recorded cron runs, oldest first. ``job`` matches a job id or name."""
    rows = store.cron_trace_rows(job)
    runs: dict[str, Run] = {}
    bindings: dict[str, dict[str, set[str]]] = {}
    for r in rows:
        binding = bindings.setdefault(r["cron_execution_id"], {"jobs": set(), "sessions": set()})
        if r["cron_job_id"]:
            binding["jobs"].add(r["cron_job_id"])
        if r["session_id"]:
            binding["sessions"].add(r["session_id"])
        run = runs.get(r["cron_execution_id"])
        if run is None:
            run = Run(
                execution_id=r["cron_execution_id"],
                job_id=r["cron_job_id"],
                job_name=r["cron_job_name"],
                session_id=r["session_id"],
                scheduled=bool(r["cron_scheduled"]),
                started_ts=r["ts"],
                execution_binding="captured_trace",
            )
            runs[run.execution_id] = run
        if run.session_id is None and r["session_id"]:
            run.session_id = r["session_id"]
        if r["tool_name"] == "__run__":
            continue  # the run is on record but made no tool calls
        run.steps.append(Step(
            tool_name=r["tool_name"],
            args=_parse_args(r["args_json"]),
            result_preview=r["result_preview"],
            result_sha256=r["result_sha256"],
            result_len=r["result_len"],
            status=r["status"],
            duration_ms=r["duration_ms"],
        ))

    ordered = sorted(runs.values(), key=lambda x: x.started_ts)
    outcomes = _ledger_outcomes([r.execution_id for r in ordered])
    costs = _session_costs([r.session_id for r in ordered if r.session_id])
    for run in ordered:
        run.outcome = outcomes.get(run.execution_id)
        _apply_session(run, costs.get(run.session_id or ""))
        binding = bindings[run.execution_id]
        if len(binding["jobs"]) > 1 or not binding["sessions"] <= set(run.session_ids or [run.session_id]):
            _unproven_binding(run, "Captured execution identity conflicts across jobs or unverified session continuations.")
    _reject_shared_sessions(ordered, check_captured_owners=True)

    if only_usable:
        ordered = [r for r in ordered if r.usable]
    return ordered


def ledger_runs(job_id: Optional[str], limit: int = 20) -> list[Run]:
    """Finished runs of a job from Hermes's own records, including ones from before
    Crystallize was recording. They have costs and answers but no step details, so a slow
    job doesn't have to wait weeks for a trial to have something to compare with."""
    if not job_id:
        return []
    conn = _open_readonly(_hermes_home() / "cron" / "executions.db")
    state = _open_readonly(_hermes_home() / "state.db")
    if conn is None or state is None:
        if conn is not None:
            conn.close()
        if state is not None:
            state.close()
        return []
    try:
        rows = conn.execute("SELECT id, started_at, finished_at FROM executions WHERE job_id = ? AND status = 'completed' "
                            "AND scheduled_instant IS NOT NULL ORDER BY started_at DESC LIMIT ?", (job_id, limit)).fetchall()
        out: list[Run] = []
        used: set[str] = set()
        for row in reversed(rows):
            try:
                start = datetime.fromisoformat(row["started_at"]).timestamp()
                end = datetime.fromisoformat(row["finished_at"]).timestamp()
            except (TypeError, ValueError):
                continue
            match = _ledger_session(state, str(job_id), str(row["id"]), start, end)
            if not match or match["id"] in used:
                continue
            used.add(match["id"])
            out.append(Run(execution_id=row["id"], job_id=job_id, job_name=None, session_id=match["id"],
                           scheduled=True, started_ts=start, outcome="completed", execution_binding="scheduler_admission"))
        costs = _session_costs([r.session_id for r in out])
        for run in out:
            _apply_session(run, costs.get(run.session_id or ""))
        _reject_shared_sessions(out)
        return out
    except sqlite3.Error:
        logger.debug("crystallize: reading older runs failed", exc_info=True)
        return []
    finally:
        conn.close()
        state.close()


def _ledger_session(state: sqlite3.Connection, job_id: str, execution_id: str,
                    start: float, end: float) -> Optional[sqlite3.Row]:
    """Use durable scheduler identity; a nearby timestamp is never identity."""
    have = _columns(state, "session_admissions")
    if {"target_session_id", "principal_id", "request_id"} <= have:
        matches = state.execute(
            "SELECT DISTINCT s.id FROM session_admissions a JOIN sessions s ON s.id = a.target_session_id "
            "WHERE a.principal_id = 'cron-owner' AND a.request_id = ? AND s.source = 'cron'",
            (f"cron:{job_id}:{execution_id}",)).fetchall()
        if len(matches) == 1:
            other = state.execute(
                "SELECT 1 FROM session_admissions WHERE principal_id = 'cron-owner' "
                "AND target_session_id = ? AND request_id != ? LIMIT 1",
                (matches[0]["id"], f"cron:{job_id}:{execution_id}")).fetchone()
            return None if other else matches[0]
        if matches:
            return None
    # cron_<job>_<timestamp> establishes only a job, never an execution.
    # Manual, retry and concurrent fires can share this window. An old run without
    # an admission or a captured execution/session binding remains unproven.
    return None


def _avg(values: list[Optional[float]]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


def runs_for_job(job_id: str, only_usable: bool = False) -> list[Run]:
    """Read an authoritative job ID without also accepting another job's name.

    The interactive history search accepts either names or IDs. Once a caller
    has a durable ID, its accounting must never inherit a matching display name.
    """
    if not isinstance(job_id, str) or not job_id or job_id == "?":
        return []
    return [run for run in load_runs(job_id, only_usable=only_usable) if run.job_id == job_id]


def job_summaries() -> list[dict[str, Any]]:
    """One line per cron job: how many runs we can learn from and what they cost."""
    # A display name can be reused by an unrelated/recreated job.
    by_job: dict[str, list[Run]] = {}
    for run in load_runs():
        by_job.setdefault(run.job_id or f"unknown:{run.execution_id}", []).append(run)
    out = []
    for _, runs in by_job.items():
        job_id = runs[-1].job_id or "?"
        usable = [r for r in runs if r.usable]
        out.append({
            "job_id": job_id,
            "job_name": runs[-1].job_name,
            "runs": len(runs),
            "usable_runs": len(usable),
            "avg_tool_calls": _avg([len(r.steps) for r in usable]),
            "avg_cost_usd": _avg([r.cost_usd for r in usable]),
            "avg_duration_s": _avg([r.duration_s for r in usable]),
            "avg_api_calls": _avg([r.api_calls for r in usable]),
        })
    out.sort(key=lambda s: s["usable_runs"], reverse=True)
    return out
