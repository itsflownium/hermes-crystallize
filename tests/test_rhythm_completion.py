"""Rhythm uses real full-content observations and explicit, isolated host edits."""

import copy
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from crystallize import contracts, history, lifecycle, rhythm, store
from crystallize.history import Run, Step


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def observed(i, body="same", *, ts=None, outcome="completed", scheduled=True, job_id="j1", tool="web_extract", args=None):
    return Run(execution_id=f"e{i}", job_id=job_id, job_name="Snapshot", session_id=f"s{i}",
               scheduled=scheduled, started_ts=ts if ts is not None else 1793577600 + i * 3600,
               steps=[Step(tool, args if args is not None else {"urls": ["https://example.invalid/feed"]},
                           body[:10], digest(body), len(body), "ok", 1)],
               outcome=outcome, finished=outcome == "completed", cost_usd=.02, cost_provenance="actual")


@pytest.fixture
def env(monkeypatch):
    job = {"id": "j1", "name": "Snapshot", "prompt": "Summarize the current feed as a snapshot",
           "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
           "model": "user-model", "provider": "user-provider", "reasoning_effort": "high",
           "enabled": True, "deliver": "local", "repeat": {"times": None, "completed": 0}}
    jobs = [job]
    runs = [observed(i) for i in range(4)]
    edits = []
    monkeypatch.setattr(lifecycle, "cron_job", lambda ref: next((j for j in jobs if j["id"] == ref), None))
    monkeypatch.setattr(history, "load_runs", lambda ref=None, **kwargs: runs)
    monkeypatch.setattr(history, "running_job_ids", lambda: set())
    monkeypatch.setattr(store, "cron_trace_rows", lambda ref=None: [])
    monkeypatch.setattr(rhythm, "_control_capability", lambda: (True, "isolated supported host"))

    def edit(job_id, *flags):
        assert job_id == "j1"
        assert flags[0] == "--schedule" and len(flags) == 2
        edits.append((job_id, flags))
        minutes = int(flags[1].removeprefix("every ").removesuffix("m"))
        job["schedule"] = {"kind": "interval", "minutes": minutes, "display": f"every {minutes}m"}

    monkeypatch.setattr(lifecycle, "_edit_job", edit)
    return SimpleNamespace(job=job, jobs=jobs, runs=runs, edits=edits)


def approve(env, interval=120, **overrides):
    values = {"expected_config_fingerprint": rhythm.configuration_fingerprint(env.job),
              "noncritical": True, "allowed_staleness_minutes": interval, "confirmed": True}
    values.update(overrides)
    return rhythm.apply("j1", interval, **values)


def test_heatmap_uses_full_hash_changes_beyond_preview_and_real_timezone(env, monkeypatch):
    # UTC Sunday 14:30 is Sydney Monday 01:30 at this date. Neither the
    # preview prefix nor the proposed schedule invents a content change.
    start = datetime(2026, 11, 1, 14, 30, tzinfo=timezone.utc).timestamp()
    env.runs[:] = [observed(0, "identical prefix" + "a" * 3000, ts=start - 3600),
                   observed(1, "identical prefix" + "a" * 2999 + "b", ts=start)]
    out = rhythm.report("j1", "Australia/Sydney")
    bucket = next(c for c in out["heatmap"] if c["weekday"] == 0 and c["hour"] == 1)
    assert bucket == {"weekday": 0, "hour": 1, "observations": 1, "comparisons": 1, "changes": 1, "unchanged": 0}
    assert sum(c["observations"] for c in out["heatmap"]) == 2
    assert out["timezone"] == "Australia/Sydney" and out["timezone_source"] == "explicit"
    assert rhythm.report("j1")["timezone"] == "UTC"
    assert not env.edits


def test_trace_observation_time_is_used_instead_of_scheduled_or_run_time(env, monkeypatch):
    start = datetime(2026, 11, 2, 0, tzinfo=timezone.utc).timestamp()
    env.runs[:] = [observed(0, ts=start), observed(1, "changed", ts=start + 3600)]
    monkeypatch.setattr(store, "cron_trace_rows", lambda ref=None: [
        {"cron_job_id": "j1", "cron_scheduled": 1, "cron_execution_id": "e1", "ts": start + 7200},
        {"cron_job_id": "other", "cron_scheduled": 1, "cron_execution_id": "e1", "ts": start + 99999},
    ])
    out = rhythm.report("j1")
    assert out["heatmap"][2]["changes"] == 1
    assert out["heatmap"][1]["observations"] == 0


def test_unknown_failed_manual_and_other_jobs_never_manufacture_comparisons(env):
    env.runs[:] = [observed(0), observed(1, outcome="failed"), observed(2, "new"),
                   observed(3, "manual", scheduled=False), observed(4, "other", job_id="other"),
                   observed(5, "incomplete"), observed(6, "new")]
    env.runs[5].steps[0].result_sha256 = None
    out = rhythm.report("j1")
    assert out["evidence"]["complete_observations"] == 3
    assert out["evidence"]["comparisons"] == 0
    assert out["evidence"]["changes"] == 0


def test_completed_ledger_required_even_with_finished_session(env):
    for run in env.runs:
        run.outcome = None
    out = rhythm.report("j1")
    assert out["evidence"]["complete_observations"] == 0
    assert not rhythm.preview("j1", 120, noncritical=True, allowed_staleness_minutes=120)["can_apply"]


def test_known_source_error_does_not_count_as_content_observation(env):
    env.runs[1].steps[0].result_preview = '{"ok":false,"error":"upstream unavailable"}'
    out = rhythm.report("j1")
    assert out["evidence"]["complete_observations"] == 3
    assert out["evidence"]["comparisons"] == 1


def test_suggestion_needs_real_coverage_and_always_requires_explicit_acknowledgement(env):
    assert rhythm.report("j1")["recommendation"] is None
    env.runs[:] = [observed(i, ts=1793577600 + i * 86400) for i in range(12)]
    out = rhythm.report("j1")
    assert out["recommendation"]["interval_minutes"] == 120
    assert out["recommendation"]["requires_acknowledgement"] is True
    assert out["recommendation"]["classification"] == "suggestion"
    assert not rhythm.preview("j1", 120)["can_apply"]
    env.job["prompt"] = "Alert immediately when the feed changes"
    assert rhythm.report("j1")["recommendation"] is None


@pytest.mark.parametrize("prompt", [
    "Alert when something changes", "Remind me to read the report", "Read the feed before 9am",
    "Summarize all new events", "Monitor the feed", "Summarize and send the result",
    "Write a snapshot to disk", "Daily snapshot", "Summarize unread mail", "Check current information",
])
def test_critical_event_action_and_unclear_tasks_excluded_even_with_rare_changes(env, prompt):
    env.job["prompt"] = prompt
    preview = rhythm.preview("j1", 120, noncritical=True, allowed_staleness_minutes=120)
    assert not preview["can_apply"] and preview["blockers"]
    assert not preview["can_export"] and preview["export"] is None
    with pytest.raises(rhythm.RhythmError):
        approve(env)
    assert not env.edits


@pytest.mark.parametrize("tool,args", [
    ("terminal", {"command": "cat /tmp/feed"}),
    ("execute_code", {"code": "print('reading')"}),
    ("custom_get", {"action": "delete"}),
    ("unknown_fetch", {}),
    ("web_extract", {"operation": "update", "urls": ["https://e.invalid"]}),
    ("web_search", {"since": "yesterday"}),
    ("web_search", {"query": "breaking news since yesterday"}),
    ("web_extract", {"url": "https://e.invalid/feed?cursor=next"}),
    ("read_file", {"cursor": "last-item"}),
])
def test_robust_conservative_action_and_window_check(env, tool, args):
    env.runs[0].steps = [Step(tool, args, "ok", digest("ok"), 2, "ok", 1)]
    assert not rhythm.preview("j1", 120, noncritical=True, allowed_staleness_minutes=120)["can_apply"]
    assert not env.edits


@pytest.mark.parametrize("change", [
    {"schedule": {"kind": "cron", "expr": "0 9 * * *"}},
    {"schedule": {"kind": "once", "run_at": "2026-12-01T09:00:00Z"}},
    {"schedule": {"kind": "interval", "minutes": 60, "timezone": "Australia/Sydney"}},
    {"repeat": {"times": 4, "completed": 0}}, {"deliver": "telegram"},
    {"script": "custom.sh"}, {"skills": ["custom"]}, {"enabled": False}, {"state": "paused"},
])
def test_jobs_with_unverified_per_run_obligations_excluded(env, change):
    env.job.update(change)
    assert not rhythm.preview("j1", 120, noncritical=True, allowed_staleness_minutes=120)["can_apply"]


def test_required_actions_contract_blocks_schedule_change(env):
    contracts.set("j1", {"required_actions": [{"tool": "send_message", "target": "team"}]})
    assert not rhythm.preview("j1", 120, noncritical=True, allowed_staleness_minutes=120)["can_apply"]


def test_running_execution_blocks_schedule_change(env, monkeypatch):
    monkeypatch.setattr(history, "running_job_ids", lambda: {"j1"})
    assert not rhythm.preview("j1", 120, noncritical=True, allowed_staleness_minutes=120)["can_apply"]


def test_preview_projects_invocations_and_separately_estimated_money_without_writes(env, monkeypatch):
    before = copy.deepcopy(env.job)
    monkeypatch.setattr(store, "set_setting", lambda *a: pytest.fail("preview must not persist consent or write settings"))
    out = rhythm.preview("j1", 120, noncritical=True, allowed_staleness_minutes=180)
    p = out["projection"]
    assert p["classification"] == "projection"
    assert (p["current_scheduled_invocations"], p["proposed_scheduled_invocations"], p["avoided_scheduled_invocations"]) == (720, 360, 360)
    assert p["estimated_avoided_usd"] == pytest.approx(7.2)
    assert p["cost_samples"] == 4 and p["cost_basis"] == ["actual"]
    assert out["frequency_factor"] == 2 and out["can_apply"]
    assert env.job == before and not env.edits
    assert "measured_savings" not in str(out)


def test_unknown_costs_stay_unknown_and_included_cost_is_zero(env):
    for run in env.runs:
        run.cost_usd, run.cost_provenance, run.api_cost_usd = None, "unknown", 12.0
    out = rhythm.preview("j1", 120, noncritical=True, allowed_staleness_minutes=120)
    assert out["projection"]["estimated_avoided_usd"] is None
    assert out["projection"]["cost_samples"] == 0
    env.runs[0].cost_usd, env.runs[0].cost_provenance = 0.0, "included"
    out = rhythm.preview("j1", 120, noncritical=True, allowed_staleness_minutes=120)
    assert out["projection"]["estimated_avoided_usd"] == 0
    assert out["projection"]["cost_basis"] == ["included"]


@pytest.mark.parametrize("interval,staleness,ack", [(120, None, True), (120, 60, True), (120, 120, False), (30, 120, True)])
def test_interval_and_explicit_staleness_acknowledgement_enforced(env, interval, staleness, ack):
    out = rhythm.preview("j1", interval, noncritical=ack, allowed_staleness_minutes=staleness)
    assert not out["can_apply"] and out["blockers"]


@pytest.mark.parametrize("value", [True, False, 0, -1, 2.5, "120", float("nan"), float("inf"), None])
def test_invalid_slider_values_rejected(env, value):
    with pytest.raises(rhythm.RhythmError):
        rhythm.preview("j1", value)


def test_invalid_timezone_and_unstable_name_are_not_silently_accepted(env, monkeypatch):
    with pytest.raises(rhythm.RhythmError, match="timezone"):
        rhythm.report("j1", "Not/AZone")
    monkeypatch.setattr(lifecycle, "cron_job", lambda ref: env.job)
    with pytest.raises(rhythm.RhythmError, match="stable"):
        rhythm.report("Snapshot")
    with pytest.raises(rhythm.RhythmError):
        rhythm.report("--all")


def test_explicit_confirmation_and_expected_fingerprint_required(env):
    with pytest.raises(rhythm.RhythmError, match="confirmation"):
        approve(env, confirmed=False)
    with pytest.raises(rhythm.RhythmError, match="fingerprint"):
        approve(env, expected_config_fingerprint="")
    with pytest.raises(rhythm.RhythmError, match="changed"):
        approve(env, expected_config_fingerprint="0" * 64)
    assert not env.edits


def test_apply_only_uses_public_schedule_flag_preserves_model_and_reasoning_and_is_idempotent(env):
    before = copy.deepcopy(env.job)
    fingerprint = rhythm.configuration_fingerprint(env.job)
    out = approve(env)
    assert out["applied"] and out["can_undo"]
    assert env.edits == [("j1", ("--schedule", "every 120m"))]
    assert {k: v for k, v in env.job.items() if k != "schedule"} == {k: v for k, v in before.items() if k != "schedule"}
    retry = approve(env, expected_config_fingerprint=fingerprint)
    assert retry["already_applied"] and not retry["applied"] and len(env.edits) == 1
    assert "no atomic" in out["concurrency_limit"]


def test_recovery_record_exists_before_host_schedule_edit(env, monkeypatch):
    original = lifecycle._edit_job

    def edit(job_id, *flags):
        saved = store.get_setting("rhythm:j1")
        assert saved["status"] == "pending"
        assert saved["original_schedule"]["minutes"] == 60
        original(job_id, *flags)

    monkeypatch.setattr(lifecycle, "_edit_job", edit)
    approve(env)


def test_concurrent_edit_detected_immediately_before_dispatch(env, monkeypatch):
    original = rhythm.preview

    def preview(*args, **kwargs):
        result = original(*args, **kwargs)
        env.job["schedule"]["minutes"] = 75
        return result

    monkeypatch.setattr(rhythm, "preview", preview)
    with pytest.raises(rhythm.RhythmError, match="changed"):
        approve(env)
    assert env.job["schedule"]["minutes"] == 75 and not env.edits
    assert store.get_setting("rhythm:j1") is None


def test_changed_contract_also_invalidates_preview(env):
    fingerprint = rhythm.configuration_fingerprint(env.job)
    contracts.set("j1", {"minimum_items": 2})
    with pytest.raises(rhythm.RhythmError, match="changed"):
        approve(env, expected_config_fingerprint=fingerprint)
    assert not env.edits


def test_concurrent_edit_while_persisting_recovery_data_is_detected_before_cli(env, monkeypatch):
    original = store.set_setting

    def save(key, value):
        original(key, value)
        if key == "rhythm:j1" and value.get("status") == "pending":
            env.job["schedule"]["minutes"] = 75

    monkeypatch.setattr(store, "set_setting", save)
    with pytest.raises(rhythm.RhythmError, match="changed"):
        approve(env)
    assert not env.edits and env.job["schedule"]["minutes"] == 75
    assert store.get_setting("rhythm:j1")["status"] == "cancelled"


def test_unknown_cli_capability_exports_suggestion_and_never_writes(env, monkeypatch):
    monkeypatch.setattr(rhythm, "_control_capability", lambda: (False, "host has no --schedule"))
    out = rhythm.preview("j1", 120, noncritical=True, allowed_staleness_minutes=120)
    assert not out["can_apply"] and out["can_export"]
    assert out["control"]["mode"] == "export_only"
    assert out["export"]["argv"] == ["hermes", "cron", "edit", "j1", "--schedule", "every 120m"]
    result = approve(env)
    assert result["ok"] and not result["applied"] and "export" in result
    assert not env.edits and store.get_setting("rhythm:j1") is None


def test_cli_capability_probe_is_read_only_help_and_honors_profile(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="usage: edit JOB_ID [--schedule SCHEDULE]", stderr="")

    monkeypatch.setattr(rhythm.subprocess, "run", run)
    monkeypatch.setattr(lifecycle, "hermes_env", lambda: {"HERMES_HOME": "/isolated/profile"})
    rhythm._probe_schedule_flag.cache_clear()
    assert rhythm._probe_schedule_flag(("isolated-hermes",), "/isolated/profile")[0]
    assert calls[0][0] == ["isolated-hermes", "cron", "edit", "--help"]
    assert calls[0][1]["env"]["HERMES_HOME"] == "/isolated/profile"
    rhythm._probe_schedule_flag.cache_clear()


def test_undo_restores_owned_original_interval_and_preserves_user_edits_and_renames(env):
    approve(env)
    env.job.update(name="Renamed", model="new-user-model", reasoning_effort="medium", prompt="User's later task")
    out = rhythm.undo("j1", expected_config_fingerprint=rhythm.configuration_fingerprint(env.job), confirmed=True)
    assert out["undone"] and env.job["schedule"]["minutes"] == 60
    assert env.job["model"] == "new-user-model" and env.job["reasoning_effort"] == "medium"
    assert env.job["prompt"] == "User's later task" and env.job["name"] == "Renamed"
    assert all(job_id == "j1" for job_id, _ in env.edits)
    result = rhythm.undo("j1", expected_config_fingerprint=out["config_fingerprint"], confirmed=True)
    assert not result["undone"] and len(env.edits) == 2


def test_undo_preserves_intervening_schedule_edit(env):
    approve(env)
    env.job["schedule"]["minutes"] = 180
    with pytest.raises(rhythm.RhythmError, match="newer schedule"):
        rhythm.undo("j1", expected_config_fingerprint=rhythm.configuration_fingerprint(env.job), confirmed=True)
    assert env.job["schedule"]["minutes"] == 180 and len(env.edits) == 1


def test_repeated_changes_keep_original_interval_until_undo(env):
    approve(env, 120)
    approve(env, 240)
    out = rhythm.undo("j1", expected_config_fingerprint=rhythm.configuration_fingerprint(env.job), confirmed=True)
    assert out["undone"] and env.job["schedule"]["minutes"] == 60


def test_failed_host_edit_retains_recovery_data_and_reports_failure(env, monkeypatch):
    monkeypatch.setattr(lifecycle, "_edit_job", lambda *a: (_ for _ in ()).throw(RuntimeError("host failed")))
    with pytest.raises(rhythm.RhythmError, match="did not confirm"):
        approve(env)
    assert env.job["schedule"]["minutes"] == 60
    assert store.get_setting("rhythm:j1")["original_schedule"]["minutes"] == 60
    assert not rhythm.report("j1")["control"]["active"]


def test_timeout_after_host_commit_is_recoverable_without_duplicate_edit(env, monkeypatch):
    original = lifecycle._edit_job

    def edit(*args):
        original(*args)
        raise TimeoutError("unknown outcome")

    fingerprint = rhythm.configuration_fingerprint(env.job)
    monkeypatch.setattr(lifecycle, "_edit_job", edit)
    with pytest.raises(rhythm.RhythmError):
        approve(env)
    assert rhythm.report("j1")["control"]["active"]
    result = approve(env, expected_config_fingerprint=fingerprint)
    assert result["already_applied"] and len(env.edits) == 1


def test_unknown_undo_outcome_is_recoverable_without_duplicate_edit(env, monkeypatch):
    approve(env)
    original = lifecycle._edit_job

    def edit(*args):
        original(*args)
        raise TimeoutError("unknown outcome")

    monkeypatch.setattr(lifecycle, "_edit_job", edit)
    with pytest.raises(rhythm.RhythmError, match="did not confirm Undo"):
        rhythm.undo("j1", expected_config_fingerprint=rhythm.configuration_fingerprint(env.job), confirmed=True)
    out = rhythm.undo("j1", expected_config_fingerprint=rhythm.configuration_fingerprint(env.job), confirmed=True)
    assert not out["undone"] and out["already_undone"] and len(env.edits) == 2
    assert env.job["schedule"]["minutes"] == 60


def test_idempotent_undo_without_owned_change_never_dispatches(env):
    out = rhythm.undo("j1", expected_config_fingerprint=rhythm.configuration_fingerprint(env.job), confirmed=True)
    assert not out["undone"] and not env.edits
    with pytest.raises(rhythm.RhythmError, match="confirmation"):
        rhythm.undo("j1", expected_config_fingerprint=out["config_fingerprint"])
