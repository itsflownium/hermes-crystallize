"""Read-only SDK compatibility diagnostics, without probing credentials or providers.

This is an inventory of known integration gaps, not a replacement for
``hermes plugins validate``. No undocumented host API is imported to produce it.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

_ctx: Any = None

# These modules are still private according to the host's admission validator.
# Keep imports at their real call sites: aliases/dynamic imports do not make an
# undocumented dependency public, and must not be used to suppress warnings.
_PRIVATE_MODULES = frozenset({
    "model_tools", "cron.jobs", "agent.usage_pricing", "hermes_cli.models",
    "hermes_cli.inventory",
})


def set_context(ctx: Any) -> None:
    global _ctx
    _ctx = ctx


def _private_imports() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parent
    sites = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part in ("tests", "__pycache__", "node_modules") for part in relative.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and not node.level and node.module in _PRIVATE_MODULES:
                for name in node.names:
                    # CostResult alone is documented for provider profiles.
                    if node.module == "agent.usage_pricing" and name.name == "CostResult":
                        continue
                    sites.append({"file": relative.as_posix(), "line": node.lineno,
                                  "target": node.module + "." + name.name})
            elif isinstance(node, ast.Import):
                for name in node.names:
                    if name.name in _PRIVATE_MODULES:
                        sites.append({"file": relative.as_posix(), "line": node.lineno, "target": name.name})
    return sorted(sites, key=lambda site: (site["file"], site["line"], site["target"]))


def report(ctx: Any = None) -> dict[str, Any]:
    """Describe public support and unresolved gaps; never call a model/tool/CLI.

    The supported host's ``dispatch_tool`` is a raw registry dispatch. Presence
    of that method is not evidence of guarded execution. A future SDK seam must
    have a documented contract and explicit capability before we adopt it.
    """
    context = ctx if ctx is not None else _ctx
    has_dispatch = callable(getattr(context, "dispatch_tool", None)) if context is not None else None
    gaps = [
        {"id": "guarded_tool_dispatch", "public": False,
         "current": "model_tools.handle_function_call",
         "needed": "Public nested dispatch preserving availability, approval, hooks, middleware and caller identity."},
        {"id": "model_metadata_and_pricing", "public": False,
         "current": "Public provider declarations plus private model catalogs and usage_pricing rate cards.",
         "needed": "Public exact-route metadata/rate cards with tiers, fees, provenance and request-level observed costs."},
        {"id": "cron_toolsets_and_atomic_update", "public": False,
         "current": "Documented CLI for prompt/script/interpreter/mode; private cron.jobs.update_job for toolsets.",
         "needed": "Public revision-checked atomic field updates, including nullable enabled_toolsets."},
        {"id": "connected_model_inventory", "public": False,
         "current": "hermes_cli.inventory",
         "needed": "Public inventory of connected model routes without exposing credentials."},
    ]
    return {"schema_version": 1, "catalog_ready": False, "status": "public_sdk_gaps",
            "context_bound": context is not None,
            "public_dispatch_method_present": has_dispatch,
            "public_guarded_dispatch_available": False,
            "reasoning_reductions_enabled": False, "raw_registry_fallback_enabled": False,
            "private_core_imports": _private_imports(), "sdk_gaps": gaps,
            "warnings": ["Private host dependencies remain; catalog readiness is not established.",
                         "Run the host's plugin validator for the authoritative complete admission checks."],
            "proposal": "docs/sdk-gaps.md"}
