"""Current public SDK behavior: host plans, observation and reversible recovery."""

from copy import deepcopy
import hashlib
import json
import time

import pytest

from crystallize import dispatch_support, history, lifecycle, prerun, runner, store, tools, tuning
from crystallize.history import Run


@pytest.fixture
def public_job(monkeypatch):
    job = {"id": "public-job", "name": "public job", "state": "scheduled", "prompt": "Read the current source and verify the report.",
           "script": "own.sh", "interpreter": "/user/python", "workdir": "/user/work", "no_agent": False,
           "model": "main-model", "provider": "main-provider", "reasoning_effort": "high", "enabled_toolsets": ["web", "file"]}
    edits, files = [], []
    monkeypatch.setattr(lifecycle, "all_cron_jobs", lambda: [job])
    monkeypatch.setattr(prerun, "write_script", lambda *args, **kwargs: files.append("write") or "crystallize/public-routine.py")
    monkeypatch.setattr(prerun, "remove_script", lambda ref: files.append(("remove", ref)))
    allowed = {"--prompt": "prompt", "--script": "script", "--interpreter": "interpreter", "--workdir": "workdir",
               "--reasoning-effort": "reasoning_effort"}
    def edit(ref, *flags):
        assert ref == job["id"]
        edits.append(flags)
        index = 0
        while index < len(flags):
            flag = flags[index]
            if flag in {"--agent", "--no-agent"}:
                job["no_agent"] = flag == "--no-agent"
                index += 1
            else:
                assert flag in allowed
                job[allowed[flag]] = flags[index + 1] or None
                index += 2
    monkeypatch.setattr(lifecycle, "_edit_job", edit)
    recipe = {"version": 1, "steps": [{"tool": "web_extract", "args": {"url": "https://example.invalid/current"},
                                        "check": {"min_len": 1}}]}
    routine = {"id": "public-routine", "name": job["name"], "source_ref": job["id"], "status": "testing", "steps": recipe,
               "meta": {"ready": True, "matches": 3, "created_at": 10, "validation_mode": "normal_host_observations"}}
    routine["meta"]["config_snapshot"] = lifecycle.configuration_fingerprint(job, routine)
    store.save_routine(routine["id"], name=routine["name"], source="cron", source_ref=job["id"], status="testing",
                       steps=recipe, meta=routine["meta"])
    return job, routine["id"], edits, files, edit


def test_raw_public_method_presence_never_enables_automatic_replay():
    class RawContext:
        def dispatch_tool(self, *args, **kwargs):
            pytest.fail("Raw dispatch must never be called")
    support = dispatch_support.support(RawContext())
    assert support["public_dispatch_method_present"]
    assert not support["guarded_dispatch_available"] and not support["automatic_nested_tools"]
    assert not lifecycle._replay_available()


def test_availability_offers_explicit_host_plan_and_labels_observation(public_job):
    job, rid, edits, files, _ = public_job
    availability = lifecycle.promotion_availability(rid)
    assert availability["modes"]["tool"]["available"]
    assert not availability["modes"]["prerun"]["available"] and not availability["modes"]["no_ai"]["available"]
    assert availability["test_execution"] == "observe_normal_host_runs"
    assert availability["validation_mode"] == "normal_host_observations"
    assert not availability["automatic_replay_validated"]
    assert not edits and not files


@pytest.mark.parametrize("mode", ["prerun", "no_ai"])
def test_unsupported_automatic_modes_never_mutate_a_job(public_job, mode):
    job, rid, edits, files, _ = public_job
    original = deepcopy(job)
    with pytest.raises(lifecycle.LifecycleError, match="Choose explicit mode 'tool'"):
        lifecycle.promote(rid, mode=mode)
    assert job == original and not edits and not files


def test_explicit_plan_promotion_changes_only_prompt_and_undo_restores_it(public_job):
    job, rid, edits, files, _ = public_job
    original = deepcopy(job)
    promoted = lifecycle.promote(rid, mode="tool")
    assert promoted["status"] == "live" and promoted["meta"]["mode"] == "tool"
    assert edits and all(flags[0] == "--prompt" and len(flags) == 2 for flags in edits)
    assert {key: value for key, value in job.items() if key != "prompt"} == {key: value for key, value in original.items() if key != "prompt"}
    assert "pending_host_tools" in job["prompt"] and "normal host tools and approvals" in job["prompt"]
    assert not files
    lifecycle.undo(rid)
    assert job == original


def test_live_plan_returns_guidance_without_success_or_nested_execution(public_job, monkeypatch):
    job, rid, edits, files, _ = public_job
    lifecycle.promote(rid, mode="tool")
    monkeypatch.setattr(runner, "run", lambda *args, **kwargs: pytest.fail("No routine may run on this SDK"))
    monkeypatch.setattr(lifecycle, "live_result", lambda *args, **kwargs: pytest.fail("Guidance is not an executed result"))
    result = json.loads(tools.crystal_run({"routine_id": rid}))
    assert not result["ok"] and not result["completed"] and result["status"] == "pending_host_tools"
    assert result["host_tool_plan"]["actions"][0]["tool"] == "web_extract"
    assert result["outputs"] == []


def _attach_legacy(public_job, *, changed_prompt=False, changed_script=False):
    job, rid, edits, files, edit = public_job
    original = deepcopy(job)
    routine = store.get_routine(rid)
    meta = {**routine["meta"], "mode": "no_ai", "original_prompt": job["prompt"], "own_script": job["script"],
            "own_interpreter": job["interpreter"], "own_workdir": job["workdir"], "installed_script": f"crystallize/{rid}.py",
            "installed_interpreter": "/host/python", "original_no_agent": False, "promoted_at": 20}
    job.update(prompt=lifecycle.prerun_prompt(rid, job["prompt"]), script=meta["installed_script"],
               interpreter=meta["installed_interpreter"], no_agent=True)
    if changed_prompt:
        job["prompt"] = job["prompt"].replace("The Script Output", "My edited Script Output") + "\nKeep my added instruction."
    if changed_script:
        job["script"] = "user-replacement.sh"
        job["interpreter"] = "/user/new-python"
    meta["config_snapshot"] = lifecycle.configuration_fingerprint(job, {**routine, "meta": meta})
    store.update_routine(rid, status="live", meta=meta)
    return original


@pytest.mark.parametrize("changed_prompt,changed_script", [(False, False), (True, False), (False, True), (True, True)])
def test_sdk_loss_restores_legacy_agent_without_disabling_job_or_overwriting_edits(public_job, changed_prompt, changed_script):
    original = _attach_legacy(public_job, changed_prompt=changed_prompt, changed_script=changed_script)
    job, rid, edits, files, _ = public_job
    edited_prompt = job["prompt"]
    assert lifecycle.restore_unsupported_replay(rid)
    assert job["state"] == "scheduled" and not job["no_agent"]
    assert job["model"] == original["model"] and job["reasoning_effort"] == original["reasoning_effort"]
    assert job["script"] == ("user-replacement.sh" if changed_script else original["script"])
    assert job["interpreter"] == ("/user/new-python" if changed_script else original["interpreter"])
    assert job["prompt"] == (lifecycle.SDK_FALLBACK_NOTE + edited_prompt if changed_prompt else original["prompt"])
    current = store.get_routine(rid)
    assert current["status"] == "testing" and not current["meta"]["ready"]
    assert current["meta"]["revalidation"]["validation_mode"] == "normal_host_observations"
    assert not current["meta"]["dispatch_recovery"]["pending"]
    assert not lifecycle.restore_unsupported_replay(rid)  # idempotent after owned fields are detached


def test_failed_upgrade_restoration_keeps_live_recovery_and_retries(public_job, monkeypatch):
    _attach_legacy(public_job)
    job, rid, edits, files, healthy = public_job
    monkeypatch.setattr(lifecycle, "_edit_job", lambda *args: (_ for _ in ()).throw(lifecycle.LifecycleError("CLI unavailable")))
    assert lifecycle.restore_unsupported_replay(rid)
    pending = store.get_routine(rid)
    assert pending["status"] == "live" and pending["meta"]["dispatch_recovery"]["pending"]
    assert job["state"] == "scheduled" and job["no_agent"]
    monkeypatch.setattr(lifecycle, "_edit_job", healthy)
    assert lifecycle.restore_unsupported_replay(rid)
    assert not job["no_agent"] and store.get_routine(rid)["status"] == "testing"


def test_original_script_only_job_keeps_its_user_owned_no_agent_setting(public_job):
    _attach_legacy(public_job)
    job, rid, edits, files, _ = public_job
    routine = store.get_routine(rid)
    store.update_routine(rid, meta={**routine["meta"], "original_no_agent": True})
    lifecycle.restore_unsupported_replay(rid)
    assert job["no_agent"] and job["script"] == "own.sh"


@pytest.mark.parametrize("restore", ["automatic", "undo"])
def test_interrupted_automatic_promotion_keeps_recovery_until_owned_fields_restore(public_job, restore):
    _attach_legacy(public_job)
    job, rid, edits, files, _ = public_job
    routine = store.get_routine(rid)
    recovery = {key: routine["meta"][key] for key in ("mode", "original_prompt", "own_script", "own_interpreter",
                "installed_script", "installed_interpreter", "original_no_agent")}
    recovery.update(job_id=job["id"], at=time.time())  # recent, not stale
    store.update_routine(rid, status="testing", meta={"promoting": recovery})
    if restore == "automatic":
        lifecycle.recover_promotions()
    else:
        lifecycle.undo(rid)
    assert not job["no_agent"] and job["script"] == "own.sh" and job["interpreter"] == "/user/python"
    current = store.get_routine(rid)
    assert "promoting" not in current["meta"]
    if restore == "automatic":
        assert current["status"] == "testing" and not current["meta"]["ready"]
    else:
        assert current["status"] == "retired"


def test_prerun_never_dispatches_on_current_sdk_and_reports_old_no_ai_failure(public_job, monkeypatch):
    _attach_legacy(public_job)
    job, rid, edits, files, _ = public_job
    monkeypatch.setattr(runner, "run", lambda *args: pytest.fail("No nested routine tools may execute"))
    text, code = prerun.run(rid, lambda *args: pytest.fail("No raw or private dispatch fallback"))
    assert code == 1 and "No routine tools ran" in text
    assert not job["no_agent"]  # future scheduled runs regain their normal agent


def test_observation_readiness_uses_real_normal_runs_and_never_claims_replay(public_job, monkeypatch):
    job, rid, edits, files, _ = public_job
    routine = store.get_routine(rid)
    store.update_routine(rid, meta={**routine["meta"], "ready": False, "matches": 0})
    text = '{"status":200,"items":[{"title":"current"}]}'
    traces = {f"run-{i}": [{"tool_name": "web_extract", "args_json": '{"url":"https://example.invalid/current"}',
                           "status": "ok", "result_preview": text, "result_len": len(text),
                           "result_sha256": hashlib.sha256(text.encode()).hexdigest()}] for i in range(3)}
    runs = [Run(f"run-{i}", job["id"], job["name"], f"s{i}", True, 20 + i, finished=True,
                session_started=20 + i, outcome="completed") for i in range(3)]
    monkeypatch.setattr(history, "load_runs", lambda *args, **kwargs: runs)
    monkeypatch.setattr(history, "final_answers", lambda ids: {sid: "Current report" for sid in ids})
    monkeypatch.setattr(store, "execution_traces", lambda ref: traces[ref])
    monkeypatch.setattr(runner, "run", lambda *args: pytest.fail("Normal observations must never replay"))
    result = lifecycle.test_after("run-2", job["id"], lambda *args: pytest.fail("No dispatcher calls"))
    assert result["ready"] and result["matches"] == 3 and not result["executed"]
    assert result["validation_mode"] == "normal_host_observations" and not result["automatic_replay_validated"]
    assert lifecycle.test_after("run-2", job["id"], lambda *args: None) is None
    assert store.get_routine(rid)["meta"]["matches"] == 3  # no duplicate observation credit
    assert not edits and not files


def test_legacy_toolsets_are_confirmed_or_left_pending_for_normal_host_controls(public_job):
    job, rid, edits, files, _ = public_job
    assert lifecycle.set_toolsets(job["id"], job["enabled_toolsets"]) is None
    before = deepcopy(job)
    with pytest.raises(lifecycle.PublicToolsetRecovery, match="cronjob_manage"):
        lifecycle.set_toolsets(job["id"], ["different"])
    assert job == before and not edits
    job["enabled_toolsets"] = None
    assert lifecycle.set_toolsets(job["id"], None) is None
    job["enabled_toolsets"] = []
    assert lifecycle.set_toolsets(job["id"], []) is None


def test_pending_toolset_recovery_restores_other_owned_fields_and_can_finish_idempotently(public_job):
    job, rid, edits, files, _ = public_job
    job.update(reasoning_effort="low", enabled_toolsets=["web"], prompt=job["prompt"] + tuning.LEGACY_EFFICIENCY_HINT)
    tune = {"job_name": job["name"], "job_id": job["id"], "status": "kept", "finished_at": 10,
            "level": "low", "previous": "high", "toolsets": ["web"], "previous_toolsets": ["web", "file"],
            "tools_changed": True, "hint": True, "hint_added": True}
    store.save_tuning(job["name"], tune)
    with pytest.raises(lifecycle.PublicToolsetRecovery):
        tuning.undo(job["id"])
    pending = store.get_tuning(job["name"])
    assert pending["needs_restore"] and pending["recovery"]["pending"]
    assert pending["recovery"]["normal_host_tool_plan"]["arguments"]["enabled_toolsets"] == ["web", "file"]
    assert job["reasoning_effort"] == "high" and tuning.LEGACY_EFFICIENCY_HINT not in job["prompt"]
    assert job["enabled_toolsets"] == ["web"] and "Restoration pending" in tuning.status_text(pending)
    job["enabled_toolsets"] = ["web", "file"]  # simulate the user's ordinary guarded host edit
    result = tuning.undo(job["id"])
    assert result["status"] == "undone" and not result.get("needs_restore")
    assert not result["recovery"]["pending"]


def test_public_cli_child_env_never_inherits_yolo_override(monkeypatch):
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")
    monkeypatch.setenv("HERMES_PROFILE", "isolated-test-profile")
    child = lifecycle.hermes_env()
    assert "HERMES_YOLO_MODE" not in child and child["HERMES_PROFILE"] == "isolated-test-profile"
    assert child["HERMES_HOME"] == str(history._hermes_home())


@pytest.mark.parametrize("choice", ["repeat", "quiet", "auto"])
def test_current_sdk_cannot_claim_automatic_unchanged_behavior(public_job, choice):
    job, rid, edits, files, _ = public_job
    before = store.get_routine(rid)
    with pytest.raises(lifecycle.LifecycleError, match="unchanged-run behavior is unavailable"):
        lifecycle.set_when_unchanged(rid, choice)
    assert store.get_routine(rid) == before and not edits and not files


@pytest.mark.parametrize("choice", ["run", "off"])
def test_run_or_off_clears_legacy_unchanged_choices_on_current_sdk(public_job, choice):
    job, rid, edits, files, _ = public_job
    routine = store.get_routine(rid)
    store.update_routine(rid, meta={**routine["meta"], "when_unchanged": "quiet", "skip_unchanged": True})
    lifecycle.set_when_unchanged(rid, choice)
    meta = store.get_routine(rid)["meta"]
    assert meta["when_unchanged"] == "run" and "skip_unchanged" not in meta
    assert not edits and not files


@pytest.mark.parametrize("choice", ["repeat", "quiet", "auto"])
def test_verified_fake_automatic_mode_can_configure_unchanged_behavior(public_job, monkeypatch, choice):
    job, rid, edits, files, _ = public_job
    routine = store.get_routine(rid)
    store.update_routine(rid, meta={**routine["meta"], "mode": "prerun", "skip_unchanged": False})
    # This test injects a verified fake capability only for the configuration
    # branch; it never calls a host dispatcher or enables a production override.
    monkeypatch.setattr(lifecycle, "_replay_available", lambda: True)
    lifecycle.set_when_unchanged(rid, choice)
    meta = store.get_routine(rid)["meta"]
    assert meta.get("when_unchanged") == (None if choice == "auto" else choice)
    assert "skip_unchanged" not in meta and not edits and not files


def test_host_plan_mode_cannot_claim_automatic_checks_even_with_verified_fake_sdk(public_job, monkeypatch):
    job, rid, edits, files, _ = public_job
    lifecycle.promote(rid, mode="tool")
    before = store.get_routine(rid)
    monkeypatch.setattr(lifecycle, "_replay_available", lambda: True)
    with pytest.raises(lifecycle.LifecycleError, match="Host-tool plan mode"):
        lifecycle.set_when_unchanged(rid, "repeat")
    assert store.get_routine(rid) == before
