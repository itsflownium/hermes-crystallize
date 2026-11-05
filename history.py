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
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from . import store

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
    api_calls: Optional[int] = None
    duration_s: Optional[float] = None
    model: Optional[str] = None

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
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
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
    conn = _open_readonly(_hermes_home() / "cron" / "executions.db")
    if conn is None or not execution_ids:
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


_SESSION_COLS = (
    "estimated_cost_usd", "actual_cost_usd", "input_tokens", "output_tokens",
    "api_call_count", "started_at", "ended_at", "model",
)


def _session_costs(session_ids: list[str]) -> dict[str, dict[str, Any]]:
    conn = _open_readonly(_hermes_home() / "state.db")
    if conn is None or not session_ids:
        return {}
    try:
        have = _columns(conn, "sessions")
        cols = [c for c in _SESSION_COLS if c in have]
        if "id" not in have or not cols:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for chunk in _chunks(session_ids):
            q = (f"SELECT id, {', '.join(cols)} FROM sessions "
                 f"WHERE id IN ({','.join('?' * len(chunk))})")
            for row in conn.execute(q, chunk):
                out[row["id"]] = {c: row[c] for c in cols}
        return out
    except sqlite3.Error:
        logger.debug("crystallize: reading session costs failed", exc_info=True)
        return {}
    finally:
        conn.close()


def _parse_args(text: Optional[str]) -> Any:
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return text  # truncated or non-JSON args stay as text


# ---------------------------------------------------------------- public API

def load_runs(job: Optional[str] = None, only_usable: bool = False) -> list[Run]:
    """All recorded cron runs, oldest first. ``job`` matches a job id or name."""
    rows = store.cron_trace_rows(job)
    runs: dict[str, Run] = {}
    for r in rows:
        run = runs.get(r["cron_execution_id"])
        if run is None:
            run = Run(
                execution_id=r["cron_execution_id"],
                job_id=r["cron_job_id"],
                job_name=r["cron_job_name"],
                session_id=r["session_id"],
                scheduled=bool(r["cron_scheduled"]),
                started_ts=r["ts"],
            )
            runs[run.execution_id] = run
        if run.session_id is None and r["session_id"]:
            run.session_id = r["session_id"]
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
        info = costs.get(run.session_id or "")
        if info:
            run.cost_usd = info.get("actual_cost_usd") or info.get("estimated_cost_usd")
            run.input_tokens = info.get("input_tokens")
            run.output_tokens = info.get("output_tokens")
            run.api_calls = info.get("api_call_count")
            run.model = info.get("model")
            if info.get("started_at") and info.get("ended_at"):
                run.duration_s = float(info["ended_at"]) - float(info["started_at"])

    if only_usable:
        ordered = [r for r in ordered if r.usable]
    return ordered


def _avg(values: list[Optional[float]]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


def job_summaries() -> list[dict[str, Any]]:
    """One line per cron job: how many runs we can learn from and what they cost."""
    by_job: dict[str, list[Run]] = {}
    for run in load_runs():
        by_job.setdefault(run.job_id or "?", []).append(run)
    out = []
    for job_id, runs in by_job.items():
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
