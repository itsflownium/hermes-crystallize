"""Public provider cost quotes and explicit profile-local user rate cards.

No rate is derived from a model name, another billing route or a blended cost.
Provider quotes are totals for the supplied usage; only a user-supplied card
exposes separate token rates. Cards are estimates, never provider invoices.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import math
import time
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from . import store

SETTING = "pricing_rate_cards"
_BUCKETS = ("input", "output", "cache_read", "cache_write")
_RATE_FIELDS = tuple(key + suffix for key in _BUCKETS for suffix in ("", "_above"))
_CARD_FIELDS = frozenset({"schema_version", "provider", "model", "base_url", "currency", "unit",
    *_RATE_FIELDS, "request_cost", "tier_threshold_tokens", "tiers_known", "pricing_version", "source_url",
    "source", "provenance", "origin", "user_authorized", "verified", "updated_at", "expires_at"})
_UNRESOLVED_PROFILE = object()


class PricingError(ValueError):
    """An explicit rate card or usage description is malformed."""


def number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise PricingError(f"{label} must be a nonempty exact identifier.")
    value = value.strip()
    if value in ("*", "auto"):
        raise PricingError(f"{label} must identify one exact route, not an automatic route or wildcard.")
    return value


def endpoint(value: Any) -> Optional[str]:
    """Normalize an explicit endpoint without inventing a default or hostname alias."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 4096:
        raise PricingError("base_url must be an exact HTTP(S) endpoint or null for the provider default.")
    try:
        url = urlsplit(value.strip())
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError
        _ = url.port
    except ValueError as exc:
        raise PricingError("base_url must be HTTP(S), without credentials, a query or fragment.") from exc
    return urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path.rstrip("/"), "", ""))


def _route(provider: Any, model: Any, base_url: Any = None) -> tuple[str, str, Optional[str]]:
    return _identity(provider, "provider"), _identity(model, "model"), endpoint(base_url)


def validate_rate_card(raw: Any, *, origin: str = "plugin_data") -> dict[str, Any]:
    """Normalize explicit numeric units; missing rates and fees remain unknown."""
    if not isinstance(raw, dict) or set(raw) - _CARD_FIELDS:
        raise PricingError("Supply a rate-card object using the documented pricing fields only.")
    if raw.get("schema_version", 1) != 1 or isinstance(raw.get("schema_version"), bool):
        raise PricingError("Unsupported rate-card schema_version.")
    provider, model, base_url = _route(raw.get("provider"), raw.get("model"), raw.get("base_url"))
    if raw.get("currency") != "USD" or raw.get("unit") not in ("per_token", "per_million_tokens"):
        raise PricingError("Declare currency USD and unit per_token or per_million_tokens explicitly.")
    scale = 1_000_000 if raw["unit"] == "per_million_tokens" else 1
    card: dict[str, Any] = {"schema_version": 1, "provider": provider, "model": model, "base_url": base_url,
                            "currency": "USD", "unit": "per_token"}
    for key in (*_RATE_FIELDS, "request_cost"):
        value = raw.get(key)
        parsed = number(value)
        if value is not None and parsed is None:
            raise PricingError(f"{key} must be a finite nonnegative number, or null when unknown.")
        card[key] = parsed / (scale if key in _RATE_FIELDS else 1) if parsed is not None else None
    threshold = raw.get("tier_threshold_tokens")
    if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, int) or threshold <= 0):
        raise PricingError("tier_threshold_tokens must be a positive integer or null.")
    known = raw.get("tiers_known", "tier_threshold_tokens" in raw)
    if not isinstance(known, bool) or (threshold is not None and not known):
        raise PricingError("tiers_known must be boolean and cannot contradict a declared threshold.")
    if threshold is None and any(card[key + "_above"] is not None for key in _BUCKETS):
        raise PricingError("Above-tier rates need an explicit tier_threshold_tokens.")
    card.update(tier_threshold_tokens=threshold, tiers_known=known)
    for key in ("pricing_version", "source_url"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 4096):
            raise PricingError(f"{key} must be text or null.")
        card[key] = value or None
    if card["source_url"]:
        try:
            url = urlsplit(card["source_url"])
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
                raise ValueError
        except ValueError as exc:
            raise PricingError("source_url must be an HTTP(S) reference without credentials.") from exc
    expires = raw.get("expires_at")
    if expires is not None and number(expires) is None:
        raise PricingError("expires_at must be a Unix timestamp or null.")
    card.update(source="user_rate_card", provenance="user_supplied_rate_card", origin=origin,
                user_authorized=True, verified=False, expires_at=number(expires),
                updated_at=number(raw.get("updated_at")))
    return card


def _config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly

        value = load_config_readonly()
        return value if isinstance(value, dict) else {}
    except (ImportError, OSError, ValueError):
        return {}


def _configured_cards(config: dict[str, Any]) -> Any:
    current: Any = config
    for key in ("plugins", "entries", store.PLUGIN_NAME):
        current = current.get(key) if isinstance(current, dict) else None
    if not isinstance(current, dict):
        return []
    settings = current.get("settings", current.get("config", {}))
    return settings.get(SETTING, []) if isinstance(settings, dict) else []


def rate_cards_report(config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """List cards and malformed entries; data overrides the same configured route."""
    config = _config() if config is None else config
    sources = (("config", _configured_cards(config)), ("plugin_data", store.get_setting(SETTING, [])))
    cards: dict[tuple[str, str, Optional[str]], dict[str, Any]] = {}
    errors = []
    for origin, rows in sources:
        if not isinstance(rows, list):
            errors.append({"origin": origin, "error": "Rate cards must be a list."})
            continue
        for index, raw in enumerate(rows):
            try:
                card = validate_rate_card(raw, origin=origin)
                cards[_route(card["provider"], card["model"], card["base_url"])] = card
            except PricingError as exc:
                errors.append({"origin": origin, "index": index, "error": str(exc)})
    return {"cards": sorted(cards.values(), key=lambda c: (c["provider"], c["model"], c["base_url"] or "")),
            "invalid_cards": errors, "currency": "USD", "source": "explicit_user_rate_cards",
            "prices_are_estimates": True}


def list_rate_cards(config: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    return rate_cards_report(config)["cards"]


def save_rate_card(raw: dict[str, Any]) -> dict[str, Any]:
    card = validate_rate_card(raw)
    card["updated_at"] = time.time()
    key = _route(card["provider"], card["model"], card["base_url"])
    # Own plugin storage, under the same lock as its other writers. The read and
    # replace must be atomic so concurrent saves cannot erase another route.
    with store._lock:
        conn = store._db()
        try:
            # The host and local CLI can be separate processes; the Python lock
            # alone cannot protect their shared read/modify/write transaction.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (SETTING,)).fetchone()
            rows = json.loads(row[0]) if row else []
            if not isinstance(rows, list):
                raise PricingError("Saved rate cards are malformed; repair them before saving another card.")
            kept = []
            for item in rows:
                existing = validate_rate_card(item)
                if _route(existing["provider"], existing["model"], existing["base_url"]) != key:
                    kept.append(existing)
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (SETTING, json.dumps([*kept, card])))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return dict(card)


def delete_rate_card(provider: str, model: str, base_url: Optional[str] = None) -> dict[str, Any]:
    key = _route(provider, model, base_url)
    with store._lock:
        conn = store._db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (SETTING,)).fetchone()
            rows = json.loads(row[0]) if row else []
            if not isinstance(rows, list):
                raise PricingError("Saved rate cards are malformed; repair them before deleting one.")
            kept = []
            for raw in rows:
                card = validate_rate_card(raw)
                if _route(card["provider"], card["model"], card["base_url"]) != key:
                    kept.append(card)
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (SETTING, json.dumps(kept)))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"removed": len(kept) != len(rows), "provider": key[0], "model": key[1], "base_url": key[2],
            "configured_cards_require_config_edit": True}


def exact_rate_card(model: str, provider: str, base_url: Optional[str] = None, *,
                    config: Optional[dict[str, Any]] = None, discovered: bool = True) -> Optional[dict[str, Any]]:
    """Your own card for this route, otherwise its public list price if one was found."""
    try:
        key = _route(provider, model, base_url)
    except PricingError:
        return None
    for card in list_rate_cards(config):
        if _route(card["provider"], card["model"], card["base_url"]) == key:
            if card["expires_at"] is not None and card["expires_at"] <= time.time():
                return None
            return dict(card)
    return _discovered_card(model, provider, base_url) if discovered else None


def _discovered_card(model: str, provider: str, base_url: Optional[str]) -> Optional[dict[str, Any]]:
    from . import price_discovery

    try:
        found = price_discovery.rates(model, provider, endpoint(base_url))
    except Exception:  # noqa: BLE001 — a price list is optional evidence
        return None
    if not found:
        return None
    card = {"schema_version": 1, "provider": provider, "model": model, "base_url": endpoint(base_url), "currency": "USD",
            "unit": "per_token", **{key + "_above": None for key in _BUCKETS}, "tier_threshold_tokens": None,
            "tiers_known": True, "pricing_version": None, "source_url": None, "source": "public_price_list",
            "provenance": "public_list_price", "origin": "discovered", "user_authorized": False, "verified": False,
            "expires_at": None, "updated_at": None}
    card.update({key: found[key] for key in _BUCKETS})
    card["request_cost"] = found.get("request_cost", 0.0)
    card["cache_rates_assumed"] = bool(found.get("cache_rates_assumed"))
    return card


@dataclass(frozen=True)
class Usage:
    """Local data object satisfying the documented public provider usage contract."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    request_count: int = 1
    raw_usage: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "request_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PricingError("Usage counters must be known nonnegative integers.")
        if self.raw_usage is not None and not isinstance(self.raw_usage, dict):
            raise PricingError("raw_usage must be an object or null.")
        if self.request_count == 0 and (self.total_tokens or self.reasoning_tokens):
            raise PricingError("Nonzero tokens cannot belong to zero requests.")

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


def usage_from_tokens(tokens: dict[str, Any]) -> Usage:
    if not isinstance(tokens, dict):
        raise PricingError("Usage must be an object of known token counts.")
    fields: dict[str, Any] = {}
    for key in (*_BUCKETS, "reasoning"):
        name = key + "_tokens"
        value = tokens.get(key, tokens.get(name, 0))
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PricingError(f"{name} is unknown or is not a nonnegative integer.")
        fields[name] = value
    count = tokens.get("request_count", 1)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise PricingError("request_count must be a known nonnegative integer.")
    fields["request_count"] = count
    raw = tokens.get("raw_usage")
    if raw is not None and not isinstance(raw, dict):
        raise PricingError("raw_usage must be an object or null.")
    return Usage(**fields, raw_usage=raw)


def _card_quote(card: dict[str, Any], usage: Usage) -> Optional[dict[str, Any]]:
    if card["request_cost"] is None or not card["tiers_known"]:
        return None
    threshold = card["tier_threshold_tokens"]
    if threshold is not None and usage.request_count != 1:
        return None  # Aggregate buckets cannot identify each request's context tier.
    above = threshold is not None and usage.prompt_tokens > threshold
    total = usage.request_count * card["request_cost"]
    for key in _BUCKETS:
        count = getattr(usage, key + "_tokens")
        if not count:
            continue
        price = card[key + ("_above" if above else "")]
        if price is None:
            return None
        total += count * price
    amount = number(total)
    if amount is None:
        return None
    return {"amount_usd": amount, "status": "estimated", "source": card["source"],
            "provenance": card["provenance"], "pricing_version": card["pricing_version"],
            "source_url": card["source_url"], "origin": card["origin"], "notes": []}


def quote_cost(model: str, provider: str, usage: Usage, base_url: Optional[str] = None, *,
               config: Optional[dict[str, Any]] = None, profile: Any = _UNRESOLVED_PROFILE) -> Optional[dict[str, Any]]:
    """Exact-route total estimate; never infer a rate card from a provider quote.

    Your own card comes first, then the provider's own quote, then a public list price."""
    quote = _card_or_provider_quote(model, provider, usage, base_url, config=config, profile=profile)
    if quote is not None:
        return quote
    try:
        _route(provider, model, base_url)
    except PricingError:
        return None
    card = _discovered_card(model, provider, base_url)
    return _card_quote(card, usage) if card else None


def _card_or_provider_quote(model: str, provider: str, usage: Usage, base_url: Optional[str] = None, *,
                            config: Optional[dict[str, Any]] = None, profile: Any = _UNRESOLVED_PROFILE) -> Optional[dict[str, Any]]:
    try:
        _route(provider, model, base_url)
    except PricingError:
        return None
    card = exact_rate_card(model, provider, base_url, config=config, discovered=False)
    if card is not None:
        return _card_quote(card, usage)
    try:
        if profile is _UNRESOLVED_PROFILE:
            from providers import get_provider_profile

            profile = get_provider_profile(provider)
        quote = getattr(profile, "get_usage_cost", None)
        # The public hook has no endpoint argument; it cannot price an unrelated
        # custom endpoint just because its provider/model strings look familiar.
        if base_url is not None and endpoint(base_url) != endpoint(getattr(profile, "base_url", None)):
            return None
        result = quote(model, usage) if callable(quote) else None
        if isinstance(result, dict):
            data = result
        else:
            # Literal documented CostResult fields only; this is not an escape
            # hatch for arbitrary attributes of a host-owned object.
            data = {"status": getattr(result, "status", None), "amount_usd": getattr(result, "amount_usd", None),
                    "source": getattr(result, "source", "provider_profile"),
                    "pricing_version": getattr(result, "pricing_version", None),
                    "notes": getattr(result, "notes", ())}
        status = data.get("status")
        amount = number(data.get("amount_usd"))
        if status not in ("actual", "estimated", "included") or amount is None or (status == "included" and amount != 0):
            return None
        return {"amount_usd": amount, "status": status, "source": data.get("source", "provider_profile"),
                "provenance": "provider_profile_quote", "pricing_version": data.get("pricing_version"),
                "source_url": None, "notes": list(data.get("notes", ()) or ())}
    except Exception:  # A missing/broken optional provider quote is unknown pricing.
        return None
