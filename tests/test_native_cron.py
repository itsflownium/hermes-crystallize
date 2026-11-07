"""Native cron sources: explicit review, unchanged reasoning, failure recovery and Undo."""
import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from crystallize import cli, contracts, history, lifecycle, native_cron as native, store
from crystallize.dashboard import plugin_api as api


@pytest.fixture
def world(monkeypatch):
    home = history._hermes_home()
    (home / "scripts").mkdir()
    (home / "cron").mkdir()
    (home / "config.yaml").write_text("model: unchanged\nagent: {reasoning_effort: max}\n")
    source = home / "scripts" / "snapshot.py"
    source.write_text('print("complete snapshot: value 42")\n')
    job = {"id": "native-job", "name": "A snapshot", "state": "scheduled", "deliver": "local",
           "prompt": "Return the latest complete snapshot.", "model": "arbitrary-main", "provider": "arbitrary-provider",
           "reasoning_effort": "max", "script": None, "monitor_script": None, "monitor_url": None,
           "no_agent": False, "interpreter": sys.executable, "workdir": str(home)}
    edits = []
    def save():
        (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [job]}))
    def edit(job_id, *flags):
        assert job_id == job["id"]
        edits.append(flags)
        pos = 0
        while pos < len(flags):
            flag = flags[pos]
            if flag in {"--agent", "--no-agent"}:
                job["no_agent"] = flag == "--no-agent"
                pos += 1
            else:
                assert flag in {"--script", "--monitor-script"}
                job[flag[2:].replace("-", "_")] = flags[pos + 1] or None
                pos += 2
        save()
    save()
    db = sqlite3.connect(home / "cron" / "executions.db")
    db.execute("CREATE TABLE executions(id TEXT PRIMARY KEY,job_id TEXT,status TEXT,claimed_at TEXT)")
    db.commit()
    db.close()
    monkeypatch.setattr(native, "_capability", lambda mode: True)
    monkeypatch.setattr(history, "running_job_ids", lambda: set())
    monkeypatch.setattr(lifecycle, "_edit_job", edit)
    return home, source, job, edits, save


def attach(world, mode="monitor"):
    opts = {"mode": mode, "snapshot_only": True, "verbatim_output": mode == "verbatim"}
    proposal = native.preview("native-job", "snapshot.py", **opts)
    return native.apply("native-job", "snapshot.py", **opts,
                        expected_preview_sha256=proposal["preview_sha256"], confirmed=True)


def execute(world):
    home, source, job, edits, save = world
    return subprocess.run([sys.executable, str(home / "scripts" / (job.get("monitor_script") or job["script"]))],
                           capture_output=True, text=True, timeout=10)


def ledger(world, execution_id, status, job_id="native-job"):
    with sqlite3.connect(world[0] / "cron" / "executions.db") as db:
        db.execute("INSERT INTO executions VALUES(?,?,?,?)", (execution_id, job_id, status, execution_id))


def test_preview_never_executes_source_or_edits_job(world):
    home, source, job, edits, save = world
    marker = home / "executed"
    source.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")
    result = native.preview(job["id"], "snapshot.py")
    assert not result["can_apply"] and not marker.exists() and not edits
    assert result["source"]["text"] == source.read_text() and result["model_calls"] == 0
    assert not (home / "scripts" / "crystallize-native").exists()


@pytest.mark.parametrize("mode", ["monitor", "verbatim"])
def test_only_native_fields_change_and_undo_preserves_main_reasoning(world, mode):
    original = deepcopy(world[2])
    result = attach(world, mode)
    allowed = {"monitor_script"} if mode == "monitor" else {"script", "no_agent"}
    assert result["applied"] and {k: v for k, v in world[2].items() if k not in allowed} == {
        k: v for k, v in original.items() if k not in allowed}
    assert native.undo("native-job", expected_config_fingerprint=native.status("native-job")["config_fingerprint"],
                       confirmed=True)["undone"]
    assert world[2] == original


def test_monitor_output_is_stable_then_failure_forces_exactly_a_new_source_hash(world):
    attach(world)
    first, second = execute(world), execute(world)
    assert first.returncode == second.returncode == 0 and first.stdout == second.stdout
    assert json.loads(first.stdout)["snapshot"] == "complete snapshot: value 42\n"
    ledger(world, "1-failure", "failed")
    retry = execute(world)
    assert retry.stdout != first.stdout
    assert json.loads(retry.stdout)["crystallize_native"]["retry_after"]["failures_retained"] == 1
    ledger(world, "2-success", "completed")
    assert execute(world).stdout == retry.stdout  # success doesn't add an unnecessary wake
    ledger(world, "3-foreign-failure", "failed", job_id="foreign-job")
    assert execute(world).stdout == retry.stdout
    ledger(world, "0-late-receipt", "failed")
    assert execute(world).stdout != retry.stdout


@pytest.mark.parametrize("status", ["unknown", "cancelled", "missed"])
def test_uncertain_terminal_attempts_cannot_suppress_a_retry(world, status):
    attach(world)
    original = execute(world).stdout
    ledger(world, "retry", status)
    assert execute(world).stdout != original


def test_missing_ledger_disables_skips_with_fresh_nonces(world):
    attach(world)
    (world[0] / "cron" / "executions.db").unlink()
    first, second = execute(world), execute(world)
    assert first.returncode == second.returncode == 0 and first.stdout != second.stdout
    assert "unverified_ledger_nonce" in first.stdout


def test_profile_config_change_wakes_once_and_can_stabilize_without_reapproval(world):
    attach(world)
    before = execute(world).stdout
    with (world[0] / "config.yaml").open("a") as stream:
        stream.write("# user changed profile settings\n")
    after = execute(world)
    assert after.returncode == 0 and after.stdout != before
    assert execute(world).stdout == after.stdout
    assert "snapshot" in json.loads(after.stdout)


def test_global_model_settings_do_not_break_complete_verbatim_output(world):
    attach(world, "verbatim")
    (world[0] / "config.yaml").write_text("model: user-selected-new-main\n")
    result = execute(world)
    assert result.returncode == 0 and result.stdout == "complete snapshot: value 42\n"


@pytest.mark.parametrize("mode", ["monitor", "verbatim"])
def test_source_failure_is_a_fresh_main_run_or_visible_failure(world, mode):
    world[1].write_text("raise RuntimeError('private source detail')\n")
    attach(world, mode)
    result = execute(world)
    assert "private source detail" not in result.stdout + result.stderr
    if mode == "monitor":
        assert result.returncode == 0 and json.loads(result.stdout)["crystallize_native"]["status"] == "source_failed"
    else:
        assert result.returncode != 0 and "native source failed" in result.stderr


def test_verbatim_is_exact_and_never_silently_consumes_reserved_control(world):
    world[1].write_text('import sys\nsys.stdout.write("Report 42\\nsecond line\\n")\n')
    attach(world, "verbatim")
    assert execute(world).stdout == "Report 42\nsecond line\n"


def test_scheduler_bookkeeping_does_not_disable_an_approved_source(world):
    attach(world, "verbatim")
    world[2].update(repeat={"times": 1, "completed": 1}, failure_streak=0, last_failure=None,
                    last_run_at="now", state="running", enabled=True,
                    fire_claim={"by": "scheduler", "at": "now"}, pending_slot="current",
                    canonical_completions=["previous-success"], last_delivery_error=None)
    world[4]()
    assert execute(world).returncode == 0
    assert execute(world).stdout == "complete snapshot: value 42\n"


def test_host_omitting_null_optional_fields_does_not_change_task_semantics(world):
    world[2]["optional_host_field"] = None
    world[4]()
    attach(world, "verbatim")
    world[2].pop("optional_host_field")
    world[2].pop("monitor_script")
    world[2].pop("monitor_url")
    world[4]()
    assert execute(world).returncode == 0


def test_source_without_explicit_job_workdir_keeps_its_original_relative_data_directory(world):
    world[2]["workdir"] = None
    world[4]()
    (world[0] / "scripts/value.txt").write_text("current relative input")
    world[1].write_text('from pathlib import Path\nprint(Path("value.txt").read_text())\n')
    attach(world, "verbatim")
    result = execute(world)
    assert result.returncode == 0 and result.stdout == "current relative input\n"


@pytest.mark.parametrize("body", ['print(\'\')\n', 'print(\'{"wakeAgent":false}\')\n', 'print("x"*6001)\n'])
def test_empty_silent_control_and_oversized_verbatim_outputs_fail(world, body):
    world[1].write_text(body)
    attach(world, "verbatim")
    assert execute(world).returncode != 0


def test_changed_source_after_preview_is_rejected_without_job_edits(world):
    result = native.preview("native-job", "snapshot.py", snapshot_only=True)
    world[1].write_text("print('a new source')\n")
    with pytest.raises(native.NativeCronError, match="changed after preview"):
        native.apply("native-job", "snapshot.py", snapshot_only=True,
                     expected_preview_sha256=result["preview_sha256"], confirmed=True)
    assert not world[3]


def test_contract_change_revokes_prior_snapshot_approval_before_it_can_skip(world):
    attach(world)
    before = execute(world).stdout
    contracts.set("native-job", {"required_fields": ["new_requirement"]})
    after = execute(world)
    assert after.returncode == 0 and after.stdout != before
    assert json.loads(after.stdout)["crystallize_native"]["status"] == "configuration_changed"
    assert native.status("native-job")["state"]["approval_revoked"]


@pytest.mark.parametrize("field,value", [("prompt", "A changed task"), ("reasoning_effort", "high"), ("model", "changed")])
def test_semantic_job_drift_disables_source_and_wakes_normal_agent(world, field, value):
    attach(world)
    world[2][field] = value
    world[4]()
    result = execute(world)
    assert json.loads(result.stdout)["crystallize_native"]["status"] == "configuration_changed"


def test_undo_preserves_newer_native_source_and_pending_recovery(world):
    attach(world)
    world[2]["monitor_script"] = "user-replacement.py"
    world[4]()
    with pytest.raises(native.NativeCronError, match="edited elsewhere"):
        native.undo("native-job", expected_config_fingerprint=native.status("native-job")["config_fingerprint"], confirmed=True)
    assert world[2]["monitor_script"] == "user-replacement.py"
    assert native.status("native-job")["state"]["status"] == "applied"


def test_symlinks_absolute_paths_and_traversal_are_rejected(world):
    (world[0] / "scripts" / "alias.py").symlink_to(world[1])
    for reference in (str(world[1]), "../snapshot.py", "alias.py"):
        with pytest.raises(native.NativeCronError):
            native.preview("native-job", reference)


def test_cli_is_preview_only_and_api_refuses_unconfirmed_apply(world, monkeypatch):
    parser = argparse.ArgumentParser()
    cli._setup(parser)
    args = parser.parse_args(["native-cron", "native-job", "--source-script", "snapshot.py"])
    assert not args.apply and not args.confirm and not args.snapshot_only
    monkeypatch.setattr(api, "_module", lambda name: native)
    result = asyncio.run(api.native_cron_apply({"job_id": "native-job", "source_script": "snapshot.py",
        "mode": "monitor", "snapshot_only": True, "verbatim_output": False, "confirmed": False,
        "expected_preview_sha256": "unreviewed"}))
    assert result["code"] == "action_blocked" and not world[3]
