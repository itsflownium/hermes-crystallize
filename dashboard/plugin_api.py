"""Backend for the Crystallize Desktop panel. Mounted at /api/plugins/crystallize/."""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

_PKG = "crystallize_panel_pkg"


_loaded_at = 0.0
_load_lock = threading.RLock()


def _newest_source(root: Path) -> float:
    return max((f.stat().st_mtime for f in root.glob("*.py")), default=0.0)


def _overview():
    """Load this plugin's own modules (the backend file is imported on its own).

    Reloaded whenever a source file changes, so a plugin update reaches the panel without
    restarting Hermes, which would also interrupt any cron job in progress.
    """
    global _loaded_at
    root = Path(__file__).resolve().parent.parent
    with _load_lock:  # requests run on several threads, and a half-reloaded plugin fails them
        newest = _newest_source(root)
        if _PKG in sys.modules and newest > _loaded_at:
            for name in [m for m in sys.modules if m == _PKG or m.startswith(f"{_PKG}.")]:
                del sys.modules[name]
        if _PKG not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                _PKG, root / "__init__.py", submodule_search_locations=[str(root)])
            mod = importlib.util.module_from_spec(spec)
            sys.modules[_PKG] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:
                for name in [m for m in sys.modules if m == _PKG or m.startswith(f"{_PKG}.")]:
                    del sys.modules[name]
                raise
            _loaded_at = newest
        return importlib.import_module(f"{_PKG}.overview")


@router.post("/diag")
async def diag(body: dict) -> dict[str, Any]:
    """The Desktop half reports what it's doing here (loads, clicks, errors).

    Written to <hermes home>/plugin-data/crystallize/desktop-diag.log, because
    Desktop's own log doesn't reliably keep plugin console output.
    """
    try:
        import json
        import time

        from plugins.plugin_storage import plugin_data_dir

        line = json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **(body or {})}, default=str)[:2000]
        with open(plugin_data_dir("crystallize") / "desktop-diag.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: diag write failed", exc_info=True)
    return {"ok": True}


@router.get("/summary")
async def summary(session_id: Optional[str] = None) -> dict[str, Any]:
    import asyncio

    try:
        # Reads several databases and may look up prices: not on the server's event loop.
        return await asyncio.to_thread(lambda: _overview().build(session_id))
    except Exception as exc:  # noqa: BLE001 — the panel shows a friendly error instead
        logger.warning("crystallize: summary failed: %s", exc, exc_info=True)
        return {"error": "Couldn't load Crystallize data."}


def _lifecycle():
    _overview()
    return importlib.import_module(f"{_PKG}.lifecycle")


@router.post("/try")
async def try_routine(body: dict) -> dict[str, Any]:
    lc = _lifecycle()
    try:
        r = lc.try_job(str((body or {}).get("job_id") or ""))
        return {"ok": True, "message": f"{r['name']} is being tested. It needs {lc.MATCHES_NEEDED} matching runs.", "routine_id": r["id"]}
    except lc.LifecycleError as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/promote")
async def promote_routine(body: dict) -> dict[str, Any]:
    lc = _lifecycle()
    try:
        r = lc.promote(str((body or {}).get("routine_id") or ""))
        return {"ok": True, "message": f"{r['name']} is live."}
    except lc.LifecycleError as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/undo")
async def undo_routine(body: dict) -> dict[str, Any]:
    lc = _lifecycle()
    try:
        r = lc.undo(str((body or {}).get("routine_id") or ""))
        return {"ok": True, "message": f"{r['name']} is back to its original prompt."}
    except lc.LifecycleError as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/mode")
async def mode_routine(body: dict) -> dict[str, Any]:
    """Switch a live routine between AI-written answers ("prerun") and no AI ("no_ai")."""
    lc = _lifecycle()
    mode = str((body or {}).get("mode") or "")
    try:
        r = lc.set_mode(str((body or {}).get("routine_id") or ""), mode)
        return {"ok": True, "message": f"{r['name']} now runs " +
                ("with no AI." if mode == "no_ai" else "with the AI writing the answer.")}
    except lc.LifecycleError as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/skip")
async def when_unchanged(body: dict) -> dict[str, Any]:
    """Choose what a live routine does when its data hasn't changed."""
    lc = _lifecycle()
    when = str((body or {}).get("when") or "")
    try:
        lc.set_when_unchanged(str((body or {}).get("routine_id") or ""), when)
        return {"ok": True, "message": {"repeat": "Unchanged runs will repeat the last answer.",
                                        "quiet": "Unchanged runs will stay quiet and cost nothing.",
                                        "run": "The AI will run every time."}.get(when, "Saved.")}
    except lc.LifecycleError as exc:
        return {"ok": False, "message": str(exc)}


def _tuning():
    _overview()
    return importlib.import_module(f"{_PKG}.tuning")


@router.post("/tune")
async def tune_job(body: dict) -> dict[str, Any]:
    """Try a lower reasoning level on a job, or (undo: true) put its old setting back."""
    lc, tn = _lifecycle(), _tuning()
    job = str((body or {}).get("job") or "")
    try:
        if (body or {}).get("undo"):
            tn.undo(job)
            return {"ok": True, "message": f"{job} is back to its previous setting."}
        t = tn.start(job)
        return {"ok": True, "message": f"Trying lower reasoning on {t['job_name']} for {tn.TRIAL_RUNS} runs."}
    except lc.LifecycleError as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/tune-all")
async def tune_all() -> dict[str, Any]:
    """Start a trial on every job that can have one."""
    result = _tuning().start_all()
    count = len(result["started"])
    return {"ok": bool(count), "message": f"Making {count} job{'' if count == 1 else 's'} cheaper." if count
            else "No jobs are ready yet. Each needs 2 finished runs first."}


@router.get("/chat-state")
async def chat_state(session_id: Optional[str] = None) -> dict[str, Any]:
    """Whether Crystallize is on for a chat (drives the message-box toggle)."""
    try:
        _overview()
        store = importlib.import_module(f"{_PKG}.store")
        return {"session_id": session_id, "enabled": store.chat_enabled(session_id)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("crystallize: chat-state failed: %s", exc)
        return {"session_id": session_id, "enabled": False, "error": "Couldn't read Crystallize state."}
