"""Bindings and caches preserve fresh data, policy gates and profile boundaries."""
import datetime as dt
import json
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from crystallize import fetch_cache, history, parameters, recipes, runner, store


def test_fresh_lookup_result_binds_to_next_read():
    recipe = {"steps": [
        {"id": "latest", "tool": "web_extract", "args": {"urls": ["https://e.invalid/latest"]}},
        {"tool": "web_extract", "args": {"urls": [{"$url": {"template": "https://e.invalid/orders/{id}",
          "values": {"id": {"$result": {"step": "latest", "path": ["id"]}}}}}]}},
    ]}
    seen = []
    def dispatch(tool, args):
        seen.append(args)
        return json.dumps({"id": "new/id?x=1"}) if len(seen) == 1 else '{"status":"new"}'
    assert runner.run(recipe, dispatch)["ok"]
    assert seen[1]["urls"] == ["https://e.invalid/orders/new%2Fid%3Fx%3D1"]


def test_missing_binding_stops_before_dependent_tool():
    recipe = {"steps": [
        {"tool": "web_extract", "args": {}},
        {"tool": "web_extract", "args": {"id": {"$result": {"step": 1, "path": ["missing"]}}}},
    ]}
    seen = []
    result = runner.run(recipe, lambda *args: seen.append(args) or '{}')
    assert not result["ok"] and result["failed_step"] == 2 and len(seen) == 1


def test_date_window_preserves_relative_days_and_timezone(tmp_path):
    now = dt.datetime(2026, 11, 6, 18, tzinfo=dt.timezone.utc)
    bound = parameters.RunParameters(tmp_path, now)
    args = {"since": {"$date": {"offset_days": -7, "timezone": "Australia/Sydney"}},
            "until": {"$date": {"timezone": "Australia/Sydney"}}}
    parameters.validate({"steps": [{"tool": "web_extract", "args": args}]})
    values = bound.resolve(args)
    assert values == {"since": "2026-10-31", "until": "2026-11-07"}


@pytest.mark.parametrize("value", [
    {"$result": {"step": 2}}, {"$artifact": "../private"},
    {"$date": {"timezone": "Not/AZone"}}, {"$argv": [{"$result": {"step": 1}}]},
])
def test_invalid_bindings_fail_before_dispatch(value):
    seen = []
    result = runner.run({"steps": [{"tool": "web_extract", "args": {"value": value}}]},
                        lambda *args: seen.append(args) or '{}')
    assert not result["ok"] and not seen


def test_dynamic_commands_must_use_quoted_argv():
    seen = []
    result = runner.run({"steps": [{"tool": "terminal", "args": {"command": {"$date": {}}}}]},
                        lambda *args: seen.append(args) or 'ok')
    assert not result["ok"] and not seen


def test_artifacts_share_one_path_and_are_cleaned_after_run():
    recipe = {"steps": [
        {"tool": "terminal", "args": {"command": {"$argv": ["curl", "--output", {"$artifact": "feed.json"}, "https://e.invalid"]}}},
        {"tool": "read_file", "args": {"path": {"$artifact": "feed.json"}}},
    ]}
    paths = []
    def dispatch(tool, args):
        if tool == "terminal":
            path = Path(shlex.split(args["command"])[2]); paths.append(path)
            path.write_text('{"fresh":true}')
            return '{"output":"saved","exit_code":0}'
        path = Path(args["path"]); paths.append(path)
        return path.read_text()
    result = runner.run(recipe, dispatch)
    assert result["ok"] and paths[0] == paths[1] and not paths[0].exists()
    assert result["outputs"][-1]["artifacts"] == [{"name": "feed.json", "content": '{"fresh":true}'}]


def test_literal_scratch_write_is_not_an_artifact():
    seen = []
    result = runner.run({"steps": [{"tool": "terminal", "args": {"command": "curl https://e.invalid > /tmp/shared-report"}}]},
                        lambda *args: seen.append(args) or 'saved')
    assert not result["ok"] and not seen and "artifact" in result["reason"]


def test_old_mutating_recipe_is_rechecked_before_any_dispatch():
    seen = []
    result = runner.run({"steps": [{"tool": "execute_code", "args": {"code": "open('a','w')"}}]},
                        lambda *args: seen.append(args) or 'saved')
    assert not result["ok"] and not seen


def test_cache_hit_still_requires_host_policy_gate():
    middleware = fetch_cache.make_tool_execution(SimpleNamespace(current_cron_execution=lambda: None))
    calls, guards = [], []
    def handler(args):
        calls.append(args); return '{"content":"current"}'
    def host_call(allowed=True):
        guards.append(allowed)
        if not allowed:
            return '{"error":"blocked"}'
        return middleware(tool_name="web_extract", args={"url": "https://e.invalid"}, next_call=handler)
    with fetch_cache.activate({"ttl_seconds": 60, "namespace": "public-test"}) as active:
        first, second = host_call(), host_call()
        assert json.loads(second)["content"] == json.loads(first)["content"]
        assert fetch_cache.is_marked(second) and not fetch_cache.is_marked(first)
        assert "blocked" in host_call(False)
        assert active.hits == 1
    assert len(calls) == 1 and guards == [True, True, False]


def test_cache_isolation_and_expiry(monkeypatch, tmp_path):
    clock = [1000.]
    monkeypatch.setattr(fetch_cache.time, "time", lambda: clock[0])
    key = fetch_cache.key("web_extract", {"url": "https://e.invalid"}, "shared")
    fetch_cache.put(key, 'first', 60)
    assert fetch_cache.get(key, 10) == 'first'
    clock[0] += 11
    assert fetch_cache.get(key, 10) is None
    assert fetch_cache.get(key, 60) == 'first'
    monkeypatch.setattr(history, "_hermes_home", lambda: tmp_path / "other-profile")
    assert fetch_cache.get(key, 60) is None


def test_cache_never_stores_secrets_errors_or_changes_result_type():
    middleware = fetch_cache.make_tool_execution(SimpleNamespace(current_cron_execution=lambda: None))
    calls = []
    def handler(args):
        calls.append(args); return {"data": "dict remains a dict"}
    with fetch_cache.activate({"ttl_seconds": 60, "namespace": "test"}):
        assert isinstance(middleware(tool_name="web_extract", args={}, next_call=handler), dict)
        middleware(tool_name="web_extract", args={}, next_call=handler)
    assert len(calls) == 2
    assert fetch_cache.key('web_extract', {'url': 'https://e.invalid?api_key=secret'}, 'n') is None
    assert fetch_cache.key('terminal', {'command': 'cat /tmp/x'}, 'n') is None


def test_install_recipe_requires_testing_and_does_not_change_live_job(monkeypatch):
    from crystallize import lifecycle
    job = {"id": "job-id", "name": "brief", "prompt": "Summarize"}
    monkeypatch.setattr(lifecycle, "cron_job", lambda ref: job)
    result = recipes.install('brief', {'version': 2, 'steps': [{'tool': 'web_extract', 'args': {'url': 'https://e.invalid'}}]})
    assert result['status'] == 'testing' and not result['meta'].get('ready')
    assert job['prompt'] == 'Summarize' and result['meta']['explicit_recipe']


def test_nested_source_errors_trigger_normal_failure(monkeypatch):
    monkeypatch.setattr(runner, 'RETRY_WAIT_S', 0)
    result = runner.run({'steps': [{'tool': 'web_extract', 'args': {}}]},
                        lambda *args: '{"results":[{"url":"https://e.invalid","error":"denied"}]}')
    assert not result['ok'] and 'source' in result['reason']


@pytest.mark.parametrize('payload', [{'ok': False}, {'success': False}, {'http_status': 503},
                                    {'results': [{'status_code': 403}]}])
def test_unsuccessful_observations_are_never_cached(payload):
    middleware = fetch_cache.make_tool_execution(SimpleNamespace(current_cron_execution=lambda: None))
    calls = []
    with fetch_cache.activate({'ttl_seconds': 60, 'namespace': 'failures'}):
        for _ in range(2):
            middleware(tool_name='web_extract', args={}, next_call=lambda args: calls.append(args) or json.dumps(payload))
    assert len(calls) == 2


def test_omitted_artifact_never_looks_unchanged():
    recipe = {'steps': [{'tool': 'terminal', 'args': {'command': {'$argv': [
        'curl', '--output', {'$artifact': 'large.txt'}, 'https://e.invalid']}}}]}
    def dispatch(tool, args):
        Path(shlex.split(args['command'])[2]).write_text('x' * 1_000_001)
        return '{"output":"saved","exit_code":0}'
    result = runner.run(recipe, dispatch)
    assert not result['ok'] and 'complete-result' in result['reason']
