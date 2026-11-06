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

CREATE TABLE IF NOT EXISTS chats (
    session_id  TEXT PRIMARY KEY,
    enabled     INTEGER NOT NULL,
    updated_at  REAL NOT NULL
);

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

CREATE TABLE IF NOT EXISTS cleaned (
    job_name    TEXT PRIMARY KEY,
    chars       INTEGER NOT NULL,
    results     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tuning (
    job_name    TEXT PRIMARY KEY,
    data_json   TEXT NOT NULL,
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
        cols = {r[1] for r in conn.execute("PRAGMA table_info(routines)")}
        if "meta_json" not in cols:
            conn.execute("ALTER TABLE routines ADD COLUMN meta_json TEXT")
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
            "SELECT id, name, source, source_ref, status, steps_json, updated_at, meta_json "
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
            "meta": json.loads(r[7]) if r[7] else {},
        })
    return out


def get_routine(routine_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        r = _db().execute(
            "SELECT id, name, source, source_ref, status, steps_json, meta_json FROM routines WHERE id = ?",
            (routine_id,),
        ).fetchone()
    if r is None:
        return None
    return {
        "id": r[0], "name": r[1], "source": r[2], "source_ref": r[3],
        "status": r[4], "steps": json.loads(r[5]),
        "meta": json.loads(r[6]) if r[6] else {},
    }


def save_routine(routine_id: str, *, name: str, source: str, source_ref: str, status: str,
                 steps: dict[str, Any], meta: dict[str, Any]) -> None:
    """Create or replace a routine."""
    t = time.time()
    with _lock:
        conn = _db()
        conn.execute(
            """INSERT INTO routines (id, name, source, source_ref, status, steps_json, meta_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, source=excluded.source,
                 source_ref=excluded.source_ref, status=excluded.status, steps_json=excluded.steps_json,
                 meta_json=excluded.meta_json, updated_at=excluded.updated_at""",
            (routine_id, name, source, source_ref, status, json.dumps(steps), json.dumps(meta), t, t),
        )
        conn.commit()


def update_routine(routine_id: str, *, status: Optional[str] = None,
                   meta: Optional[dict[str, Any]] = None) -> None:
    sets, vals = ["updated_at = ?"], [time.time()]
    if status is not None:
        sets.append("status = ?")
        vals.append(status)
    if meta is not None:
        sets.append("meta_json = ?")
        vals.append(json.dumps(meta))
    with _lock:
        conn = _db()
        conn.execute(f"UPDATE routines SET {', '.join(sets)} WHERE id = ?", (*vals, routine_id))
        conn.commit()


def set_routine_job(routine_id: str, job_id: str) -> None:
    """Point a routine at a re-created cron job (same name, new id)."""
    with _lock:
        conn = _db()
        conn.execute("UPDATE routines SET source_ref = ?, updated_at = ? WHERE id = ?", (job_id, time.time(), routine_id))
        conn.commit()


def routine_for_job(job_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        r = _db().execute(
            "SELECT id FROM routines WHERE source = 'cron' AND source_ref = ? AND status != 'retired'",
            (job_id,),
        ).fetchone()
    return get_routine(r[0]) if r else None


def execution_traces(execution_id: str) -> list[dict[str, Any]]:
    """Tool calls recorded for one cron run, in order."""
    cols = ("tool_name", "args_json", "result_preview", "result_len", "status")
    with _lock:
        rows = _db().execute(
            f"SELECT {', '.join(cols)} FROM traces WHERE cron_execution_id = ? AND tool_name != '__run__' "
            "ORDER BY ts, id",
            (execution_id,),
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def chat_enabled(session_id: Optional[str]) -> bool:
    """Whether the user turned Crystallize on for this chat (off by default)."""
    if not session_id:
        return False
    with _lock:
        r = _db().execute("SELECT enabled FROM chats WHERE session_id = ?", (session_id,)).fetchone()
    return bool(r and r[0])


def set_chat_enabled(session_id: str, enabled: bool) -> None:
    with _lock:
        conn = _db()
        conn.execute(
            """INSERT INTO chats (session_id, enabled, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (session_id, 1 if enabled else 0, time.time()),
        )
        conn.commit()


def get_tuning(job_name: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _db().execute("SELECT data_json FROM tuning WHERE job_name = ?", (job_name,)).fetchone()
    return json.loads(row[0]) if row else None


def list_tuning() -> list[dict[str, Any]]:
    with _lock:
        rows = _db().execute("SELECT data_json FROM tuning ORDER BY updated_at DESC").fetchall()
    return [json.loads(r[0]) for r in rows]


def save_tuning(job_name: str, data: dict[str, Any]) -> None:
    with _lock:
        conn = _db()
        conn.execute("INSERT OR REPLACE INTO tuning (job_name, data_json, updated_at) VALUES (?, ?, ?)",
                     (job_name, json.dumps(data, default=str), time.time()))
        conn.commit()


def add_cleaned(job_name: str, chars: int) -> None:
    """Note characters removed from a job's tool results, kept apart from its trial state."""
    with _lock:
        conn = _db()
        conn.execute("INSERT INTO cleaned (job_name, chars, results) VALUES (?, ?, 1) ON CONFLICT(job_name) "
                     "DO UPDATE SET chars = chars + excluded.chars, results = results + 1", (job_name, chars))
        conn.commit()


def get_cleaned(job_name: str) -> int:
    with _lock:
        row = _db().execute("SELECT chars FROM cleaned WHERE job_name = ?", (job_name,)).fetchone()
    return int(row[0]) if row else 0


def now() -> float:
    return time.time()
