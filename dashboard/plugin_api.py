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
        code = "action_blocked" if type(exc).__name__ in {"ExperimentError", "ComparisonError", "AuctionError", "BlindError"} else "invalid_request"
        return {"ok": False, "code": code, "error": str(exc), "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 — preserve friendly domain errors, hide internals
        if type(exc).__name__ in {"LifecycleError", "PublicToolsetRecovery", "ExperimentError", "ComparisonError", "RhythmError", "AuctionError", "BlindError", "NativeCronError"}:
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
        mode = body.get("mode", "tool")
        if mode not in ("prerun", "no_ai", "tool"):
            raise ValueError("Choose a supported execution mode.")
        r = _lifecycle().promote(_identifier(body.get("routine_id"), "routine"), mode=mode)
        return {"ok": True, "message": f"{r['name']} is ready for normal host-tool guidance." if mode == "tool" else f"{r['name']} is live."}
    return await _serve(apply, message="Couldn't promote the routine.")


@router.get("/native-cron")
async def native_cron_status(job_id: str) -> dict[str, Any]:
    return await _serve(lambda: _module("native_cron").status(_identifier(job_id, "job")),
                        message="Couldn't inspect the native cron source.")


@router.post("/native-cron-preview")
async def native_cron_preview(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("native_cron").preview(_identifier(body.get("job_id"), "job"),
        body.get("source_script"), mode=_choice(body, "mode", ("monitor", "verbatim")),
        snapshot_only=_boolean(body, "snapshot_only"), verbatim_output=_boolean(body, "verbatim_output")),
        message="Couldn't preview the native cron source.")


@router.post("/native-cron")
async def native_cron_apply(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("native_cron").apply(_identifier(body.get("job_id"), "job"),
        body.get("source_script"), mode=_choice(body, "mode", ("monitor", "verbatim")),
        snapshot_only=_boolean(body, "snapshot_only"), verbatim_output=_boolean(body, "verbatim_output"),
        expected_preview_sha256=body.get("expected_preview_sha256"), confirmed=_boolean(body, "confirmed")),
        message="Couldn't attach the native cron source.")


@router.post("/native-cron-undo")
async def native_cron_undo(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("native_cron").undo(_identifier(body.get("job_id"), "job"),
        expected_config_fingerprint=body.get("expected_config_fingerprint"), confirmed=_boolean(body, "confirmed")),
        message="Couldn't restore the native cron source fields.")


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
    return await _serve(lambda: _module("configured_models").inventory(),
                        message="Couldn't read configured model routes.")


@router.post("/chat-model")
async def chat_model(body: dict) -> dict[str, Any]:
    def apply():
        provider = _reference(body.get("provider"), "provider")
        model = _reference(body.get("model"), "model")
        ch = _chat()
        listed = ch.connected_models()
        if not any(p["slug"] == provider and any(m["id"] == model for m in p["models"]) for p in listed):
            return {"ok": False, "code": "invalid_request", "message": "Declare that provider/model route before selecting it. Hermes checks authentication when it is used."}
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
        if on:
            raise ValueError("Crystallize preserves reasoning settings. Lowering reasoning is not an efficiency saving.")
        _module("store").set_setting("lower_reasoning", False)
        return {"ok": True, "message": "Jobs keep your reasoning setting."}
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
    def read():
        experiments = _module("experiments")
        job = _identifier(job_id, "job")
        result = experiments.preview(job)
        latest = experiments.comparisons(job, limit=1)
        result["latest_blind"] = _module("blind").present(latest[0]["id"]) if latest else None
        return result
    return await _serve(read, message="Couldn't prepare the same-input comparison.")


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
                "comparison": _module("blind").present(result["id"])}
    return await _serve(apply, message="Couldn't complete the same-input comparison.")


@router.get("/blind")
async def blind_comparison(comparison_id: str) -> dict[str, Any]:
    return await _serve(lambda: _module("blind").present(_identifier(comparison_id, "comparison")),
                        message="Couldn't load the anonymous comparison.")


@router.post("/blind-choice")
async def blind_choice(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("blind").choose(_identifier(body.get("comparison_id"), "comparison"),
                        _choice(body, "choice", ("A", "B", "tie", "neither"))),
                        message="Couldn't save the answer preference.")


@router.post("/blind-reveal")
async def blind_reveal(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("blind").reveal(_identifier(body.get("comparison_id"), "comparison")),
                        message="Couldn't reveal the comparison.")


@router.get("/reread")
async def reread_policy(session_id: str) -> dict[str, Any]:
    return await _serve(lambda: {"ok": True, "policy": _module("reread").policy(_identifier(session_id, "chat")),
                                "summary": _module("reread").summary(session_id)},
                        message="Couldn't load re-read controls.")


@router.post("/reread")
async def set_reread_policy(body: dict) -> dict[str, Any]:
    def apply():
        sid = _identifier(body.get("session_id"), "chat")
        keys = ("enabled", "writes", "results", "batching")
        settings = {k: _boolean(body, k) for k in keys if k in body}
        return {"ok": True, "policy": _module("reread").configure(sid, **settings)}
    return await _serve(apply, message="Couldn't save re-read controls.")


@router.get("/rhythm")
async def rhythm_report(job_id: str, timezone: str = "UTC") -> dict[str, Any]:
    return await _serve(lambda: _module("rhythm").report(_job_id(job_id), timezone=timezone),
                        message="Couldn't load schedule evidence.")


def _rhythm_args(body: dict) -> dict:
    return {"noncritical": _boolean(body, "noncritical"),
            "allowed_staleness_minutes": body.get("allowed_staleness_minutes")}


@router.post("/rhythm-preview")
async def rhythm_preview(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("rhythm").preview(_job_id(body.get("job_id")),
        body.get("interval_minutes"), timezone=body.get("timezone", "UTC"), **_rhythm_args(body)),
        message="Couldn't preview the schedule recommendation.")


@router.post("/rhythm")
async def rhythm_apply(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("rhythm").apply(_job_id(body.get("job_id")),
        body.get("interval_minutes"), expected_config_fingerprint=body.get("expected_config_fingerprint"),
        confirmed=_boolean(body, "confirmed"), timezone=body.get("timezone", "UTC"), **_rhythm_args(body)),
        message="Couldn't change the schedule.")


@router.post("/rhythm-undo")
async def rhythm_undo(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("rhythm").undo(_job_id(body.get("job_id")),
        expected_config_fingerprint=body.get("expected_config_fingerprint"), confirmed=_boolean(body, "confirmed")),
        message="Couldn't restore the schedule.")


def _auction_args(body: dict) -> dict:
    return {"models": body.get("models"), "cost_cap_usd": body.get("cost_cap_usd"),
            "max_tokens": body.get("max_tokens", 1024),
            "execution_id": _identifier(body.get("execution_id"), "recorded run", optional=True),
            "accept_estimated_cap": body.get("accept_estimated_cap", False)}


@router.get("/auction")
async def auction_history(job_id: str) -> dict[str, Any]:
    return await _serve(lambda: {"ok": True, "auctions": _module("auction").history(_identifier(job_id, "job"))},
                        message="Couldn't load model comparisons.")


@router.post("/auction-reconcile")
async def auction_reconcile(body: dict) -> dict[str, Any]:
    return await _serve(lambda: _module("auction").reconcile(confirmed=_boolean(body.get("confirmed"), "confirmed")),
                        "Couldn't clear that auction")


@router.post("/auction-preview")
async def auction_preview(body: dict) -> dict[str, Any]:
    return await _serve(lambda: {"ok": True, **_module("auction").preview(_job_id(body.get("job_id")), **_auction_args(body))},
                        message="Couldn't prepare the model scoreboard.")


@router.post("/auction")
async def run_auction(body: dict) -> dict[str, Any]:
    def run():
        result = _module("auction").run(_job_id(body.get("job_id")), confirmed=_boolean(body, "confirmed"),
            expected_input_sha256=body.get("expected_input_sha256"), **_auction_args(body))
        return {"ok": result.get("status") == "completed", "auction": result,
                "message": "Review quality and cost evidence. Model comparisons do not establish efficiency savings."}
    return await _serve(run, message="Couldn't finish the model scoreboard.")


@router.get("/delegation")
async def delegation_summary() -> dict[str, Any]:
    return await _serve(lambda: {"ok": True, **_module("delegation").summary()},
                        message="Couldn't load delegation consent.")


@router.post("/delegation")
async def delegation_consent(body: dict) -> dict[str, Any]:
    return await _serve(lambda: {"ok": True, **_module("delegation").configure(
        _boolean(body, "enabled"), body.get("provider", ""), body.get("model", ""), confirmed=_boolean(body, "confirmed"))},
        message="Couldn't save profile-wide delegation consent.")


@router.get("/compatibility")
async def compatibility() -> dict[str, Any]:
    return await _serve(lambda: _module("host_api").report(), message="Couldn't inspect public SDK compatibility.")


@router.post("/model-route")
async def configure_model_route(body: dict) -> dict[str, Any]:
    def apply():
        result = _module("configured_models").configure_route(
            _reference(body.get("provider"), "provider"), _reference(body.get("model"), "model"))
        return {"ok": True, **result}
    return await _serve(apply, message="Couldn't save the configured model route.")


@router.get("/pricing-cards")
async def pricing_cards() -> dict[str, Any]:
    return await _serve(lambda: {"ok": True, **_module("public_pricing").rate_cards_report()},
                        message="Couldn't load explicit rate cards.")


@router.post("/pricing-card")
async def save_pricing_card(body: dict) -> dict[str, Any]:
    def apply():
        if not _boolean(body, "confirmed"):
            raise ValueError("Confirm the exact route, source and pricing assumptions before saving a rate card.")
        import json
        if len(json.dumps(body.get("card"), allow_nan=False).encode()) > 32_000:
            raise ValueError("A rate card must fit within 32,000 bytes.")
        result = _module("public_pricing").save_rate_card(body.get("card"))
        return {"ok": True, "card": result, "message": "Saved the user-supplied price estimate. It is not proof of billing."}
    return await _serve(apply, message="Couldn't save that rate card.")


@router.post("/pricing-card-delete")
async def delete_pricing_card(body: dict) -> dict[str, Any]:
    def apply():
        if not _boolean(body, "confirmed"):
            raise ValueError("Confirm removal of the explicit rate card.")
        result = _module("public_pricing").delete_rate_card(
            _reference(body.get("provider"), "provider"), _reference(body.get("model"), "model"), body.get("base_url"))
        return {"ok": True, **result,
                "message": "Removed the saved plugin-data card. Cards declared in Hermes configuration require a config edit."
                if result["removed"] else "No saved plugin-data card matched. Cards declared in Hermes configuration require a config edit."}
    return await _serve(apply, message="Couldn't remove that rate card.")
