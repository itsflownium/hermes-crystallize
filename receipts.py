"""Durable, explicitly qualified records of optimization spend and payback.

Only Crystallize's database is written. Hermes history is read through its
read-only adapter. An Undo never deletes these records. Dollar totals retain
their provenance; an unknown price is never treated as a free call.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import time
from types import SimpleNamespace
from typing import Any, Optional

from . import history, store

MAX_RECORD_BYTES = 64_000
MAX_RECEIPT_ROWS = 10_000
MAX_EXPORT_ROWS = 200
_SCHEMA = """
CREATE TABLE IF NOT EXISTS proof_runs (
    job_id TEXT NOT NULL, execution_id TEXT NOT NULL, data_json TEXT NOT NULL,
    updated_at REAL NOT NULL, PRIMARY KEY(job_id, execution_id)
);
CREATE TABLE IF NOT EXISTS proof_events (
    job_id TEXT NOT NULL, kind TEXT NOT NULL, source_id TEXT NOT NULL,
    data_json TEXT NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY(job_id, kind, source_id)
);
CREATE TABLE IF NOT EXISTS proof_campaigns (
    job_id TEXT NOT NULL, campaign_id TEXT NOT NULL, data_json TEXT NOT NULL,
    updated_at REAL NOT NULL, PRIMARY KEY(job_id, campaign_id)
);
CREATE TABLE IF NOT EXISTS proof_checks (
    job_id TEXT NOT NULL, execution_id TEXT NOT NULL, contract_fingerprint TEXT NOT NULL,
    data_json TEXT NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY(job_id, execution_id, contract_fingerprint)
);
"""

_SECRET_KEY = re.compile(
    r"(?i)(?:^|[_\-\s])(?:api[_\-\s]?key|access[_\-\s]?token|refresh[_\-\s]?token|"
    r"auth(?:orization)?|password|passwd|secret|client[_\-\s]?secret|cookie|private[_\-\s]?key|"
    r"credential|bearer)(?:$|[_\-\s])"
)
_ASSIGNMENT = re.compile(
    r'''(?ix)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|client[_-]?secret|secret)\b["']?\s*[:=]\s*)
    (?:"[^"\n]*"|'[^'\n]*'|[^\s&,;\}\]]+)'''
)
_QUERY_SECRET = re.compile(r"(?i)([?&](?:api[_-]?key|key|access[_-]?token|refresh[_-]?token|token|password|secret)=)[^&#\s]+")


def redact_export(value: Any) -> Any:
    """Return an export-safe copy, redacting credential keys and common inline forms.

    Apply at the export boundary, including model answers and diffs, rather than
    altering the captured observations used for the comparison itself.
    """
    if isinstance(value, dict):
        secret_names = {"apikey", "accesstoken", "refreshtoken", "authorization", "auth", "password",
                        "passwd", "secret", "clientsecret", "cookie", "privatekey", "credential", "bearer",
                        "token", "authtoken", "sessiontoken", "sessionkey", "xapikey"}
        return {str(k): "[REDACTED]" if (_SECRET_KEY.search(str(k)) or
                                        re.sub(r"[^a-z0-9]", "", str(k).lower()) in secret_names) else redact_export(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_export(v) for v in value]
    if not isinstance(value, str):
        return value
    text = re.sub(r"(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED PRIVATE KEY]", value)
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/-]+=*", r"\1 [REDACTED]", text)
    text = re.sub(r"(?im)(\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*)[^\r\n]+", r"\1[REDACTED]", text)
    text = re.sub(r"\bsk[-_][A-Za-z0-9_-]{12,}", "[REDACTED]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text)
    text = _QUERY_SECRET.sub(r"\1[REDACTED]", text)
    return _ASSIGNMENT.sub(r"\1[REDACTED]", text)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) and n >= 0 else None


def _json(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(text.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ValueError("The proof record is too large; it was not silently truncated.")
    return text


def _init(conn: Any) -> None:
    conn.executescript(_SCHEMA)


def record_overhead(job_id: str, *, kind: str, source_id: str,
                    actual_cost_usd: Optional[float] = None,
                    estimated_cost_usd: Optional[float] = None,
                    api_equivalent_usd: Optional[float] = None,
                    status: str = "completed", provenance: str = "unknown",
                    tokens: Optional[dict[str, Any]] = None,
                    duration_ms: Optional[float] = None,
                    session_id: Optional[str] = None,
                    included_in_session: bool = False,
                    details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Upsert a call/trial/helper expense using a stable, deduplicating source ID.

    ``included_in_session`` prevents subtracting a helper again when Hermes
    already included that call in a scheduled run's total.
    """
    if not job_id or not source_id or kind not in {"comparison", "helper", "trial", "replay", "other"}:
        raise ValueError("An overhead record needs a job ID, source ID and supported kind.")
    data = {"kind": kind, "source_id": str(source_id), "status": str(status),
            "actual_usd": _number(actual_cost_usd), "estimated_usd": _number(estimated_cost_usd),
            "api_equivalent_usd": _number(api_equivalent_usd), "provenance": str(provenance),
            "tokens": tokens or {}, "duration_ms": _number(duration_ms),
            "session_id": session_id, "included_in_session": bool(included_in_session),
            "details": details or {}}
    encoded = _json(data)
    with store._lock:
        conn = store._db()
        _init(conn)
        conn.execute("INSERT INTO proof_events VALUES (?, ?, ?, ?, ?) ON CONFLICT(job_id, kind, source_id) "
                     "DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at",
                     (str(job_id), kind, str(source_id), encoded, time.time()))
        conn.commit()
    return data


def record_campaign(job_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Save durable campaign boundaries without storing private rollback prompts."""
    start = _number(data.get("started_at"))
    if not job_id or start is None:
        raise ValueError("A campaign needs its stable job ID and start time.")
    kind = str(data.get("kind") or "tuning")
    campaign_id = str(data.get("campaign_id") or hashlib.sha256(f"{job_id}:{kind}:{start}".encode()).hexdigest()[:24])
    allowed = {"started_at", "activated_at", "ended_at", "baseline_until", "baseline_run_ids", "status",
               "kind", "baseline", "configuration_id", "quality", "uncertainty"}
    clean = {k: v for k, v in data.items() if k in allowed}
    clean.update({"campaign_id": campaign_id, "kind": kind, "started_at": start})
    with store._lock:
        conn = store._db()
        _init(conn)
        previous = conn.execute("SELECT data_json FROM proof_campaigns WHERE job_id=? AND campaign_id=?",
                                (str(job_id), campaign_id)).fetchone()
        old = json.loads(previous[0]) if previous else {}
        # Undo may no longer expose the earlier activation boundary. Keep it.
        merged = {**old, **{k: v for k, v in clean.items() if v is not None}}
        conn.execute("INSERT INTO proof_campaigns VALUES (?, ?, ?, ?) ON CONFLICT(job_id,campaign_id) "
                     "DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at",
                     (str(job_id), campaign_id, _json(merged), time.time()))
        conn.commit()
    return merged


def record_outcome(job_id: str, execution_id: str, *, ok: bool,
                   contract_fingerprint: Optional[str] = None,
                   configuration_id: Optional[str] = None, why: str = "") -> dict[str, Any]:
    """Attach recorded run-bound deterministic validation, including a failed answer."""
    if not job_id or not execution_id or not isinstance(ok, bool):
        raise ValueError("A quality record needs its job, execution ID and a Boolean outcome.")
    data = {"execution_id": str(execution_id), "ok": ok, "why": str(why)[:2000],
            "contract_fingerprint": str(contract_fingerprint or "unconfigured"),
            "configuration_id": configuration_id, "semantic_equivalence_proven": False}
    with store._lock:
        conn = store._db()
        _init(conn)
        conn.execute("INSERT INTO proof_checks VALUES (?, ?, ?, ?, ?) ON CONFLICT(job_id,execution_id,contract_fingerprint) "
                     "DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at",
                     (str(job_id), str(execution_id), data["contract_fingerprint"], _json(data), time.time()))
        conn.commit()
    return data


def _cost(run: Any, info: Optional[dict[str, Any]]) -> dict[str, Any]:
    info = info or {}
    actual = _number(info.get("actual_cost_usd"))
    estimated = _number(info.get("estimated_cost_usd"))
    provenance = getattr(run, "cost_provenance", None)
    uncertainty = list(dict.fromkeys([*((info.get("attribution_evidence") or {}).get("uncertainty") or []),
                                      *((getattr(run, "attribution_evidence", {}) or {}).get("uncertainty") or [])]))
    usage_complete = info.get("usage_totals_complete", True) and getattr(run, "usage_totals_complete", True)
    if not usage_complete or (info.get("cost_status") == "unknown" and actual is None):
        actual = estimated = None
        provenance = "unknown"
    elif actual is not None:
        provenance = "session_actual"
    elif info.get("cost_status") == "included" or getattr(run, "included", False):
        actual, provenance = 0.0, "subscription_incremental"
    elif estimated is not None:
        provenance = "session_estimated"
    elif _number(getattr(run, "cost_usd", None)) is not None:
        if provenance in {"actual", "session_actual"}:
            actual = _number(run.cost_usd)
        else:
            estimated = _number(run.cost_usd)
            provenance = provenance or "legacy_unclassified_estimate"
            uncertainty.append("The saved session total has no invoice-level provenance.")
    api = _number(getattr(run, "api_cost_usd", None))
    if not usage_complete:
        api = None
    api_provenance = getattr(run, "api_cost_provenance", None) or "aggregate_session_api_equivalent_estimate"
    if api is not None and "request" not in str(api_provenance):
        uncertainty.append("API-equivalent session totals may span request pricing tiers; they are approximate, not exact prices.")
    return {"actual_usd": actual, "estimated_usd": estimated, "api_equivalent_usd": api,
            "provenance": provenance or "unknown", "api_equivalent_provenance": api_provenance,
            "pricing_uncertain": bool(getattr(run, "pricing_uncertain", False)),
            "usage_totals_complete": bool(usage_complete),
            "uncertainty": uncertainty}


def _sync_runs(job_id: str, runs: list[Any]) -> None:
    ids = [r.session_id for r in runs if getattr(r, "session_id", None)]
    infos = history._session_costs(ids) if ids else {}
    with store._lock:
        conn = store._db()
        _init(conn)
        for run in runs:
            if str(getattr(run, "job_id", "")) != job_id:
                continue
            info = infos.get(run.session_id or "") or {}
            effort, fingerprint = history._reasoning(info)
            identity_complete = info.get("identity_attribution_complete", getattr(run, "identity_attribution_complete", None))
            if getattr(run, "identity_attribution_complete", None) is False:
                identity_complete = False
            data = {"execution_id": str(run.execution_id), "session_id": run.session_id,
                    "started_at": run.started_ts, "scheduled": bool(run.scheduled), "outcome": run.outcome,
                    "finished": bool(run.finished), "cost": _cost(run, infos.get(run.session_id or "")),
                    "tokens": {k: getattr(run, k, None) for k in
                               ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens", "cache_write_tokens")},
                    "duration_s": run.duration_s, "model": info.get("model", run.model),
                    "provider": info.get("billing_provider", getattr(run, "provider", None)),
                    "reasoning_effort": info.get("reasoning_effort", getattr(run, "reasoning_effort", None) or effort),
                    "reasoning_config_fingerprint": info.get("reasoning_config_fingerprint", getattr(run, "reasoning_config_fingerprint", None) or fingerprint),
                    "identity_attribution_complete": identity_complete,
                    "usage_totals_complete": info.get("usage_totals_complete", True) and getattr(run, "usage_totals_complete", True),
                    "execution_binding": getattr(run, "execution_binding", "unknown"),
                    "session_ids": list(info.get("session_ids") or getattr(run, "session_ids", []) or ([run.session_id] if run.session_id else [])),
                    "attribution_evidence": info.get("attribution_evidence", getattr(run, "attribution_evidence", {}))}
            previous = conn.execute("SELECT data_json FROM proof_runs WHERE job_id=? AND execution_id=?",
                                    (job_id, str(run.execution_id))).fetchone()
            if previous:
                old = json.loads(previous[0])
                # Temporary loss of Hermes history must not erase previously
                # recorded spend/outcomes. An explicit current 'unknown' status
                # is still respected as a correction of earlier pricing.
                if not infos.get(run.session_id or ""):
                    for key in ("actual_usd", "estimated_usd", "api_equivalent_usd"):
                        if data["cost"].get(key) is None:
                            data["cost"][key] = old.get("cost", {}).get(key)
                    if data["cost"]["provenance"] in {"unknown", "legacy_unclassified_estimate"} and old.get("cost", {}).get("provenance"):
                        data["cost"]["provenance"] = old["cost"]["provenance"]
                    for key in ("model", "provider", "reasoning_effort", "reasoning_config_fingerprint", "identity_attribution_complete"):
                        if data.get(key) is None:
                            data[key] = old.get(key)
                    if not data["attribution_evidence"]:
                        data["attribution_evidence"] = old.get("attribution_evidence", {})
                    if old.get("session_ids"):
                        data["session_ids"] = old["session_ids"]
                    if old.get("usage_totals_complete") is False:
                        data["usage_totals_complete"] = False
                    if data["execution_binding"] == "unknown":
                        data["execution_binding"] = old.get("execution_binding", "unknown")
                if data["outcome"] is None:
                    data["outcome"] = old.get("outcome")
            data["comparison_identity"] = _comparison_key(data)
            data["comparison_settings_verified"] = data["comparison_identity"] is not None
            conn.execute("INSERT INTO proof_runs VALUES (?, ?, ?, ?) ON CONFLICT(job_id,execution_id) "
                         "DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at",
                         (job_id, str(run.execution_id), _json(data), time.time()))
        conn.commit()


def _quality_summary(quality: Any) -> Any:
    if quality is None:
        return None
    if not isinstance(quality, dict):
        return {"detail": str(quality)[:1000], "semantic_equivalence_proven": False}
    encoded = json.dumps(quality, sort_keys=True, ensure_ascii=False, default=str)
    reports = quality.get("contract_checks") or []
    return {"ok": quality.get("ok"), "contract_configured": quality.get("contract_configured"),
            "stale": quality.get("stale"), "why": str(quality.get("why") or "")[:1000],
            "semantic_equivalence_proven": False, "report_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "contract_report_count": len(reports),
            "contract_reports": [{"execution_id": r.get("execution_id"), "ok": r.get("ok"),
                                  "configured": r.get("configured"), "check_count": len(r.get("checks") or []),
                                  "failure_count": len(r.get("failures") or []), "fingerprint": r.get("fingerprint")}
                                 for r in reports[:32] if isinstance(r, dict)],
            "contract_reports_truncated": len(reports) > 32}


def snapshot_tuning(t: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Persist a tuning row before Undo/retrial replaces its mutable current state."""
    job_id = t.get("job_id")
    start = t.get("campaign_started_at") or t.get("first_started_at") or t.get("started_at")
    if not job_id or start is None:
        return None
    result = t.get("result") or {}
    quality = result.get("quality") or result.get("contract")
    reports = result.get("contract_checks") or (quality.get("contract_checks") if isinstance(quality, dict) else []) or []
    configuration_id = t.get("configuration_id") or (t.get("config_snapshot") or {}).get("fingerprint")
    for report in reports:
        if isinstance(report, dict) and report.get("execution_id") and isinstance(report.get("ok"), bool):
            record_outcome(str(job_id), str(report["execution_id"]), ok=report["ok"],
                           contract_fingerprint=report.get("fingerprint"), configuration_id=configuration_id,
                           why=report.get("why") or "")
    activated = t.get("kept_at") or t.get("accepted_at")
    if activated is None and (t.get("status") == "kept" or result.get("ok") is True):
        activated = t.get("trial_finished_at")
    if activated is None and t.get("status") == "kept":
        activated = t.get("finished_at")
    ended = t.get("undone_at") or t.get("reverted_at")
    if ended is None and t.get("status") in {"undone", "reverted"}:
        ended = t.get("finished_at")
    return record_campaign(str(job_id), {"kind": "tuning", "started_at": start,
                           "campaign_id": t.get("campaign_id"),
                           "activated_at": activated, "ended_at": ended,
                           "baseline_until": t.get("baseline_until") or start,
                           "baseline_run_ids": t.get("baseline_run_ids"), "status": t.get("status"),
                           "quality": _quality_summary(quality), "configuration_id": configuration_id})


def snapshot_routine(routine: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Keep each promoted interval when a recipe is retired, rebuilt or re-promoted."""
    meta = routine.get("meta") or {}
    job_id = routine.get("source_ref") or meta.get("job_id")
    activated = _number(meta.get("promoted_at"))
    if not job_id or not routine.get("id") or activated is None:
        return None
    ended = _number(meta.get("undone_at"))
    revalidation = _number((meta.get("revalidation") or {}).get("at"))
    if routine.get("status") != "live" and revalidation is not None and revalidation >= activated:
        ended = min(ended, revalidation) if ended is not None and ended >= activated else revalidation
    if ended is not None and ended < activated:
        ended = None  # A prior interval's Undo is not the new interval's end.
    return record_campaign(str(job_id), {"kind": "routine", "campaign_id": f"routine:{routine['id']}:{activated}",
                           "started_at": meta.get("testing_started_at") or meta.get("created_at") or activated,
                           "activated_at": activated, "ended_at": ended,
                           "baseline_until": meta.get("first_promoted_at") or activated,
                           "status": routine.get("status"),
                           "configuration_id": (meta.get("config_snapshot") or {}).get("fingerprint")})


def _sync_campaigns(job_id: str) -> None:
    # Match stable IDs only. A renamed/recreated job must not inherit another job's spend.
    for t in store.list_tuning():
        if str(t.get("job_id")) == job_id:
            snapshot_tuning(t)
    with store._lock:
        conn = store._db()
        _init(conn)
        rows = conn.execute("SELECT id, meta_json, status FROM routines WHERE source='cron' AND source_ref=?", (job_id,)).fetchall()
    for row in rows:
        meta = json.loads(row[1]) if row[1] else {}
        snapshot_routine({"id": row[0], "source_ref": job_id, "meta": meta, "status": row[2]})


def _sync_helpers(job_id: str, runs: list[dict[str, Any]]) -> None:
    reader = getattr(store, "chat_events", None)
    if reader is None:
        return
    # Host auxiliary calls may not be included in the main session's price. The
    # ledger explicitly marks this when known; otherwise show the missing data.
    session_ids = {sid for row in runs for sid in (row.get("session_ids") or [row.get("session_id")]) if sid}
    for sid in session_ids:
        for row in reader(sid, limit=1000):
            if not row.get("helper_call"):
                continue
            source = row.get("cost_source") or "unclassified_estimate"
            record_overhead(job_id, kind="helper", source_id="chat:" + str(row["id"]),
                            estimated_cost_usd=row.get("cheap_cost") if source != "list_price_estimate" else None,
                            api_equivalent_usd=row.get("cheap_cost") if source == "list_price_estimate" else None,
                            status=row.get("status") or "unknown",
                            provenance="helper_ledger:" + source, session_id=sid,
                            included_in_session=bool(row.get("included_in_session", False)),
                            duration_ms=row.get("duration_ms"),
                            tokens={"input_tokens": row.get("input_tokens"), "output_tokens": row.get("output_tokens")},
                            details={"model": row.get("model"), "provider": row.get("provider"),
                                     "session_inclusion": "explicit" if "included_in_session" in row else "not_reported; treated_as_additional"})


def _load(table: str, job_id: str) -> tuple[list[dict[str, Any]], bool]:
    with store._lock:
        conn = store._db()
        _init(conn)
        rows = conn.execute(f"SELECT data_json FROM {table} WHERE job_id=? ORDER BY updated_at LIMIT ?",
                            (job_id, MAX_RECEIPT_ROWS + 1)).fetchall()
    return [json.loads(r[0]) for r in rows[:MAX_RECEIPT_ROWS]], len(rows) <= MAX_RECEIPT_ROWS


def _value(cost: dict[str, Any], basis: str) -> Optional[float]:
    if basis == "actual":
        return _number(cost.get("actual_usd"))
    if basis == "estimated":
        actual = _number(cost.get("actual_usd"))
        return actual if actual is not None else _number(cost.get("estimated_usd"))
    return _number(cost.get("api_equivalent_usd"))


MIN_PROVEN_COMPARISONS = 5


def _beyond_noise(savings: list[float]) -> bool:
    """Enough matched runs, with a typical saving larger than their spread can explain."""
    if len(savings) < MIN_PROVEN_COMPARISONS:
        return False
    typical = statistics.fmean(savings)
    return typical > 2 * statistics.stdev(savings) / math.sqrt(len(savings))


def _successful(row: dict[str, Any]) -> bool:
    return row.get("outcome") in {"completed", "no_change", "skipped", "silent"} and row.get("contract_ok") is not False


def _comparison_key(row: dict[str, Any]) -> Optional[tuple[str, str, str, str]]:
    return history.comparison_identity(SimpleNamespace(**row))


def _legacy_settings_unknown(row: dict[str, Any]) -> bool:
    # Preserve old receipt arithmetic, but never call two unknown settings equal.
    # Modern host reads explicitly set attribution_complete=False for unknowns.
    return (row.get("identity_attribution_complete") is None and
            all(row.get(key) is None for key in ("model", "provider", "reasoning_effort", "reasoning_config_fingerprint")))


def _tally(rows: list[dict[str, Any]], basis: str) -> dict[str, Any]:
    values = [_value(r.get("cost", r), basis) for r in rows]
    known = [v for v in values if v is not None]
    return {"known_usd": sum(known), "total_usd": sum(known) if len(known) == len(values) else None,
            "known_count": len(known), "unknown_count": len(values) - len(known), "count": len(values)}


def _basis_receipt(runs: list[dict[str, Any]], campaigns: list[dict[str, Any]],
                   events: list[dict[str, Any]], basis: str) -> dict[str, Any]:
    campaigns = sorted(campaigns, key=lambda c: c["started_at"])
    live_rows: list[dict[str, Any]] = []
    trial_rows: list[dict[str, Any]] = []
    baseline_rows: dict[str, list[dict[str, Any]]] = {}
    campaign_baselines = []
    contributions = []
    matched_savings = []
    comparisons = []
    unproven_comparisons = 0
    confounded_comparisons = 0
    verified_comparisons = 0
    incomplete_baseline_comparisons = 0
    unknown_savings = 0
    # The earliest optimization establishes a clean baseline. Later experiments
    # must not quietly turn already-optimized runs into the original baseline.
    original_cutoff = min((c.get("baseline_until") or c["started_at"] for c in campaigns), default=None)
    for campaign in campaigns:
        specified = set(campaign.get("baseline_run_ids") or [])
        before = [r for r in runs if r.get("scheduled") and _successful(r)
                  and ((r["execution_id"] in specified) if specified else
                       (original_cutoff is not None and r["started_at"] < original_cutoff))]
        baseline_rows[campaign["campaign_id"]] = before
        prices = [_value(r["cost"], basis) for r in before]
        known = [v for v in prices if v is not None]
        cohorts: dict[Any, list[dict[str, Any]]] = {}
        for row in before:
            identity = _comparison_key(row)
            key = identity or ("legacy_unproven" if _legacy_settings_unknown(row) else f"unknown:{row['execution_id']}")
            cohorts.setdefault(key, []).append(row)
        # A mixed baseline has no single representative price. Each live run
        # chooses its own same-settings cohort below.
        baseline = statistics.median(known) if known and len(cohorts) == 1 else None
        cohort_rows = []
        for key, samples in cohorts.items():
            values = [_value(sample["cost"], basis) for sample in samples]
            numbers = [value for value in values if value is not None]
            cohort_rows.append({"comparison_identity": key if isinstance(key, tuple) else None,
                                "settings_verified": isinstance(key, tuple),
                                "per_run_usd": statistics.median(numbers) if numbers else None,
                                "sample_count": len(numbers), "missing_cost_count": len(values) - len(numbers),
                                "execution_ids": [sample["execution_id"] for sample in samples[:MAX_EXPORT_ROWS]]})
        campaign_baselines.append({"campaign_id": campaign["campaign_id"], "per_run_usd": baseline,
                                   "sample_count": len(known), "missing_cost_count": len(prices) - len(known),
                                   "execution_ids": [r["execution_id"] for r in before[:MAX_EXPORT_ROWS]],
                                   "execution_ids_truncated": len(before) > MAX_EXPORT_ROWS,
                                   "settings_cohorts": cohort_rows[:MAX_EXPORT_ROWS],
                                   "settings_cohorts_truncated": len(cohort_rows) > MAX_EXPORT_ROWS,
                                   "provenance": "median_of_recorded_baseline_sessions"})
    for row in runs:
        if not row.get("scheduled"):
            continue
        applicable = [c for c in campaigns if row["started_at"] >= c["started_at"]
                      and (c.get("ended_at") is None or row["started_at"] <= c["ended_at"])]
        if not applicable:
            continue
        # The newest applicable trial owns its setup spend even if older kept
        # settings are still installed underneath it. Never count a cost twice.
        campaign = applicable[-1]
        if campaign.get("activated_at") is None or row["started_at"] < campaign["activated_at"]:
            trial_rows.append(row)
            continue
        live_rows.append(row)
        price = _value(row["cost"], basis)
        identity = _comparison_key(row)
        candidates = baseline_rows[campaign["campaign_id"]]
        legacy = _legacy_settings_unknown(row)
        matched = [sample for sample in candidates if
                   (identity is not None and _comparison_key(sample) == identity) or
                   (legacy and _legacy_settings_unknown(sample))]
        matched_prices = [_value(sample["cost"], basis) for sample in matched]
        known_prices = [value for value in matched_prices if value is not None]
        baseline = statistics.median(known_prices) if known_prices else None
        status = "failed_spend" if not _successful(row) else "matched" if identity and matched else "legacy_unproven" if legacy and matched else "unknown_settings" if identity is None else "no_equivalent_baseline"
        if _successful(row):
            incomplete_baseline_comparisons += bool(matched and len(known_prices) != len(matched_prices))
            if status == "matched":
                verified_comparisons += 1
            elif status == "legacy_unproven":
                unproven_comparisons += 1
            else:
                confounded_comparisons += 1
        comparisons.append({"execution_id": row["execution_id"], "status": status,
                            "comparison_identity": identity, "baseline_per_run_usd": baseline,
                            "missing_baseline_cost_count": len(matched_prices) - len(known_prices),
                            "baseline_execution_ids": [sample["execution_id"] for sample in matched[:MAX_EXPORT_ROWS]],
                            "baseline_execution_ids_truncated": len(matched) > MAX_EXPORT_ROWS})
        # A failed run delivered no result: all its known spend is a loss.
        saving = (-price if not _successful(row) else baseline - price) if price is not None and (baseline is not None or not _successful(row)) else None
        if saving is None:
            unknown_savings += 1
        else:
            contributions.append(saving)
            if _successful(row):
                matched_savings.append(saving)
    additional_events = [e for e in events if not e.get("included_in_session")]
    event_cost = _tally(additional_events, basis)
    trial_cost = _tally(trial_rows, basis)
    overhead_known = event_cost["known_usd"] + trial_cost["known_usd"]
    unknown_overhead = event_cost["unknown_count"] + trial_cost["unknown_count"]
    historical_known = sum(contributions)
    net_known = historical_known - overhead_known
    complete = unknown_savings == 0 and unknown_overhead == 0 and bool(campaigns or events)
    per_run = statistics.median(matched_savings) if matched_savings and not confounded_comparisons else None
    pricing_uncertain = basis == "api_equivalent" and any(r["cost"].get("pricing_uncertain") or
                        "request" not in str(r["cost"].get("api_equivalent_provenance")) for r in live_rows + trial_rows)
    qualified = (complete and unproven_comparisons == 0 and confounded_comparisons == 0
                 and incomplete_baseline_comparisons == 0 and not pricing_uncertain)
    active = any(c.get("activated_at") is not None and c.get("ended_at") is None for c in campaigns)
    remaining = math.ceil(max(0.0, -net_known) / per_run) if complete and per_run is not None and per_run > 0 and (active or net_known >= 0) else None
    return {"basis": basis, "baseline": campaign_baselines,
            "spend_provenance": {"actual": "recorded_actual_or_subscription_incremental", "estimated": "recorded_actual_or_estimated",
                                 "api_equivalent": "listed_API_equivalent_estimate; not a bill"}[basis],
            "historical_savings_are_counterfactual_estimates": True,
            "pricing_uncertain": pricing_uncertain,
            "equivalent_settings_comparison_count": verified_comparisons,
            "unproven_settings_comparison_count": unproven_comparisons,
            "excluded_settings_comparison_count": confounded_comparisons,
            "efficiency_savings_proven": qualified and _beyond_noise(matched_savings) and net_known > 0,
            "minimum_comparisons_for_proof": MIN_PROVEN_COMPARISONS,
            "qualified_historical_savings_usd": historical_known if qualified else None,
            "qualified_efficiency_savings_usd": historical_known if qualified else None,
            "qualified_net_payback_usd": net_known if qualified else None,
            "comparisons": comparisons[-MAX_EXPORT_ROWS:],
            "comparisons_truncated": len(comparisons) > MAX_EXPORT_ROWS,
            "comparison_policy": "Same recorded model, provider and full reasoning configuration; unknown settings do not establish equivalence.",
            "live_run_count": len(live_rows), "failed_live_run_count": sum(not _successful(r) for r in live_rows),
            "trial_run_count": len(trial_rows), "failed_trial_run_count": sum(not _successful(r) for r in trial_rows),
            "historical_savings_usd": historical_known if unknown_savings == 0 else None,
            "known_historical_savings_usd": historical_known, "unknown_savings_run_count": unknown_savings,
            "overhead_usd": overhead_known if unknown_overhead == 0 else None,
            "known_overhead_usd": overhead_known, "unknown_overhead_count": unknown_overhead,
            "trial_spend": trial_cost, "additional_spend": event_cost,
            "net_payback_usd": net_known if complete else None, "known_net_payback_usd": net_known,
            "complete": complete, "estimated_saving_per_future_run_usd": per_run,
            "active_optimization": active,
            "estimated_break_even_remaining_runs": remaining,
            "break_even_assumption": "Future successful runs have the observed median cost; no guarantee of future savings."}


def receipt(job_id: str, *, runs: Optional[list[Any]] = None) -> dict[str, Any]:
    """Build a redacted receipt. This function never makes a model call."""
    job_id = str(job_id or "").strip()
    if not job_id:
        raise ValueError("Choose a stable cron job ID for the receipt.")
    fetched = history.load_runs(job_id) if runs is None else runs
    fetched = [r for r in fetched if str(getattr(r, "job_id", "")) == job_id]
    _sync_runs(job_id, fetched)
    _sync_campaigns(job_id)
    rows, runs_complete = _load("proof_runs", job_id)
    rows.sort(key=lambda r: (r["started_at"], r["execution_id"]))
    _sync_helpers(job_id, rows)
    campaigns, campaigns_complete = _load("proof_campaigns", job_id)
    events, events_complete = _load("proof_events", job_id)
    quality_checks, checks_complete = _load("proof_checks", job_id)
    checks_by_execution: dict[str, list[dict[str, Any]]] = {}
    for check in quality_checks:
        checks_by_execution.setdefault(check["execution_id"], []).append(check)
    for row in rows:
        checks = checks_by_execution.get(row["execution_id"])
        if checks:
            row["contract_ok"] = all(c["ok"] for c in checks)
            row["recorded_validation"] = checks[:8]
            row["recorded_validation_truncated"] = len(checks) > 8
    bases = {basis: _basis_receipt(rows, campaigns, events, basis)
             for basis in ("actual", "estimated", "api_equivalent")}
    uncertainties = {"Baseline-relative savings are estimates of a counterfactual, even when spend is actual.",
                     "Answer-only comparisons do not measure the cost or correctness of a full live tool execution.",
                     "Trial job spend is charged as setup overhead; trial runs receive no claimed baseline saving.",
                     "Timestamp-only skip/no-AI markers are not credited unless linked to a recorded completed execution."}
    if not campaigns:
        uncertainties.add("No durable optimization start/activation boundary is known; historical savings and break-even are unavailable.")
    if any(c.get("activated_at") is None for c in campaigns):
        uncertainties.add("An activation boundary is missing; affected runs are charged as trial overhead, with no claimed live saving.")
    if any(b["unproven_settings_comparison_count"] for b in bases.values()):
        uncertainties.add("Legacy counterfactual totals have unknown model/provider/reasoning settings; they are not qualified efficiency savings.")
    if any(b["excluded_settings_comparison_count"] for b in bases.values()):
        uncertainties.add("Runs without an equivalent recorded model, provider and full reasoning configuration baseline are excluded from savings credit.")
    for row in rows:
        uncertainties.update(row["cost"].get("uncertainty") or [])
    if any(e.get("details", {}).get("session_inclusion") == "not_reported; treated_as_additional" for e in events):
        uncertainties.add("Helper ledger costs are charged separately; the host has not reported whether any are already included in a session total.")
    if any(r.get("outcome") in {"claimed", "running", "pending"} for r in rows):
        uncertainties.add("Some recorded executions are still in progress; their current spend is incomplete.")
        for value in bases.values():
            value["complete"] = False
            value["net_payback_usd"] = None
            value["estimated_break_even_remaining_runs"] = None
            value["qualified_historical_savings_usd"] = value["qualified_efficiency_savings_usd"] = value["qualified_net_payback_usd"] = None
            value["efficiency_savings_proven"] = False
    if not (runs_complete and campaigns_complete and events_complete and checks_complete):
        uncertainties.add("The receipt row limit was reached; totals cover the bounded recorded subset only.")
        for value in bases.values():
            value["complete"] = False
            value["net_payback_usd"] = None
            value["estimated_break_even_remaining_runs"] = None
            value["qualified_historical_savings_usd"] = value["qualified_efficiency_savings_usd"] = value["qualified_net_payback_usd"] = None
            value["efficiency_savings_proven"] = False
    if any(b["unknown_overhead_count"] or b["unknown_savings_run_count"] for b in bases.values()):
        uncertainties.add("Missing prices remain unknown; known subtotals are not complete net payback.")
    if any(baseline["missing_cost_count"] for basis in bases.values() for baseline in basis["baseline"]):
        uncertainties.add("Some baseline sessions have no price for a displayed cost basis; its median uses the known subset only.")
    preferred = "actual" if bases["actual"]["complete"] else "estimated" if bases["estimated"]["complete"] else None
    result = {"version": 1, "job_id": job_id, "generated_at": time.time(), "scope": "recorded_cron_history",
              "sample_count": len(rows), "comparison_call_count": sum(e["kind"] == "comparison" for e in events),
              "helper_call_count": sum(e["kind"] == "helper" for e in events), "campaigns": campaigns,
              "cost_bases": bases, "preferred_basis": preferred,
              "net_payback_usd": bases[preferred]["net_payback_usd"] if preferred else None,
              "qualified_efficiency_savings_usd": bases[preferred]["qualified_efficiency_savings_usd"] if preferred else None,
              "qualified_net_payback_usd": bases[preferred]["qualified_net_payback_usd"] if preferred else None,
              "efficiency_savings_proven": bool(preferred and bases[preferred]["efficiency_savings_proven"]),
              "estimated_break_even_remaining_runs": bases[preferred]["estimated_break_even_remaining_runs"] if preferred else None,
              "recorded_spend": {basis: _tally(rows, basis) for basis in bases},
              "overhead_by_kind": {kind: {basis: _tally([e for e in events if e["kind"] == kind], basis) for basis in bases}
                                   for kind in sorted({e["kind"] for e in events})},
              "runs": rows[-MAX_EXPORT_ROWS:], "overhead": events[-MAX_EXPORT_ROWS:],
              "validation_failure_count": sum(not c["ok"] for c in quality_checks),
              "exported_run_count": min(MAX_EXPORT_ROWS, len(rows)),
              "history_complete": runs_complete and campaigns_complete and events_complete and checks_complete,
              "coverage": "Only executions linked to this stable job ID and recorded plugin/host costs are included; unrecorded work is not extrapolated.",
              "uncertainty": sorted(uncertainties)}
    return redact_export(result)
