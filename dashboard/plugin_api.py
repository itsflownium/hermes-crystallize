"""Backend for the Crystallize Desktop panel. Mounted at /api/plugins/crystallize/."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

_PKG = "crystallize_panel_pkg"


def _overview():
    """Load this plugin's own modules (the backend file is imported on its own)."""
    if _PKG not in sys.modules:
        root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            _PKG, root / "__init__.py", submodule_search_locations=[str(root)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_PKG] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop(_PKG, None)
            raise
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
    try:
        return _overview().build(session_id)
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
        return {"ok": True, "message": f"{r['name']} is being tested. It needs 3 matching runs.", "routine_id": r["id"]}
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
async def skip_unchanged(body: dict) -> dict[str, Any]:
    """Turn 'skip the AI when nothing changed' on or off for a live routine."""
    lc = _lifecycle()
    on = bool((body or {}).get("on"))
    try:
        lc.set_skip_unchanged(str((body or {}).get("routine_id") or ""), on)
        return {"ok": True, "message": "Unchanged runs will skip the AI." if on else "The AI will run every time."}
    except lc.LifecycleError as exc:
        return {"ok": False, "message": str(exc)}


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
