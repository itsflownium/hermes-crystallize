"""The pre-run step: cleaning data, spotting unchanged runs, and the no-AI check."""

import json
import os
import subprocess
import sys
import time

from crystallize import analyze, history, lifecycle, prerun, store
from crystallize.history import Run, Step


def test_json_loses_link_fields_and_empty_values():
    issue = {"url": "https://api.github.com/repos/x/y/issues/1", "html_url": "https://github.com/x/y/issues/1",
             "labels_url": "https://api.github.com/x", "node_id": "abc", "title": "Bug", "number": 1,
             "user": {"login": "pat", "id": 9, "avatar_url": "a", "type": "User", "site_admin": False},
             "assignee": None, "labels": [], "reactions": {"url": "u", "total_count": 0, "+1": 0},
             "body": "x" * 5000}
    out = json.loads(prerun.clean_text(json.dumps(issue)))
    assert out["html_url"] and out["title"] == "Bug" and out["user"] == "pat"
    for gone in ("url", "labels_url", "node_id", "assignee", "labels", "reactions"):
        assert gone not in out
    assert len(out["body"]) < 1000


def test_json_lists_are_one_item_per_line():
    text = prerun.clean_text(json.dumps([{"title": "a"}, {"title": "b"}]))
    assert text.splitlines() == ['{"title":"a"}', '{"title":"b"}']


def test_rss_becomes_short_items():
    feed = ("<?xml version='1.0'?><rss><channel><title>Feed</title>"
            "<item><title><![CDATA[First post]]></title><link>https://e.com/1</link>"
            "<pubDate>Thu, 05 Nov 2026 12:00:00 +0000</pubDate><description>&lt;p&gt;Hello there&lt;/p&gt;</description></item>"
            "<item><title>Second</title><link>https://e.com/2</link></item></channel></rss>")
    lines = prerun.clean_text(feed).splitlines()
    assert lines[0].startswith("First post | https://e.com/1 | Thu, 05 Nov 2026")
    assert "Hello there" in lines[1]
    assert lines[2] == "Second | https://e.com/2"


def test_markdown_feed_markers_are_tidied():
    text = prerun.clean_text("<!\\[CDATA\\[Title one\\]\\]>Points: 5 \\# Comments [https://a.b/c](https://a.b/c)")
    assert "CDATA" not in text and "Title one" in text and "# Comments" in text
    assert "[https://a.b/c](https://a.b/c)" not in text and "https://a.b/c" in text


def test_every_source_gets_a_share(tmp_path):
    pages = [{"url": f"https://f{i}.com", "title": f"Feed {i}", "content": f"line {i}\n" * 20000} for i in range(3)]
    block = prerun.step_block({"args": {}}, {"output": json.dumps({"results": pages})})
    for i in range(3):
        assert f"### Feed {i}" in block  # no feed is pushed out by an earlier long one
    assert len(block) < prerun.MAX_STEP_CHARS + 1000


def test_files_written_by_a_step_are_included(tmp_path):
    started = time.time()
    f = tmp_path / "issues.json"
    f.write_text(json.dumps([{"title": "From the file"}]))
    block = prerun.step_block({"args": {}}, {"output": json.dumps({"output": f"saved {f}"}),
                                             "args": {"command": f"curl -s https://x -o {f}"}, "started": started})
    assert "### File issues.json" in block and "From the file" in block


def test_fingerprint_ignores_timings_and_timestamps():
    a = "https://a.com HTTP 200 127 ms\nchecked 2026-11-06T09:57:01Z at 09:57 in 0.31s"
    b = "https://a.com HTTP 200 98 ms\nchecked 2026-11-06T10:12:44Z at 10:12 in 1.02s"
    c = "https://a.com HTTP 503 98 ms\nchecked 2026-11-06T10:12:44Z at 10:12 in 1.02s"
    assert prerun.fingerprint(a) == prerun.fingerprint(b)
    assert prerun.fingerprint(a) != prerun.fingerprint(c)


def _fake_store(monkeypatch, routine):
    saved = {"r": routine}
    monkeypatch.setattr(store, "get_routine", lambda rid: saved["r"])

    def update(rid, **kw):
        saved["r"] = {**saved["r"], **kw}
    monkeypatch.setattr(store, "update_routine", update)
    monkeypatch.setattr(lifecycle, "live_result", lambda *a, **k: None)
    return saved


def _routine(meta=None):
    return {"id": "cr_1", "name": "site-check", "status": "live", "meta": meta or {},
            "steps": {"steps": [{"tool": "terminal", "args": {"command": "check"}, "fill_ins": [],
                                 "check": {"min_len": 1}}]}}


def test_unchanged_data_can_stay_quiet(monkeypatch):
    saved = _fake_store(monkeypatch, _routine({"when_unchanged": "quiet"}))
    dispatch = lambda name, args: json.dumps({"output": "https://a.com HTTP 200 120 ms", "exit_code": 0})
    text, code = prerun.run("cr_1", dispatch)
    assert code == 0 and "HTTP 200" in text and prerun.SKIP_GATE not in text
    text, code = prerun.run("cr_1", dispatch)
    assert text.strip().endswith(prerun.SKIP_GATE)
    assert len(saved["r"]["meta"]["skips"]) == 1


def test_unchanged_data_repeats_the_last_answer(monkeypatch):
    saved = _fake_store(monkeypatch, _routine({"when_unchanged": "repeat"}))
    monkeypatch.setattr(prerun, "_recent_answers", lambda name: ["old", "All sites are up."])
    dispatch = lambda name, args: json.dumps({"output": "same data", "exit_code": 0})
    prerun.run("cr_1", dispatch)
    text, _ = prerun.run("cr_1", dispatch)
    assert "All sites are up." in text and "same data" not in text and prerun.SKIP_GATE not in text
    assert not saved["r"]["meta"].get("skips")


def test_repeat_stays_quiet_if_the_last_answer_was_silent(monkeypatch):
    _fake_store(monkeypatch, _routine({"when_unchanged": "repeat"}))
    monkeypatch.setattr(prerun, "_recent_answers", lambda name: ["[SILENT]"])
    dispatch = lambda name, args: json.dumps({"output": "same data", "exit_code": 0})
    prerun.run("cr_1", dispatch)
    assert prerun.SKIP_GATE in prerun.run("cr_1", dispatch)[0]


def test_repeat_falls_back_to_the_data_with_no_last_answer(monkeypatch):
    _fake_store(monkeypatch, _routine({"when_unchanged": "repeat"}))
    monkeypatch.setattr(prerun, "_recent_answers", lambda name: [])
    dispatch = lambda name, args: json.dumps({"output": "same data", "exit_code": 0})
    prerun.run("cr_1", dispatch)
    assert "same data" in prerun.run("cr_1", dispatch)[0]


def test_no_ai_job_still_delivers_unchanged_data(monkeypatch):
    _fake_store(monkeypatch, _routine({"mode": "no_ai"}))
    dispatch = lambda name, args: json.dumps({"output": "a.com HTTP 200", "exit_code": 0})
    prerun.run("cr_1", dispatch)
    text, _ = prerun.run("cr_1", dispatch)
    assert "HTTP 200" in text and prerun.SKIP_GATE not in text


def _job(monkeypatch, job):
    monkeypatch.setattr(lifecycle, "cron_job", lambda ref: job)


def test_default_is_quiet_only_when_no_one_is_waiting(monkeypatch):
    routine = _routine()
    monkeypatch.setattr(prerun, "_recent_answers", lambda name: ["An answer"])
    _job(monkeypatch, {"deliver": "local"})
    assert prerun.unchanged_action(routine) == "quiet"
    _job(monkeypatch, {"deliver": "telegram"})
    assert prerun.unchanged_action(routine) == "repeat"
    monkeypatch.setattr(prerun, "_recent_answers", lambda name: ["[SILENT]", "An answer"])
    assert prerun.unchanged_action(routine) == "quiet"
    assert prerun.unchanged_action(_routine({"skip_unchanged": False})) == "run"
    assert prerun.unchanged_action(_routine({"when_unchanged": "repeat"})) == "repeat"


def test_skip_can_be_turned_off(monkeypatch):
    _fake_store(monkeypatch, _routine({"when_unchanged": "run"}))
    dispatch = lambda name, args: json.dumps({"output": "same data", "exit_code": 0})
    prerun.run("cr_1", dispatch)
    text, _ = prerun.run("cr_1", dispatch)
    assert prerun.SKIP_GATE not in text


def test_failed_routine_hands_the_job_back(monkeypatch):
    _fake_store(monkeypatch, _routine())
    text, code = prerun.run("cr_1", lambda n, a: json.dumps({"output": "", "exit_code": 7}))
    assert code == 0 and "Do the whole task yourself" in text
    _fake_store(monkeypatch, _routine({"mode": "no_ai"}))
    _, code = prerun.run("cr_1", lambda n, a: json.dumps({"output": "", "exit_code": 7}))
    assert code == 1  # with no AI to fall back on, Hermes reports the failure


def _run(i, preview, sid):
    r = Run(execution_id=f"e{i}", job_id="j", job_name="site-check", session_id=sid, scheduled=True,
            started_ts=float(i), outcome="completed", cost_usd=0.001)
    r.steps = [Step("terminal", {"command": "c"}, preview, None, len(preview), "ok", 5)]
    return r


def test_no_ai_fit_for_a_status_check(monkeypatch):
    runs = [_run(i, f'{{"output": "https://a.com HTTP 200, {100 + i} ms"}}', f"s{i}") for i in range(4)]
    answers = {f"s{i}": f"Up at 2026-11-06 09:{10 + i}: https://a.com — HTTP 200, {100 + i} ms" for i in range(4)}
    monkeypatch.setattr(history, "final_answers", lambda ids: answers)
    ok, why = analyze.no_ai_fit(runs)
    assert ok, why


def test_no_ai_fit_refuses_a_digest(monkeypatch):
    runs = [_run(i, '{"output": "30 issues"}', f"s{i}") for i in range(4)]
    monkeypatch.setattr(history, "final_answers", lambda ids: {f"s{i}": "A long summary. " * 200 for i in range(4)})
    assert not analyze.no_ai_fit(runs)[0]


def test_promote_attaches_the_script_and_undo_removes_it(monkeypatch, tmp_path):
    routine = _routine({"ready": True, "matches": 3})
    routine.update(source_ref="job1", status="testing")
    saved = _fake_store(monkeypatch, routine)
    monkeypatch.setattr(lifecycle, "cron_job", lambda ref: {"id": "job1", "prompt": "Check the sites."})
    monkeypatch.setattr(prerun, "scripts_dir", lambda: tmp_path)
    monkeypatch.setattr(lifecycle, "_hermes_cmd", lambda: ["hermes"])
    edits = []
    monkeypatch.setattr(lifecycle, "_edit_job", lambda job_id, *flags: edits.append((job_id, flags)))
    lifecycle.promote("cr_1")
    job_id, flags = edits[-1]
    assert job_id == "job1" and "--script" in flags and flags[flags.index("--script") + 1] == "crystallize/cr_1.py"
    assert "Script Output" in flags[flags.index("--prompt") + 1]
    assert (tmp_path / "cr_1.py").exists()
    assert saved["r"]["status"] == "live" and saved["r"]["meta"]["mode"] == "prerun"
    lifecycle.undo("cr_1")
    _, flags = edits[-1]
    assert flags[:2] == ("--prompt", "Check the sites.") and flags[2:4] == ("--script", "")
    assert not (tmp_path / "cr_1.py").exists()


def test_script_uses_the_python_hermes_started_it_on(monkeypatch, tmp_path):
    monkeypatch.setattr(prerun, "scripts_dir", lambda: tmp_path)
    prerun.write_script("cr_1", "job")
    body = (tmp_path / "cr_1.py").read_text()
    assert "sys.executable" in body and '"crystal", "prerun", "cr_1"' in body
    compile(body, "cr_1.py", "exec")


def test_clean_result_keeps_the_shape_and_shrinks():
    page = "# Title\n\n\n" + "\\[item\\]   with   spaces\n\n" * 200
    out = prerun.clean_result("web_extract", json.dumps({"results": [{"url": "https://e.com", "title": "T", "content": page}]}))
    data = json.loads(out)
    assert data["results"][0]["url"] == "https://e.com" and len(out) < len(page)
    assert "[item] with spaces" in data["results"][0]["content"]


def test_clean_result_keeps_long_text_whole():
    body = "word " * 300
    links = {f"{k}_url": "https://api.github.com/repos/a/b/issues/1/" + k for k in ("labels", "comments", "events", "timeline")}
    issue = [{"title": "Bug", "body": body, "html_url": "https://g/1", "node_id": "x", **links}] * 3
    data = json.loads(prerun.clean_result("terminal", json.dumps({"output": json.dumps(issue), "exit_code": 0})))
    line = json.loads(data["output"].splitlines()[0])
    assert line["body"].strip() == body.strip() and line["html_url"] and "node_id" not in line
    assert data["exit_code"] == 0


def test_clean_result_leaves_alone_what_it_should():
    big = "x y " * 1000
    assert prerun.clean_result("read_file", big) is None                     # files stay exact
    assert prerun.clean_result("terminal", "short") is None                  # too small to matter
    assert prerun.clean_result("terminal", json.dumps({"error": big})) is None  # errors stay exact
    assert prerun.clean_result("terminal", big) is None                      # nothing to remove
    assert prerun.clean_result("terminal", None) is None


def _feed(*ids):
    return "\n".join(f"Title {i}\nArticle URL: https://e.com/{i}" for i in ids)


def test_new_only_sends_new_items_and_the_last_answer():
    seen = prerun._links(_feed(1, 2, 3, 4, 5, 6))
    text = prerun._new_only_text(_feed(1, 2, 3, 4, 5, 7), seen, "My answer")
    assert "Title 7" in text and "https://e.com/7" in text and "My answer" in text and "Title 3" not in text


def test_new_only_matches_items_across_formats():
    seen = prerun._links(_feed(1, 2, 3, 4, 5, 6))
    other = "\n".join(f"Title {i} | https://e.com/{i}/ | Fri" for i in (1, 2, 3, 4, 5, 8))
    text = prerun._new_only_text(other, seen, "My answer")
    assert "Title 8" in text and "Title 2" not in text


def test_new_only_sends_everything_when_only_the_text_changed():
    """Same links can hide a changed status, so the old answer doesn't get repeated."""
    seen = prerun._links(_feed(1, 2, 3, 4, 5, 6))
    assert prerun._new_only_text(_feed(1, 2, 3, 4, 5, 6), seen, "My answer") is None


def test_summary_and_failures_survive_collapsing():
    log = "\n".join([f"tests/a.py::t{i} PASSED [ 10%]" for i in range(30)] +
                    ["tests/a.py::bad FAILED", "=== 1 failed, 30 passed in 2.1s ==="])
    out = prerun.collapse_passing(log)
    assert "30 passing" in out and "tests/a.py::bad FAILED" in out and "1 failed, 30 passed" in out


def test_failed_commands_and_code_keep_their_shape():
    code = "def f():\n" + "    return 1   # keep\n" * 200
    ok = json.loads(prerun.clean_result("terminal", json.dumps({"output": code + "   \n" * 300, "exit_code": 0})))
    assert "    return 1   # keep" in ok["output"]
    assert prerun.clean_result("terminal", json.dumps({"output": "x  y\n" * 900, "exit_code": 1})) is None


def test_download_links_and_prices_stay():
    slim = prerun._slim_json({"download_url": "d", "pdf_url": "p", "html_url": "h", "labels_url": "x"})
    assert set(slim) == {"download_url", "pdf_url", "html_url"}
    assert prerun.fingerprint("price 1.25") != prerun.fingerprint("price 1.75")
    assert prerun.fingerprint("took 1.25s") == prerun.fingerprint("took 1.75s")


def test_new_only_stands_aside_when_it_cannot_help():
    seen = prerun._links(_feed(1, 2, 3, 4, 5, 6))
    assert prerun._new_only_text(_feed(1, 2, 3, 4, 5, 7), set(), "My answer") is None      # nothing to compare
    assert prerun._new_only_text(_feed(11, 12, 13, 14, 15, 16), seen, "My answer") is None # mostly new
    assert prerun._new_only_text(_feed(1, 2, 3, 4, 5, 7), seen, "") is None                # nothing to update
    assert prerun._new_only_text("just text, no links", seen, "My answer") is None         # not a list of items


def test_a_job_that_acts_always_runs(monkeypatch):
    routine = _routine({"when_unchanged": "quiet"})
    routine["steps"]["leaves_to_ai"] = True
    assert prerun.unchanged_action(routine) == "run"


def _run_script(path, cwd):
    # hermes_cli isn't importable here, so the routine call itself fails: that's expected.
    return subprocess.run([sys.executable, str(path)], capture_output=True, text=True, cwd=cwd)


def test_script_runs_for_a_job_with_no_script_of_its_own(monkeypatch, tmp_path):
    d = tmp_path / "crystallize"
    monkeypatch.setattr(prerun, "scripts_dir", lambda: d)
    prerun.write_script("cr_1", "job")
    out = _run_script(d / "cr_1.py", tmp_path)
    assert "NameError" not in out.stderr and "SyntaxError" not in out.stderr and "OWN_SCRIPT = None" in (d / "cr_1.py").read_text()


def test_own_script_output_comes_first(monkeypatch, tmp_path):
    d = tmp_path / "crystallize"
    monkeypatch.setattr(prerun, "scripts_dir", lambda: d)
    (tmp_path / "own.py").write_text("print('team note')")
    prerun.write_script("cr_1", "job", "own.py")
    out = _run_script(d / "cr_1.py", tmp_path)
    assert out.stdout.startswith("team note") and "NameError" not in out.stderr


def test_own_script_can_still_stop_the_run(monkeypatch, tmp_path):
    d = tmp_path / "crystallize"
    monkeypatch.setattr(prerun, "scripts_dir", lambda: d)
    (tmp_path / "own.py").write_text("print('{" + chr(34) + "wakeAgent" + chr(34) + ": false}')")
    prerun.write_script("cr_1", "job", "own.py")
    out = _run_script(d / "cr_1.py", tmp_path)
    assert out.returncode == 0 and "wakeAgent" in out.stdout and "hermes_cli" not in out.stderr


def test_own_script_failure_is_passed_on(monkeypatch, tmp_path):
    d = tmp_path / "crystallize"
    monkeypatch.setattr(prerun, "scripts_dir", lambda: d)
    (tmp_path / "own.py").write_text("import sys; print('broken'); sys.exit(3)")
    prerun.write_script("cr_1", "job", "own.py")
    assert _run_script(d / "cr_1.py", tmp_path).returncode == 3


def _crashing_hermes(tmp_path):
    """Run scripts against a stand-in Hermes whose command fails with no output."""
    pkg = tmp_path / "fake" / "hermes_cli"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text("import sys; sys.exit(7)")
    return {**os.environ, "PYTHONPATH": str(tmp_path / "fake")}


def test_a_routine_crash_hands_the_task_to_the_ai(monkeypatch, tmp_path):
    d = tmp_path / "crystallize"
    monkeypatch.setattr(prerun, "scripts_dir", lambda: d)
    prerun.write_script("cr_1", "job")
    out = subprocess.run([sys.executable, str(d / "cr_1.py")], capture_output=True, text=True, env=_crashing_hermes(tmp_path))
    assert out.returncode == 0 and "Do the whole task yourself" in out.stdout


def test_a_no_ai_routine_crash_is_reported_as_a_failure(monkeypatch, tmp_path):
    d = tmp_path / "crystallize"
    monkeypatch.setattr(prerun, "scripts_dir", lambda: d)
    prerun.write_script("cr_1", "job", no_ai=True)
    out = subprocess.run([sys.executable, str(d / "cr_1.py")], capture_output=True, text=True, env=_crashing_hermes(tmp_path))
    assert out.returncode == 7 and "Do the whole task" not in out.stdout


def test_a_link_poor_run_keeps_the_remembered_items(monkeypatch):
    saved = _fake_store(monkeypatch, _routine({"when_unchanged": "run", "new_only": True}))
    monkeypatch.setattr(prerun, "_recent_answers", lambda name: ["An answer"])
    outputs = [_feed(1, 2, 3, 4, 5, 6), "No links in this format", _feed(1, 2, 3, 4, 5, 7)]
    dispatch = lambda name, args: json.dumps({"output": outputs.pop(0), "exit_code": 0})
    prerun.run("cr_1", dispatch)
    assert "No links" in prerun.run("cr_1", dispatch)[0]
    assert len(saved["r"]["meta"]["last_links"]) == 6
    assert "Title 7" in prerun.run("cr_1", dispatch)[0]


def test_long_test_logs_lose_only_passing_lines():
    passing = [f"test_{i} (tests.test_a.T) ... ok" for i in range(300)]
    failure = ["test_x (tests.test_a.T) ... FAIL", "FAIL: test_x", "AssertionError: 2 != 4", "FAILED (failures=1)"]
    out = json.loads(prerun.clean_result("terminal", json.dumps({"output": "\n".join(passing + failure), "exit_code": 1, "error": None})))
    assert "300 passing test lines removed" in out["output"] and "test_5 " not in out["output"]
    for line in failure:
        assert line in out["output"]
    assert out["exit_code"] == 1


def test_a_short_log_stays_whole():
    text = "\n".join(f"test_{i} ... ok" for i in range(5))
    assert prerun.collapse_passing(text) == text
