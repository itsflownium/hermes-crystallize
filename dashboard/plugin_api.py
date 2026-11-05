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


@router.get("/summary")
async def summary(session_id: Optional[str] = None) -> dict[str, Any]:
    try:
        return _overview().build(session_id)
    except Exception as exc:  # noqa: BLE001 — the panel shows a friendly error instead
        logger.warning("crystallize: summary failed: %s", exc, exc_info=True)
        return {"error": "Couldn't load Crystallize data."}
