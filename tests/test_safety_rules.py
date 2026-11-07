"""The rules that keep Crystallize safe: no repeated actions, no stale answers, stable routines,
fixed baselines, working Undo and honest savings."""

import pytest

from crystallize import analyze, history, lifecycle, overview, prerun, store, tuning
from crystallize.history import Run, Step


def _run(i, steps, started=None, cost=0.01, outcome="completed"):
    run = Run(execution_id=f"e{i}", job_id="j", job_name="job", session_id=f"s{i}", scheduled=True,
              started_ts=started or float(i), outcome=outcome, cost_usd=cost, api_calls=3, session_started=started or float(i),
              model="same-test-model", provider="same-test-provider", reasoning_effort="high")
    run.steps = [Step(t, a, preview, None, 500, "ok", 5) for t, a, preview in steps]
    return run


# ------------------------------------------------ repeated actions

@pytest.mark.parametrize("code", [
    "from pathlib import Path; Path('a').write_text('x')",
    "open('a', 'w').write('x')",
    "open('a', mode='a')",
    "import requests; requests.post('https://e.com')",
    "import os; os.remove('a')",
    "import subprocess; subprocess.run(['ls'])",
    "import urllib.request as u; u.urlopen('https://e.com', data=b'x')",
    "getattr(__builtins__, 'open')('a', 'w')",
    "from hermes_tools import terminal; terminal('ls')",
    "not python at all (",
])
def test_python_that_might_write_is_an_action(code):
    assert analyze._is_side_effect("execute_code", {"code": code})


@pytest.mark.parametrize("code", [
    "import json, re; print(json.dumps({'a': 1}))",
    "import requests; print(requests.get('https://e.com').text)",
    "print(open('a').read())",
    "print('a b'.replace('a', 'c'))",
    "from hermes_tools import web_extract; print(web_extract(['https://e.com']))",
])
def test_python_that_only_reads_can_repeat(code):
    assert not analyze._is_side_effect("execute_code", {"code": code})


@pytest.mark.parametrize("command", [
    "curl -sXPOST https://e.com", "curl -sd a=b https://e.com", "curl --data-binary @f https://e.com",
    "curl --request PUT https://e.com", "curl -o report.txt https://e.com", "curl -so report.txt https://e.com",
    "wget https://e.com/f", "wget -qO- --post-data=a https://e.com", "http POST https://e.com",
    "python3 -c \"open('a','w')\"", "bash -c 'rm a'", "sort -o out in", "uniq in out", "date -s now",
])
def test_shell_that_might_write_is_an_action(command):
    assert analyze._is_side_effect("terminal", {"command": command})


@pytest.mark.parametrize("command", [
    "curl -sL https://e.com | jq .", "curl -X GET https://e.com", "curl -so /tmp/x https://e.com", "wget -qO- https://e.com",
    "python3 -c 'import json; print(1)'", "git log -5", "sort a | uniq -c",
])
def test_shell_that_only_reads_can_repeat(command):
    assert not analyze._is_side_effect("terminal", {"command": command})


# ------------------------------------------------ change detection

def _list(*rows):
    return "\n".join(f"{title}\nLink: https://e.com/{key}\nPrice {price}" for key, title, price in rows)


ITEMS = [(i, f"Item {i}", f"{i}0.50") for i in range(1, 9)]


def test_a_changed_price_is_a_change():
    assert prerun.fingerprint("Total 10.50") != prerun.fingerprint("Total 99.95")


def test_an_update_at_the_same_link_is_sent():
    seen = prerun._items(_list(*ITEMS))
    changed = ITEMS[:2] + [(3, "Item 3", "99.95")] + ITEMS[3:]
    text = prerun._new_only_text(_list(*changed), seen, "Old answer")
    assert "99.95" in text and "https://e.com/3" in text and "https://e.com/5" not in text and "Old answer" in text


def test_a_deleted_item_is_reported():
    seen = prerun._items(_list(*ITEMS))
    text = prerun._new_only_text(_list(*ITEMS[:-1], (9, "Item 9", "1.00")), seen, "Old answer")
    assert "Item 9" in text and "No longer listed" in text and "https://e.com/8" in text


def test_only_deletions_are_explicit_and_a_changed_format_sends_everything():
    seen = prerun._items(_list(*ITEMS))
    delta = prerun._new_only_text(_list(*ITEMS[:-1]), seen, "Old answer")
    assert "No longer listed" in delta and "https://e.com/8" in delta and "Old answer" in delta
    other = "\n".join(f"{t} ({p}) in stock https://e.com/{k}" for k, t, p in ITEMS)
    assert prerun._new_only_text(other, seen, "Old answer") is None


def test_a_job_with_no_item_record_yet_sends_everything():
    assert prerun._new_only_text(_list(*ITEMS), {}, "Old answer") is None


# ------------------------------------------------ dynamic dependencies

def _ready(steps_for_run):
    return analyze.analyze_runs([_run(i, steps_for_run(i), started=1793900000.0 + i * 86400) for i in range(1, 5)])


def test_stable_independent_reads_can_become_a_routine():
    a = _ready(lambda i: [("web_extract", {"url": "https://e.com/feed"}, f"story {i}"), ("web_extract", {"url": "https://e.com/news"}, "x")])
    assert a.ready


def test_a_step_using_an_id_from_an_earlier_step_cant():
    a = _ready(lambda i: [("web_search", {"q": "latest"}, "found build-4821"), ("web_extract", {"url": "https://e.com/build-4821"}, "x")])
    assert not a.ready and "earlier step" in a.reason


def test_a_changing_step_cant():
    a = _ready(lambda i: [("web_extract", {"url": f"https://e.com/page-{i * 7}"}, "x")])
    assert not a.ready and "changes from run to run" in a.reason


def test_a_date_range_cant():
    a = _ready(lambda i: [("web_extract", {"url": f"https://e.com/?from=2026-10-{i:02d}&to=2026-11-{i:02d}"}, "x")])
    assert not a.ready and "date" in a.reason


def test_a_date_from_another_day_cant_and_today_can():
    day = lambda i, shift: analyze._dt.datetime.fromtimestamp(1793900000.0 + (i + shift) * 86400, analyze._dt.timezone.utc).date().isoformat()  # noqa: E731
    assert not _ready(lambda i: [("web_extract", {"url": f"https://e.com/?d={day(i, -9)}"}, "x")]).ready
    assert _ready(lambda i: [("web_extract", {"url": f"https://e.com/?d={day(i, 0)}"}, "x")]).ready


def test_temporary_paths_and_ids_cant():
    a = _ready(lambda i: [("terminal", {"command": f"curl -s https://e.com > /tmp/run-{i}abc/x"}, "x")])
    assert not a.ready and "temporary paths" in a.reason


# ------------------------------------------------ fixed baseline

def _answered(run):
    return run


def test_a_required_action_stays_required_through_later_baselines():
    send = ("send_message", {"text": "hi"}, None)
    weak = [_run(i, [("web_search", {}, None)]) for i in (1, 2)]
    trial = [_run(i, [("web_search", {}, None)], cost=0.001) for i in (3, 4)]
    answers = {r.session_id: "An answer with some real content" for r in weak + trial}
    assert tuning.evaluate(weak, trial, answers)["ok"]
    result = tuning.evaluate(weak, trial, answers, ["send_message"])
    assert not result["ok"] and "send_message" in result["why"]
    assert send  # the action the job used to take


def test_a_rejected_stage_never_joins_the_baseline(monkeypatch):
    old, stage = _run(1, [], started=10.0), _run(2, [], started=30.0)
    monkeypatch.setattr(tuning, "_split", lambda name, at: ([old, stage], []))
    assert tuning._trial_runs({"job_name": "j", "started_at": 50.0, "baseline_until": 20.0})[0] == [old]
    assert tuning._trial_runs({"job_name": "j", "started_at": 50.0, "first_started_at": 20.0})[0] == [old]


# ------------------------------------------------ rollback

@pytest.fixture
def job(monkeypatch):
    calls, saved = [], {}
    found = {"id": "j1", "name": "job", "prompt": "Do it", "enabled_toolsets": ["web", "terminal"], "reasoning_effort": "high", "state": "scheduled"}
    monkeypatch.setattr(tuning, "_job_or_error", lambda name: found)
    monkeypatch.setattr(lifecycle, "cron_job", lambda name: found)
    monkeypatch.setattr(store, "get_tuning", lambda name: saved.get(name))
    monkeypatch.setattr(store, "save_tuning", lambda name, data: (saved.__setitem__(name, dict(data)), calls.append(("saved", data["status"]))))
    monkeypatch.setattr(tuning, "_split", lambda name, at: ([_run(i, []) for i in range(1, 5)], []))
    monkeypatch.setattr(tuning, "_tool_choices", lambda f, n: (["web"], None))
    monkeypatch.setattr(tuning, "lowers_reasoning", lambda: False)
    monkeypatch.setattr(tuning, "_set_new_only", lambda job_id, on: False)
    def edit(job_id, *flags):
        calls.append(("edit", flags))
        for flag, key in (("--prompt", "prompt"), ("--reasoning-effort", "reasoning_effort")):
            if flag in flags:
                found[key] = flags[flags.index(flag) + 1]
    def toolsets(job_id, tools):
        calls.append(("tools", tools))
        found["enabled_toolsets"] = tools
    monkeypatch.setattr(lifecycle, "_edit_job", edit)
    monkeypatch.setattr(lifecycle, "set_toolsets", toolsets)
    return calls, saved, found


def test_recovery_data_is_saved_before_anything_changes(job):
    calls, saved, _ = job
    tuning.start("job")
    assert calls[0] == ("saved", "starting") and saved["job"]["status"] == "trial"
    assert saved["job"]["previous_toolsets"] == ["web", "terminal"]


def test_a_partial_failure_puts_everything_back(job, monkeypatch):
    calls, saved, found = job

    def fail_on_prompt(job_id, *flags):
        calls.append(("edit", flags))
        found["prompt"] = flags[flags.index("--prompt") + 1]
        if tuning.EFFICIENCY_HINT in " ".join(flags):
            # Hermes applied the prompt before returning an error.
            raise lifecycle.LifecycleError("nope")

    monkeypatch.setattr(lifecycle, "_edit_job", fail_on_prompt)
    with pytest.raises(lifecycle.LifecycleError, match="old settings are back"):
        tuning.start("job")
    assert calls[-2] == ("edit", ("--prompt", "Do it"))
    assert found["prompt"] == "Do it" and found["reasoning_effort"] == "high"
    assert found["enabled_toolsets"] == ["web", "terminal"] and not any(call[0] == "tools" for call in calls)
    assert saved["job"]["status"] == "reverted"


def test_a_restore_that_fails_is_tried_again_and_undo_still_works(job, monkeypatch):
    calls, saved, found = job
    healthy = lifecycle._edit_job

    def broken(job_id, *flags):
        prompt = flags[flags.index("--prompt") + 1]
        if tuning.EFFICIENCY_HINT in prompt:
            found["prompt"] = prompt  # apply succeeded before the host failed
        raise lifecycle.LifecycleError("still down")

    monkeypatch.setattr(lifecycle, "_edit_job", broken)
    with pytest.raises(lifecycle.LifecycleError):
        tuning.start("job")
    assert saved["job"].get("needs_restore") and saved["job"]["status"] == "starting"
    assert tuning.EFFICIENCY_HINT in found["prompt"]
    monkeypatch.setattr(lifecycle, "_edit_job", healthy)
    tuning._finish_restore(saved["job"])
    assert saved["job"]["status"] == "reverted" and not saved["job"].get("needs_restore")
    assert calls[-2] == ("edit", ("--prompt", "Do it"))
    assert found["prompt"] == "Do it" and found["reasoning_effort"] == "high"
    tuning.start("job")
    assert saved["job"]["status"] == "trial" and tuning.EFFICIENCY_HINT in found["prompt"]
    tuning.undo("job")
    assert saved["job"]["status"] == "undone" and found["prompt"] == "Do it"
    assert found["reasoning_effort"] == "high" and found["enabled_toolsets"] == ["web", "terminal"]


def test_a_stage_that_gives_all_tools_back_really_does(job):
    calls, _, _ = job
    tuning._apply_stage("j1", {"level": None, "toolsets": None}, {"tools_changed": True, "previous_toolsets": ["web", "terminal"]})
    assert calls == [("tools", ["web", "terminal"])]


def test_a_crashed_promote_is_put_back(monkeypatch):
    routine = {"id": "cr_1", "name": "job", "status": "testing", "source_ref": "j1",
               "meta": {"promoting": {"job_id": "j1", "original_prompt": "Do it", "own_script": None,
                                      "mode": "no_ai", "installed_script": "crystallize/cr_1.py", "at": 1.0}}}
    edits, updates = [], []
    monkeypatch.setattr(store, "list_routines", lambda: [routine])
    monkeypatch.setattr(store, "update_routine", lambda rid, **kw: updates.append(kw))
    monkeypatch.setattr(lifecycle, "cron_job", lambda ref: {"id": "j1", "prompt": lifecycle.prerun_prompt("cr_1", "Do it"),
                                                         "script": "crystallize/cr_1.py", "no_agent": True})
    monkeypatch.setattr(lifecycle, "_edit_job", lambda job_id, *flags: edits.append(flags))
    monkeypatch.setattr(prerun, "remove_script", lambda rid: None)
    lifecycle.recover_promotions()
    assert edits[0][:2] == ("--prompt", "Do it") and "--agent" in edits[0]
    assert "promoting" not in updates[0]["meta"]


# ------------------------------------------------ savings

def test_a_dearer_run_counts_against_savings():
    runs = [_run(1, [], cost=0.01), _run(2, [], cost=0.05)]
    assert overview._saved_since(runs, 0.02) == pytest.approx(0.01 - 0.03)


def test_a_failed_run_loses_all_it_cost():
    runs = [_run(1, [], cost=0.01), _run(2, [], cost=0.015, outcome="failed")]
    assert overview._saved_since(runs, 0.02) == pytest.approx(0.01 - 0.015)
    assert history.net_saving(runs[1], 0.02, 0.015) == pytest.approx(-0.015)


def test_failed_live_runs_stay_in_spend_totals(monkeypatch):
    runs = [_run(1, [], started=100.0, cost=0.01), _run(2, [], started=200.0, cost=0.03, outcome="failed"),
            _run(3, [], started=300.0, cost=9.0, outcome="running")]
    monkeypatch.setattr(history, "load_runs", lambda name=None, only_usable=False: runs)
    monkeypatch.setattr(overview, "_skipped_runs", lambda meta, name: [])
    live = overview._live_runs("j", {"promoted_at": 50.0})
    assert [r.outcome for r in live] == ["completed", "failed"]
    assert overview.period_rows(live, 0.02, live, now=400.0)[0]["spent_usd"] == pytest.approx(0.04)
