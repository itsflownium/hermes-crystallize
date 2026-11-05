"""Crystallize's own database: recorded tool calls and saved routines.

Lives in Hermes's per-plugin data folder (``<hermes home>/plugin-data/crystallize/``),
so it survives plugin updates and follows the active profile.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Optional

PLUGIN_NAME = "crystallize"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL    NOT NULL,
    session_id      TEXT,
    task_id         TEXT,
    tool_call_id    TEXT,
    tool_name       TEXT    NOT NULL,
    args_json       TEXT,
    result_sha256   TEXT,
    result_len      INTEGER,
    result_preview  TEXT,
    status          TEXT,
    duration_ms     INTEGER,
    cron_job_id     TEXT,
    cron_job_name   TEXT,
    cron_execution_id TEXT,
    cron_scheduled  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_traces_cron ON traces(cron_job_id, cron_execution_id, ts);

CREATE TABLE IF NOT EXISTS routines (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,          -- 'cron' or 'chat'
    source_ref  TEXT,                   -- cron job id or session id
    status      TEXT NOT NULL,          -- spotted | testing | live | retired
    steps_json  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
"""

_conn: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    """Open the database once per process and make sure the tables exist."""
    global _conn
    if _conn is None:
        from plugins.plugin_storage import plugin_db

        conn = plugin_db(PLUGIN_NAME)
        conn.executescript(_SCHEMA)
        conn.commit()
        _conn = conn
    return _conn


def add_trace(row: dict[str, Any]) -> None:
    cols = (
        "ts", "session_id", "task_id", "tool_call_id", "tool_name", "args_json",
        "result_sha256", "result_len", "result_preview", "status", "duration_ms",
        "cron_job_id", "cron_job_name", "cron_execution_id", "cron_scheduled",
    )
    values = [row.get(c) for c in cols]
    with _lock:
        conn = _db()
        conn.execute(
            f"INSERT INTO traces ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            values,
        )
        conn.commit()


def trace_stats() -> dict[str, Any]:
    """Counts used by /crystallize status."""
    with _lock:
        conn = _db()
        total = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
        sessions = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM traces WHERE session_id IS NOT NULL"
        ).fetchone()[0]
        jobs = conn.execute(
            """SELECT cron_job_id, cron_job_name,
                      COUNT(DISTINCT cron_execution_id) AS runs,
                      COUNT(*) AS calls
               FROM traces
               WHERE cron_job_id IS NOT NULL AND cron_scheduled = 1
               GROUP BY cron_job_id, cron_job_name
               ORDER BY runs DESC"""
        ).fetchall()
    return {
        "tool_calls": total,
        "sessions": sessions,
        "cron_jobs": [
            {"job_id": j[0], "job_name": j[1], "runs": j[2], "tool_calls": j[3]} for j in jobs
        ],
    }


def cron_trace_rows(job: Optional[str] = None) -> list[dict[str, Any]]:
    """Recorded tool calls that belong to cron runs, oldest first.

    ``job`` filters by cron job id or job name.
    """
    cols = (
        "ts", "session_id", "tool_name", "args_json", "result_sha256", "result_len",
        "result_preview", "status", "duration_ms", "cron_job_id", "cron_job_name",
        "cron_execution_id", "cron_scheduled",
    )
    q = f"SELECT {', '.join(cols)} FROM traces WHERE cron_execution_id IS NOT NULL"
    params: list[Any] = []
    if job:
        q += " AND (cron_job_id = ? OR cron_job_name = ?)"
        params = [job, job]
    q += " ORDER BY ts, id"
    with _lock:
        rows = _db().execute(q, params).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def list_routines() -> list[dict[str, Any]]:
    with _lock:
        rows = _db().execute(
            "SELECT id, name, source, source_ref, status, steps_json, updated_at "
            "FROM routines WHERE status != 'retired' ORDER BY updated_at DESC"
        ).fetchall()
    out = []
    for r in rows:
        try:
            step_count = len(json.loads(r[5]).get("steps", []))
        except (ValueError, AttributeError):
            step_count = 0
        out.append({
            "id": r[0], "name": r[1], "source": r[2], "source_ref": r[3],
            "status": r[4], "steps": step_count, "updated_at": r[6],
        })
    return out


def get_routine(routine_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        r = _db().execute(
            "SELECT id, name, source, source_ref, status, steps_json FROM routines WHERE id = ?",
            (routine_id,),
        ).fetchone()
    if r is None:
        return None
    return {
        "id": r[0], "name": r[1], "source": r[2], "source_ref": r[3],
        "status": r[4], "steps": json.loads(r[5]),
    }


def now() -> float:
    return time.time()
