"""Opt-in read caching, isolated by profile and an explicit sharing namespace.

Only successful web observations are cached. No credentials, commands or local
file reads are admitted. Entries expire and never replace the host's tool policy.
"""
from __future__ import annotations

import hashlib
import contextvars
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlsplit

from . import history, store

TOOLS = frozenset({"web_extract", "web_search", "web_crawl"})
MAX_TTL = 3600
MAX_RESULT = 500_000
MAX_ENTRIES = 128
_SECRET = re.compile(r"auth|cookie|token|secret|password|api.?key|credential", re.I)


def validate(config: Any) -> dict[str, Any]:
    if config in (None, False, {}):
        return {"ttl_seconds": 0, "namespace": ""}
    if not isinstance(config, dict) or set(config) - {"ttl_seconds", "namespace"}:
        raise ValueError("Cache accepts ttl_seconds and namespace only.")
    ttl = config.get("ttl_seconds", 0)
    namespace = config.get("namespace", "")
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 0 <= ttl <= MAX_TTL:
        raise ValueError("Cache TTL must be an integer from 0 to 3600 seconds.")
    if not isinstance(namespace, str) or len(namespace) > 100 or any(ord(c) < 32 for c in namespace):
        raise ValueError("Cache namespace must be a short printable name.")
    return {"ttl_seconds": ttl, "namespace": namespace}


def _has_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_SECRET.search(str(k)) or _has_secret(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_secret(v) for v in value)
    if isinstance(value, str):
        parsed = urlsplit(value) if value.startswith(("https://", "http://")) else None
        return bool(parsed and (parsed.username or parsed.password or any(_SECRET.search(k) for k, _ in parse_qsl(parsed.query))))
    return False


def key(tool: str, args: dict, namespace: str, scope: Any = None) -> str | None:
    if tool not in TOOLS or _has_secret(args):
        return None
    encoded = json.dumps([tool, args, namespace, scope], sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    root = history._hermes_home() / "plugin-data" / "crystallize"
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "fetch-cache.db", timeout=2)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, result TEXT NOT NULL, "
                     "created REAL NOT NULL, expires REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0)")
        yield conn
        conn.commit()
    finally:
        conn.close()


CACHE_MARKER = "crystallize_cached_result"


def mark(result: str, age_seconds: float) -> str:
    """Tell the AI this came from the cache, keeping a JSON result valid JSON."""
    age = max(0, int(age_seconds))
    try:
        data = json.loads(result)
    except ValueError:
        data = None
    if isinstance(data, dict):
        return json.dumps({CACHE_MARKER: {"age_seconds": age}, **data}, ensure_ascii=False)
    return f"[{CACHE_MARKER} age_seconds={age}]\n{result}"


def is_marked(text: object) -> bool:
    """A cached copy says nothing about whether the source changed."""
    return isinstance(text, str) and CACHE_MARKER in text[:120]


def age_of(cache_key: str | None) -> float:
    if not cache_key:
        return 0.0
    with _db() as conn:
        row = conn.execute("SELECT created FROM entries WHERE key = ?", (cache_key,)).fetchone()
    return time.time() - row[0] if row else 0.0


def get(cache_key: str | None, ttl_seconds: int) -> str | None:
    if not cache_key or not ttl_seconds:
        return None
    now = time.time()
    with _db() as conn:
        conn.execute("DELETE FROM entries WHERE expires <= ?", (now,))
        row = conn.execute("SELECT result, created FROM entries WHERE key = ?", (cache_key,)).fetchone()
        if row is None or now - row[1] > ttl_seconds:
            return None
        conn.execute("UPDATE entries SET hits = hits + 1 WHERE key = ?", (cache_key,))
        return row[0]


def put(cache_key: str | None, result: str, ttl_seconds: int) -> None:
    if not cache_key or not ttl_seconds or len(result) > MAX_RESULT:
        return
    now = time.time()
    with _db() as conn:
        conn.execute("DELETE FROM entries WHERE expires <= ?", (now,))
        conn.execute("INSERT OR REPLACE INTO entries (key,result,created,expires) VALUES (?,?,?,?)",
                     (cache_key, result, now, now + ttl_seconds))
        conn.execute("DELETE FROM entries WHERE key NOT IN (SELECT key FROM entries ORDER BY created DESC LIMIT ?)", (MAX_ENTRIES,))


def clear() -> int:
    with _db() as conn:
        count = conn.execute("SELECT count(*) FROM entries").fetchone()[0]
        conn.execute("DELETE FROM entries")
    return count


def summary() -> dict[str, Any]:
    with _db() as conn:
        conn.execute("DELETE FROM entries WHERE expires <= ?", (time.time(),))
        row = conn.execute("SELECT count(*), COALESCE(sum(hits),0) FROM entries").fetchone()
    return {"entries": row[0], "hits": row[1], "max_ttl_seconds": MAX_TTL}


def configure(job_id: str, ttl_seconds: int, namespace: str = "") -> dict[str, Any]:
    if not job_id or not isinstance(job_id, str):
        raise ValueError("A cron job id is required.")
    config = validate({"ttl_seconds": ttl_seconds, "namespace": namespace})
    from . import lifecycle, rhythm
    job = lifecycle.cron_job(job_id)
    minutes = rhythm._interval(job) if job else None
    if minutes and config["ttl_seconds"] >= minutes * 60:
        raise ValueError(f"This job runs every {minutes} minutes, so a cache that long would give it old data as if it "
                         "were new. Use a shorter time.")
    store.set_setting("fetch_cache:" + job_id, config)
    return config


class CacheSession:
    def __init__(self, config: dict, job_id: str, scope: Any = None):
        self.config = config
        self.namespace = config["namespace"] or job_id
        self.scope = scope
        self.hits = 0


_active: contextvars.ContextVar[CacheSession | None] = contextvars.ContextVar("crystallize_fetch_cache", default=None)


@contextmanager
def activate(config: Any, job_id: str = "", scope: Any = None) -> Iterator[CacheSession]:
    session = CacheSession(validate(config), job_id, scope)
    token = _active.set(session)
    try:
        yield session
    finally:
        _active.reset(token)


def make_tool_execution(ctx: Any):
    """Cache inside Hermes execution middleware, AFTER its normal policy gates.

    A cache hit still traverses host availability/approval/pre-tool checks. A
    cache or storage failure never re-executes a handler that already ran.
    """
    def _execute(tool_name: str = "", args: Any = None, next_call=None, **_: Any) -> Any:
        if not callable(next_call):
            raise ValueError("Tool execution middleware needs the host's next_call.")
        active = _active.get()
        try:
            if active is None:
                run = ctx.current_cron_execution()
                job_id = str(getattr(run, "job_id", "") or "") if run else ""
                config = validate(store.get_setting("fetch_cache:" + job_id)) if job_id else validate(None)
                active = CacheSession(config, job_id)
            from . import analyze
            eligible = bool(active.config["ttl_seconds"] and active.namespace and isinstance(args, dict)
                            and analyze.observation_safety_error(tool_name, args) is None)
            cache_key = key(tool_name, args, active.namespace, active.scope) if eligible else None
            result = get(cache_key, active.config["ttl_seconds"])
        except (OSError, ValueError, sqlite3.Error):
            cache_key, result = None, None
        if result is not None:
            active.hits += 1
            try:
                return mark(result, age_of(cache_key))
            except (OSError, sqlite3.Error):
                return mark(result, 0)
        result = next_call(args)
        try:
            from . import runner
            if cache_key and isinstance(result, str) and runner.step_failure(result) is None:
                put(cache_key, result, active.config["ttl_seconds"])
        except (OSError, ValueError, sqlite3.Error):
            pass
        return result
    def execute(tool_name: str = "", args: Any = None, next_call=None, **identity: Any) -> Any:
        from . import tools
        with tools.caller_context(**identity):
            return _execute(tool_name, args, next_call)
    return execute
