"""Tool handlers. Handlers return JSON strings and never raise."""

from __future__ import annotations

import json
import logging
from typing import Any

from . import store

logger = logging.getLogger(__name__)


def crystal_list(args: dict[str, Any] | None = None, **_: Any) -> str:
    try:
        return json.dumps({"routines": store.list_routines()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("crystallize: crystal_list failed: %s", exc)
        return json.dumps({"error": "Couldn't read saved routines."})


def crystal_run(args: dict[str, Any] | None = None, **_: Any) -> str:
    routine_id = str((args or {}).get("routine_id") or "").strip()
    if not routine_id:
        return json.dumps({"error": "routine_id is required."})
    try:
        routine = store.get_routine(routine_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("crystallize: crystal_run lookup failed: %s", exc)
        return json.dumps({"error": "Couldn't read saved routines."})
    if routine is None:
        return json.dumps({"error": f"No routine with id {routine_id!r}."})
    if routine["status"] != "live":
        return json.dumps({
            "error": f"Routine {routine_id!r} is {routine['status']}, not live. "
                     "It has to pass its test runs and be promoted first."
        })
    # The step runner is the next piece to build.
    return json.dumps({"error": "The step runner isn't built yet."})
