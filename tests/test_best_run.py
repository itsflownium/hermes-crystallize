"""Best-run strategy: for jobs Hermes does differently every run."""

from crystallize import analyze, lifecycle, steplist
from crystallize.history import Run, Step

PROMPT = ("Fetch the latest posts from https://hnrss.org/frontpage and "
          "https://simonwillison.net/atom/everything/, then summarize them.")


def _run(i, cost, steps):
    run = Run(execution_id=f"e{i}", job_id="j", job_name="morning-brief", session_id=f"s{i}",
              scheduled=True, started_ts=float(i), outcome="completed", cost_usd=cost, api_calls=5)
    run.steps = [Step(t, a, None, None, 500, "ok", 10) for t, a in steps]
    return run


def test_prompt_sources_finds_urls_and_repos():
    assert analyze.prompt_sources(PROMPT) == ["hnrss.org/frontpage", "simonwillison.net/atom/everything"]
    assert "nousresearch/hermes-agent" in analyze.prompt_sources("List issues on NousResearch/hermes-agent")


def test_wandering_runs_freeze_the_cheapest_complete_fetch():
    runs = [
        _run(1, 0.02, [("skill_view", {"name": "x"}),
                       ("terminal", {"command": "curl https://hnrss.org/frontpage"}),
                       ("execute_code", {"code": "parse()"}),
                       ("web_extract", {"urls": ["https://simonwillison.net/atom/everything/"]})]),
        _run(2, 0.01, [("web_extract", {"urls": ["https://hnrss.org/frontpage",
                                                 "https://simonwillison.net/atom/everything/"]}),
                       ("web_extract", {"urls": ["https://example.com/todays-story"]})]),
        _run(3, 0.005, [("web_extract", {"urls": ["https://hnrss.org/frontpage"]})]),  # misses a source
    ]
    a = analyze.analyze_runs(runs, PROMPT)
    assert a.ready and a.strategy == "best-run" and a.source_execution_id == "e2"
    assert [p.kind for p in a.steps] == ["frozen"]
    assert "todays-story" not in str(a.steps[0].args)  # follow-ups stay with Hermes
    r = steplist.build(a)
    assert r["strategy"] == "best-run" and r["source_execution_id"] == "e2"


def test_without_a_prompt_wandering_runs_are_not_ready():
    tools = ["terminal", "execute_code", "web_extract"]  # a different tool every run
    runs = [_run(i, 0.01, [(tools[i - 1], {"x": f"step {i}"})]) for i in range(1, 4)]
    a = analyze.analyze_runs(runs)
    assert not a.ready and a.strategy == "common-steps"


def test_best_run_routines_compare_against_their_source_run(monkeypatch):
    seen = []
    monkeypatch.setattr(lifecycle.store, "execution_traces", lambda eid: seen.append(eid) or [])
    lifecycle._reference_traces({"steps": {"strategy": "best-run", "source_execution_id": "e2"}}, "latest")
    lifecycle._reference_traces({"steps": {"strategy": "common-steps"}}, "latest")
    assert seen == ["e2", "latest"]
