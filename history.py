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
    "cost_status", "cache_write_tokens", "billing_provider",
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


def final_answers(session_ids: list[str]) -> dict[str, str]:
    """The last thing the AI wrote in each session (a cron run's result)."""
    conn = _open_readonly(_hermes_home() / "state.db")
    if conn is None or not session_ids:
        return {}
    try:
        if not {"session_id", "role", "content"} <= _columns(conn, "messages"):
            return {}
        out: dict[str, str] = {}
        for sid in session_ids:
            row = conn.execute(
                "SELECT content FROM messages WHERE session_id = ? AND role = 'assistant' "
                "AND COALESCE(content, '') != '' ORDER BY rowid DESC LIMIT 1", (sid,)).fetchone()
            if row and isinstance(row["content"], str):
                out[sid] = row["content"]
        return out
    except sqlite3.Error:
        logger.debug("crystallize: reading final answers failed", exc_info=True)
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


def _apply_session(run: Run, info: Optional[dict[str, Any]]) -> None:
    """Fill in a run's cost, tokens and timing from its Hermes session."""
    if not info:
        return
    run.cost_usd = info.get("actual_cost_usd") or info.get("estimated_cost_usd")
    if info.get("cost_status") == "unknown":
        run.cost_usd = None  # Hermes has no price for it. That isn't the same as free.
    run.input_tokens = info.get("input_tokens")
    run.output_tokens = info.get("output_tokens")
    run.reasoning_tokens = info.get("reasoning_tokens")
    run.cache_read_tokens = info.get("cache_read_tokens")
    run.cache_write_tokens = info.get("cache_write_tokens")
    run.session_started = info.get("started_at")
    run.finished = bool(info.get("ended_at"))
    run.included = info.get("cost_status") == "included"
    if run.included or run.cost_usd is None:
        run.api_cost_usd = modelinfo.api_cost(info.get("model"), info.get("billing_provider"), {
            "input": run.input_tokens, "output": run.output_tokens, "cache_read": run.cache_read_tokens,
            "cache_write": run.cache_write_tokens, "reasoning": run.reasoning_tokens})
    run.api_calls = info.get("api_call_count")
    run.model = info.get("model")
    if info.get("started_at") and info.get("ended_at"):
        run.duration_s = float(info["ended_at"]) - float(info["started_at"])


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

    if only_usable:
        ordered = [r for r in ordered if r.usable]
    return ordered


def ledger_runs(job_id: Optional[str], limit: int = 20) -> list[Run]:
    """Finished runs of a job from Hermes's own records, including ones from before
    Crystallize was recording. They have costs and answers but no step details, so a slow
    job doesn't have to wait weeks for a trial to have something to compare with."""
    conn = _open_readonly(_hermes_home() / "cron" / "executions.db")
    state = _open_readonly(_hermes_home() / "state.db")
    if conn is None or state is None or not job_id:
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
            match = state.execute("SELECT id FROM sessions WHERE source = 'cron' AND started_at BETWEEN ? AND ? "
                                  "AND api_call_count > 0 ORDER BY ABS(ended_at - ?) LIMIT 1",
                                  (start - 5, end + 5, end)).fetchone()
            if not match or match["id"] in used:
                continue
            used.add(match["id"])
            out.append(Run(execution_id=row["id"], job_id=job_id, job_name=None, session_id=match["id"],
                           scheduled=True, started_ts=start, outcome="completed"))
        costs = _session_costs([r.session_id for r in out])
        for run in out:
            _apply_session(run, costs.get(run.session_id or ""))
        return out
    except sqlite3.Error:
        logger.debug("crystallize: reading older runs failed", exc_info=True)
        return []
    finally:
        conn.close()
        state.close()


def _avg(values: list[Optional[float]]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


def job_summaries() -> list[dict[str, Any]]:
    """One line per cron job: how many runs we can learn from and what they cost."""
    # Grouped by job NAME: a cron job that was re-created (same name, new id)
    # keeps its earlier runs, so before-and-after numbers stay together.
    by_job: dict[str, list[Run]] = {}
    for run in load_runs():
        by_job.setdefault(run.job_name or run.job_id or "?", []).append(run)
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
