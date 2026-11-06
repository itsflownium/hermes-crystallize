"""Configured routes, declared capabilities and explicitly sourced Hermes prices.

Provider declarations use the public SDK. Hermes has no public rate-card reader yet;
the explicit pricing/catalog imports below are compatibility dependencies, documented
in docs/sdk-gaps.md. Missing metadata is unknown. No function proposes less reasoning.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Optional

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


def _pricing_entry(model: str, provider: Optional[str], base_url: Optional[str] = None) -> Any:
    """Private host compatibility seam, intentionally visible to admission lint."""
    from agent.usage_pricing import get_pricing_entry

    return get_pricing_entry(model, provider=provider, base_url=base_url)


def _nonnegative(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def exact_token_prices(model: Optional[str], provider: Optional[str],
                       base_url: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Rate card for this exact billing route, in USD/token; never API equivalents.

    Missing cache/tier rates remain None. The host interprets an absent request
    fee as no fixed fee. Metadata lets callers disclose the source and version;
    a subscription's explicit zero rate remains zero. This does not establish a
    per-request invoice or permission to call a model.
    """
    if not isinstance(model, str) or not model.strip() or not isinstance(provider, str) or not provider.strip() or provider.strip().lower() == "auto":
        return None

    def fetch() -> Optional[dict[str, Any]]:
        try:
            entry = _pricing_entry(model, provider, base_url)
            if entry is None:
                return None
            prices: dict[str, Any] = {}
            for field in ("input", "output", "cache_read", "cache_write"):
                for suffix in ("", "_above"):
                    raw = getattr(entry, field + "_cost_per_million" + suffix, None)
                    value = _nonnegative(raw)
                    if raw is not None and value is None:
                        return None
                    prices[field + suffix] = value / 1_000_000 if value is not None else None
            if prices["input"] is None or prices["output"] is None:
                return None
            fee = getattr(entry, "request_cost", None)
            prices["request_cost"] = _nonnegative(fee) if fee is not None else 0.0
            if prices["request_cost"] is None:
                return None
            threshold = getattr(entry, "tier_threshold_tokens", None)
            if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0):
                return None
            prices.update(tier_threshold_tokens=threshold,
                          source=getattr(entry, "source", "unknown"),
                          pricing_version=getattr(entry, "pricing_version", None),
                          source_url=getattr(entry, "source_url", None))
            return prices
        except Exception:  # noqa: BLE001
            logger.debug("crystallize: exact-route price lookup failed", exc_info=True)
            return None
    prices = _cached(("exact prices", provider, model, base_url), fetch)
    return dict(prices) if prices is not None else None


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
    from agent.usage_pricing import CanonicalUsage

    return CanonicalUsage(input_tokens=tokens.get("input") or 0, output_tokens=tokens.get("output") or 0,
                          cache_read_tokens=tokens.get("cache_read") or 0,
                          cache_write_tokens=tokens.get("cache_write") or 0,
                          reasoning_tokens=tokens.get("reasoning") or 0)


def _listed_models() -> dict[str, tuple[str, str]]:
    """Unambiguous namespaced model IDs, provider declarations before legacy catalogs.

    The legacy catalogs remain a private host dependency until the SDK exposes
    cached model metadata. A matching leaf under two vendors is ambiguous.
    """
    def fetch() -> dict[str, tuple[str, str]]:
        listed: dict[str, tuple[str, str]] = {}
        ambiguous: set[str] = set()

        def remember(name: Any, catalog: str) -> None:
            if not isinstance(name, str) or "/" not in name or ":" in name or name.startswith("~"):
                return
            key = name.split("/", 1)[1].lower()
            if key in ambiguous:
                return
            if key in listed and listed[key][0].lower() != name.lower():
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
        try:
            from hermes_cli.models import provider_model_ids

            for catalog in ("nous", "openrouter"):
                for name in provider_model_ids(catalog) or []:
                    remember(name, catalog)
        except Exception:  # noqa: BLE001
            logger.debug("crystallize: reading model catalogs failed", exc_info=True)
        return listed
    return _cached(("listed",), fetch)


def _estimate(name: str, vendor: str, usage: Any) -> Optional[float]:
    from agent.usage_pricing import estimate_usage_cost

    result = estimate_usage_cost(name, usage, provider=vendor)
    return _nonnegative(result.amount_usd) if result.status == "estimated" else None


def _price_route(model: str, provider: Optional[str]) -> Optional[tuple[str, str]]:
    """Find an API-equivalent price, separately from the actual billing route.

    Prefer declared provider identity. The Codex subscription's API vendor is
    a narrow legacy compatibility mapping; hyphens in arbitrary provider names
    carry no vendor meaning. This function is never used for spend preflight.
    """
    probe = _token_usage({"input": 1000, "output": 1000})
    profile = _profile(provider)
    canonical = getattr(profile, "name", None) or provider or ""
    api_vendor = {"openai-codex": "openai"}.get(canonical, canonical)
    overlay = getattr(profile, "provider_overlay", {}) or {}
    declared = getattr(profile, "models_dev_id", None) or (overlay.get("id") if isinstance(overlay, dict) else None)
    tries = [(model, api_vendor)]
    if declared and declared != api_vendor:
        tries.append((model, declared))
    short = model
    if "/" in model:
        vendor, short = model.split("/", 1)
        tries.append((short, vendor))
    tries.append((model, canonical))
    for name, vendor in tries:
        if vendor and _estimate(name, vendor, probe) is not None:
            return name, vendor
    # Catalog lookup can fetch metadata in old Hermes; do not do it when the
    # exact route/vendor already has a price.
    listed = _listed_models().get(short.lower())
    if listed and _estimate(listed[0], listed[1], probe) is not None:
        return listed
    return None


def api_cost(model: Optional[str], provider: Optional[str], tokens: dict[str, Optional[int]]) -> Optional[float]:
    """What a run costs at listed API prices, for one Hermes has no cost for: a subscription,
    or a route with no price. None if no list has this model."""
    if not model:
        return None
    try:
        where = _cached(("price route", provider, model), lambda: _price_route(model, provider))
        if not where:
            return None
        exact = _estimate(where[0], where[1], _token_usage(tokens))
        if exact is not None:
            return exact
        # Coarse API-equivalent display only. Filling a missing cache rate with
        # plain input is an assumption, not an invoice or a spend preflight.
        # exact_token_prices preserves unknown buckets for budgeted callers.
        prices = token_prices(model, provider)
        if not prices:
            return None
        return ((tokens.get("input") or 0) * prices["input"] + (tokens.get("output") or 0) * prices["output"]
                + (tokens.get("cache_read") or 0) * prices["cache_read"]
                + (tokens.get("cache_write") or 0) * prices["cache_write"])
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: API cost lookup failed", exc_info=True)
        return None


def token_prices(model: Optional[str], provider: Optional[str]) -> Optional[dict[str, float]]:
    """Dollars per token from Hermes's price list. A plan's model uses its API prices."""
    if not model:
        return None

    def fetch() -> Optional[dict[str, float]]:
        try:
            where = _cached(("price route", provider, model), lambda: _price_route(model, provider))
            for name, vendor in ((model, provider), *([where] if where else [])):
                entry = _pricing_entry(name, vendor)
                # Included billing is separate from the model's API equivalent.
                # A genuinely free API rate card still has a meaningful zero.
                if entry and getattr(entry, "pricing_version", None) != "included-route" and _nonnegative(entry.output_cost_per_million) is not None:
                    def per(value: Any, fallback: Any = None) -> float:
                        chosen = value if value is not None else fallback
                        parsed = _nonnegative(chosen)
                        if parsed is None:
                            raise ValueError("Unknown or invalid token rate")
                        return parsed / 1_000_000
                    return {"input": per(entry.input_cost_per_million),
                            "output": per(entry.output_cost_per_million),
                            "cache_read": per(entry.cache_read_cost_per_million, entry.input_cost_per_million),
                            "cache_write": per(entry.cache_write_cost_per_million, entry.input_cost_per_million)}
        except Exception:  # noqa: BLE001
            logger.debug("crystallize: token price lookup failed", exc_info=True)
        return None
    return _cached(("prices", provider, model), fetch)
