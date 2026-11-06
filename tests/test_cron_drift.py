"""Configuration drift revalidates by stable ID and preserves intervening edits."""

from copy import deepcopy

import pytest

from crystallize import contracts, history, lifecycle, prerun, store, tuning
from crystallize.history import Run


def env(monkeypatch, *, prompt="Summarize https://example.com/feed", own_script="own.sh"):
    # This fixture tests restoration with an injected, non-executing fake host.
    # Real public SDK support remains false and is tested separately.
    monkeypatch.setattr(lifecycle, "replay_support", lambda: {"guarded_dispatch_available": True, "automatic_nested_tools": True})
    job = {"id": "j1", "name": "job", "prompt": prompt, "model": "test-model", "provider": "test",
           "reasoning_effort": "high", "enabled_toolsets": ["web", "messaging"], "script": own_script,
           "no_agent": False}
    jobs = [job]
    edits = []
    monkeypatch.setattr(lifecycle, "all_cron_jobs", lambda: jobs)

    def edit(jid, *flags):
        target = next(j for j in jobs if j["id"] == jid)
        edits.append((jid, flags))
        i = 0
        names = {"--prompt": "prompt", "--script": "script", "--reasoning-effort": "reasoning_effort"}
        while i < len(flags):
            flag = flags[i]
            if flag in ("--agent", "--no-agent"):
                target["no_agent"] = flag == "--no-agent"
                i += 1
            else:
                target[names[flag]] = flags[i + 1] or None
                i += 2

    monkeypatch.setattr(lifecycle, "_edit_job", edit)
    monkeypatch.setattr(lifecycle, "set_toolsets", lambda jid, tools: next(j for j in jobs if j["id"] == jid).update(enabled_toolsets=tools))
    monkeypatch.setattr(lifecycle, "_set_job_fields", lambda jid, fields: next(j for j in jobs if j["id"] == jid).update(fields))
    steps = {"version": 1, "job_id": "j1", "steps": [{"tool": "web_extract", "args": {"urls": ["https://example.com/feed"]},
             "fill_ins": [], "check": {"min_len": 1}}]}
    routine = {"id": "cr_1", "name": "job", "source": "cron", "source_ref": "j1", "status": "testing",
               "steps": steps, "meta": {"ready": True, "matches": 3}}
    routine["meta"]["config_snapshot"] = lifecycle.configuration_fingerprint(job, routine)
    store.save_routine("cr_1", name="job", source="cron", source_ref="j1", status="testing", steps=steps, meta=routine["meta"])
    return job, jobs, edits


def test_installed_wrappers_hints_and_tool_order_do_not_create_drift(monkeypatch):
    job, _, _ = env(monkeypatch)
    original = lifecycle.configuration_fingerprint(job, store.get_routine("cr_1"))
    lifecycle.promote("cr_1")
    assert lifecycle.check_drift("cr_1")
    assert store.get_routine("cr_1")["meta"]["config_snapshot"]["fingerprint"] == original["fingerprint"]
    job["prompt"] += tuning.EFFICIENCY_HINT
    job["enabled_toolsets"] = list(reversed(job["enabled_toolsets"]))
    assert lifecycle.check_drift("cr_1")


def test_harmless_prompt_formatting_is_ignored_but_code_indentation_is_not(monkeypatch):
    job, _, _ = env(monkeypatch)
    a = lifecycle.configuration_fingerprint(job)
    job["prompt"] = "  Summarize\n\n https://example.com/feed  "
    assert lifecycle.configuration_fingerprint(job)["fingerprint"] == a["fingerprint"]
    job["prompt"] = "```python\nif True:\n    print(1)\n```"
    a = lifecycle.configuration_fingerprint(job)
    job["prompt"] = job["prompt"].replace("    print", "print")
    assert lifecycle.configuration_fingerprint(job)["fingerprint"] != a["fingerprint"]


@pytest.mark.parametrize("field,value", [("prompt", "Read https://new.example/feed"), ("model", "another-model"),
                                               ("provider", "another-route"), ("reasoning_effort", "medium"),
                                               ("enabled_toolsets", ["web"]), ("workdir", "/new/work")])
def test_meaningful_user_changes_detach_and_require_fresh_revalidation(monkeypatch, field, value):
    job, _, _ = env(monkeypatch)
    lifecycle.promote("cr_1")
    job[field] = value
    assert not lifecycle.check_drift("cr_1")
    current = store.get_routine("cr_1")
    assert current["status"] == "testing" and not current["meta"]["ready"]
    assert current["meta"]["revalidation"]["needs_rebuild"]
    assert job[field] == value
    assert job["script"] == "own.sh" and not job["no_agent"]


def test_contract_recipe_bindings_and_shared_cache_settings_are_fingerprinted(monkeypatch):
    job, _, _ = env(monkeypatch)
    baseline = lifecycle.configuration_fingerprint(job, store.get_routine("cr_1"))
    contracts.set("j1", {"minimum_items": 2})
    assert lifecycle.configuration_fingerprint(job, store.get_routine("cr_1"))["fingerprint"] != baseline["fingerprint"]
    contracts.set("j1", None)
    routine = store.get_routine("cr_1")
    routine["steps"]["steps"][0]["args"] = {"url": {"$result": {"step": 1, "path": ["url"]}}}
    assert lifecycle.configuration_fingerprint(job, routine)["fingerprint"] != baseline["fingerprint"]
    store.set_setting("fetch_cache:j1", {"ttl_seconds": 60, "namespace": "demo"})
    assert lifecycle.configuration_fingerprint(job, store.get_routine("cr_1"))["fingerprint"] != baseline["fingerprint"]


def test_undo_preserves_updated_task_and_user_script_instead_of_saved_snapshot(monkeypatch):
    job, _, _ = env(monkeypatch)
    lifecycle.promote("cr_1")
    job["prompt"] = lifecycle.prerun_prompt("cr_1", "New user task; retain this edit")
    job["script"] = "user-replacement.sh"
    lifecycle.undo("cr_1")
    assert job["prompt"] == "New user task; retain this edit"
    assert job["script"] == "user-replacement.sh"


def test_unrecognized_edited_wrapper_keeps_fallback_script_and_user_text(monkeypatch):
    job, _, _ = env(monkeypatch)
    lifecycle.promote("cr_1")
    job["prompt"] = job["prompt"].replace("Current and complete", "checked") + "\nExtra user instruction"
    # Make a real wrapper-prefix edit, rather than an edit in its task body.
    job["prompt"] = job["prompt"].replace("The Script Output", "My Script Output")
    edited = job["prompt"]
    assert not lifecycle.check_drift("cr_1")
    assert job["prompt"] == edited and job["script"] == "crystallize/cr_1.py"
    assert store.get_routine("cr_1")["status"] == "testing"
    assert prerun.run("cr_1", lambda *args: pytest.fail("must not replay while testing"))[1] == 0


def test_tuning_restore_targets_renamed_id_and_preserves_overrides(monkeypatch):
    job, jobs, edits = env(monkeypatch)
    t = {"job_id": "j1", "job_name": "job", "status": "kept", "finished_at": 10, "level": "low",
         "previous": "high", "toolsets": ["web"], "previous_toolsets": ["web", "messaging"],
         "tools_changed": True, "hint": True}
    job.update(name="renamed", reasoning_effort="low", enabled_toolsets=["web"], prompt=job["prompt"] + tuning.EFFICIENCY_HINT)
    jobs.append({"id": "j2", "name": "job", "prompt": "unrelated", "reasoning_effort": "low"})
    store.save_tuning("job", t)
    assert tuning.status("j1")["job_id"] == "j1"
    assert tuning.status("renamed")["job_id"] == "j1"
    assert tuning.status("j2") is None
    tuning.undo("j1")
    assert job["reasoning_effort"] == "high" and jobs[1]["prompt"] == "unrelated"
    assert edits and all(jid == "j1" for jid, flags in edits)
    assert store.get_tuning("job")["kept_at"] == 10 and store.get_tuning("job")["undone_at"]
    # Restoring again must not overwrite settings a user selected afterwards.
    job.update(reasoning_effort="medium", enabled_toolsets=["custom"])
    tuning._restore(t)
    assert job["reasoning_effort"] == "medium" and job["enabled_toolsets"] == ["custom"]


def test_routine_undo_does_not_resurrect_an_undone_efficiency_hint(monkeypatch):
    job, _, _ = env(monkeypatch)
    job["prompt"] += tuning.EFFICIENCY_HINT
    store.save_tuning("job", {"job_name": "job", "job_id": "j1", "status": "kept", "hint": True, "finished_at": 1})
    routine = store.get_routine("cr_1")
    store.update_routine("cr_1", meta={**routine["meta"], "config_snapshot": lifecycle.configuration_fingerprint(job, routine)})
    lifecycle.promote("cr_1")
    tuning.undo("j1")
    lifecycle.undo("cr_1")
    assert job["prompt"] == "Summarize https://example.com/feed"


def test_tuning_drift_stops_transforms_and_uses_fresh_baseline(monkeypatch):
    job, _, _ = env(monkeypatch)
    t = {"job_id": "j1", "job_name": "job", "status": "kept", "finished_at": 1, "level": "low", "previous": "high",
         "hint": True, "clean": True, "tools_changed": True, "toolsets": ["web"], "previous_toolsets": ["web", "messaging"]}
    t["config_snapshot"] = tuning._snapshot(job, t)
    job.update(reasoning_effort="low", enabled_toolsets=["web"], prompt=job["prompt"] + tuning.EFFICIENCY_HINT)
    assert not tuning._configuration_drift(t)  # our own mutations are recognized
    job["prompt"] = "New user task" + tuning.EFFICIENCY_HINT
    assert tuning._configuration_drift(t)
    assert t["status"] == "revalidation" and not tuning.cleans_results("j1")
    assert job["prompt"] == "New user task" and job["reasoning_effort"] == "high"
    assert t["kept_at"] == 1
    monkeypatch.setattr(tuning, "_split", lambda *args: ([Run("old", "j1", "job", "sid", True, 0, session_started=0)], []))
    with pytest.raises(lifecycle.LifecycleError, match="finished runs"):
        tuning.start("j1")


def test_revalidation_rebuilds_targets_from_fresh_runs_not_old_shape(monkeypatch):
    job, _, _ = env(monkeypatch)
    lifecycle.promote("cr_1")
    monkeypatch.setattr(lifecycle.time, "time", lambda: 10)
    job["prompt"] = "Fetch https://new.example/feed"
    assert not lifecycle.check_drift("cr_1")
    routine = store.get_routine("cr_1")
    old = Run("old", "j1", "job", "old", True, 1, session_started=1)
    fresh = [Run(str(i), "j1", "job", str(i), True, float(i), session_started=float(i)) for i in (11, 12, 13)]
    monkeypatch.setattr(lifecycle.history, "load_runs", lambda *args, **kwargs: [old, *fresh])
    seen = []
    monkeypatch.setattr(lifecycle.analyze, "analyze_runs", lambda runs, prompt: seen.append((runs, prompt)) or object())
    monkeypatch.setattr(lifecycle.analyze, "regular_actions", lambda runs: set())
    new_steps = {"version": 1, "steps": [{"tool": "web_extract", "args": {"urls": ["https://new.example/feed"]}}]}
    monkeypatch.setattr(lifecycle.steplist, "build", lambda analysis: deepcopy(new_steps))
    rebuilt = lifecycle._rebuild_after_drift(routine)
    assert seen[0] == (fresh, "Fetch https://new.example/feed")
    assert rebuilt["steps"] == new_steps and not rebuilt["meta"]["ready"]
    assert not rebuilt["meta"]["revalidation"]["needs_rebuild"]
    assert lifecycle.check_drift(rebuilt)


def test_cli_children_keep_the_resolved_profile_home(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE", "isolated")
    assert lifecycle.hermes_env()["HERMES_HOME"] == str(history._hermes_home())
    assert lifecycle.hermes_env()["HERMES_PROFILE"] == "isolated"
