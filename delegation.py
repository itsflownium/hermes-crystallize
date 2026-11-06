"""Explicit per-task delegation routing via the public provider interface.

Registration only adds a choice. Selecting this backend and granting its helper
route are separate, deliberate profile-wide actions. Normal tasks inherit their
existing route and reasoning. Model-switch outcomes never count as efficiency
savings.
"""
from __future__ import annotations

from typing import Any

from . import store


def summary() -> dict:
    value = store.get_setting("delegation_consent") or {}
    return {"enabled": value.get("enabled") is True, "provider": value.get("provider"),
            "model": value.get("model"), "scope": "profile-wide backend; each task must request the helper",
            "reasoning_unchanged": True, "activation": "Select /delegation crystallize yourself; this control never selects it.",
            "savings_classification": "model_comparison_not_efficiency_proof"}


def configure(enabled: bool, provider: str = "", model: str = "", *, confirmed: bool = False) -> dict:
    if not isinstance(enabled, bool) or confirmed is not True:
        raise ValueError("Confirm the profile-wide delegation consent explicitly.")
    if enabled:
        from . import chat_savings
        available = chat_savings.connected_models()
        if not any(p["slug"] == provider and any(m["id"] == model for m in p["models"]) for p in available):
            raise ValueError("Choose a model from your connected providers.")
    store.set_setting("delegation_consent", {"enabled": enabled, "provider": provider, "model": model})
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
            route_keys = {"model", "provider", "base_url", "api_key", "api_mode", "request_overrides",
                          "reasoning_effort", "fallback_providers", "profile"}
            inherited = {k: v for k, v in config.items() if k in route_keys}
            choice = task.get("crystallize_route", "inherit")
            if choice == "inherit":
                return inherited
            if choice != "helper" or not own["enabled"]:
                raise DelegationRouteError("The user has not consented to this helper route. Use the inherited route.")
            if inherited.get("profile"):
                raise DelegationRouteError("A profile route cannot be replaced by a helper model. Use inherit.")
            if not own["provider"] or not own["model"]:
                raise DelegationRouteError("The consented helper is unavailable. Use inherit.")
            # Credentials and provider endpoints remain Hermes-owned. Never carry
            # another route's API key/base URL into the helper provider.
            return {**{k: v for k, v in inherited.items() if k == "reasoning_effort"},
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
