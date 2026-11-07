"""Crystallize's own database: recorded tool calls and saved routines.

Lives in Hermes's per-plugin data folder (``<hermes home>/plugin-data/crystallize/``),
so it survives plugin updates and follows the active profile.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any, Callable, Optional

PLUGIN_NAME = "crystallize"
logger = logging.getLogger(__name__)
MAX_COMPLETE_TRACE_BYTES = 500_000
MAX_COMPLETE_TRACE_ROWS = 256

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

CREATE TABLE IF NOT EXISTS complete_trace_results (
    trace_id INTEGER PRIMARY KEY,
    result_text TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS chat_savings (
    session_id  TEXT PRIMARY KEY,
    chars       INTEGER NOT NULL,
    results     INTEGER NOT NULL,
    cheap_tokens INTEGER NOT NULL,
    cheap_cost  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_saving_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    turn_id         TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL,
    status          TEXT NOT NULL,
    chars           INTEGER NOT NULL DEFAULT 0,
    output_chars    INTEGER NOT NULL DEFAULT 0,
    added_input_chars INTEGER NOT NULL DEFAULT 0,
    results         INTEGER NOT NULL DEFAULT 0,
    helper_call     INTEGER NOT NULL DEFAULT 0,
    cheap_tokens    INTEGER NOT NULL DEFAULT 0,
    cheap_cost      REAL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    duration_ms     REAL,
    model           TEXT,
    provider        TEXT,
    cost_source     TEXT,
    ts              REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_events_session ON chat_saving_events(session_id, ts);

CREATE TABLE IF NOT EXISTS brake_stops (
    session_id  TEXT PRIMARY KEY,
    job_name    TEXT NOT NULL,
    used        INTEGER NOT NULL,
    limit_tokens INTEGER NOT NULL,
    normal      INTEGER NOT NULL,
    at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS evictions (
    session_id  TEXT NOT NULL,
    call_id     TEXT NOT NULL,
    full_id     TEXT NOT NULL,
    at          REAL NOT NULL,
    PRIMARY KEY (session_id, call_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
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
_connections: dict[str, sqlite3.Connection] = {}
_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    """Open one database per active profile, without leaking another profile's state.

    An explicitly injected connection remains supported for offline integrations/tests.
    Normal connections are keyed by the ContextVar-aware Hermes home, not process env.
    """
    global _conn
    if _conn is not None and not any(_conn is c for c in _connections.values()):
        return _conn
    from . import history

    home = str(history._hermes_home().resolve())
    if home not in _connections:
        from plugins.plugin_storage import plugin_db

        conn = plugin_db(PLUGIN_NAME)
        conn.executescript(_SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(routines)")}
        if "meta_json" not in cols:
            conn.execute("ALTER TABLE routines ADD COLUMN meta_json TEXT")
        _migrate_chat_savings(conn)
        conn.commit()
        _connections[home] = conn
    _conn = _connections[home]
    return _conn


def _migrate_chat_savings(conn: sqlite3.Connection) -> None:
    """Keep historical totals; a historical zero with helper tokens is unpriced.

    The old writer substituted zero for unknown prices. It is impossible to recover
    that distinction, so migration must not present those calls as known free calls.
    """
    columns = {r[1] for r in conn.execute("PRAGMA table_info(chat_saving_events)")}
    for column in ("output_chars", "added_input_chars"):
        if column not in columns:
            conn.execute(f"ALTER TABLE chat_saving_events ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
    marker = "chat_saving_events_v1"
    if conn.execute("SELECT 1 FROM settings WHERE key = ?", (marker,)).fetchone():
        return
    conn.execute(
        """INSERT INTO chat_saving_events
           (session_id, turn_id, kind, status, chars, results, helper_call, cheap_tokens,
            cheap_cost, cost_source, ts, updated_at)
           SELECT session_id, '', 'legacy', 'legacy', chars, results,
                  CASE WHEN cheap_tokens > 0 THEN 1 ELSE 0 END, cheap_tokens,
                  CASE WHEN cheap_tokens > 0 AND cheap_cost = 0 THEN NULL ELSE cheap_cost END,
                  'legacy_estimate', updated_at, updated_at FROM chat_savings""")
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (marker, "true"))


def add_trace(row: dict[str, Any]) -> None:
    cols = (
        "ts", "session_id", "task_id", "tool_call_id", "tool_name", "args_json",
        "result_sha256", "result_len", "result_preview", "status", "duration_ms",
        "cron_job_id", "cron_job_name", "cron_execution_id", "cron_scheduled",
    )
    values = [row.get(c) for c in cols]
    with _lock:
        conn = _db()
        try:
            cursor = conn.execute(
                f"INSERT INTO traces ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", values)
            full = row.get("result_full")
            if (isinstance(full, str) and row.get("cron_execution_id")
                    and len(full.encode("utf-8", "replace")) <= MAX_COMPLETE_TRACE_BYTES):
                conn.execute("INSERT INTO complete_trace_results (trace_id, result_text) VALUES (?, ?)",
                             (cursor.lastrowid, full))
                conn.execute("DELETE FROM complete_trace_results WHERE trace_id NOT IN "
                             "(SELECT trace_id FROM complete_trace_results ORDER BY trace_id DESC LIMIT ?)",
                             (MAX_COMPLETE_TRACE_ROWS,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


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
    """Chronological evidence, using a verified complete result when retained.

    display_preview stays bounded for presentation. Legacy, oversized or evicted
    results retain their preview and fail complete-evidence checks normally.
    """
    import hashlib

    cols = ("tool_name", "args_json", "result_preview", "result_len", "status", "result_sha256")
    with _lock:
        rows = _db().execute(
            f"SELECT {', '.join('t.' + key for key in cols)}, f.result_text FROM traces t "
            "LEFT JOIN complete_trace_results f ON f.trace_id = t.id "
            "WHERE t.cron_execution_id = ? AND t.tool_name != '__run__' ORDER BY t.ts, t.id",
            (execution_id,),
        ).fetchall()
    results = []
    for raw in rows:
        row = dict(zip(cols, raw))
        row["display_preview"] = row["result_preview"]
        full = raw[len(cols)]
        valid = (isinstance(full, str) and len(full) == row["result_len"]
                 and hashlib.sha256(full.encode("utf-8", "replace")).hexdigest() == row["result_sha256"])
        if valid:
            row["result_preview"] = full
        row["complete_result_retained"] = valid
        results.append(row)
    return results


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


def add_chat_event(session_id: str, *, turn_id: str = "", kind: str = "clean", status: str = "accepted",
                   chars: int = 0, output_chars: int = 0, added_input_chars: int = 0,
                   results: Optional[int] = None, helper_call: bool = False,
                   cheap_tokens: int = 0, cheap_cost: Optional[float] = 0.0,
                   input_tokens: Optional[int] = None, output_tokens: Optional[int] = None,
                   duration_ms: Optional[float] = None, model: Optional[str] = None,
                   provider: Optional[str] = None, cost_source: Optional[str] = None) -> int:
    """Record transformations and helper attempts separately, including unsuccessful calls.

    ``cheap_cost=None`` means unknown, never zero. Saving events use helper_call=False;
    a helper attempt has exactly one row that is updated as its outcome becomes known.
    """
    if not session_id:
        raise ValueError("Chat accounting requires a session.")
    ts = time.time()
    with _lock:
        conn = _db()
        cur = conn.execute(
            """INSERT INTO chat_saving_events
               (session_id, turn_id, kind, status, chars, output_chars, added_input_chars, results, helper_call, cheap_tokens,
                cheap_cost, input_tokens, output_tokens, duration_ms, model, provider, cost_source, ts, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, str(turn_id or ""), kind, status, max(0, chars), max(0, output_chars), max(0, added_input_chars),
             int(chars > 0) if results is None else max(0, results), int(helper_call),
             max(0, cheap_tokens), cheap_cost, input_tokens, output_tokens, duration_ms,
             model, provider, cost_source, ts, ts))
        conn.commit()
        return int(cur.lastrowid)


def update_chat_event(event_id: int, *, _connection: Optional[sqlite3.Connection] = None, **fields: Any) -> None:
    allowed = {"status", "cheap_tokens", "cheap_cost", "input_tokens", "output_tokens",
               "duration_ms", "model", "provider", "cost_source"}
    if not fields or not set(fields) <= allowed:
        raise ValueError("Invalid chat accounting fields.")
    with _lock:
        conn = _connection if _connection is not None else _db()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"UPDATE chat_saving_events SET {assignments}, updated_at = ? WHERE id = ?",
                     (*fields.values(), time.time(), event_id))
        conn.commit()


def chat_event_updater(event_id: int) -> Callable[..., None]:
    """Pin late helper accounting to the connection that owns the event.

    A helper may finish after its caller times out or a test/host shuts down. It
    must never resolve a newer profile's global connection and update a reused id.
    Connection closing, like writing, must be protected by ``_lock``.
    """
    with _lock:
        connection = _db()

    def update(**fields: Any) -> None:
        try:
            update_chat_event(event_id, _connection=connection, **fields)
        except sqlite3.ProgrammingError as exc:
            if "closed" not in str(exc).lower():
                raise
            # An orderly shutdown can close the original connection. Its pending
            # cost remains unknown, which is more honest than retargeting a write.
            logger.debug("crystallize: helper accounting completed after its store closed", exc_info=True)

    return update


def add_chat_saving(session_id: str, chars: int, cheap_tokens: int, cheap_cost: Optional[float]) -> None:
    """Compatibility writer for integrations predating the per-attempt ledger."""
    add_chat_event(session_id, kind="legacy", chars=chars, cheap_tokens=cheap_tokens,
                   cheap_cost=cheap_cost, helper_call=cheap_tokens > 0,
                   cost_source="legacy_estimate")


def chat_events(session_id: Optional[str] = None, limit: int = 500) -> list[dict[str, Any]]:
    query = "SELECT * FROM chat_saving_events"
    params: list[Any] = []
    if session_id is not None:
        query += " WHERE session_id = ?"
        params.append(session_id)
    query += " ORDER BY ts DESC, id DESC LIMIT ?"
    params.append(min(5000, max(1, int(limit))))
    with _lock:
        cur = _db().execute(query, params)
        keys = [c[0] for c in cur.description]
        rows = cur.fetchall()
    return [dict(zip(keys, row)) for row in rows]


_CHAT_TOTALS = """SUM(chars) AS chars, SUM(output_chars) AS output_chars, SUM(added_input_chars) AS added_input_chars,
    SUM(results) AS results,
    SUM(cheap_tokens) AS cheap_tokens, SUM(COALESCE(cheap_cost, 0)) AS known_cheap_cost,
    SUM(CASE WHEN helper_call = 1 AND cheap_cost IS NULL THEN 1 ELSE 0 END) AS unknown_cost_calls,
    SUM(CASE WHEN helper_call = 1 AND cost_source = 'list_price_upper_bound_unknown_cache'
        THEN 1 ELSE 0 END) AS upper_bound_cost_calls,
    SUM(helper_call) AS helper_calls,
    SUM(CASE WHEN helper_call = 1 AND status IN ('failed', 'timeout', 'timeout_finished') THEN 1 ELSE 0 END) AS failed_calls,
    SUM(CASE WHEN helper_call = 1 AND status = 'rejected' THEN 1 ELSE 0 END) AS rejected_calls,
    AVG(CASE WHEN helper_call = 1 THEN duration_ms END) AS helper_latency_ms,
    MAX(updated_at) AS updated_at"""


def _chat_totals(group: str, session_id: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    if group not in {"session_id", "turn_id", "kind"}:
        raise ValueError("Unknown accounting grouping.")
    query = f"SELECT {group}, {_CHAT_TOTALS} FROM chat_saving_events"
    params: list[Any] = []
    if session_id is not None:
        query += " WHERE session_id = ?"
        params.append(session_id)
    query += f" GROUP BY {group} ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with _lock:
        cur = _db().execute(query, params)
        keys = [c[0] for c in cur.description]
        rows = cur.fetchall()
    out = [dict(zip(keys, row)) for row in rows]
    for row in out:
        row["cheap_cost"] = None if row["unknown_cost_calls"] else row["known_cheap_cost"]
    return out


def list_chat_savings(session_id: Optional[str] = None) -> list[dict[str, Any]]:
    return _chat_totals("session_id", session_id)


def chat_turn_savings(session_id: str) -> list[dict[str, Any]]:
    return _chat_totals("turn_id", session_id, 100)


def chat_savings_breakdown(session_id: str) -> list[dict[str, Any]]:
    return _chat_totals("kind", session_id)


def add_brake_stop(session_id: str, job_name: str, used: int, limit_tokens: int, normal: int) -> None:
    """One row per stopped run, keeping its latest total."""
    with _lock:
        conn = _db()
        conn.execute("INSERT INTO brake_stops (session_id, job_name, used, limit_tokens, normal, at) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(session_id) DO UPDATE SET used = excluded.used",
                     (session_id, job_name, used, limit_tokens, normal, time.time()))
        conn.commit()


def brake_stopped_sessions(job_name: str) -> set[str]:
    with _lock:
        rows = _db().execute("SELECT session_id FROM brake_stops WHERE job_name = ?", (job_name,)).fetchall()
    return {r[0] for r in rows}


def list_brake_stops(limit: int = 10) -> list[dict[str, Any]]:
    with _lock:
        rows = _db().execute("SELECT session_id, job_name, used, limit_tokens, normal, at FROM brake_stops "
                             "ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
    keys = ("session_id", "job_name", "used", "limit_tokens", "normal", "at")
    return [dict(zip(keys, tuple(r))) for r in rows]


def add_eviction(session_id: str, call_id: str, full_id: str) -> None:
    with _lock:
        conn = _db()
        conn.execute("INSERT OR REPLACE INTO evictions (session_id, call_id, full_id, at) VALUES (?, ?, ?, ?)",
                     (session_id, call_id, full_id, time.time()))
        conn.commit()


def remove_eviction(session_id: str, call_id: str) -> None:
    with _lock:
        conn = _db()
        conn.execute("DELETE FROM evictions WHERE session_id = ? AND call_id = ?", (session_id, call_id))
        conn.commit()


def evictions(session_id: str) -> dict[str, str]:
    """Tool results removed from this chat: call id to where the original is kept."""
    with _lock:
        rows = _db().execute("SELECT call_id, full_id FROM evictions WHERE session_id = ?", (session_id,)).fetchall()
    return {r[0]: r[1] for r in rows}


def get_setting(key: str, default: Any = None) -> Any:
    with _lock:
        row = _db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(key: str, value: Any) -> None:
    with _lock:
        conn = _db()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        conn.commit()


def now() -> float:
    return time.time()
