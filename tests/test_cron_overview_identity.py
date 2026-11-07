"""Cron cards must follow durable job IDs and currently available execution."""
import json

import pytest

from crystallize import analyze, cli, history, lifecycle, modelinfo, overview, store, tuning


def runs_for(job_id, name, cost=0.12):
    return [history.Run(f"{job_id}-{i}", job_id, name, f"s-{job_id}-{i}", True, float(i),
                        steps=[history.Step("web_extract", {"url": "https://example.invalid/source"},
                                            "current data", None, 12, "ok", 1)],
                        outcome="completed", cost_usd=cost, finished=True, session_started=float(i),
                        api_calls=6, model="main", provider="provider", reasoning_effort="high")
            for i in range(1, 4)]


def panel(monkeypatch, jobs, runs):
    def load(job=None, only_usable=False):
        return [r for r in runs if (job is None or job in (r.job_id, r.job_name))
                and (not only_usable or r.usable)]
    monkeypatch.setattr(history, "load_runs", load)
    monkeypatch.setattr(history, "running_job_ids", lambda: set())
    monkeypatch.setattr(lifecycle, "all_cron_jobs", lambda: jobs)
    monkeypatch.setattr(tuning, "refresh", lambda: None)
    monkeypatch.setattr(tuning, "cards", lambda: [])
    monkeypatch.setattr(modelinfo, "token_prices", lambda *args: None)
    monkeypatch.setattr(overview, "_chat_spent", lambda sid: None)
    return overview.build()


def test_same_named_jobs_keep_distinct_costs_and_cards(monkeypatch):
    jobs = [{"id": jid, "name": "same title", "state": "scheduled", "schedule": {"minutes": 60}}
            for jid in ("first", "second")]
    result = panel(monkeypatch, jobs, runs_for("first", "same title") + runs_for("second", "same title", 0.6))
    cards = {c["job_id"]: c for c in result["spotted"]}
    assert set(cards) == {"first", "second"}
    assert cards["first"]["cost_now"] == pytest.approx(0.12)
    assert cards["second"]["cost_now"] == pytest.approx(0.6)
    assert all(c["runs"] == 3 for c in cards.values())


def test_recreated_job_never_inherits_deleted_jobs_live_routine(monkeypatch):
    store.save_routine("old-routine", name="same title", source="cron", source_ref="deleted",
                       status="live", steps={"version": 1, "steps": []},
                       meta={"mode": "tool", "promoted_at": 1.5})
    result = panel(monkeypatch, [{"id": "replacement", "name": "same title", "state": "scheduled"}],
                   runs_for("deleted", "same title"))
    assert not result["live"]
    assert [c["job_id"] for c in result["finished"]] == ["deleted"]
    replacement = next(c for c in result["watching"] if c["job_id"] == "replacement")
    assert "routine_id" not in replacement and replacement["tune"] is None
    assert "first runs" in replacement["reason"]


def test_renamed_job_keeps_its_history_under_current_title(monkeypatch):
    result = panel(monkeypatch, [{"id": "stable", "name": "new title", "state": "scheduled"}],
                   runs_for("stable", "old title"))
    assert not result["finished"] and not result["watching"]
    assert [(c["job_id"], c["name"], c["runs"]) for c in result["spotted"]] == [("stable", "new title", 3)]


def test_job_id_that_is_another_jobs_name_does_not_merge_runs(monkeypatch):
    runs = runs_for("target", "own title") + runs_for("other", "target", 9)
    monkeypatch.setattr(history, "load_runs", lambda job=None, only_usable=False: runs)
    assert [r.execution_id for r in history.runs_for_job("target")] == [f"target-{i}" for i in range(1, 4)]


def test_unavailable_replay_does_not_publish_a_usable_savings_projection(monkeypatch):
    result = panel(monkeypatch, [{"id": "one", "name": "task", "state": "scheduled", "schedule": {"minutes": 60}}],
                   runs_for("one", "task"))
    card = result["spotted"][0]
    assert card["cost_after"] is None and card["saving_per_week"] is None
    assert result["could_save_usd_per_week"] is None
    assert card["projection"]["status"] == "unavailable"
    assert card["projection"]["requires"] == "verified_guarded_automatic_replay"


def test_cli_analysis_does_not_offer_unavailable_zero_ai_or_numeric_projection(monkeypatch, capsys):
    analysis = analyze.analyze_runs(runs_for("one", "task"))
    monkeypatch.setattr(analyze, "analyze_job", lambda job: analysis)
    cli._print_analysis("one", False)
    output = capsys.readouterr().out
    assert "Estimated:" not in output and "could run with no AI at all" not in output
    assert "Automatic replay is unavailable" in output and "--mode tool" in output
    cli._print_analysis("one", True)
    data = json.loads(capsys.readouterr().out)
    assert data["est_cost_after_usd"] is None and data["est_api_calls_after"] is None
    assert data["projection"]["status"] == "unavailable"


def test_deleted_job_baseline_never_reads_the_unscoped_ledger(monkeypatch):
    seen = []
    monkeypatch.setattr(tuning, "_finished_scheduled", lambda name: [])
    monkeypatch.setattr(lifecycle, "cron_job", lambda name: None)
    def ledger(job_id):
        seen.append(job_id)
        return []
    monkeypatch.setattr(history, "ledger_runs", ledger)
    assert tuning._split("deleted-id", 10) == ([], [])
    assert seen == ["deleted-id"]


def test_measured_tuning_uses_saved_job_identity_after_a_rename(monkeypatch):
    seen = []
    monkeypatch.setattr(tuning, "_split", lambda job, started: seen.append(job) or ([], []))
    assert tuning.measured("old title", {"job_id": "stable", "status": "kept", "started_at": 2}) is None
    assert seen == ["stable"]


def test_analysis_resolves_current_job_before_searching_a_reused_name(monkeypatch):
    runs = runs_for("deleted", "same title", 9) + runs_for("current", "same title")
    monkeypatch.setattr(lifecycle, "all_cron_jobs", lambda: [{"id": "current", "name": "same title"}])
    monkeypatch.setattr(history, "load_runs", lambda *args, **kwargs: runs)
    result = analyze.analyze_job("same title")
    assert result.job_id == "current" and result.runs_used == 3
    assert result.avg_cost_usd == pytest.approx(0.12)


def test_cli_does_not_offer_the_deleted_routine_for_a_recreated_job(monkeypatch):
    store.save_routine("old", name="same title", source="cron", source_ref="deleted", status="live",
                       steps={"steps": []}, meta={})
    monkeypatch.setattr(lifecycle, "all_cron_jobs", lambda: [{"id": "current", "name": "same title"}])
    assert cli._routine_for("same title") is None
    assert cli._routine_for("old")["source_ref"] == "deleted"


def test_a_foreign_name_collision_cannot_be_a_tuning_baseline(monkeypatch):
    mixed = runs_for("target", "own title") + runs_for("foreign", "target", 9)
    monkeypatch.setattr(lifecycle, "cron_job", lambda name: {"id": "target", "name": "own title"})
    monkeypatch.setattr(tuning, "_finished_scheduled", lambda name: mixed)
    monkeypatch.setattr(history, "ledger_runs", lambda job_id: [])
    before, trial = tuning._split("target", 10)
    assert len(before) == 3 and all(r.job_id == "target" for r in before) and not trial


def test_foreign_name_collision_cannot_become_a_matching_observation(monkeypatch):
    foreign = runs_for("foreign", "target")
    monkeypatch.setattr(history, "load_runs", lambda *args, **kwargs: foreign)
    routine = {"id": "r", "source_ref": "target", "meta": {"created_at": 0}}
    monkeypatch.setattr(lifecycle, "record_test", lambda *args, **kwargs: pytest.fail("Foreign observation accepted"))
    assert lifecycle.observe_host_runs(routine) is None
