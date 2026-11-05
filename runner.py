"""Run a routine's steps and judge whether their outputs look right.

Steps run through ``ctx.dispatch_tool``: the same tool registry, approvals,
redaction and budgets as if the model had called the tool. Crystallize never
runs a command any other way.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from . import steplist

logger = logging.getLogger(__name__)

# What crystal_run hands back to Hermes per step. Test comparisons use the full output.
MAX_OUTPUT_CHARS = 60000

Dispatch = Callable[[str, dict], str]


def _parse(result: Any) -> Any:
    if isinstance(result, str):
        try:
            return json.loads(result)
        except ValueError:
            return result
    return result


def step_failure(result: Any, check: Optional[dict] = None) -> Optional[str]:
    """Why a step's output fails its check, or None if it passes."""
    data = _parse(result)
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    if isinstance(data, dict):
        if data.get("error"):
            return f"the tool reported an error: {str(data['error'])[:200]}"
        code = data.get("exit_code")
        if isinstance(code, int) and code != 0:
            return f"the command exited with code {code}"
        body = data.get("output", data.get("content", data.get("result")))
        if body is not None:
            text = body if isinstance(body, str) else json.dumps(body, default=str)
    min_len = (check or {}).get("min_len") or 1
    if len(text.strip()) < min_len:
        return f"the output was too short ({len(text.strip())} characters, expected at least {min_len})"
    return None


def run(routine: dict[str, Any], dispatch: Dispatch) -> dict[str, Any]:
    """Run every step in order. Stops at the first step that fails its check."""
    outputs = []
    for i, step in enumerate(routine.get("steps", []), 1):
        args = steplist.fill(step["args"], step.get("fill_ins") or [])
        try:
            result = dispatch(step["tool"], args if isinstance(args, dict) else {"input": args})
        except Exception as exc:  # noqa: BLE001 — reported as a failed step
            return {"ok": False, "failed_step": i, "tool": step["tool"],
                    "reason": f"the tool raised {type(exc).__name__}: {exc}", "outputs": outputs}
        why = step_failure(result, step.get("check"))
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        outputs.append({"step": i, "tool": step["tool"], "output": text})
        if why:
            return {"ok": False, "failed_step": i, "tool": step["tool"], "reason": why, "outputs": outputs}
    return {"ok": True, "outputs": outputs}


def same_shape(new: str, original: Optional[str], original_len: Optional[int]) -> tuple[bool, str]:
    """Does a test-run output look like the original run's output for the same step?

    Live data changes between runs (new posts, new response times), so this checks
    shape, not exact text: both succeeded, similar size, same JSON keys.
    """
    if step_failure(new):
        return False, step_failure(new) or ""
    if original_len:
        ratio = len(new) / max(1, original_len)
        if ratio < 0.3 or ratio > 3.5:
            return False, f"output size differs a lot ({len(new)} vs {original_len} characters)"
    a, b = _parse(new), _parse(original) if original else None
    if isinstance(a, dict) and isinstance(b, dict):
        ka, kb = set(a), set(b)
        if ka != kb:
            return False, f"different fields ({', '.join(sorted(ka ^ kb))})"
    return True, "same shape"
