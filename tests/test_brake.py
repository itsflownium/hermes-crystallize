"""Runaway Brake: limits come from each job's own runs, and only runaway read-only runs stop."""

import pytest

from crystallize import brake, history, store
from crystallize.history import Run, Step


def _run(i, tokens, steps=None, outcome="completed"):
    run = Run(execution_id=f"e{i}", job_id="j", job_name="job", session_id=f"s{i}", scheduled=True,
              started_ts=float(i), outcome=outcome, input_tokens=tokens, output_tokens=0)
    run.steps = steps or []
    return run


@pytest.fixture
def jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    settings, stops = {brake.SETTING: True}, []
    runs = [_run(i, 1000 + i) for i in range(6)]
    monkeypatch.setattr(store, "get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(store, "set_setting", settings.__setitem__)
    monkeypatch.setattr(store, "brake_stopped_sessions", lambda name: {s[0] for s in stops})
    monkeypatch.setattr(store, "add_brake_stop", lambda *a: stops.append(a))
    monkeypatch.setattr(history, "load_runs", lambda name=None, only_usable=False: runs)
    brake._used.clear()
    brake._limits.clear()
    return runs, stops, settings


def test_the_limit_comes_from_the_job_itself(jobs):
    found = brake.plan("job")
    assert found["normal"] == 1002 and found["limit"] == 3006


def test_a_large_normal_run_raises_the_limit(jobs):
    jobs[0].append(_run(9, 5000))
    assert brake.plan("job")["limit"] == 7500


def test_stays_out_of_the_way_until_the_limit(jobs):
    brake.note_usage("now", {"total_tokens": 3000})
    assert brake.check("job", "now") is None and not jobs[1]


def test_a_runaway_run_is_stopped_and_told_to_answer(jobs):
    brake.note_usage("now", {"total_tokens": 2000})
    brake.note_usage("now", {"prompt_tokens": 1500, "output_tokens": 500})
    block = brake.check("job", "now")
    assert block["action"] == "block" and "stopped early" in block["message"]
    assert jobs[1][0][:2] == ("now", "job")


def test_off_means_nothing_is_stopped(jobs):
    jobs[2][brake.SETTING] = False
    brake.note_usage("now", {"total_tokens": 99999})
    assert brake.check("job", "now") is None


def test_jobs_that_act_are_never_stopped(jobs):
    jobs[0][-1].steps = [Step("send_message", {"text": "hi"}, None, None, 2, "ok", 5)]
    found = brake.plan("job")
    assert found["limit"] is None and "sends or changes" in found["why"]
    brake.note_usage("now", {"total_tokens": 99999})
    assert brake.check("job", "now") is None


def test_too_little_history_has_no_limit(jobs):
    del jobs[0][2:]
    assert brake.plan("job")["limit"] is None


def test_failed_and_stopped_runs_dont_count_as_normal(jobs):
    jobs[0].append(_run(7, 90000, outcome="failed"))
    jobs[0].append(_run(8, 90000))
    jobs[1].append(("s8", "job", 90000, 3009, 1003))
    assert brake.plan("job")["normal"] == 1002
