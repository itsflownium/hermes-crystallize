"""Find a route's list prices from public model catalogs, so savings work without a rate card.

Only exact provider and model matches count, never a similar name. It sends no credentials and no
chat data: just a plain GET for a public price list, cached for a day. Turn it off with the
`price_discovery` setting. A rate card you enter always wins. These are list prices, not your bill.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from . import history, public_pricing, store

logger = logging.getLogger(__name__)

SETTING = "price_discovery"
CATALOG_URL = "https://models.dev/api.json"
CACHE_SECONDS = 86400
RETRY_SECONDS = 900
TIMEOUT_SECONDS = 10
MAX_BYTES = 40_000_000
_PER_MILLION = 1_000_000

# Subscription routes bill by plan, not per token, so they publish no price. They serve the same
# models as the vendor's API, so that model's public API list price stands in: it lets Context Rent
# weigh a removal on a real price. Exact model names only; a rate card you enter still wins.
LIST_PRICE_SOURCE = {"openai-codex": "openai"}

_lock = threading.Lock()
_memory: dict[str, dict[str, Any]] = {}
_running: set[str] = set()


def enabled() -> bool:
    return bool(store.get_setting(SETTING, True))


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "crystallize-hermes-plugin"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 — https only, checked by callers
        return json.loads(response.read(MAX_BYTES))


def _number(value: Any) -> Optional[float]:
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    return public_pricing.number(value)


def _entry(input_rate: Any, output_rate: Any, cache_read: Any, cache_write: Any, request: Any = None) -> Optional[dict[str, Any]]:
    """A flat rate per token. A route with no cache price just charges its input rate for those tokens."""
    prompt, completion = _number(input_rate), _number(output_rate)
    if prompt is None or completion is None:
        return None  # includes negative "price varies" markers
    fee = _number(request)
    if request not in (None, "", "0") and fee is None:
        return None
    read, write = _number(cache_read), _number(cache_write)
    return {"input": prompt, "output": completion, "cache_read": prompt if read is None else read,
            "cache_write": prompt if write is None else write, "request_cost": fee or 0.0,
            "cache_rates_assumed": read is None or write is None}


def parse_endpoint(data: Any) -> dict[str, dict[str, Any]]:
    """An OpenAI-style /models list that includes per-token pricing."""
    rows = data.get("data") if isinstance(data, dict) else None
    models: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        price = row.get("pricing") if isinstance(row, dict) else None
        if isinstance(price, dict) and isinstance(row.get("id"), str):
            entry = _entry(price.get("prompt"), price.get("completion"),
                           price.get("input_cache_read", price.get("cache_read", price.get("cached_prompt"))),
                           price.get("input_cache_write", price.get("cache_write", price.get("cache_creation"))),
                           price.get("request"))
            if entry:
                windows = _windows(price)
                if windows:
                    entry["windows"] = windows
                models[row["id"]] = entry
    return models


def _windows(price: dict[str, Any]) -> list[dict[str, Any]]:
    """Time-of-day prices, such as a cheaper overnight rate. Each has a UTC start and end as HHMM;
    an end at or before the start wraps past midnight. Unreadable windows are left out."""
    found = []
    for row in price.get("overrides") or [] if isinstance(price.get("overrides"), list) else []:
        if not isinstance(row, dict):
            continue
        start, end = row.get("utc_start"), row.get("utc_end")
        if not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 2400 and v % 100 < 60 for v in (start, end)):
            continue
        entry = _entry(row.get("prompt", price.get("prompt")), row.get("completion", price.get("completion")),
                       row.get("input_cache_read", price.get("input_cache_read", price.get("cache_read"))),
                       row.get("input_cache_write", price.get("input_cache_write", price.get("cache_write"))),
                       row.get("request", price.get("request")))
        if entry:
            found.append({"utc_start": start, "utc_end": end, **entry})
    return found


def _at(entry: dict[str, Any], when: float) -> dict[str, Any]:
    """The price in force at this moment."""
    windows = entry.get("windows") or []
    stamp = time.gmtime(when)
    now = stamp.tm_hour * 100 + stamp.tm_min
    base = {key: value for key, value in entry.items() if key != "windows"}
    for window in windows:
        start, end = window["utc_start"] % 2400, window["utc_end"] % 2400
        inside = start <= now < end if start < end else (now >= start or now < end)
        if inside:
            return {**base, **{key: value for key, value in window.items() if key not in ("utc_start", "utc_end")},
                    "time_of_day_price": True}
    return base


def parse_catalog(data: Any, provider: str) -> dict[str, dict[str, Any]]:
    """One provider's models from a catalog keyed by provider id, with rates per million tokens."""
    block = data.get(provider) if isinstance(data, dict) else None
    rows = block.get("models") if isinstance(block, dict) else None
    models: dict[str, dict[str, Any]] = {}
    for name, row in rows.items() if isinstance(rows, dict) else ():
        cost = row.get("cost") if isinstance(row, dict) else None
        if not isinstance(cost, dict):
            continue
        scaled = [None if _number(cost.get(key)) is None else _number(cost[key]) / _PER_MILLION
                  for key in ("input", "output", "cache_read", "cache_write")]
        entry = _entry(*scaled)
        if entry:
            models[name] = entry
    return models


def _public_https(url: Any) -> bool:
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password and not parts.query


def _profile_urls(provider: str) -> tuple[Optional[str], Optional[str]]:
    try:
        from providers import get_provider_profile

        profile = get_provider_profile(provider)
    except Exception:  # noqa: BLE001 — an unknown provider has no profile to read
        return None, None
    base = getattr(profile, "base_url", None) or None
    listed = getattr(profile, "models_url", None) or (base.rstrip("/") + "/models" if base else None)
    return base, listed


def _load(provider: str, base_url: Optional[str]) -> dict[str, dict[str, Any]]:
    profile_base, listed = _profile_urls(provider)
    custom = public_pricing.endpoint(base_url) if base_url else None
    if custom and custom != public_pricing.endpoint(profile_base):
        listed = custom + "/models"  # a custom endpoint's own list is the only evidence for it
        use_catalog = False
    else:
        use_catalog = True
    models: dict[str, dict[str, Any]] = {}
    if _public_https(listed):
        try:
            models.update(parse_endpoint(_fetch_json(listed)))
        except Exception:  # noqa: BLE001 — many endpoints don't publish prices or need a key
            logger.debug("crystallize: no public price list at %s", listed, exc_info=True)
    if use_catalog:
        try:
            catalog = _fetch_json(CATALOG_URL)
            for name, entry in parse_catalog(catalog, provider).items():
                models.setdefault(name, entry)  # the route's own list wins
            source = LIST_PRICE_SOURCE.get(provider)
            if source:
                for name, entry in parse_catalog(catalog, source).items():
                    models.setdefault(name, {**entry, "list_price_of": source})
        except Exception:  # noqa: BLE001
            logger.debug("crystallize: the public price catalog is unavailable", exc_info=True)
    return models


def _path(key: str) -> Path:
    import hashlib

    return history._hermes_home() / "plugin-data" / store.PLUGIN_NAME / "prices" / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def _read_disk(key: str) -> Optional[dict[str, Any]]:
    try:
        saved = json.loads(_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and isinstance(saved.get("models"), dict) else None


def _write_disk(key: str, value: dict[str, Any]) -> None:
    path = _path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def refresh(provider: str, base_url: Optional[str] = None) -> dict[str, Any]:
    """Fetch now and remember the result, including finding nothing."""
    key = f"{provider}|{base_url or ''}"
    models = _load(provider, base_url)
    value = {"fetched_at": time.time(), "models": models}
    previous = _memory.get(key) or _read_disk(key)
    if not models and previous and previous.get("models"):
        value = {**previous, "retry_at": time.time() + RETRY_SECONDS}  # a failed fetch keeps old prices
    with _lock:
        _memory[key] = value
    try:
        _write_disk(key, value)
    except OSError:
        logger.debug("crystallize: couldn't save the price list", exc_info=True)
    return value


def _refresh_in_background(key: str, provider: str, base_url: Optional[str], home_scope: Callable[[], Any]) -> None:
    with _lock:
        if key in _running:
            return
        _running.add(key)

    def work() -> None:
        try:
            refresh(provider, base_url)
        except Exception:  # noqa: BLE001 — prices are optional
            logger.debug("crystallize: refreshing prices failed", exc_info=True)
        finally:
            with _lock:
                _running.discard(key)

    import contextvars

    threading.Thread(target=contextvars.copy_context().run, args=(work,), name="crystallize-prices", daemon=True).start()


def rates(model: Optional[str], provider: Optional[str], base_url: Optional[str] = None,
          when: Optional[float] = None, fetch: bool = True) -> Optional[dict[str, Any]]:
    """Per-token rates for this exact route, or None. Never waits on the network: a chat's hooks
    call this, so the first look starts a fetch and the prices apply as soon as it finishes. With
    fetch off it only reads prices already saved."""
    if not model or not provider or not enabled():
        return None
    key = f"{provider}|{base_url or ''}"
    with _lock:
        saved = _memory.get(key)
    if saved is None:
        saved = _read_disk(key)
        if saved:
            with _lock:
                _memory[key] = saved
    now = time.time()
    stale = saved is None or now - saved.get("fetched_at", 0) > CACHE_SECONDS
    if fetch and stale and now >= (saved or {}).get("retry_at", 0):
        _refresh_in_background(key, provider, base_url, history._hermes_home)
    entry = (saved or {}).get("models", {}).get(model)
    return _at(entry, now if when is None else when) if isinstance(entry, dict) else None
