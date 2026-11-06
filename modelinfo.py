"""What Hermes knows about a job's model: which reasoning levels it takes and its prices.

Nothing about any model is written down here. Levels and prices come from Hermes when
asked, so new models and providers work without changing Crystallize. Hermes internals
are imported inside functions: if one moves, the answer is "unknown", not a crash.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# For a model that doesn't list its levels. These are the levels most providers accept,
# and Hermes moves any it can't send to the nearest weaker one.
_WHEN_UNLISTED = ("low", "medium", "high")
_NOT_A_TARGET = ("none", "ultra")  # off changes what the job does; ultra is a harness mode


def _config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        return load_config() or {}
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: couldn't read Hermes config", exc_info=True)
        return {}


def route(job: dict[str, Any]) -> dict[str, Optional[str]]:
    """The model and provider a cron job actually runs on."""
    cfg = _config().get("model") or {}
    cfg = cfg if isinstance(cfg, dict) else {"default": cfg}
    model = job.get("model") or cfg.get("default") or cfg.get("model")
    provider = job.get("provider") or cfg.get("provider")
    same = not job.get("provider") or job.get("provider") == cfg.get("provider")
    return {"model": model, "provider": provider,
            "base_url": job.get("base_url") or (cfg.get("base_url") if same else None),
            "api_mode": cfg.get("api_mode") if same else None}


def _catalog_caps(r: dict[str, Optional[str]]) -> Optional[dict[str, Any]]:
    try:
        from hermes_cli import models_reasoning_caps as caps

        lookup = {"nous": caps.nous_model_reasoning_capabilities,
                  "openrouter": caps.openrouter_model_reasoning_capabilities}.get(r.get("provider") or "")
        return lookup(r.get("model"), timeout=4.0, allow_fetch=True) if lookup else None
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: reasoning catalog lookup failed", exc_info=True)
        return None


def _profile_levels(r: dict[str, Optional[str]]) -> Optional[tuple[str, ...]]:
    """What the provider's own Hermes profile declares. None = it doesn't say."""
    try:
        from providers import get_provider_profile

        profile = get_provider_profile(r.get("provider") or "")
        return profile.supported_reasoning_efforts(r.get("model")) if profile else None
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: provider profile lookup failed", exc_info=True)
        return None


def _models_dev_says_no_reasoning(r: dict[str, Optional[str]]) -> bool:
    try:
        from agent.models_dev import get_model_capabilities, models_dev_id_for

        caps = get_model_capabilities(models_dev_id_for(r.get("provider") or "") or r.get("provider"), r.get("model"))
        return bool(caps) and caps.supports_reasoning is False
    except Exception:  # noqa: BLE001
        return False


def reasoning_levels(r: dict[str, Optional[str]]) -> list[str]:
    """Levels this model can be set to, weakest first. Empty if it has no reasoning control."""
    return _cached(("levels", r.get("provider"), r.get("model")), lambda: _reasoning_levels(r))


def _reasoning_levels(r: dict[str, Optional[str]]) -> list[str]:
    try:
        from agent.reasoning_effort import EFFORT_LADDER, route_supported_efforts
    except Exception:  # noqa: BLE001
        return list(_WHEN_UNLISTED)
    caps = _catalog_caps(r)
    if caps and caps.get("supports_reasoning") is False:
        return []
    listed = (caps or {}).get("supported_efforts")
    if not listed:
        declared = _profile_levels(r)
        if declared is not None and not declared:
            return []  # the provider says this model takes no reasoning settings
        listed = declared
    if not listed and _models_dev_says_no_reasoning(r):
        return []
    if not listed:
        if (r.get("provider") or "").lower() == "openai-codex":
            listed = route_supported_efforts(r.get("provider"), r.get("model"), r.get("api_mode"))
        else:
            listed = _WHEN_UNLISTED
    return sorted({lv for lv in listed if lv in EFFORT_LADDER and lv not in _NOT_A_TARGET},
                  key=EFFORT_LADDER.index)


def current_level(job: dict[str, Any]) -> Optional[str]:
    """The reasoning level a job asks for: its own pin, else the global setting."""
    pin = job.get("reasoning_effort")
    if pin:
        return str(pin).strip().lower()
    agent = _config().get("agent") or {}
    value = agent.get("reasoning_effort") if isinstance(agent, dict) else None
    return str(value).strip().lower() if value else None


def levels_to_try(job: dict[str, Any]) -> list[str]:
    """Levels below what the job uses now, weakest (cheapest) first."""
    levels = reasoning_levels(route(job))
    current = current_level(job)
    try:
        from agent.reasoning_effort import EFFORT_LADDER, clamp_effort
    except Exception:  # noqa: BLE001
        return levels[:-1]
    if current in ("none", "false", "disabled"):
        return []
    if current not in EFFORT_LADDER:  # following the provider's default: its strength is unknown
        return levels[:-1] or levels
    now = clamp_effort(current, levels) or current  # what the model really runs at
    return [lv for lv in levels if EFFORT_LADDER.index(lv) < EFFORT_LADDER.index(now)]


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
    try:
        from agent.usage_pricing import get_pricing_entry

        entry = get_pricing_entry(r.get("model") or "", provider=r.get("provider"), base_url=r.get("base_url"))
        per_million = entry.output_cost_per_million if entry else None
        return float(per_million) / 1_000_000 if per_million is not None else None
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: price lookup failed", exc_info=True)
        return None


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


def api_cost(model: Optional[str], provider: Optional[str], tokens: dict[str, Optional[int]]) -> Optional[float]:
    """What a run included in a subscription would cost at the vendor's API prices, using
    Hermes's price list: "openai-codex" looks under "openai", "vendor/model" under "vendor".
    None if Hermes has no price for it, as with a local model."""
    if not model:
        return None
    try:
        from agent.usage_pricing import estimate_usage_cost

        usage = _token_usage(tokens)
        names = [(model, (provider or "").split("-")[0])]  # "openai-codex" is priced under "openai"
        if "/" in model:
            names.append((model.split("/", 1)[1], model.split("/", 1)[0]))
        for name, vendor in names:
            if vendor:
                result = estimate_usage_cost(name, usage, provider=vendor)
                if result.status == "estimated" and result.amount_usd is not None:
                    return float(result.amount_usd)
    except Exception:  # noqa: BLE001
        logger.debug("crystallize: API cost lookup failed", exc_info=True)
    return None


def token_prices(model: Optional[str], provider: Optional[str]) -> Optional[dict[str, float]]:
    """Dollars per token from Hermes's price list. A plan's model uses its API prices."""
    if not model:
        return None

    def fetch() -> Optional[dict[str, float]]:
        try:
            from agent.usage_pricing import get_pricing_entry

            for name, vendor in ((model, provider), (model, (provider or "").split("-")[0])):
                entry = get_pricing_entry(name, provider=vendor)
                if entry and entry.output_cost_per_million:
                    def per(value: Any, fallback: Any = None) -> float:
                        chosen = value if value is not None else fallback
                        return float(chosen or 0) / 1_000_000
                    return {"input": per(entry.input_cost_per_million),
                            "output": per(entry.output_cost_per_million),
                            "cache_read": per(entry.cache_read_cost_per_million, entry.input_cost_per_million),
                            "cache_write": per(entry.cache_write_cost_per_million, entry.input_cost_per_million)}
        except Exception:  # noqa: BLE001
            logger.debug("crystallize: token price lookup failed", exc_info=True)
        return None
    return _cached(("prices", provider, model), fetch)
