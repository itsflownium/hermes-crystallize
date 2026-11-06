"""Offline replay regressions: no host install, provider calls, or external writes."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from crystallize import analyze, brake, capture, chat_savings, history, lifecycle, prerun, runner, store


@pytest.mark.parametrize("code", [
    "from pathlib import Path; Path('report').open('w')",
    "from pathlib import Path; Path('report').open(mode='a')",
    "from pathlib import Path; p = Path('report'); f = p.open; f('w')",
    "from pathlib import Path; p = Path('report'); f = p.write_text; f('x')",
    "f = open; f('report', 'w')",
    "import requests; f = requests.post; f('https://example.invalid')",
    "import requests; client = requests.Client(); client.get('https://example.invalid')",
    "do_something()",
    "thing.harmless_name()",
    "from json import made_up; made_up()",
    "import json; json.loads = print",
    "open('report', mode=unknown_mode)",
    "print('x', file=open('report', 'w'))",
    "class C: pass",
    "def read():\n    open('report', 'w')\nread()",
])
def test_python_unknowns_and_method_aliases_never_replay(code):
    assert analyze.observation_safety_error("execute_code", {"code": code})


@pytest.mark.parametrize("code", [
    "import json; print(json.dumps({'ok': True}))",
    "from pathlib import Path; print(Path('report').read_text())",
    "from pathlib import Path; print(Path('report').open('r').read())",
    "with open('report', 'rb') as f:\n    print(f.read())",
    "import requests as r; response = r.get('https://example.invalid'); print(response.text)",
    "from json import loads as parse; print(parse('{\"x\": 1}').get('x'))",
])
def test_recognized_python_read_subset_remains_usable(code):
    assert analyze.observation_safety_error("execute_code", {"code": code}) is None


@pytest.mark.parametrize("command", [
    r"curl -\XPOST https://example.invalid",
    "curl --request=POST https://example.invalid",
    "curl --request post https://example.invalid",
    "curl --request MKCOL https://example.invalid",
    "curl --config /tmp/config https://example.invalid",
    "curl --libcurl report.c https://example.invalid",
    "curl -c cookies.txt https://example.invalid",
    "curl --dump-header headers.txt https://example.invalid",
    "curl --quote 'DELE file' ftp://example.invalid",
    "curl $'-XPOST' https://example.invalid",
    "curl -X$METHOD https://example.invalid",
    "curl -{d,X} POST https://example.invalid",
    "curl * https://example.invalid",
    "curl <(touch report)",
    "date -u --set='2026-01-01'",
    "date -us2026-01-01",
    "wget -qO- --save-cookies=cookies.txt https://example.invalid",
    "find . -execdir touch '{}' ';'",
    "awk 'BEGIN { system (\"touch report\") }'",
    "sed -e '1e touch report' input",
    "rg --pre=some-program pattern input",
    "sort --compress-program=some-program input",
    "git diff --ext-diff",
    "./cat report",
])
def test_shell_executable_extensions_are_not_read_operations(command):
    assert analyze.observation_safety_error("terminal", {"command": command})


def test_scratch_outputs_are_normalized_for_declared_artifact_enforcement(tmp_path):
    target = tmp_path / "output with spaces.json"
    command = f"curl -s -o {str(target)!r} https://example.invalid 2>/dev/null"
    assert set(analyze.scratch_destinations(command)) == {str(target.resolve()), str(Path(os.devnull).resolve())}
    assert analyze.scratch_destinations("curl -so/tmp/report.json https://example.invalid") == [str(Path("/tmp/report.json").resolve())]
    assert analyze.observation_safety_error("terminal", {"command": "printf x > /tmp/../../outside-report"})


@pytest.mark.parametrize("args", [
    {"method": "POST"}, {"operation": "delete"}, {"query": "DELETE FROM events"},
])
def test_read_named_tools_cannot_hide_mutating_operation_arguments(args):
    assert analyze.observation_safety_error("mcp_inventory_query", args)


def test_saved_unsafe_and_implicit_legacy_recipes_fail_before_dispatch():
    calls = []
    recipes = [
        {"steps": [{"tool": "execute_code", "args": {"code": "unknown()"}}]},
        {"steps": [{"tool": "web_extract", "args": {"url": "https://example.invalid/?from=2020-01-01&to=2020-01-02"},
                    "fill_ins": ["date"]}]},
        {"steps": [{"tool": "web_extract", "args": {"url": "https://example.invalid/id"}, "fill_ins": ["uuid"]}]},
    ]
    for recipe in recipes:
        result = runner.run(recipe, lambda *args: calls.append(args))
        assert not result["ok"] and result["reason"]
    assert calls == []


def _items(text="is available", count=6):
    return "\n".join(f"Item {i} {text if i == 2 else 'is unchanged'} https://example.invalid/{i}" for i in range(count))


@pytest.mark.parametrize("old,new", [
    ("is available", "is not available"),
    ("buyer pays seller", "seller pays buyer"),
    ("no no problems", "no problems"),
    ("stock 5", "stock 5 plus recall"),
    ("status active and verified", "status active"),
])
def test_item_deltas_keep_negations_order_repetitions_and_added_or_deleted_content(old, new):
    previous = prerun._items(_items(old))
    delta = prerun._new_only_text(_items(new), previous, "Previous answer")
    assert delta and new in delta and "Item 2" in delta and "Item 5" not in delta


def test_item_deletions_and_ranking_are_preserved():
    previous = prerun._items(_items())
    delta = prerun._new_only_text(_items(count=5), previous, "Previous answer")
    assert "No longer listed" in delta and "https://example.invalid/5" in delta
    assert prerun._new_only_text("\n".join(reversed(_items().splitlines())), previous, "Previous answer") is None


def test_new_item_details_are_included_in_full():
    old = "\n".join(f"Title {i}\nLink: https://example.invalid/{i}\nDetails unchanged" for i in range(6))
    new = old.replace("Title 5\nLink: https://example.invalid/5\nDetails unchanged",
                      "Title 7\nLink: https://example.invalid/7\nDetails NOT SAFE\nRequired action: recall now")
    delta = prerun._new_only_text(new, prerun._items(old), "Previous answer")
    assert "Details NOT SAFE" in delta and "Required action: recall now" in delta


@pytest.mark.parametrize("old,new", [
    ("deadline 2026-11-06T10:00:00Z", "deadline 2026-11-07T10:00:00Z"),
    ("price 19.95", "price 99.95"),
    ("ratio 0.20", "ratio 0.98"),
    ("SHA256 " + "a" * 64, "SHA256 " + "b" * 64),
    ("if x:\n    run()", "if x:\nrun()"),
])
def test_full_fingerprints_keep_meaningful_numbers_dates_hashes_and_indentation(old, new):
    assert prerun.fingerprint(old) != prerun.fingerprint(new)


def test_json_presentation_can_change_without_changing_content():
    assert prerun.fingerprint('{"a":1,"b":2}') == prerun.fingerprint('{\n "b": 2, "a": 1\n}')


def test_generic_cleaning_keeps_permissions_nulls_urls_and_nested_errors():
    value = {"login": "operator", "id": 9, "role": "limited", "site_admin": True,
             "permissions": {"delete": True}, "payment_url": "https://example.invalid/pay", "labels": [],
             "error": {"retry_after": 0, "message": "  exact diagnostic\n    code\n"}, "details": "x" * 5000, "missing": None}
    assert json.loads(prerun.clean_text(json.dumps(value, indent=2))) == value
    assert prerun._slim_json(value, 5) == value


def test_search_and_non_web_result_fields_survive_unwrapping():
    result = {"results": [{"title": "Recall", "url": "https://example.invalid/a", "snippet": "withdrawn from sale", "score": 0.95}],
              "next": "cursor", "total": 34, "http_status": 503}
    block = prerun.step_block({}, {"output": json.dumps(result)})
    assert "withdrawn from sale" in block and '"score":0.95' in block and '"http_status":503' in block and "cursor" in block
    inventory = {"results": [{"sku": "A", "stock": 0, "status": "recalled"}], "total": 1}
    assert json.loads(prerun.step_block({}, {"output": json.dumps(inventory)})) == inventory


def test_declared_artifact_snapshot_is_present_after_runner_cleanup():
    body = prerun.step_block({}, {"output": "", "artifacts": [{"name": "report.txt", "content": "Critical detail"}]})
    assert "### File report.txt" in body and "Critical detail" in body


def _stored_routine(meta=None):
    store.save_routine("cr_safe", name="job", source="cron", source_ref="job-id", status="live",
                       steps={"steps": [{"tool": "web_extract", "args": {"url": "https://example.invalid"}, "check": {"min_len": 1}}]},
                       meta=meta or {})


def test_changes_after_display_cap_do_not_repeat_old_answer(monkeypatch):
    _stored_routine({"when_unchanged": "repeat"})
    monkeypatch.setattr(lifecycle, "check_drift", lambda _: True)
    monkeypatch.setattr(lifecycle, "live_result", lambda *args: None)
    monkeypatch.setattr(prerun, "_recent_answers", lambda _: ["stale answer"])
    prefix = "ordinary content\n" * 4000
    values = [prefix + "Tail AVAILABLE", prefix + "Tail NOT AVAILABLE"]
    dispatch = lambda *args: json.dumps({"content": values.pop(0)})
    first, _ = prerun.run("cr_safe", dispatch)
    second, _ = prerun.run("cr_safe", dispatch)
    assert "display limits" in first and "more characters not shown" in second
    assert "stale answer" not in second and prerun.SKIP_GATE not in second


def test_live_source_schema_change_invalidates_before_delivering(monkeypatch):
    _stored_routine()
    monkeypatch.setattr(lifecycle, "check_drift", lambda _: True)
    monkeypatch.setattr(lifecycle, "live_result", lambda *args: None)
    invalid = []
    monkeypatch.setattr(lifecycle, "invalidate", lambda *args: invalid.append(args))
    values = [{"price": 5}, {"price": {"error": "renamed schema"}}]
    dispatch = lambda *args: json.dumps({"output": json.dumps(values.pop(0))})
    assert "Captured data" in prerun.run("cr_safe", dispatch)[0]
    text, _ = prerun.run("cr_safe", dispatch)
    assert invalid == [("cr_safe", "source schema changed")] and "tested again" in text


def test_pre_run_drift_failure_never_dispatches(monkeypatch):
    _stored_routine()
    monkeypatch.setattr(lifecycle, "check_drift", lambda _: False)
    calls = []
    text, code = prerun.run("cr_safe", lambda *args: calls.append(args))
    assert code == 0 and "configuration changed" in text and calls == []


def test_no_ai_unexpected_error_reports_failure_and_records_it(monkeypatch):
    _stored_routine({"mode": "no_ai"})
    monkeypatch.setattr(lifecycle, "check_drift", lambda _: True)
    monkeypatch.setattr(runner, "run", lambda *args: (_ for _ in ()).throw(RuntimeError("failure")))
    failures = []
    monkeypatch.setattr(lifecycle, "live_result", lambda *args: failures.append(args))
    text, code = prerun.run("cr_safe", lambda *args: None)
    assert code == 1 and "could not finish" in text and failures[0][1] is False


def test_brake_keeps_every_active_counter_and_separates_profiles(monkeypatch, tmp_path):
    active_home = [tmp_path / "profile-a"]
    monkeypatch.setattr(history, "_hermes_home", lambda: active_home[0])
    monkeypatch.setattr(brake, "enabled", lambda: True)
    monkeypatch.setattr(brake, "plan", lambda _: {"limit": 100, "normal": 20})
    brake._used.clear()
    brake._limits.clear()
    brake.note_usage("first", {"total_tokens": 500})
    for i in range(250):
        brake.note_usage(f"s{i}", {"total_tokens": 5})
        brake._plan_for_run("job", f"s{i}")
    assert brake.check("job", "first")["action"] == "block"
    active_home[0] = tmp_path / "profile-b"
    assert brake.check("job", "first") is None
    brake.note_usage("first", {"total_tokens": 2})
    brake.forget("first")
    active_home[0] = tmp_path / "profile-a"
    assert brake.check("job", "first")["action"] == "block"
    brake.forget("first")
    assert brake.check("job", "first") is None


def test_capture_preserves_complete_valid_argument_json():
    run = SimpleNamespace(execution_id="e", job_id="j", job_name="job", scheduled_instant="now")
    ctx = SimpleNamespace(get_config=lambda key, default=None: default, current_cron_execution=lambda: run)
    args = {"code": "print('x')\n" * 1200, "tail": "important final parameter"}
    capture.make_post_tool_call(ctx)(tool_name="execute_code", args=args, result="ok", session_id="s")
    assert json.loads(store.execution_traces("e")[0]["args_json"]) == args


def test_transforms_forward_session_and_turn_and_session_end_cleans_only_that_session(monkeypatch):
    ctx = SimpleNamespace(current_cron_execution=lambda: None)
    store.set_chat_enabled("s", True)
    calls = []
    monkeypatch.setattr(chat_savings, "shrink", lambda *args, **kw: calls.append(("shrink", args, kw)))
    monkeypatch.setattr(chat_savings, "fill_placeholders", lambda *args, **kw: calls.append(("fill", args, kw)))
    monkeypatch.setattr(chat_savings, "cleanup_session", lambda sid: calls.append(("cleanup", sid)))
    monkeypatch.setattr(brake, "forget", lambda sid: calls.append(("forget", sid)))
    capture.make_transform_tool_result(ctx)(tool_name="terminal", result="result", session_id="s", turn_id="t")
    capture.make_transform_llm_output(ctx)(response_text="text", session_id="s", turn_id="t")
    capture.make_on_session_end(ctx)(session_id="s")
    assert calls[0][2] == {"turn_id": "t"}
    assert calls[1][2] == {"session_id": "s", "turn_id": "t"}
    assert calls[-2:] == [("forget", "s"), ("cleanup", "s")]


def _fake_hermes(tmp_path):
    package = tmp_path / "fake" / "hermes_cli"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "main.py").write_text("print('routine-data')")
    return {**os.environ, "PYTHONPATH": str(package.parent)}


def test_generated_job_name_is_data_and_never_executes(monkeypatch, tmp_path):
    monkeypatch.setattr(prerun, "scripts_dir", lambda: tmp_path)
    marker = tmp_path / "injected"
    name = f'job\n"""\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"""'
    prerun.write_script("cr_safe", name)
    path = tmp_path / "cr_safe.py"
    compile(path.read_text(), str(path), "exec")
    result = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, env=_fake_hermes(tmp_path))
    assert result.returncode == 0 and "routine-data" in result.stdout and not marker.exists()


@pytest.mark.parametrize("routine_id", ["../escape", "/tmp/escape", "a/b", "..", "bad\nname"])
def test_generated_script_path_cannot_escape(routine_id):
    with pytest.raises(ValueError):
        prerun.script_rel(routine_id)


def test_existing_non_executable_bash_script_and_workdir_survive(monkeypatch, tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    workdir = tmp_path / "project"
    workdir.mkdir()
    (workdir / "input.txt").write_text("project-data")
    original = scripts / "own.SH"
    original.write_text("cat input.txt\nprintf '\\n'\n")
    original.chmod(0o600)
    monkeypatch.setattr(prerun, "scripts_dir", lambda: scripts / "crystallize")
    prerun.write_script("cr_safe", "job", "own.SH", own_workdir=str(workdir))
    result = subprocess.run([sys.executable, str(scripts / "crystallize" / "cr_safe.py")],
                            capture_output=True, text=True, env=_fake_hermes(tmp_path))
    assert result.returncode == 0 and result.stdout.startswith("project-data") and "routine-data" in result.stdout


def test_large_original_output_does_not_overflow_child_environment(monkeypatch, tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "own.py").write_text("print('x' * 300000)")
    monkeypatch.setattr(prerun, "scripts_dir", lambda: scripts / "crystallize")
    prerun.write_script("cr_safe", "job", "own.py")
    result = subprocess.run([sys.executable, str(scripts / "crystallize" / "cr_safe.py")],
                            capture_output=True, text=True, env=_fake_hermes(tmp_path))
    assert result.returncode == 0 and len(result.stdout) > 300000 and result.stdout.endswith("routine-data\n")


def test_isolated_fixture_exercises_real_sqlite_schema(isolated_plugin_storage):
    store.set_setting("regression", {"actual": True})
    assert store.get_setting("regression") == {"actual": True}
    assert store._db() is isolated_plugin_storage
    assert history._hermes_home().name == "hermes"
