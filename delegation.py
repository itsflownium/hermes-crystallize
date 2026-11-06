"""Explicit per-task delegation routing via the public provider interface.

Registration only adds a choice. Selecting this backend and granting its helper
route are separate, deliberate profile-wide actions. Normal tasks inherit their
existing route and reasoning. Model-switch outcomes never count as efficiency
savings.
"""
from __future__ import annotations

from typing import Any

from . import configured_models, store


def summary() -> dict:
    value = store.get_setting("delegation_consent") or {}
    value = value if isinstance(value, dict) else {}
    try:
        route = configured_models.validate_route(value.get("provider"), value.get("model"))
    except ValueError:
        route = {"provider": None, "model": None}
    return {"enabled": value.get("enabled") is True and bool(route["provider"] and route["model"]), **route,
            "scope": "profile-wide backend; each task must request the helper",
            "availability": "authentication_unverified", "authentication_verified": False,
            "provenance": ["explicit_profile_consent"] if route["model"] else [],
            "warnings": ["The configured helper route has not been authenticated or probed. Hermes owns authentication at task execution."],
            "reasoning_unchanged": True, "activation": "Select /delegation crystallize yourself; this control never selects it.",
            "savings_classification": "model_comparison_not_efficiency_proof"}


def configure(enabled: bool, provider: str = "", model: str = "", *, confirmed: bool = False) -> dict:
    if not isinstance(enabled, bool) or confirmed is not True:
        raise ValueError("Confirm the profile-wide delegation consent explicitly.")
    if enabled:
        route = configured_models.validate_route(provider, model)
        # A public declaration is not an exhaustive catalog or proof of auth.
        # Explicit identifiers remain selectable for any host-supported route.
        configured_models.configure_route(**route)
    else:
        route = {"provider": "", "model": ""}
    store.set_setting("delegation_consent", {"enabled": enabled, **route})
    return summary()


def provider_class():
    # Public documented provider base, imported only when hosted by Hermes.
    from agent.delegation_provider import DelegationProvider, DelegationRouteError

    class CrystallizeDelegation(DelegationProvider):
        @property
        def name(self):
            return "crystallize"

        @property
        def display_name(self):
            return "Crystallize · explicit task routes"

        @property
        def summary(self):
            return "Keep the configured route unless a task explicitly requests the consented helper. Reasoning stays unchanged."

        def task_parameters(self, config):
            return {"crystallize_route": {"type": "string", "enum": ["inherit", "helper"],
                    "description": "Use inherit unless the user has explicitly allowed the helper for this self-contained task."}}

        def description(self, config):
            return "Crystallize helper routing requires profile-wide user consent and crystallize_route=helper. Do not use it when quality is uncertain."

        def route_task(self, task, config):
            own = summary()
            choice = task.get("crystallize_route", "inherit")
            if choice == "inherit":
                # The public provider contract defines dict(config) as the
                # existing delegation route. Core retains its original auth;
                # this is pass-through input, never a credential-store read.
                route_keys = {"model", "provider", "base_url", "api_key", "api_mode", "request_overrides",
                              "reasoning_effort", "service_tier", "fallback_providers", "profile"}
                return {k: v for k, v in config.items() if k in route_keys}
            if choice != "helper" or not own["enabled"]:
                raise DelegationRouteError("The user has not consented to this helper route. Use the inherited route.")
            if config.get("profile"):
                raise DelegationRouteError("A profile route cannot be replaced by a helper model. Use inherit.")
            if not own["provider"] or not own["model"]:
                raise DelegationRouteError("The consented helper is unavailable. Use inherit.")
            # Credentials and provider endpoints remain Hermes-owned. Never carry
            # another route's API key/base URL into the helper provider.
            reasoning = {"reasoning_effort": config["reasoning_effort"]} if "reasoning_effort" in config else {}
            return {**reasoning,
                    "provider": own["provider"], "model": own["model"],
                    "fallback_providers": [],
                    "label": "Crystallize helper · explicit model switch"}

    return CrystallizeDelegation


def register(ctx: Any) -> None:
    method = getattr(ctx, "register_delegation_provider", None)
    if not callable(method):
        return
    try:
        method(provider_class()())
    except ImportError:
        return
