"""The panel shows what each cron job is really doing."""

import pytest

from crystallize import overview
from crystallize.history import Run


def test_cron_state_follows_hermes():
    job = {"id": "j", "state": "scheduled", "enabled": True, "repeat": {"times": 5, "completed": 2},
           "schedule_display": "every 5m", "next_run_at": "t"}
    state = overview.cron_state(job, set())
    assert state["status"] == "scheduled" and state["runs_done"] == 2 and state["runs_total"] == 5
    assert overview.cron_state(job, {"j"})["status"] == "running"
    assert overview.cron_state({**job, "state": "paused"}, set())["status"] == "paused"
    assert overview.cron_state({**job, "state": "completed", "enabled": False}, {"j"})["status"] == "finished"
    assert overview.cron_state(None, set()) == {"status": "finished", "gone": True}


def test_last_error_only_when_the_last_run_failed():
    job = {"id": "j", "state": "scheduled", "last_status": "ok", "last_error": "old"}
    assert overview.cron_state(job, set())["last_error"] is None
    assert overview.cron_state({**job, "last_status": "error"}, set())["last_error"] == "old"


def test_weekly_runs_come_from_the_job_and_stop_when_it_does():
    assert overview._schedule_runs_per_week({"schedule": {"minutes": 60}}) == 168
    assert overview._schedule_runs_per_week({"schedule": {"kind": "cron"}}) is None
    assert overview._capped_per_week(168, {"status": "scheduled"}) == 168
    assert overview._capped_per_week(168, {"status": "scheduled", "runs_total": 5, "runs_done": 3}) == 2
    assert overview._capped_per_week(168, {"status": "finished"}) == 0
    assert overview._capped_per_week(None, {"status": "scheduled"}) is None


def test_lookups_are_reused_then_refreshed(monkeypatch):
    from crystallize import modelinfo

    calls = []
    monkeypatch.setattr(modelinfo, "_lookups", {})
    fetch = lambda: calls.append(1) or len(calls)  # noqa: E731
    assert modelinfo._cached("k", fetch) == 1 and modelinfo._cached("k", fetch) == 1
    monkeypatch.setattr(modelinfo, "_LOOKUP_TTL_S", -1.0)
    assert modelinfo._cached("k", fetch) == 2


NOW = 100 * 86400.0


def _run(days_ago, cost=0.01, api=None, tokens=1000, model="m"):
    r = Run(execution_id=str(days_ago), job_id="j", job_name="job", session_id="s", scheduled=True,
            started_ts=NOW - days_ago * 86400, cost_usd=cost, input_tokens=tokens, duration_s=10.0, model=model)
    r.api_cost_usd = api
    r.outcome = "completed"
    return r


def test_time_ranges_add_up():
    runs = [_run(0.5), _run(3), _run(20), _run(60)]
    rows = {r["key"]: r for r in overview.period_rows(runs, now=NOW)}
    assert [rows[k]["runs"] for k in ("24h", "7d", "30d", "all")] == [1, 2, 3, 4]
    assert rows["7d"]["spent_usd"] == pytest.approx(0.02) and rows["7d"]["tokens"] == 2000


def test_a_longer_range_only_shows_if_it_adds_runs():
    only_today = overview.period_rows([_run(0.1), _run(0.2)], now=NOW)
    assert [r["key"] for r in overview.visible_periods(only_today)] == ["24h"]
    this_week = overview.period_rows([_run(3)], now=NOW)
    assert [r["key"] for r in overview.visible_periods(this_week)] == ["7d"]
    spread = overview.period_rows([_run(0.1), _run(3), _run(20), _run(60)], now=NOW)
    assert [r["key"] for r in overview.visible_periods(spread)] == ["24h", "7d", "30d", "all"]
    assert overview.visible_periods(overview.period_rows([], now=NOW)) == []


def test_figures_with_nothing_behind_them_stay_empty():
    row = overview.period_rows([_run(1, cost=None, tokens=None)], now=NOW)[1]
    assert row["spent_usd"] is None and row["api_cost_usd"] is None and row["tokens"] is None
    assert row["saved_usd"] is None and row["free_runs"] is None
    plan = overview.period_rows([_run(1, cost=0.0, api=0.25)], now=NOW)[1]
    assert plan["spent_usd"] == 0.0 and plan["api_cost_usd"] == 0.25


def test_savings_only_count_runs_after_promote_and_totals_add_jobs():
    before, after = _run(2, cost=0.01), _run(1, cost=0.002)
    rows = overview.period_rows([before, after], baseline=0.01, after=[after], now=NOW)
    assert rows[1]["saved_usd"] == pytest.approx(0.008)
    total = overview.total_periods([rows, overview.period_rows([_run(1)], now=NOW)])
    assert total[1]["runs"] == 3 and total[1]["saved_usd"] == pytest.approx(0.008)
    assert total[1]["spent_usd"] == pytest.approx(0.022)


PRICES = {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write": 1.0}


def test_cost_parts_show_where_the_money_goes():
    r = _run(1, tokens=100)
    r.output_tokens, r.reasoning_tokens = 50, 25
    parts = overview.cost_parts([r], PRICES)
    assert parts["prompt"] == pytest.approx(100 / 200) and parts["thinking"] == pytest.approx(50 / 200)
    assert parts["answer"] == pytest.approx(50 / 200)
    assert overview.cost_parts([r], None) is None and overview.cost_parts([], PRICES) is None


def _states(routine, tune, card=None, possible=False, can=False):
    rungs = overview.ladder(routine, tune, card or {}, routine_possible=possible, can_tune=can)
    return {r["key"]: r["state"] for r in rungs}


def test_ladder_follows_the_job():
    fresh = _states(None, None, possible=True, can=True)
    assert fresh["fetch"] == "available" and fresh["calls"] == "available" and fresh["no_ai"] == "n/a"
    trial = _states(None, {"status": "trial", "hint": True, "clean": True, "level": "low", "toolsets": ["web"]})
    assert trial["calls"] == "trying" and trial["tools"] == "trying"
    kept = _states(None, {"status": "kept", "hint": True, "clean": True, "level": None, "toolsets": None})
    assert kept["clean"] == "on" and "reasoning" not in kept
    live = {"status": "live", "meta": {"mode": "prerun"}, "steps": {}}
    on = _states(live, None, {"when_unchanged": "repeat", "no_ai_fit": True})
    assert on["fetch"] == "on" and on["unchanged"] == "on" and on["no_ai"] == "available"


def test_a_job_that_acts_never_offers_no_ai_savings_on_data():
    acting = {"status": "live", "meta": {"mode": "prerun"}, "steps": {"leaves_to_ai": True}}
    states = _states(acting, {"status": "kept", "new_only": True}, {"when_unchanged": "run"})
    assert states["unchanged"] == "n/a" and states["new"] == "n/a" and states["no_ai"] == "n/a"
