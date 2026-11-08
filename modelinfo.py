"""Configured routes, public declarations and exact-route public cost evidence.

There are no model tables or inferred API-vendor equivalences here. Provider
quotes are totals; separate token rates require an explicit user rate card.
Missing metadata/prices remain unknown. Reasoning reductions stay disabled.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from . import public_pricing

logger = logging.getLogger(__name__)

# This orders only levels a provider explicitly declares; it is not a fallback
# capability list and is never used to change the user's reasoning settings.
_EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def _config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        loaded = load_config() or {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: couldn't read Hermes config", exc_info=True)
        return {}


def route(job: dict[str, Any]) -> dict[str, Optional[str]]:
    """Configured cron route; the served route must come from runtime evidence.

    Per-job pins precede cron defaults, which precede the main configuration.
    Authentication fallback and environment-only routes cannot be resolved here.
    """
    full = _config()
    cfg = full.get("model") or {}
    cfg = cfg if isinstance(cfg, dict) else {"default": cfg}
    # An empty job is the established main-chat route query used by Chat savings.
    cron = (full.get("cron") or {}) if job else {}
    cron = cron if isinstance(cron, dict) else {}
    model = job.get("model") or cron.get("model") or cfg.get("default") or cfg.get("model") or cfg.get("name")
    provider = job.get("provider") or cron.get("model_provider") or cfg.get("provider")
    same = provider == cfg.get("provider")
    return {"model": model, "provider": provider,
            "base_url": job.get("base_url") or (cfg.get("base_url") if same else None),
            "api_mode": cfg.get("api_mode") if same else None}


def _profile(provider: Optional[str]) -> Any:
    try:
        from providers import get_provider_profile

        return get_provider_profile(provider or "")
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: provider profile lookup failed", exc_info=True)
        return None


def _profile_levels(r: dict[str, Optional[str]]) -> Optional[tuple[str, ...]]:
    """What the provider's own Hermes profile declares. None = it doesn't say."""
    try:
        profile = _profile(r.get("provider"))
        return profile.supported_reasoning_efforts(r.get("model")) if profile else None
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: provider profile lookup failed", exc_info=True)
        return None


def _declared_caps(r: dict[str, Optional[str]]) -> dict[str, Any]:
    profile = _profile(r.get("provider"))
    declarations = getattr(profile, "model_capabilities", {}) or {}
    caps = declarations.get(r.get("model"), {}) if isinstance(declarations, dict) else {}
    caps = dict(caps) if isinstance(caps, dict) else {}
    # User declarations have the same precedence as Hermes's metadata resolver.
    overrides = _config().get("model_overrides") or {}
    provider_caps = overrides.get(r.get("provider"), {}) if isinstance(overrides, dict) else {}
    declared = provider_caps.get(r.get("model"), {}) if isinstance(provider_caps, dict) else {}
    if isinstance(declared, dict):
        caps.update(declared)
    return caps


def reasoning_levels(r: dict[str, Optional[str]]) -> list[str]:
    """Explicitly declared effort vocabulary. Empty also means unknown, never a guess."""
    return _reasoning_levels(r)


def _reasoning_levels(r: dict[str, Optional[str]]) -> list[str]:
    if _declared_caps(r).get("supports_reasoning") is False:
        return []
    listed = _profile_levels(r)
    if not isinstance(listed, (tuple, list)):
        return []
    return sorted({lv for lv in listed if lv in _EFFORT_ORDER}, key=_EFFORT_ORDER.index)


def current_level(job: dict[str, Any]) -> Optional[str]:
    """Configured reasoning, preserving explicit off and per-model overrides."""
    cfg = _config()
    pin = job.get("reasoning_effort")
    try:
        from hermes_constants import parse_reasoning_effort, resolve_reasoning_config

        parsed = parse_reasoning_effort(pin) if pin is not None else None
        parsed = parsed if parsed is not None else resolve_reasoning_config(cfg, route(job).get("model") or "")
        if parsed is not None:
            return "none" if parsed.get("enabled") is False else parsed.get("effort")
        return None
    except ImportError:
        # Outside Hermes: preserve only explicit data; do not invent transport defaults.
        agent = cfg.get("agent") or {}
        agent = agent if isinstance(agent, dict) else {}
        overrides = agent.get("reasoning_overrides") or {}
        per_model = overrides.get(route(job).get("model")) if isinstance(overrides, dict) else None
        value = pin if pin is not None and pin != "" else per_model if per_model is not None else agent.get("reasoning_effort")
        if isinstance(value, dict):
            return "none" if value.get("enabled") is False else str(value.get("effort") or "").strip().lower() or None
        if value is False or str(value or "").strip().lower() in ("none", "false", "disabled"):
            return "none"
        return str(value).strip().lower() if value is not None and value != "" else None


def levels_to_try(job: dict[str, Any]) -> list[str]:
    """Reasoning reductions are disabled by policy, including explicitly requested levels."""
    return []


_lookups: dict[Any, Any] = {}
_LOOKUP_TTL_S = 300.0


def _cached(key: Any, fetch: Any) -> Any:
    """The panel refreshes often. Prices and levels don't change that fast."""
    now = time.monotonic()
    hit = _lookups.get(key)
    if hit and now - hit[0] < _LOOKUP_TTL_S:
        return hit[1]
    value = fetch()
    _lookups[key] = (now, value)
    return value


def output_price(r: dict[str, Optional[str]]) -> Optional[float]:
    """Dollars per output token, from Hermes. None when unknown; 0 for included or free."""
    prices = exact_token_prices(r.get("model"), r.get("provider"), r.get("base_url"))
    return prices["output"] if prices is not None else None


def exact_token_prices(model: Optional[str], provider: Optional[str],
                       base_url: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Explicit user card in USD/token, preserving zero and unknown buckets/fees.

    A public provider's total quote does not establish separate bucket prices.
    Cards are not cached, so saves/deletes/config changes take effect immediately.
    """
    return public_pricing.exact_rate_card(model, provider, base_url, config=_config())


# Tools for finding and describing other tools. They aren't work the job needs a toolset for.
_META_TOOLS = {"tool_describe", "tool_search", "tool_call"}


def toolsets_for(tools: set[str]) -> Optional[list[str]]:
    """The toolsets that hold these tools, or None if any of them can't be placed.
    Unknown means leave the job's tools alone: a missing tool would break it."""
    try:
        from tools.registry import discover_builtin_tools, registry

        discover_builtin_tools()
        names = registry.get_tool_to_toolset_map()
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: couldn't read the tool registry", exc_info=True)
        return None
    needed = {t for t in tools if t not in _META_TOOLS}
    if any(not names.get(t) for t in needed):
        return None
    return sorted({names[t] for t in needed})


def _token_usage(tokens: dict[str, Optional[int]]) -> Any:
    return public_pricing.usage_from_tokens(tokens)


def _listed_models() -> dict[str, tuple[str, str]]:
    """Unambiguous exact IDs from public provider declarations, without probing."""
    def fetch() -> dict[str, tuple[str, str]]:
        listed: dict[str, tuple[str, str]] = {}
        ambiguous: set[str] = set()

        def remember(name: Any, catalog: str) -> None:
            if not isinstance(name, str) or not name:
                return
            key = name
            if key in ambiguous:
                return
            if key in listed and listed[key][1] != catalog:
                listed.pop(key)
                ambiguous.add(key)
            else:
                listed.setdefault(key, (name, catalog))

        try:
            from providers import list_providers

            for profile in list_providers():
                declarations = getattr(profile, "model_capabilities", {}) or {}
                for name in (*getattr(profile, "static_models", ()),
                             *getattr(profile, "fallback_models", ()),
                             *(declarations if isinstance(declarations, dict) else ())):
                    remember(name, profile.name)
        except Exception:  # noqa: BLE001
            logger.debug("crystallize: reading provider-declared models failed", exc_info=True)
        return listed
    return _cached(("listed",), fetch)


def _estimate(name: str, vendor: str, usage: Any) -> Optional[float]:
    result = public_pricing.quote_cost(name, vendor, usage, config=_config(), profile=_profile(vendor))
    return result["amount_usd"] if result is not None else None


def _price_route(model: str, provider: Optional[str]) -> Optional[tuple[str, str]]:
    """Compatibility helper: a quoted exact route only, never another vendor."""
    if not provider:
        return None
    return (model, provider) if _estimate(model, provider, _token_usage({"input": 1000, "output": 1000})) is not None else None


def cost_quote(model: Optional[str], provider: Optional[str], tokens: dict[str, Any],
               base_url: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Public same-route quote including status/source; unknown usage stays unknown."""
    if not model or not provider:
        return None
    try:
        return public_pricing.quote_cost(model, provider, _token_usage(tokens), base_url,
                                         config=_config(), profile=_profile(provider))
    except Exception:  # noqa: BLE001 — optional price evidence cannot break a run
        logger.debug("crystallize: public cost quote unavailable", exc_info=True)
        return None


def api_cost(model: Optional[str], provider: Optional[str], tokens: dict[str, Optional[int]]) -> Optional[float]:
    """Public exact-route total; no subscription/vendor API-price substitutions."""
    result = cost_quote(model, provider, tokens)
    return result["amount_usd"] if result is not None else None


def token_prices(model: Optional[str], provider: Optional[str]) -> Optional[dict[str, float]]:
    """Rates for legacy flat arithmetic, only for a complete flat fee-free card.

    Fee/tier-aware callers use cost_quote or exact_token_prices. Missing cache
    rates do not inherit input prices, and provider totals are never split up.
    """
    card = exact_token_prices(model, provider)
    fields = ("input", "output", "cache_read", "cache_write")
    if (card is None or any(card.get(key) is None for key in fields)
            or card.get("request_cost") != 0 or card.get("tiers_known") is not True
            or card.get("tier_threshold_tokens") is not None):
        return None
    return {key: card[key] for key in fields}
