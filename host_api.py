"""Public-API compatibility and honest feature/admission readiness, without probes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import dispatch_support
from .catalog.public_api_audit import audit

_ctx: Any = None


def set_context(ctx: Any) -> None:
    global _ctx
    _ctx = ctx
    dispatch_support.set_context(ctx)


def report(ctx: Any = None) -> dict[str, Any]:
    """Inspect local facts without model, credential, tool, or CLI calls.

    Public-API compliance does not establish an upstream catalog listing.
    Missing optional SDK features are gated, never worked around.
    """
    context = ctx if ctx is not None else _ctx
    checks = audit(Path(__file__).resolve().parent)
    dispatch = dispatch_support.support(context)
    private = [f for f in checks["findings"] if f["kind"] in {
        "private_core_import", "private_core_attribute", "private_dynamic_import"}]
    clean = checks["ok"]
    return {
        "schema_version": 2,
        "status": "public_sdk_only" if clean else "source_review_required",
        "public_api_only": clean,
        "technical_public_api_ready": clean,
        "catalog_ready": False,
        "catalog_readiness_meaning": "Legacy field: local checks cannot establish upstream admission or listing.",
        "admission": {"listed": False, "maintainer_approval": "not_established",
                      "next_step": "Validate the exact published pin and submit the entry for upstream maintainer review."},
        "context_bound": context is not None,
        "public_dispatch_method_present": dispatch["public_dispatch_method_present"],
        "public_guarded_dispatch_available": dispatch["guarded_dispatch_available"],
        "raw_registry_fallback_enabled": False,
        "reasoning_reductions_enabled": False,
        "dispatch": dispatch,
        "private_core_imports": private,
        "public_api_audit": checks,
        "optional_sdk_limits": [
            {"id": "automatic_nested_tools", "available": dispatch["automatic_nested_tools"],
             "fallback": "The main agent executes returned plans through its ordinary host tools."},
            {"id": "model_inventory", "source": "public declarations and configured identifiers",
             "authentication_verified": False},
            {"id": "pricing", "source": "public provider quotes or explicit user-supplied exact-route cards",
             "unknown_rates": "No inferred or substituted cards; unknown stays unknown."},
            {"id": "legacy_cron_toolsets", "automatic_mutation": False,
             "fallback": "Retain recovery and use host job controls for a legacy toolset edit."},
        ],
        "warnings": (["Current SDK cannot safely automate nested tools; ordinary host-tool plans remain available."]
                     if clean else ["Unsupported dependencies or policy violations require source review."]),
        "proposal": "docs/sdk-gaps.md",
    }
