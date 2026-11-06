"""Tests for analyze.py using made-up runs (no Hermes needed)."""

from crystallize import analyze
from crystallize.history import Run, Step


def _run(i, steps, cost=0.004, api=6):
    run = Run(execution_id=f"e{i}", job_id="j1", job_name="morning-brief", session_id=f"s{i}",
              scheduled=True, started_ts=float(i), outcome="completed", cost_usd=cost, api_calls=api)
    run.steps = [Step(t, a, None, None, None, "ok", 10) for t, a in steps]
    return run


def _brief(i, parser="print(parse(x))"):
    return _run(i, [
        ("skill_view", {"name": "blogwatcher"}),
        ("terminal", {"command": f"curl -sL https://hnrss.org/frontpage > /tmp/feeds-{i}abc/hn.xml"}),
        ("web_extract", {"url": "https://simonwillison.net/atom/everything/"}),
        ("terminal", {"command": f"python3 -c '{parser}' # run 2026-11-0{i}"}),
    ])


def test_needs_three_runs():
    a = analyze.analyze_runs([_brief(1), _brief(2)])
    assert not a.ready and "at least 3" in a.reason


def test_classifies_each_kind():
    runs = [_brief(1, "a()"), _brief(2, "b()"), _brief(3, "c()")]
    a = analyze.analyze_runs(runs)
    kinds = [p.kind for p in a.steps]
    assert kinds == ["setup", "fill-in", "fixed", "varies"]
    assert "temp_path" in a.steps[1].fill_ins
    assert a.ready and a.same_shape_share == 1.0
    assert a.est_api_calls_after == 1.0
    assert a.est_cost_after_usd < a.avg_cost_usd


def test_side_effects_stay_with_agent():
    runs = [_run(i, [("terminal", {"command": "curl -s https://example.com"}),
                     ("send_message", {"to": "me", "text": f"hi {i}"})]) for i in range(1, 4)]
    a = analyze.analyze_runs(runs)
    assert [p.kind for p in a.steps] == ["fixed", "side"]
    assert a.est_api_calls_after == 2.0 and not a.agent_free


def test_when_unsure_a_step_counts_as_an_action():
    side = analyze._is_side_effect
    assert not side("terminal", {"command": "curl -s https://e.com | jq . 2>/dev/null"})
    for command in ("curl https://e.com > report.txt", "deploy.sh", "curl -F a=b https://e.com", "git push", "sed -i s/a/b/ f",
                    "find . -delete", "cat $(rm x)", "./send_report", "python3 send.py", "curl -XPOST https://e.com"):
        assert side("terminal", {"command": command}), command
    assert side("some_plugin_tool", {}) and side("mcp_x_get_or_create", {}) and side("browser_click", {})
    assert not side("mcp_github_list_issues", {}) and not side("web_extract", {})


def test_mutating_shell_is_side_effect():
    runs = [_run(i, [("terminal", {"command": "git push origin main"})]) for i in range(1, 4)]
    assert analyze.analyze_runs(runs).steps[0].kind == "side"


def test_extra_steps_are_tolerated():
    runs = [_brief(1, "a()"), _brief(2, "a()"), _brief(3, "a()")]
    runs[1].steps.insert(2, Step("terminal", {"command": "ls"}, None, None, None, "ok", 5))
    a = analyze.analyze_runs(runs)
    assert len(a.steps) == 4
    assert a.extra_steps_per_run > 0 and a.same_shape_share < 1.0


def test_fully_fixed_job_is_agent_free():
    runs = [_run(i, [("terminal", {"command": "curl -s -o /dev/null -w '%{http_code}' https://github.com"})])
            for i in range(1, 4)]
    a = analyze.analyze_runs(runs)
    assert a.agent_free and a.ready
