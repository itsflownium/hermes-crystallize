"""Choosing whether a lower reasoning level is worth keeping."""

import pytest

from crystallize import tuning
from crystallize.history import Run, Step

ANSWER = "\n".join(f"{i}. item https://example.com/{i}" for i in range(5))


def _run(sid, cost, outcome="completed", reasoning=1000, output=1100, inp=500, cache=100):
    return Run(execution_id=sid, job_id="j", job_name="job", session_id=sid, scheduled=True, started_ts=1.0,
               outcome=outcome, cost_usd=cost, reasoning_tokens=reasoning, output_tokens=output,
               input_tokens=inp, cache_read_tokens=cache, finished=True, session_started=1.0)


BEFORE = [_run("b1", 0.02), _run("b2", 0.02)]


def _eval(answers, costs=(0.005, 0.005), outcome="completed"):
    trial = [_run("t1", costs[0], outcome), _run("t2", costs[1], outcome)]
    return tuning.evaluate(BEFORE, trial, {"b1": ANSWER, "b2": ANSWER, **answers})


def test_keeps_when_answers_hold_and_cost_falls():
    r = _eval({"t1": ANSWER, "t2": ANSWER})
    assert r["ok"] and r["before"] == pytest.approx(0.02) and r["after"] == pytest.approx(0.005)


@pytest.mark.parametrize("answers,costs,outcome,why", [
    ({"t1": ANSWER, "t2": ANSWER}, (0.019, 0.019), "completed", "wasn't cheaper"),
    ({"t1": "", "t2": ANSWER}, (0.005, 0.005), "completed", "no usable answer"),
    ({"t1": "[CRON_FAILURE]\nbroke", "t2": ANSWER}, (0.005, 0.005), "completed", "no usable answer"),
    ({"t1": "short", "t2": "short"}, (0.005, 0.005), "completed", "much shorter"),
    ({"t1": ANSWER, "t2": ANSWER}, (0.005, 0.005), "failed", "failed"),
])
def test_rejects_worse_or_no_cheaper(answers, costs, outcome, why):
    r = _eval(answers, costs, outcome)
    assert not r["ok"] and why in r["why"]


def test_rejects_lost_items_and_links():
    two_long_lines = ("x" * len(ANSWER) + "\n") * 2
    assert "lost items" in _eval({"t1": two_long_lines, "t2": two_long_lines})["why"]
    no_links = ANSWER.replace("https://example.com/", "number ")
    assert "links" in _eval({"t1": no_links, "t2": no_links})["why"]


def test_free_runs_compare_tokens_then_time():
    before = [_run("b1", 0.0, output=5000), _run("b2", 0.0, output=5000)]
    answers = {"b1": ANSWER, "b2": ANSWER, "t1": ANSWER, "t2": ANSWER}
    r = tuning.evaluate(before, [_run("t1", 0.0, output=1000), _run("t2", 0.0, output=1000)], answers)
    assert r["ok"] and r["metric"] == "tokens"
    assert not tuning.evaluate(before, [_run("t1", 0.0, output=4900), _run("t2", 0.0, output=4900)], answers)["ok"]


def test_no_price_counts_prompt_tokens_too():
    answers = {"b1": ANSWER, "b2": ANSWER, "t1": ANSWER, "t2": ANSWER}
    before = [_run("b1", None, output=500, inp=50000), _run("b2", None, output=500, inp=50000)]
    trial = [_run("t1", None, output=500, inp=5000), _run("t2", None, output=500, inp=5000)]
    r = tuning.evaluate(before, trial, answers)
    assert r["ok"] and r["metric"] == "tokens"


def test_unknown_price_is_not_treated_as_free():
    before = [_run("b1", 0.02, output=5000), _run("b2", 0.02, output=5000)]
    answers = {"b1": ANSWER, "b2": ANSWER, "t1": ANSWER, "t2": ANSWER}
    r = tuning.evaluate(before, [_run("t1", None, output=4900), _run("t2", None, output=4900)], answers)
    assert not r["ok"] and r["metric"] == "tokens"


def test_a_quality_failure_is_marked_so_the_next_level_gets_a_turn():
    assert _eval({"t1": "short", "t2": "short"})["kind"] == "quality"
    assert _eval({"t1": ANSWER, "t2": ANSWER}, (0.019, 0.019))["kind"] == "saving"


def _with_action(run, tool="send_message", status="ok"):
    run.steps = [Step(tool, {"text": "hi"}, None, None, 2, status, 5)]
    return run


def test_rejects_a_job_that_stops_its_regular_action():
    before = [_with_action(_run("b1", 0.02)), _with_action(_run("b2", 0.02))]
    answers = {"b1": ANSWER, "b2": ANSWER, "t1": ANSWER, "t2": ANSWER}
    kept = [_with_action(_run("t1", 0.005)), _with_action(_run("t2", 0.005))]
    assert tuning.evaluate(before, kept, answers)["ok"]
    dropped = [_with_action(_run("t1", 0.005)), _run("t2", 0.005)]
    assert "send_message" in tuning.evaluate(before, dropped, answers)["why"]
    failed = [_with_action(_run("t1", 0.005)), _with_action(_run("t2", 0.005), status="error")]
    assert "send_message" in tuning.evaluate(before, failed, answers)["why"]


def test_one_off_actions_are_not_required():
    before = [_with_action(_run("b1", 0.02)), _run("b2", 0.02), _run("b3", 0.02)]
    answers = {"b1": ANSWER, "b2": ANSWER, "b3": ANSWER, "t1": ANSWER, "t2": ANSWER}
    assert tuning.evaluate(before, [_run("t1", 0.005), _run("t2", 0.005)], answers)["ok"]


def test_nothing_new_answers_wait_unless_the_job_already_goes_silent():
    trial = [_run("t1", 0.001), _run("t2", 0.005)]
    answers = {"b1": ANSWER, "b2": ANSWER, "t1": "[SILENT]", "t2": ANSWER}
    assert [r.session_id for r in tuning.counted_runs(BEFORE, trial, answers)] == ["t2"]
    assert len(tuning.counted_runs(BEFORE, trial, {**answers, "b1": "[SILENT]"})) == 2


def test_settings_text_names_what_changed():
    assert tuning._settings({"level": "low", "toolsets": ["web"]}) == "reasoning 'low' + tools: web"
    assert tuning._settings({"level": None, "toolsets": []}) == "tools: none"
    text = tuning._settings({"level": "low", "toolsets": None, "clean": True, "hint": True, "new_only": True})
    assert "cleaned results" in text and "efficiency instruction" in text and "new items only" in text


def _with_tools(sid, cost, tools):
    r = _run(sid, cost)
    r.steps = [Step(t, {}, None, None, 1, "ok", 1) for t in tools]
    return r


def _choices(monkeypatch, runs, job=None, answered=True):
    monkeypatch.setattr(tuning.history, "load_runs", lambda name: runs)
    monkeypatch.setattr(tuning.history, "final_answers", lambda ids: {i: ANSWER for i in ids} if answered else {})
    monkeypatch.setattr(tuning.store, "routine_for_job", lambda job_id: None)
    names = {"web_extract": "web", "terminal": "terminal", "skill_view": "skills", "send_message": "messaging"}
    monkeypatch.setattr(tuning.modelinfo, "toolsets_for",
                        lambda tools: None if tools - set(names) else sorted({names[t] for t in tools}))
    return tuning._tool_choices({"id": "j", **(job or {})}, "job")


def test_narrow_tools_come_from_the_cheapest_good_run(monkeypatch):
    runs = [_with_tools("a", 0.02, ["terminal", "web_extract"]), _with_tools("b", 0.01, ["web_extract"])]
    assert _choices(monkeypatch, runs) == (["web"], ["terminal", "web"])


def test_skills_are_dropped_first_unless_the_job_attaches_them(monkeypatch):
    runs = [_with_tools("a", 0.01, ["skill_view", "web_extract"])]
    assert _choices(monkeypatch, runs) == (["web"], ["skills", "web"])
    assert _choices(monkeypatch, runs, {"skills": ["x"]}) == (["skills", "web"], ["skills", "web"])


def test_regular_actions_stay_in_the_narrow_tools(monkeypatch):
    acting = [_with_tools("a", 0.02, ["send_message", "web_extract"]), _with_tools("b", 0.02, ["send_message", "web_extract"]),
              _with_tools("c", 0.01, ["web_extract"])]
    assert "messaging" in _choices(monkeypatch, acting)[0]


def test_tools_are_left_alone_when_unsure(monkeypatch):
    assert _choices(monkeypatch, []) == (None, None)
    assert _choices(monkeypatch, [_with_tools("a", 0.01, ["odd_tool"])]) == (None, None)
    outside = [_with_tools("a", 0.01, ["terminal"])]
    assert _choices(monkeypatch, outside, {"enabled_toolsets": ["web"]}) == (None, None)


def test_stages_give_back_tools_then_reasoning():
    stages = tuning.plan_stages(["low", "medium"], ["web"], ["terminal", "web"])
    assert stages == [{"level": "low", "toolsets": ["web"]}, {"level": "low", "toolsets": ["terminal", "web"]},
                      {"level": "medium", "toolsets": ["terminal", "web"]}]
    assert tuning.plan_stages([], None, None) == [{"level": None, "toolsets": None}]


def test_close_results_get_more_runs():
    assert tuning._is_close({"kind": "saving", "before": 1.0, "after": 0.95})
    assert tuning._is_close({"kind": "saving", "before": 1.0, "after": 1.1})
    assert not tuning._is_close({"kind": "saving", "before": 1.0, "after": 1.5})
    assert not tuning._is_close({"kind": "quality", "before": 1.0, "after": 0.95})


def test_included_runs_compare_at_api_prices():
    def included(sid, api):
        r = _run(sid, 0.0)
        r.included, r.api_cost_usd = True, api
        return r
    answers = {"b1": ANSWER, "b2": ANSWER, "t1": ANSWER, "t2": ANSWER}
    before = [included("b1", 0.01), included("b2", 0.01)]
    r = tuning.evaluate(before, [included("t1", 0.004), included("t2", 0.004)], answers)
    assert r["ok"] and r["metric"] == "api cost"
    assert "API prices" in tuning.status_text({"status": "kept", "level": "low", "result": r})
    assert not tuning.evaluate(before, [included("t1", 0.0098), included("t2", 0.0098)], answers)["ok"]


def test_a_run_hermes_is_still_closing_is_not_judged(monkeypatch):
    closing = _run("a", 0.01, outcome="running")
    done = _run("b", 0.01)
    monkeypatch.setattr(tuning.history, "load_runs", lambda name: [closing, done])
    assert [r.session_id for r in tuning._finished_scheduled("job")] == ["b"]


def test_kept_settings_that_keep_failing_go_back(monkeypatch):
    saved = {}
    t = {"job_name": "job", "job_id": "j", "status": "kept", "finished_at": 0.5, "result": {"ok": True}}
    monkeypatch.setattr(tuning, "_restore", lambda x: saved.setdefault("restored", True))
    monkeypatch.setattr(tuning.store, "save_tuning", lambda n, d: saved.update(d))
    bad = [_run(str(i), 0.01, outcome="failed") for i in range(3)]
    monkeypatch.setattr(tuning, "_finished_scheduled", lambda n: bad)
    tuning._guard_kept(t)
    assert saved.get("restored") and saved["status"] == "reverted" and "failed" in saved["result"]["why"]


def test_one_good_run_keeps_the_settings(monkeypatch):
    t = {"job_name": "job", "job_id": "j", "status": "kept", "finished_at": 0.5}
    monkeypatch.setattr(tuning, "_restore", lambda x: pytest.fail("shouldn't restore"))
    monkeypatch.setattr(tuning, "_finished_scheduled", lambda n: [_run("a", 0.01, outcome="failed"), _run("b", 0.01), _run("c", 0.01, outcome="failed")])
    tuning._guard_kept(t)
    assert t["status"] == "kept"


def test_the_instruction_protects_actions_and_details():
    assert "every action" in tuning.EFFICIENCY_HINT and "links" in tuning.EFFICIENCY_HINT
