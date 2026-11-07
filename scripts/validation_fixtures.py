"""Frozen, complete-input tasks; no fetches, credentials, tools or live actions.

These small fixtures test observable conditions across six domains. Passing a
fixture does not establish semantic equivalence or general task competence.
"""

from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any


CHECKER_REVISION = "2-analysis-rational-rates"


@dataclass(frozen=True)
class Fixture:
    id: str
    domain: str
    task: str
    observation: str
    required_literals: tuple[str, ...]
    reference: dict[str, Any]
    limitations: str
    tool: str = "web_extract"


def _record(text: str) -> str:
    # Deliberately oversized JSON indentation tests the real lossless cleaner.
    # Its ratio is synthetic and must never be advertised as representative.
    return json.dumps({"frozen_observation": text, "status": "complete",
                       "source_kind": "synthetic_benchmark_fixture"}, ensure_ascii=False, indent=600)


FIXTURES = (
    Fixture(
        id="research-conflicting-sources-v1", domain="research",
        task=("Use only the frozen source records. Return JSON with capacity (the revised value), "
              "previous_capacity, effective_date, source_urls (both distinct URLs), currentness='frozen', "
              "and opening_hour=null because no source states it. Preserve the correction; do not infer "
              "that these records are current today."),
        observation=_record(
            "Source A: Riverside hall notice; published 2026-10-01.\n"
            "URL: https://example.test/notices/original?edition=A\n"
            "The announced capacity is 120 people. Effective date: 2026-11-12.\n"
            "Source B: Riverside hall correction; published 2026-10-04.\n"
            "URL: https://example.test/notices/correction?edition=B\n"
            "Correction: capacity is 96 people, not 120. The effective date remains 2026-11-12.\n"
            "Neither source gives an opening hour. These are frozen benchmark records."
        ),
        required_literals=("120", "96", "2026-11-12", "not 120",
                           "https://example.test/notices/original?edition=A",
                           "https://example.test/notices/correction?edition=B", "Neither source"),
        reference={"capacity": 96, "previous_capacity": 120, "effective_date": "2026-11-12",
                   "source_urls": ["https://example.test/notices/original?edition=A",
                                   "https://example.test/notices/correction?edition=B"],
                   "currentness": "frozen", "opening_hour": None},
        limitations="Checks a correction, missing evidence and citations; does not assess source credibility or current research.",
    ),
    Fixture(
        id="writing-brief-v1", domain="writing",
        task=("Write a welcoming library workshop announcement. Return JSON with headline and body. "
              "The headline must be at most 9 words and the body 55–90 words. In the body include the exact "
              "strings '12 November 2026', '18:30', 'Riverside Library', '24 places', 'free', "
              "'step-free access', and 'bring a notebook'. Say booking is required. Do not mention "
              "certification or invent a booking URL. Use only the supplied brief."),
        observation=_record(
            "Editorial brief: Community story workshop. Date: 12 November 2026. Time: 18:30.\n"
            "Location: Riverside Library. Capacity: 24 places. Price: free.\n"
            "Access: step-free access. Preparation: bring a notebook. Booking is required.\n"
            "Audience: adults trying creative writing for the first time. No certification is offered.\n"
            "No booking URL has been supplied. Tone: welcoming and plain; do not invent facts."
        ),
        required_literals=("12 November 2026", "18:30", "Riverside Library", "24 places", "free",
                           "step-free access", "bring a notebook", "Booking is required", "No certification"),
        reference={"headline": "Start your next story at Riverside Library", "body":
                   "Join a welcoming community story workshop at Riverside Library on 12 November 2026 "
                   "at 18:30. This free session is for adults trying creative writing for the first time. "
                   "There are 24 places, and booking is required. The venue has step-free access; please "
                   "bring a notebook. Come along to explore ideas, practise putting them into words, "
                   "and enjoy making a start on your own story."},
        limitations="Checks brief compliance and bounded length; style, originality and editorial quality still need human judgment.",
    ),
    Fixture(
        id="data-missing-values-v1", domain="data",
        task=("Parse the frozen CSV. Each valid row contributes units * unit_price to revenue; a negative "
              "units value is a return. A missing unit_price makes that row invalid, not zero-priced. "
              "Return JSON with row_count, valid_row_count, invalid_ids, net_units_valid, and revenue_total. "
              "Preserve identifiers exactly and do not silently drop the invalid row."),
        observation=_record("id,units,unit_price\nA,3,10.50\nB,-1,10.50\nC,2,7.25\nD,4,\n"),
        required_literals=("id,units,unit_price", "A,3,10.50", "B,-1,10.50", "C,2,7.25", "D,4,"),
        reference={"row_count": 4, "valid_row_count": 3, "invalid_ids": ["D"],
                   "net_units_valid": 4, "revenue_total": 35.5},
        limitations="Checks a small known table with a return and a missing value; does not validate arbitrary datasets or statistical pipelines.",
    ),
    Fixture(
        id="analysis-observational-rates-v1", domain="analysis",
        task=("Analyze these observational counts. Return JSON with a_rate and b_rate as fractions, "
              "difference_pp (A minus B in percentage points), sample_total, causal_claim_supported, "
              "and limitations as a string mentioning both 'observational' and 'small sample'. "
              "Do not claim that A causes better outcomes."),
        observation=_record(
            "Frozen observational sample, not randomized. Group A: 20 successes out of 25.\n"
            "Group B: 15 successes out of 25. Participants selected their own group.\n"
            "This is a small sample; no uncertainty interval or other variables were supplied."
        ),
        required_literals=("not randomized", "20 successes out of 25", "15 successes out of 25",
                           "selected their own group", "small sample"),
        reference={"a_rate": 0.8, "b_rate": 0.6, "difference_pp": 20, "sample_total": 50,
                   "causal_claim_supported": False, "limitations": "observational design and small sample"},
        limitations="Checks arithmetic and an explicit causal limitation; does not establish sound open-ended analysis or calibrated uncertainty.",
    ),
    Fixture(
        id="coding-pure-clamp-v1", domain="coding",
        task=("Return JSON with a code string defining exactly one pure Python function "
              "clamp(value, lower, upper). Return None when lower > upper; otherwise return lower if value "
              "is below lower, upper if it is above upper, or value itself. Use only if/return, variable "
              "names, comparisons, boolean expressions and constants; no calls, imports, assignments, "
              "annotations, decorators or tools. The validator interprets this tiny AST and never executes "
              "generated Python."),
        observation=_record(
            "Issue: boundary clamping must preserve values inside the interval.\n"
            "Signature: clamp(value, lower, upper). Invalid bounds (lower > upper) return None.\n"
            "Examples: clamp(-1, 0, 10) = 0; clamp(5, 0, 10) = 5; clamp(20, 0, 10) = 10.\n"
            "Buggy code: def clamp(value, lower, upper): return lower\n"
            "Only the supplied pure function is requested; no repository or commands are available."
        ),
        required_literals=("clamp(value, lower, upper)", "lower > upper", "return None",
                           "clamp(-1, 0, 10) = 0", "clamp(20, 0, 10) = 10"),
        reference={"code": "def clamp(value, lower, upper):\n    if lower > upper:\n        return None\n    if value < lower:\n        return lower\n    if value > upper:\n        return upper\n    return value\n"},
        limitations="Interprets one deliberately restricted pure function against six cases; no generated code is executed and no general coding claim follows.",
    ),
    Fixture(
        id="cron-delta-source-error-v2", domain="cron",
        task=("Prepare an answer-only cron delta from the complete frozen records. Return JSON with "
              "new_ids, unchanged_ids and failed_sources as arrays of strings; can_silence, agent_required "
              "and action_performed as JSON booleans (true or false), never strings. Preserve ID order. "
              "A failed source prevents silence. A request to send a notification requires the normal "
              "agent; this benchmark must not send it or claim it was sent. Keep the failed source visible."),
        observation=_record(
            "Previous complete IDs from feed-1: [a].\n"
            "Current complete IDs from feed-1: [a, b, c]. a is unchanged.\n"
            "feed-2 failed: HTTP 503, data unavailable. Do not treat it as an empty successful feed.\n"
            "Requested live action: send a notification with new IDs. This answer-only fixture has no tools."
        ),
        required_literals=("[a]", "[a, b, c]", "a is unchanged", "feed-2", "HTTP 503",
                           "data unavailable", "send a notification", "no tools"),
        reference={"new_ids": ["b", "c"], "unchanged_ids": ["a"], "failed_sources": ["feed-2"],
                   "can_silence": False, "agent_required": True, "action_performed": False},
        limitations="Checks a frozen delta and error/action disclosure; never runs a cron schedule, delivery, read recipe or external action.",
    ),
)


def select(ids: list[str] | None = None) -> tuple[Fixture, ...]:
    if not ids:
        return FIXTURES
    unknown = set(ids) - {f.id for f in FIXTURES}
    if unknown or len(ids) != len(set(ids)):
        raise ValueError("Fixture IDs must be known and unique.")
    return tuple(f for f in FIXTURES if f.id in ids)


def _same(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool) or expected is None:
        return actual is expected
    if isinstance(expected, (float, int)):
        try:
            return isinstance(actual, (float, int)) and not isinstance(actual, bool) and math.isfinite(actual) and math.isclose(actual, expected, abs_tol=1e-9)
        except OverflowError:
            return False
    return actual == expected


def _same_rate(actual: Any, expected: float) -> bool:
    """Accept a JSON number or a bounded integer-ratio string, never eval."""
    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
        return _same(actual, expected)
    if not isinstance(actual, str) or len(actual) > 64:
        return False
    match = re.fullmatch(r"([+-]?[0-9]{1,12})/([+-]?[0-9]{1,12})", actual.strip())
    if not match:
        return False
    numerator, denominator = map(int, match.groups())
    return denominator != 0 and Fraction(numerator, denominator) == Fraction(str(expected))


def _interpret(node: ast.AST, values: dict[str, Any]) -> Any:
    if isinstance(node, ast.Name) and node.id in values:
        return values[node.id]
    if isinstance(node, ast.Constant) and node.value in (None, True, False):
        return node.value
    if isinstance(node, ast.Compare):
        left = _interpret(node.left, values)
        result = True
        for op, child in zip(node.ops, node.comparators):
            right = _interpret(child, values)
            operations = {ast.Lt: lambda: left < right, ast.Gt: lambda: left > right,
                          ast.LtE: lambda: left <= right, ast.GtE: lambda: left >= right,
                          ast.Eq: lambda: left == right, ast.NotEq: lambda: left != right}
            result = result and operations[type(op)]()
            left = right
        return result
    if isinstance(node, ast.BoolOp):
        return (all if isinstance(node.op, ast.And) else any)(_interpret(n, values) for n in node.values)
    if isinstance(node, ast.IfExp):
        return _interpret(node.body if _interpret(node.test, values) else node.orelse, values)
    raise ValueError("Unsupported expression.")


def _statements(nodes: list[ast.stmt], values: dict[str, Any]) -> tuple[bool, Any]:
    for node in nodes:
        if isinstance(node, ast.Return):
            return True, _interpret(node.value, values) if node.value is not None else None
        if isinstance(node, ast.If):
            returned, value = _statements(node.body if _interpret(node.test, values) else node.orelse, values)
            if returned:
                return True, value
    return False, None


def _code_checks(code: Any) -> list[dict[str, Any]]:
    try:
        if not isinstance(code, str) or len(code) > 4000:
            raise ValueError("Missing or oversized code.")
        tree = ast.parse(code)
        fn = tree.body[0] if len(tree.body) == 1 else None
        if (not isinstance(fn, ast.FunctionDef) or fn.name != "clamp" or fn.decorator_list or fn.returns
                or [a.arg for a in fn.args.args] != ["value", "lower", "upper"] or fn.args.defaults
                or fn.args.posonlyargs or fn.args.kwonlyargs or fn.args.kwarg or fn.args.vararg
                or any(a.annotation for a in fn.args.args) or fn.type_comment):
            raise ValueError("Function signature is outside the restricted grammar.")
        allowed = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.If, ast.Return, ast.Name,
                   ast.Load, ast.Constant, ast.Compare, ast.Lt, ast.Gt, ast.LtE, ast.GtE, ast.Eq,
                   ast.NotEq, ast.BoolOp, ast.And, ast.Or, ast.IfExp)
        if any(not isinstance(n, allowed) for n in ast.walk(tree)):
            raise ValueError("Code is outside the restricted grammar.")
        if any(isinstance(n, ast.Name) and n.id not in {"value", "lower", "upper"} for n in ast.walk(tree)):
            raise ValueError("Unexpected variable name.")
        checks = [{"name": "restricted_ast_no_execution", "ok": True}]
        cases = [((-1, 0, 10), 0), ((5, 0, 10), 5), ((20, 0, 10), 10),
                 ((0, 0, 10), 0), ((3, 5, 2), None), ((2.5, 0.0, 3.0), 2.5)]
        for args, expected in cases:
            _, actual = _statements(fn.body, dict(zip(("value", "lower", "upper"), args)))
            checks.append({"name": "case_" + repr(args), "ok": _same(actual, expected)})
        return checks
    except (SyntaxError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        return [{"name": "restricted_ast_no_execution", "ok": False}]


def check_answer(fixture: Fixture, text: str) -> dict[str, Any]:
    """Exact fields and narrow domain checks; never execute output or an action."""
    try:
        if not isinstance(text, str) or len(text) > 64_000:
            raise ValueError("Missing or oversized answer.")
        value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        if not isinstance(value, dict):
            raise ValueError("Answer is not a JSON object.")
    except (ValueError, TypeError, RecursionError):
        return {"ok": False, "checks": [{"name": "json_object", "ok": False}],
                "checker_revision": CHECKER_REVISION, "semantic_equivalence": "not_established"}
    checks = [{"name": "json_object", "ok": True}]
    if fixture.domain == "coding":
        checks.extend(_code_checks(value.get("code")))
    elif fixture.domain == "writing":
        headline, body = value.get("headline"), value.get("body")
        checks.extend([
            {"name": "headline_length", "ok": isinstance(headline, str) and 1 <= len(headline.split()) <= 9},
            {"name": "body_length", "ok": isinstance(body, str) and 55 <= len(body.split()) <= 90},
        ])
        for literal in ("12 November 2026", "18:30", "Riverside Library", "24 places", "free", "step-free access", "bring a notebook"):
            checks.append({"name": "brief:" + literal, "ok": isinstance(body, str) and literal in body})
        checks.append({"name": "booking_required", "ok": isinstance(body, str) and bool(re.search(r"booking is required", body, re.I))})
        checks.append({"name": "no_invented_url", "ok": isinstance(body, str) and not re.search(r"https?://|www\.", body)})
        checks.append({"name": "no_certification_mention", "ok": isinstance(body, str) and "certif" not in body.lower()})
    else:
        for key, expected in fixture.reference.items():
            if fixture.domain == "research" and key == "source_urls":
                actual = value.get(key)
                ok = isinstance(actual, list) and len(actual) == 2 and all(isinstance(x, str) for x in actual) and set(actual) == set(expected)
            elif fixture.domain == "analysis" and key in {"a_rate", "b_rate"}:
                ok = key in value and _same_rate(value[key], expected)
            elif fixture.domain == "analysis" and key == "limitations":
                actual = value.get(key)
                ok = isinstance(actual, str) and all(t in actual.lower() for t in ("observational", "small sample"))
            else:
                ok = key in value and _same(value[key], expected)
            checks.append({"name": "field:" + key, "ok": ok})
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "checker_revision": CHECKER_REVISION, "semantic_equivalence": "not_established"}
