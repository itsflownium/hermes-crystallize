"""Use the public output-buffering capability when a host explicitly exposes it."""
import inspect
from typing import Any


def supports_hold(ctx: Any) -> bool:
    try:
        parameter = inspect.signature(ctx.register_hook).parameters.get("hold_stream")
        return parameter is not None and parameter.kind in {
            inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    except (AttributeError, TypeError, ValueError):
        return False


def registration_options(ctx: Any) -> dict[str, bool]:
    # New hosts stream transform hooks by default. Expand owned placeholders
    # before delivery. Older hosts receive only their supported arguments.
    return {"hold_stream": True} if supports_hold(ctx) else {}
