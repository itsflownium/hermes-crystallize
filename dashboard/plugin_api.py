"""Backend for the Crystallize Desktop panel, at /api/plugins/crystallize/."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()
_PKG = "crystallize_panel_pkg"
_loaded_at = 0.0
_load_lock = threading.RLock()
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,255}\Z")


def _newest_source(root: Path) -> float:
    return max((f.stat().st_mtime for f in root.glob("*.py")), default=0.0)


def _overview():
    """Load this plugin's modules; plugin updates reach the panel without a restart."""
    global _loaded_at
    root = Path(__file__).resolve().parent.parent
    with _load_lock:
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


def _module(name: str):
    _overview()
    return importlib.import_module(f"{_PKG}.{name}")


def _lifecycle():
    return _module("lifecycle")


def _tuning():
    return _module("tuning")


def _chat():
    return _module("chat_savings")


def _identifier(value: Any, label: str, *, optional: bool = False) -> Optional[str]:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Choose a valid {label}.")
    return value


def _reference(value: Any, label: str) -> str:
    # Existing tuning routes also accept human job names. Stable job IDs are used
    # by new controls; neither form may contain newlines, control bytes or options.
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or value.startswith("-") or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f"Choose a valid {label}.")
    return value


def _boolean(body: dict, key: str) -> bool:
    value = body.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false.")
    return value


def _choice(body: dict, key: str, choices: tuple[str, ...]) -> str:
    value = body.get(key)
    if value not in choices:
        raise ValueError(f"{key} must be one of: {', '.join(choices)}.")
    return value


def _job_id(value: Any) -> str:
    job_id = _identifier(value, "job")
    job = _lifecycle().cron_job(job_id)
    if job is None or job.get("id") != job_id:
        raise LookupError("That scheduled job no longer exists. Refresh the panel.")
    return job_id


async def _serve(call: Callable[[], dict[str, Any]], *, message: str) -> dict[str, Any]:
    """Every storage, model discovery and mutation runs outside the event loop."""
    try:
        return await asyncio.to_thread(call)
    except LookupError as exc:
        return {"ok": False, "code": "not_found", "error": str(exc), "message": str(exc)}
    except ValueError as exc:
        code = "action_blocked" if type(exc).__name__ in {"ExperimentError", "ComparisonError"} else "invalid_request"
        return {"ok": False, "code": code, "error": str(exc), "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 — preserve friendly domain errors, hide internals
        if type(exc).__name__ in {"LifecycleError", "ExperimentError", "ComparisonError"}:
            return {"ok": False, "code": "action_blocked", "error": str(exc), "message": str(exc)}
        logger.warning("crystallize: %s: %s", message, exc, exc_info=True)
        return {"ok": False, "code": "internal_error", "error": message, "message": message}


@router.post("/diag")
async def diag(body: dict) -> dict[str, Any]:
    def write():
        import json
        import time
        try:
            from plugins.plugin_storage import plugin_data_dir
            line = json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **(body or {})}, default=str)[:2000]
            with open(plugin_data_dir("crystallize") / "desktop-diag.log", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001
            logger.debug("crystallize: diag write failed", exc_info=True)
        return {"ok": True}
    return await _serve(write, message="Couldn't write desktop diagnostics.")


@router.get("/summary")
async def summary(session_id: Optional[str] = None) -> dict[str, Any]:
    return await _serve(lambda: _overview().build(_identifier(session_id, "chat", optional=True)),
                        message="Couldn't load Crystallize data.")


@router.post("/try")
async def try_routine(body: dict) -> dict[str, Any]:
    def apply():
        lc = _lifecycle()
        r = lc.try_job(_identifier(body.get("job_id"), "job"))
        return {"ok": True, "message": f"{r['name']} is being tested. It needs {lc.MATCHES_NEEDED} matching runs.", "routine_id": r["id"]}
    return await _serve(apply, message="Couldn't start the routine trial.")


@router.post("/promote")
async def promote_routine(body: dict) -> dict[str, Any]:
    def apply():
        r = _lifecycle().promote(_identifier(body.get("routine_id"), "routine"))
        return {"ok": True, "message": f"{r['name']} is live."}
    return await _serve(apply, message="Couldn't promote the routine.")


@router.post("/undo")
async def undo_routine(body: dict) -> dict[str, Any]:
    def apply():
        r = _lifecycle().undo(_identifier(body.get("routine_id"), "routine"))
        return {"ok": True, "message": f"{r['name']} is back to its previous settings."}
    return await _serve(apply, message="Couldn't undo the routine.")


@router.post("/mode")
async def mode_routine(body: dict) -> dict[str, Any]:
    def apply():
        mode = _choice(body, "mode", ("prerun", "no_ai", "tool"))
        r = _lifecycle().set_mode(_identifier(body.get("routine_id"), "routine"), mode)
        return {"ok": True, "message": f"Saved the execution mode for {r['name']}."}
    return await _serve(apply, message="Couldn't change the routine mode.")


@router.post("/skip")
async def when_unchanged(body: dict) -> dict[str, Any]:
    def apply():
        when = _choice(body, "when", ("repeat", "quiet", "run"))
        _lifecycle().set_when_unchanged(_identifier(body.get("routine_id"), "routine"), when)
        return {"ok": True, "message": {"repeat": "Unchanged runs will repeat the last answer.",
                "quiet": "Unchanged runs will stay quiet and use no AI.", "run": "The AI will run every time."}[when]}
    return await _serve(apply, message="Couldn't change unchanged-run behavior.")


@router.post("/tune")
async def tune_job(body: dict) -> dict[str, Any]:
    def apply():
        tn = _tuning()
        job = _reference(body.get("job"), "job")
        if body.get("undo") is not None and not isinstance(body["undo"], bool):
            raise ValueError("undo must be true or false.")
        if body.get("undo"):
            tn.undo(job)
            return {"ok": True, "message": "Restored the job's previous settings."}
        t = tn.start(job)
        return {"ok": True, "message": f"Trying cheaper settings on {t['job_name']} for {tn.TRIAL_RUNS} runs."}
    return await _serve(apply, message="Couldn't change the settings trial.")


@router.post("/tune-all")
async def tune_all() -> dict[str, Any]:
    def apply():
        result = _tuning().start_all()
        count = len(result["started"])
        return {"ok": bool(count), "message": f"Started {count} settings trial{'' if count == 1 else 's'}." if count
                else "No jobs are ready for cheaper settings yet. Each needs 2 finished runs first."}
    return await _serve(apply, message="Couldn't start settings trials.")


@router.get("/chat-summary")
async def chat_summary(session_id: Optional[str] = None) -> dict[str, Any]:
    return await _serve(lambda: _chat().summary(_identifier(session_id, "chat", optional=True)),
                        message="Couldn't load Crystallize Chat data.")


@router.get("/chat-models")
async def chat_models() -> dict[str, Any]:
    return await _serve(lambda: {"providers": _chat().connected_models()},
                        message="Couldn't read your connected subscriptions.")


@router.post("/chat-model")
async def chat_model(body: dict) -> dict[str, Any]:
    def apply():
        provider = _reference(body.get("provider"), "provider")
        model = _reference(body.get("model"), "model")
        ch = _chat()
        listed = ch.connected_models()
        if not any(p["slug"] == provider and any(m["id"] == model for m in p["models"]) for p in listed):
            return {"ok": False, "code": "invalid_request", "message": "That model isn't on your connected subscriptions."}
        ch.set_model(provider, model)
        return {"ok": True, "message": f"Crystallize Chat will use {model.split('/')[-1]}."}
    return await _serve(apply, message="Couldn't save the helper model.")


@router.post("/chat-toggle")
async def chat_toggle(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _chat().set_enabled(_identifier(body.get("session_id"), "chat"), _boolean(body, "on")),
                        message="Couldn't change Crystallize Chat.")


@router.get("/chat-policy")
async def chat_policy(session_id: Optional[str] = None) -> dict[str, Any]:
    return await _serve(lambda: _module("chat_policy").summary(_identifier(session_id, "chat", optional=True)),
                        message="Couldn't load this chat's controls.")


@router.post("/chat-policy")
async def set_chat_policy(body: dict) -> dict[str, Any]:
    def apply():
        policy = _module("chat_policy").set_policy(
            _identifier(body.get("session_id"), "chat"), body.get("tool"), body.get("mode"), body.get("focus"))
        return {"ok": True, "message": "Saved this chat's controls.", "policy": policy}
    return await _serve(apply, message="Couldn't save this chat's controls.")


@router.get("/brake")
async def brake_summary() -> dict[str, Any]:
    return await _serve(lambda: _module("brake").summary(_tuning()._all_jobs()),
                        message="Couldn't load Runaway Brake data.")


@router.post("/brake")
async def brake_setting(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("brake").set_enabled(_boolean(body, "on")),
                        message="Couldn't change Runaway Brake.")


@router.get("/chat-rent")
async def chat_rent(session_id: Optional[str] = None) -> dict[str, Any]:
    return await _serve(lambda: _module("rent").report(_identifier(session_id, "chat", optional=True)),
                        message="Couldn't load Context Rent data.")


@router.post("/chat-evict")
async def chat_evict(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("rent").evict(_identifier(body.get("session_id"), "chat"), _identifier(body.get("id"), "tool result")),
                        message="Couldn't remove that tool result.")


@router.post("/chat-restore")
async def chat_restore(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("rent").restore(_identifier(body.get("session_id"), "chat"), _identifier(body.get("id"), "tool result")),
                        message="Couldn't restore that tool result.")


@router.post("/reasoning-setting")
async def reasoning_setting(body: dict) -> dict[str, Any]:
    def apply():
        on = _boolean(body, "on")
        _module("store").set_setting("lower_reasoning", on)
        return {"ok": True, "message": "Settings trials may also try lower reasoning." if on else "Jobs keep your reasoning setting."}
    return await _serve(apply, message="Couldn't change the reasoning preference.")


@router.get("/chat-state")
async def chat_state(session_id: Optional[str] = None) -> dict[str, Any]:
    return await _serve(lambda: _chat().status_for(_identifier(session_id, "chat", optional=True)),
                        message="Couldn't read Crystallize state.")


@router.get("/contract")
async def contract(job_id: str) -> dict[str, Any]:
    def read():
        job = _job_id(job_id)
        return {"ok": True, "job_id": job, "contract": _module("contracts").get(job)}
    return await _serve(read, message="Couldn't load the job's validation contract.")


@router.post("/contract")
async def set_contract(body: dict) -> dict[str, Any]:
    def apply():
        job = _job_id(body.get("job_id"))
        if "contract" not in body:
            raise ValueError("Provide a contract object, or null to remove it.")
        import json
        if len(json.dumps(body["contract"], allow_nan=False).encode("utf-8")) > 32_000:
            raise ValueError("Validation requirements must fit within 32,000 bytes.")
        spec = _module("contracts").set(job, body["contract"])
        return {"ok": True, "job_id": job, "contract": spec, "message": "Saved validation requirements. New evidence is needed after changes."}
    return await _serve(apply, message="Couldn't save the job's validation contract.")


@router.get("/receipt")
async def receipt(job_id: str) -> dict[str, Any]:
    return await _serve(lambda: _module("receipts").receipt(_identifier(job_id, "job")),
                        message="Couldn't load the comparison receipt.")


@router.get("/compare")
async def compare_preview(job_id: str) -> dict[str, Any]:
    """Read-only readiness and cost notice. This never starts model calls."""
    return await _serve(lambda: _module("experiments").preview(_identifier(job_id, "job")),
                        message="Couldn't prepare the same-input comparison.")


@router.post("/compare")
async def compare_job(body: dict) -> dict[str, Any]:
    """An explicit Compare click is the authorization for the two answer calls."""
    def apply():
        if not _boolean(body, "confirmed"):
            raise ValueError("Start the comparison using its explicit Compare action.")
        job = _identifier(body.get("job_id"), "job")
        execution = _identifier(body.get("execution_id"), "recorded run", optional=True)
        result = _module("experiments").compare(job, execution_id=execution, confirmed=True)
        ok = result.get("status") == "completed" if "status" in result else result.get("ok") is True
        return {"ok": ok, "code": "completed" if ok else "comparison_failed",
                "message": "Comparison finished. Review its checks and costs." if ok else "Comparison has failed calls. Review the receipt and costs before trying again.",
                "comparison": result}
    return await _serve(apply, message="Couldn't complete the same-input comparison.")
