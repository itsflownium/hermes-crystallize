"""The pre-run step: cleaning data, spotting unchanged runs, and the no-AI check."""

import json
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


def test_unchanged_data_skips_the_ai(monkeypatch):
    saved = _fake_store(monkeypatch, _routine())
    dispatch = lambda name, args: json.dumps({"output": "https://a.com HTTP 200 120 ms", "exit_code": 0})
    text, code = prerun.run("cr_1", dispatch)
    assert code == 0 and "HTTP 200" in text and prerun.SKIP_GATE not in text
    text, code = prerun.run("cr_1", dispatch)
    assert text.strip().endswith(prerun.SKIP_GATE)
    assert len(saved["r"]["meta"]["skips"]) == 1


def test_skip_can_be_turned_off(monkeypatch):
    _fake_store(monkeypatch, _routine({"skip_unchanged": False}))
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
