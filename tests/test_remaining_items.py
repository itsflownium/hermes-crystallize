"""Starter scripts, wider schedules, new commands and the entry points that had no tests."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from crystallize import analyze, brake, capture, chat_savings as cs, cli, history, lifecycle, native_cron, rent, rhythm, starter_script, store


def _step(tool, args, kind="fixed"):
    return SimpleNamespace(tool_name=tool, args=args, kind=kind)


@pytest.fixture
def job(monkeypatch):
    found = {"id": "j1", "name": "news", "prompt": "Summarise"}
    monkeypatch.setattr(lifecycle, "_bound_job", lambda ref: found)
    monkeypatch.setattr(history, "runs_for_job", lambda ref, only_usable=False: [object()] * 3)
    monkeypatch.setattr(analyze, "regular_actions", lambda runs: set())
    monkeypatch.setattr(analyze, "job_prompt", lambda ref: "Summarise")
    return found


def _steps(monkeypatch, *steps):
    monkeypatch.setattr(analyze, "analyze_runs", lambda runs, prompt: SimpleNamespace(steps=list(steps)))


def test_a_draft_covers_stable_reads_and_can_run(job, monkeypatch, tmp_path):
    _steps(monkeypatch, _step("terminal", {"command": "echo hello"}))
    out = starter_script.draft("j1")
    assert out["complete"] and out["commands"] == ["echo hello"] and not out["left_out"]
    path = tmp_path / "hermes" / "scripts" / out["source_script"]
    ran = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
    assert ran.returncode == 0 and "Do the task as normal" in ran.stdout and "hello" not in ran.stdout


def _run_draft(out, tmp_path):
    return subprocess.run([sys.executable, str(tmp_path / "hermes" / "scripts" / out["source_script"])], capture_output=True, text=True).stdout


def test_a_draft_ignores_formatting_but_notices_real_changes(job, monkeypatch, tmp_path):
    monkeypatch.setattr(analyze, "observation_safety_error", lambda tool, args: None)
    outputs = []
    for data in ("""printf '{\\n "a": 1}'""", """printf '{"a":1}'""", """printf '{"a":2}'"""):
        _steps(monkeypatch, _step("terminal", {"command": data}))
        path = tmp_path / "hermes" / "scripts" / starter_script.draft("j1")["source_script"]
        source = path.read_text()
        # the fingerprint covers the command too, so compare on one fixed key
        path.write_text(source.replace("snapshot[command] =", 'snapshot["data"] ='))
        outputs.append(subprocess.run([sys.executable, str(path)], capture_output=True, text=True).stdout)
    assert outputs[0] == outputs[1] and outputs[1] != outputs[2]


def test_a_draft_names_what_it_left_out(job, monkeypatch):
    _steps(monkeypatch, _step("terminal", {"command": "ls"}), _step("web_search", {"query": "x"}, kind="varies"),
           _step("search_files", {"pattern": "x"}))
    out = starter_script.plan("j1")
    assert not out["complete"] and {row["tool"] for row in out["left_out"]} == {"web_search", "search_files"}


@pytest.mark.parametrize("command", ["rm -rf /tmp/x", "curl -X POST https://e.com", "echo hi > file.txt"])
def test_a_draft_never_includes_a_command_that_changes_things(job, monkeypatch, command):
    _steps(monkeypatch, _step("terminal", {"command": command}))
    assert not starter_script.plan("j1").get("commands")


def test_a_job_with_actions_gets_no_draft(job, monkeypatch):
    monkeypatch.setattr(analyze, "regular_actions", lambda runs: {"send_message"})
    assert not starter_script.plan("j1")["ok"]


def test_a_draft_needs_enough_runs(job, monkeypatch):
    monkeypatch.setattr(history, "runs_for_job", lambda ref, only_usable=False: [object()])
    assert "3 finished runs" in starter_script.plan("j1")["why"]


def test_commands_with_quotes_stay_exact_in_the_script(job, monkeypatch, tmp_path):
    command = '''python3 -c "print('a\\"b')"'''
    monkeypatch.setattr(analyze, "observation_safety_error", lambda tool, args: None)
    _steps(monkeypatch, _step("terminal", {"command": command}))
    out = starter_script.draft("j1")
    compile(out["source"], "draft", "exec")
    assert json.dumps(command) in out["source"]


@pytest.mark.parametrize("expr,minutes", [("*/30 * * * *", 30), ("0 * * * *", 60), ("15 * * * *", 60), ("0 */2 * * *", 120),
                                          ("0 9 * * *", None), ("0 9 * * 1", None), ("*/7 * * * *", None), ("0 */5 * * *", None)])
def test_only_even_cron_schedules_count_as_intervals(expr, minutes):
    assert rhythm._interval({"schedule": {"kind": "cron", "expr": expr}}) == minutes


def test_undo_restores_the_original_cron_expression_exactly():
    assert rhythm._schedule_argument({"kind": "cron", "expr": "0 */2 * * *"}) == "0 */2 * * *"
    assert rhythm._schedule_argument({"kind": "interval", "minutes": 30}) == "every 30m"


@pytest.fixture
def attachable(monkeypatch, tmp_path):
    job = {"id": "j1", "name": "news", "prompt": "x", "deliver": "telegram", "schedule": {"kind": "interval", "minutes": 30}}
    monkeypatch.setattr(lifecycle, "_bound_job", lambda ref: job)
    monkeypatch.setattr(native_cron, "_capability", lambda mode: True)
    monkeypatch.setattr(history, "runs_for_job", lambda ref, only_usable=False: [])
    monkeypatch.setattr(history, "running_job_ids", lambda: set())
    scripts = tmp_path / "hermes" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "s.py").write_text("print(1)")
    return job


def test_a_job_that_sends_needs_consent_to_silence(attachable):
    blocked = native_cron.preview("j1", "s.py", snapshot_only=True)
    assert not blocked["can_apply"] and any("hasn't changed" in text for text in blocked["blockers"])
    assert native_cron.preview("j1", "s.py", snapshot_only=True, skipped_delivery_ok=True)["can_apply"]


def test_script_only_mode_stays_local(attachable):
    out = native_cron.preview("j1", "s.py", mode="verbatim", snapshot_only=True, verbatim_output=True, skipped_delivery_ok=True)
    assert any("local delivery" in text for text in out["blockers"])


def test_skills_still_block_an_attachment(attachable):
    attachable["skills"] = ["research"]
    assert not native_cron.preview("j1", "s.py", snapshot_only=True, skipped_delivery_ok=True)["can_apply"]


def _cli(*argv):
    parser = argparse.ArgumentParser()
    cli._setup(parser)
    return parser.parse_args(list(argv))


def _run(capsys, *argv):
    args = _cli(*argv)
    cli._handle(args)
    return capsys.readouterr().out


def test_brake_command_turns_it_on_and_off(capsys, monkeypatch):
    monkeypatch.setattr(lifecycle, "all_cron_jobs", lambda: [])
    assert "on" in _run(capsys, "brake", "on") and brake.enabled()
    assert json.loads(_run(capsys, "brake"))["on"] is True
    _run(capsys, "brake", "off")
    assert not brake.enabled()


def test_rent_command_reports_and_restores(capsys, monkeypatch):
    monkeypatch.setattr(rent, "_read_chat", lambda sid: ([], ""))
    assert json.loads(_run(capsys, "rent", "s"))["items"] == []
    store.add_eviction("s", "c1", "abc")
    assert json.loads(_run(capsys, "rent", "s", "--restore", "c1"))["ok"] and store.evictions("s") == {}


def test_prices_command_controls_lookup_and_the_unpriced_helper(capsys):
    out = json.loads(_run(capsys, "prices", "--lookup", "off", "--allow-unpriced-helper", "off", "--handoffs", "off"))
    assert out == {"lookup": False, "allow_unpriced_helper": False, "handoffs": False}


def test_auction_reconcile_command_needs_confirmation_when_locked(capsys):
    assert json.loads(_run(capsys, "auction-reconcile"))["cleared"] is False


def test_routing_note_is_only_for_chats_with_crystallize_on_and_is_charged(monkeypatch):
    monkeypatch.setattr(cs, "chosen_model", lambda: {"model": "m", "provider": "p"})
    monkeypatch.setattr(cs, "_is_cheaper", lambda *a: True)
    monkeypatch.setattr(cs, "_routing_pays", lambda *a: True)
    hook = capture.make_pre_llm_call(SimpleNamespace(current_cron_execution=lambda: None))
    assert hook(session_id="off") is None
    store.set_chat_enabled("on", True)
    assert hook(session_id="on")["context"] == cs.ROUTING_NOTE
    assert store.list_chat_savings("on")[0]["added_input_chars"] == len(cs.ROUTING_NOTE)


def test_status_command_mentions_chats():
    from crystallize import _status_text

    assert "any chat you turn it on for" in _status_text()


@pytest.mark.parametrize("digests,verdict", [
    (["a", "a", "a", "a", "a"], "good"), (["a", "b", "a", "b"], "poor"), (["a", "a", "a", "b", "b"], "good"), (["a", "b", "c", "d", "d"], "mixed"), (["a"], "unknown"),
])
def test_a_job_is_told_whether_skipping_would_help(monkeypatch, digests, verdict):
    monkeypatch.setattr(history, "runs_for_job", lambda ref, only_usable=False: digests)
    monkeypatch.setattr(rhythm, "_content_fingerprint", lambda run: run)
    assert starter_script.fit("j1")["verdict"] == verdict


def _costs(job, monkeypatch, after):
    monkeypatch.setattr(starter_script, "checks_since", lambda job_id, since: None)
    monkeypatch.setattr(starter_script.time, "time", lambda: 10_000)
    job["schedule"] = {"kind": "interval", "minutes": 10}
    runs = [SimpleNamespace(scheduled=True, finished=True, started_ts=t, cost_usd=c) for t, c in [(1, 1.0), (2, 1.0), (3, 1.0)] + after]
    monkeypatch.setattr(history, "runs_for_job", lambda ref, only_usable=False: runs)
    return starter_script.since_attached("j1", {"status": "applied", "applied_at": 4_000})


def test_an_attached_job_shows_what_it_really_saved(job, monkeypatch):
    out = _costs(job, monkeypatch, [(5_000, 1.5)])
    assert out["checks"] == 10 and out["saved_usd"] == pytest.approx(8.5) and not out["costing_more"]


def test_an_attached_job_warns_when_it_costs_more(job, monkeypatch):
    out = _costs(job, monkeypatch, [(4_000 + i * 600, 2.5) for i in range(10)])
    assert out["costing_more"] and "undo" in out["message"]


def test_checks_are_counted_from_hermes_cron_records(tmp_path):
    import sqlite3
    folder = tmp_path / "hermes" / "cron"
    folder.mkdir(parents=True)
    conn = sqlite3.connect(folder / "executions.db")
    conn.execute("CREATE TABLE executions (id TEXT, job_id TEXT, status TEXT, started_at TEXT, scheduled_instant TEXT)")
    rows = [("1", "j1", "completed", "2026-11-07T10:00:00+00:00", "x"), ("2", "j1", "completed", "2026-11-07T10:02:00+00:00", "x"),
            ("3", "j1", "completed", "2026-11-07T10:04:00+00:00", None), ("4", "j1", "failed", "2026-11-07T10:06:00+00:00", "x"),
            ("5", "other", "completed", "2026-11-07T10:08:00+00:00", "x")]
    conn.executemany("INSERT INTO executions VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    from datetime import datetime
    since = datetime.fromisoformat("2026-11-07T10:01:00+00:00").timestamp()
    assert starter_script.checks_since("j1", since) == 1



def test_a_changed_timeout_setting_doesnt_hide_a_steady_read(job, monkeypatch):
    runs = [SimpleNamespace(steps=[SimpleNamespace(tool_name="terminal", args={"command": "cat a.json", **({"timeout": 30} if i % 2 else {})})])
            for i in range(3)]
    monkeypatch.setattr(history, "runs_for_job", lambda ref, only_usable=False: runs)
    _steps(monkeypatch, _step("terminal", {"command": "cat a.json", "timeout": 30}, kind="varies"))
    out = starter_script.plan("j1")
    assert out["ok"] and out["commands"] == ["cat a.json"] and out["complete"]


def test_a_command_that_really_changes_is_still_left_out(job, monkeypatch):
    runs = [SimpleNamespace(steps=[SimpleNamespace(tool_name="terminal", args={"command": f"cat day{i}.json"})]) for i in range(3)]
    monkeypatch.setattr(history, "runs_for_job", lambda ref, only_usable=False: runs)
    _steps(monkeypatch, _step("terminal", {"command": "cat day2.json"}, kind="varies"))
    assert not starter_script.plan("j1")["ok"]
