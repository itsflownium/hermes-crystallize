"""Step lists, the runner's checks and test-run comparison."""

import json
import ntpath

import pytest

from crystallize import analyze, cli, lifecycle, runner, steplist
from crystallize.history import Run, Step


def _run(i, steps, cost=0.004, api=6):
    run = Run(execution_id=f"e{i}", job_id="j1", job_name="site-check", session_id=f"s{i}",
              scheduled=True, started_ts=float(i), outcome="completed", cost_usd=cost, api_calls=api)
    run.steps = [Step(t, a, None, None, n, "ok", 10) for t, a, n in steps]
    return run


def _analysis(cmd="curl -s https://github.com", tool="terminal"):
    return analyze.analyze_runs([_run(i, [(tool, {"command": cmd}, 120)]) for i in range(1, 4)])


def test_build_routine_has_checks():
    r = steplist.build(_analysis())
    assert r["steps"][0]["tool"] == "terminal"
    assert r["steps"][0]["check"]["min_len"] == 40
    assert r["baseline"]["cost_usd"] == pytest.approx(0.004)


def test_side_effect_job_is_refused():
    with pytest.raises(steplist.NotCrystallizable):
        steplist.build(_analysis(cmd="git push origin main"))


def test_fill_replaces_temp_paths_posix_and_windows():
    posix = steplist.fill({"command": "curl x > /tmp/run-1abc/hn.xml"}, ["temp_path"])
    assert "/tmp/run-1abc" not in posix["command"] and posix["command"].endswith("hn.xml")
    win = steplist.fill({"command": "curl x > C:\\Users\\a\\AppData\\Local\\Temp\\run1\\hn.xml"}, ["temp_path"])
    assert "run1" not in win["command"] and win["command"].endswith("hn.xml")
    json.dumps(win)  # still valid JSON-able text


def test_step_failure_detects_errors_and_short_output():
    assert runner.step_failure(json.dumps({"output": "", "exit_code": 1, "error": None}))
    assert runner.step_failure(json.dumps({"error": "Unknown tool: terminal"}))
    assert runner.step_failure(json.dumps({"output": "ok", "exit_code": 0}), {"min_len": 10})
    assert runner.step_failure(json.dumps({"output": "x" * 50, "exit_code": 0}), {"min_len": 10}) is None


def test_runner_stops_at_first_failing_step(monkeypatch):
    monkeypatch.setattr(runner, "RETRY_WAIT_S", 0)
    routine = {"steps": [
        {"tool": "terminal", "args": {"command": "a"}, "fill_ins": [], "check": {"min_len": 1}},
        {"tool": "terminal", "args": {"command": "b"}, "fill_ins": [], "check": {"min_len": 1}},
    ]}
    calls = []

    def dispatch(name, args):
        calls.append(args["command"])
        return json.dumps({"output": "", "exit_code": 2, "error": None})

    out = runner.run(routine, dispatch)
    assert not out["ok"] and out["failed_step"] == 1 and calls == ["a", "a"]


def test_runner_retries_a_step_once(monkeypatch):
    monkeypatch.setattr(runner, "RETRY_WAIT_S", 0)
    routine = {"steps": [{"tool": "web_extract", "args": {"urls": ["x"]}, "fill_ins": [], "check": {"min_len": 20}}]}
    answers = [json.dumps({"results": []}), json.dumps({"output": "y" * 50})]
    calls = []

    def dispatch(name, args):
        calls.append(name)
        return answers[len(calls) - 1]

    out = runner.run(routine, dispatch)
    assert out["ok"] and len(calls) == 2 and "y" * 50 in out["outputs"][0]["output"]


def test_runner_retries_after_an_exception(monkeypatch):
    monkeypatch.setattr(runner, "RETRY_WAIT_S", 0)
    routine = {"steps": [{"tool": "terminal", "args": {"command": "a"}, "fill_ins": [], "check": {"min_len": 1}}]}
    calls = []

    def dispatch(name, args):
        calls.append(name)
        if len(calls) == 1:
            raise RuntimeError("flaky import")
        return json.dumps({"output": "fine", "exit_code": 0})

    assert runner.run(routine, dispatch)["ok"] and len(calls) == 2


def test_history_label_for_a_run_with_no_tool_calls():
    run = _run(1, [])
    assert "not used" not in cli._run_flag(run) and "straight from its data" in cli._run_flag(run)
    run.scheduled = False
    assert "manual run" in cli._run_flag(run)
    assert cli._run_flag(_run(2, [("terminal", {"command": "a"}, 10)])) == ""


def test_same_shape_allows_changing_live_data():
    a = json.dumps({"output": "github.com -> HTTP 200, 82 ms", "exit_code": 0, "error": None})
    b = json.dumps({"output": "github.com -> HTTP 200, 97 ms", "exit_code": 0, "error": None})
    assert runner.same_shape(a, b, len(b))[0]
    assert not runner.same_shape(json.dumps({"output": "x", "exit_code": 7, "error": None}), b, len(b))[0]


def test_compare_aligns_routine_steps_with_real_run():
    routine = {"steps": {"steps": [{"tool": "terminal", "args": {}, "fill_ins": [], "check": {}}]}}
    out = json.dumps({"output": "github.com -> HTTP 200, 82 ms", "exit_code": 0, "error": None})
    result = {"ok": True, "outputs": [{"step": 1, "tool": "terminal", "output": out}]}
    traces = [{"tool_name": "skill_view", "result_preview": "{}", "result_len": 2},
              {"tool_name": "terminal", "result_preview": out, "result_len": len(out)}]
    assert lifecycle.compare(routine, result, traces)[0]
    assert not lifecycle.compare(routine, result, traces[:1])[0]


def test_crystallized_prompt_keeps_original_for_fallback():
    p = lifecycle.crystallized_prompt("cr_1", "Check github.com")
    assert lifecycle.MARKER in p and "crystal_run" in p and p.endswith("Check github.com")


def test_routine_stops_at_the_first_action():
    plans = [analyze.StepPlan(position=0, tool_name="web_extract", kind="fixed", args={"urls": ["u"]}, result_lens=[300]),
             analyze.StepPlan(position=1, tool_name="send_message", kind="side", args={"text": "t"}, result_lens=[10]),
             analyze.StepPlan(position=2, tool_name="terminal", kind="fixed", args={"command": "c"}, result_lens=[90])]
    a = _analysis()
    a.steps, a.ready = plans, True
    built = steplist.build(a)
    assert [s["tool"] for s in built["steps"]] == ["web_extract"] and built["leaves_to_ai"]


def test_an_action_first_leaves_nothing_to_fetch():
    a = _analysis()
    a.steps = [analyze.StepPlan(position=0, tool_name="send_message", kind="side", args={"text": "t"}, result_lens=[10])]
    a.ready = True
    with pytest.raises(steplist.NotCrystallizable):
        steplist.build(a)
