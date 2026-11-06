"""Public provider declarations and sanitized, explicitly configured model routes.

This is not an authenticated inventory. Reading it never checks credentials,
calls a catalog/probe/account callback, or sends a provider request. The host's
public LLM facade remains responsible for authentication when the user explicitly
requests a completion.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import modelinfo, store

_ADDITIONAL_KEY = "additional_model_routes"
MAX_ADDITIONAL_ROUTES = 128
MAX_PROVIDERS = 256
MAX_DECLARED_MODELS = 2048
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,511}\Z")
_NOTICE = (
    "These routes come from public provider declarations and selected configuration. "
    "Authentication, account access and model availability have not been checked. "
    "Hermes validates them only when you explicitly request a model call."
)


def _reference(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not _REFERENCE.fullmatch(value.strip())
            or "://" in value or value.strip().lower() == "auto"):
        raise ValueError(f"Enter an explicit bounded {label} identifier, without credentials, URL parameters or whitespace.")
    return value.strip()


def validate_route(provider: Any, model: Any) -> dict[str, str]:
    """Validate identifiers only; never interpret validation as authenticated access."""
    return {"provider": _reference(provider, "provider"), "model": _reference(model, "model")}


def _route(value: Any, provider: Any = None) -> dict[str, str] | None:
    if isinstance(value, dict):
        # Deliberately allowlist selected fields. No endpoint, API key, auth,
        # account metadata or arbitrary config content enters the inventory.
        provider = value.get("provider") or value.get("model_provider") or provider
        model = value.get("model") or value.get("default")
    else:
        model = value
    try:
        return validate_route(provider, model)
    except ValueError:
        return None


def _selected() -> tuple[list[tuple[dict[str, str], str]], list[str]]:
    selected: list[tuple[dict[str, str], str]] = []
    warnings: list[str] = []
    cfg = modelinfo._config()
    cfg = cfg if isinstance(cfg, dict) else {}
    main_value = cfg.get("model")
    if isinstance(main_value, dict) and not (main_value.get("model") or main_value.get("default")):
        main_value = {"provider": main_value.get("provider"), "model": main_value.get("name")}
    main = _route(main_value, cfg.get("provider"))
    if main:
        selected.append((main, "main_configuration"))
    elif cfg.get("model"):
        warnings.append("The main model has no explicit provider/model pair; no provider was guessed.")
    auxiliary = cfg.get("auxiliary")
    for value in auxiliary.values() if isinstance(auxiliary, dict) else ():
        route = _route(value)
        if route:
            selected.append((route, "auxiliary_configuration"))
        elif isinstance(value, dict) and value.get("model"):
            warnings.append("An auxiliary model has no explicit provider; no provider was guessed.")
    cron = cfg.get("cron")
    cron = cron if isinstance(cron, dict) else {}
    default_cron = _route(cron, main["provider"] if main else None)
    if default_cron:
        selected.append((default_cron, "cron_default_configuration"))
    try:
        from . import lifecycle
        jobs = lifecycle.all_cron_jobs()
    except Exception:
        jobs = []
        warnings.append("Configured cron pins could not be read; no routes were inferred from job names.")
    for job in jobs if isinstance(jobs, list) else ():
        if not isinstance(job, dict) or not (job.get("model") or job.get("provider") or job.get("model_provider")):
            continue
        default = default_cron or main or {}
        pin = {"provider": job.get("provider") or job.get("model_provider") or default.get("provider"),
               "model": job.get("model") or default.get("model")}
        route = _route(pin)
        if route:
            selected.append((route, "cron_pin_configuration"))
    additional = store.get_setting(_ADDITIONAL_KEY) or []
    for value in additional[:MAX_ADDITIONAL_ROUTES] if isinstance(additional, list) else ():
        route = _route(value)
        if route:
            selected.append((route, "explicit_plugin_route"))
    # A direct plugin config route is useful in installations managed as config
    # files. It has the same sanitized shape as routes entered through the UI.
    plugins = cfg.get("plugins")
    entries = plugins.get("entries") if isinstance(plugins, dict) else None
    own = entries.get(store.PLUGIN_NAME) if isinstance(entries, dict) else None
    settings = own.get("settings", own.get("config", {})) if isinstance(own, dict) else {}
    configured_lists = [own.get("additional_routes") if isinstance(own, dict) else None,
                        settings.get("additional_routes") if isinstance(settings, dict) else None]
    for configured in configured_lists:
        for value in configured[:MAX_ADDITIONAL_ROUTES] if isinstance(configured, list) else ():
            route = _route(value)
            if route:
                selected.append((route, "plugin_route_configuration"))
    return selected, warnings


def _profiles() -> tuple[list[Any], Any, list[str]]:
    try:
        from providers import get_provider_profile, list_providers
        profiles = list_providers()
        return list(profiles)[:MAX_PROVIDERS], get_provider_profile, []
    except Exception:
        return [], lambda provider: None, ["Public provider declarations are unavailable; explicit route entry still works."]


def _declared(profile: Any) -> list[tuple[str, str]]:
    values: list[tuple[Any, str]] = []
    # These are genuine public ProviderProfile data fields. Never invoke model
    # listing, account status, auth, probe, pricing, or setup callbacks here.
    for key in ("static_models", "fallback_models"):
        ids = getattr(profile, key, ())
        if isinstance(ids, (list, tuple)):
            values.extend((value, "provider_" + key) for value in ids[:MAX_DECLARED_MODELS])
    caps = getattr(profile, "model_capabilities", None)
    if isinstance(caps, dict):
        values.extend((value, "provider_model_capabilities") for value in list(caps)[:MAX_DECLARED_MODELS])
    default = getattr(profile, "default_aux_model", None)
    if default:
        values.append((default, "provider_default_aux_model"))
    result = []
    for value, source in values:
        try:
            result.append((_reference(value, "model"), source))
        except ValueError:
            continue
    return result[:MAX_DECLARED_MODELS]


def inventory() -> dict[str, Any]:
    """Return picker-compatible routes with explicit unverified availability."""
    selected, warnings = _selected()
    profiles, lookup, declaration_warnings = _profiles()
    warnings.extend(declaration_warnings)
    rows: dict[str, dict[str, Any]] = {}
    by_name = {}

    def provider_row(provider: str, profile: Any = None) -> dict[str, Any]:
        if provider not in rows:
            name = getattr(profile, "display_name", None)
            rows[provider] = {"slug": provider, "name": name if isinstance(name, str) and name.strip() else provider,
                              "models": [], "availability": "authentication_unverified",
                              "authentication_verified": False, "provenance": [], "warnings": [_NOTICE]}
        return rows[provider]

    def add(provider: str, model: str, source: str, profile: Any = None, configured: bool = False) -> None:
        row = provider_row(provider, profile)
        models = row["models"]
        found = next((entry for entry in models if entry["id"] == model), None)
        if found is None:
            reasoning = None
            caps = getattr(profile, "model_capabilities", None)
            caps = caps.get(model) if isinstance(caps, dict) else None
            if isinstance(caps, dict):
                declared = caps.get("supports_reasoning", caps.get("reasoning"))
                reasoning = declared if isinstance(declared, bool) else None
            # Inventory does not fetch prices. Public pricing has its own exact
            # card/quote surface; absent picker prices remain unknown.
            found = {"id": model, "featured": False, "configured": False,
                     "input_per_million": None, "output_per_million": None, "reasoning": reasoning,
                     "availability": "declared_unverified", "authentication_verified": False,
                     "provenance": [], "warnings": [_NOTICE]}
            models.append(found)
        found["configured"] = found["configured"] or configured
        found["featured"] = found["configured"]
        if configured:
            found["availability"] = "configured_unverified"
        if source not in found["provenance"]:
            found["provenance"].append(source)
        if source not in row["provenance"]:
            row["provenance"].append(source)

    for profile in profiles:
        try:
            name = _reference(getattr(profile, "name", None), "provider")
        except ValueError:
            continue
        by_name[name] = profile
        if getattr(profile, "hidden", False) is True:
            continue
        provider_row(name, profile)
        for model, source in _declared(profile):
            add(name, model, source, profile)
    for route, source in selected:
        provider, model = route["provider"], route["model"]
        profile = by_name.get(provider)
        if profile is None:
            try:
                profile = lookup(provider)
            except Exception:
                profile = None
        add(provider, model, source, profile, configured=True)
    for row in rows.values():
        row["models"].sort(key=lambda model: (not model["configured"], model["id"].lower()))
    return {"providers": sorted(rows.values(), key=lambda row: (not any(m["configured"] for m in row["models"]), row["slug"])),
            "availability": "configured_and_declared_only", "authentication_verified": False, "catalog_complete": False,
            "manual_route_entry": True, "provenance": ["public_provider_declarations", "sanitized_selected_configuration", "explicit_plugin_routes"],
            "warnings": list(dict.fromkeys([_NOTICE, *warnings]))}


def describe_routes(routes: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Annotate explicit selections; absence from declarations never rejects one."""
    available = inventory()
    lookup = {(p["slug"], m["id"]): m for p in available["providers"] for m in p["models"]}
    result = []
    for value in routes:
        route = validate_route(value.get("provider"), value.get("model"))
        known = lookup.get((route["provider"], route["model"]))
        result.append({**route, "availability": known["availability"] if known else "explicit_selection_unverified",
                       "authentication_verified": False, "provenance": list(known["provenance"]) if known else ["explicit_user_selection"],
                       "warnings": [_NOTICE]})
    return result


def configure_route(provider: Any, model: Any) -> dict[str, Any]:
    """Save identifiers in this profile's plugin data, without activating a route."""
    route = validate_route(provider, model)
    # A single settings mutation prevents concurrent manual entries losing one
    # another while retaining the store's current-profile isolation.
    def update(current: Any) -> list[dict[str, str]]:
        values = current if isinstance(current, list) else []
        clean = [value for value in (_route(value) for value in values) if value]
        if route not in clean:
            if len(clean) >= MAX_ADDITIONAL_ROUTES:
                raise ValueError(f"At most {MAX_ADDITIONAL_ROUTES} explicit additional routes can be saved.")
            clean.append(route)
        return clean
    with store._lock:
        conn = store._db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM settings WHERE key=?", (_ADDITIONAL_KEY,)).fetchone()
            values = update(json.loads(row[0]) if row else [])
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (_ADDITIONAL_KEY, json.dumps(values)))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {**inventory(), "ok": True, "configured_route": route,
            "activated": False, "message": "Saved this route for explicit selection. Authentication remains Hermes-owned; no model or delegation backend was activated."}
