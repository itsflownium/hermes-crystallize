"""Review regressions exercised at real hooks/storage boundaries, without models."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from crystallize import capture, chat_policy, chat_savings as chat, handoffs, lifecycle, modelinfo, output_delivery, public_pricing, reread, store, summary_checks

PRICES = dict(input=10e-6, output=20e-6, cache_read=1e-6, cache_write=12e-6)
CANONICAL = dict(input_tokens=1000, output_tokens=100, cache_read_tokens=9000,
                 cache_write_tokens=0, prompt_tokens=10000, total_tokens=10100)


@pytest.fixture
def world(monkeypatch, tmp_path):
    store.set_chat_enabled('s', True)
    monkeypatch.setattr(chat, '_main_model', lambda sid: dict(model='main', provider='p'))
    monkeypatch.setattr(chat, 'chosen_model', lambda: dict(model='helper', provider='p'))
    monkeypatch.setattr(chat, '_full_dir', lambda: tmp_path / 'originals')
    monkeypatch.setattr(modelinfo, 'token_prices', lambda model, provider: PRICES if model == 'main' else {k: v / 10 for k, v in PRICES.items()})
    monkeypatch.setattr(chat, '_seen', {})
    monkeypatch.setattr(chat, '_pauses', {})


def test_real_main_hook_prices_each_canonical_bucket_once(world):
    hook = capture.make_post_api_request(SimpleNamespace(current_cron_execution=lambda: None))
    hook(session_id='s', usage=dict(CANONICAL))
    usage = handoffs.main_usage('s')
    assert usage['input_tokens'] == usage['prompt_tokens'] == 10000
    assert usage['uncached_input_tokens'] == 1000 and usage['cache_read_tokens'] == 9000
    assert usage['usage_basis'] == 'canonical_disjoint'
    plan = chat._routing_plan(dict(model='helper', provider='p'), 's', input_tokens=100, expected_output_tokens=1000)
    assert plan['extra_main_read_usd'] == pytest.approx(.019)
    assert plan['extra_main_read_cost_basis'] == 'observed_disjoint_buckets'


def test_unknown_cache_is_not_free_and_inconsistent_usage_cannot_price_prefix(world):
    handoffs.note_usage('s', dict(prompt_tokens=10000, output_tokens=100))
    row = handoffs.main_usage('s')
    assert row['cache_read_tokens'] is None and row['cache_write_tokens'] is None
    plan = chat._routing_plan(dict(model='helper', provider='p'), 's')
    assert plan['extra_main_read_usd'] == pytest.approx(10000 * max(PRICES['input'], PRICES['cache_write']))
    assert plan['extra_main_read_cost_basis'] == 'upper_bound_unknown_cache'
    handoffs.note_usage('s', {**CANONICAL, 'prompt_tokens': 1})
    assert handoffs.main_usage('s')['usage_basis'] == 'inconsistent_buckets'
    assert chat._routing_plan(dict(model='helper', provider='p'), 's')['extra_main_read_usd'] is None


def test_legacy_ambiguous_usage_needs_fresh_hook_before_rewriting(world):
    store.set_setting(handoffs.usage_key('s'), dict(CANONICAL, calls=4))
    assert handoffs.main_usage('s')['input_tokens'] is None
    handoffs.note_usage('s', dict(CANONICAL))
    assert handoffs.main_usage('s')['calls'] == 5
    assert handoffs.main_usage('s')['prompt_tokens'] == 10000


def test_high_cache_observation_gets_a_bounded_reread_estimate(world):
    handoffs.note_usage('s', dict(CANONICAL))
    items = [dict(role='tool', tool_call_id='t', content='x' * 20000)]
    estimate = reread._estimate({'messages': items}, items, 0, {**items[0], 'content': 'original reference'}, handoffs.main_usage('s'), PRICES)
    assert estimate is not None and estimate['input_tokens'] == 10000
    assert estimate['cache_read_tokens'] == 9000
    assert estimate['potential_cached_tokens'] == 10000


def cron_capture(text, execution='e'):
    run = SimpleNamespace(job_id='j', job_name='job', execution_id=execution, scheduled_instant='fixture')
    ctx = SimpleNamespace(get_config=lambda name, default=None: default, current_cron_execution=lambda: run)
    args = {'url': 'https://example.test/current'}
    capture.make_post_tool_call(ctx)(tool_name='web_extract', args=args, result=text, session_id='cron', status='completed')
    return {'id': 'r', 'source_ref': 'j', 'steps': {'version': 1, 'steps': [{'tool': 'web_extract', 'args': args}]}}


def test_default_capture_validates_long_complete_cron_evidence():
    result = json.dumps({'content': 'source text ' * 1000})
    recipe = cron_capture(result)
    traces = store.execution_traces('e')
    assert len(traces[0]['display_preview']) == 2000
    assert traces[0]['result_preview'] == result and traces[0]['complete_result_retained']
    assert traces[0]['result_sha256'] == hashlib.sha256(result.encode()).hexdigest()
    assert lifecycle._observation_match(recipe, traces, 'complete report')[0]


def test_modified_complete_capture_is_never_used_as_successful_evidence():
    recipe = cron_capture(json.dumps({'content': 'x' * 5000}))
    store._db().execute("UPDATE complete_trace_results SET result_text='tampered'")
    store._db().commit()
    traces = store.execution_traces('e')
    assert not traces[0]['complete_result_retained']
    assert not lifecycle._observation_match(recipe, traces, 'report')[0]


def test_capture_bound_is_utf8_bytes_and_expired_payloads_remain_incomplete(monkeypatch):
    monkeypatch.setattr(store, 'MAX_COMPLETE_TRACE_BYTES', 3000)
    recipe = cron_capture(json.dumps({'content': 'é' * 2000}, ensure_ascii=False), 'oversized')
    assert not store.execution_traces('oversized')[0]['complete_result_retained']
    assert not lifecycle._observation_match(recipe, store.execution_traces('oversized'), 'report')[0]
    monkeypatch.setattr(store, 'MAX_COMPLETE_TRACE_ROWS', 2)
    for index in range(3):
        cron_capture('x' * 2500, str(index))
    assert not store.execution_traces('0')[0]['complete_result_retained']
    assert store.execution_traces('2')[0]['complete_result_retained']
    assert store._db().execute('SELECT count(*) FROM complete_trace_results').fetchone()[0] == 2


class Helper:
    def __init__(self, text='An incomplete summary.'):
        self.calls = []
        self.text = text
        self.llm = SimpleNamespace(complete=self.complete)
    def complete(self, messages, **options):
        self.calls.append((messages, options))
        return SimpleNamespace(text=self.text, model='helper', provider='p', finish_reason='stop',
                               usage=SimpleNamespace(input_tokens=100, output_tokens=30, cost_usd=.001))


@pytest.mark.parametrize('failure', [dict(success=False), dict(ok=False), dict(status_code=503),
    dict(status_code='503'), dict(status='failed'), dict(status='pending'), dict(isError=True),
    dict(results=[dict(content='ok'), dict(status=403)]), dict(data={'error': 'unavailable'}),
    dict(output=json.dumps({'success': False})), dict(exit_code=1), dict(errors=['source failure']), dict(truncated=True)])
def test_failed_or_pending_results_stay_exact_before_any_paid_boundary(world, failure):
    chat_policy.set_policy('s', '*', 'summary')
    helper = Helper()
    source = json.dumps({**failure, 'padding': 'source data ' * 1500})
    assert chat.shrink(helper, 's', 'web_extract', source) is None
    assert helper.calls == [] and store.chat_events('s') == []


def test_missing_observable_facts_rejects_summary_but_retains_attempt_cost(world):
    chat_policy.set_policy('s', '*', 'summary')
    helper = Helper()
    source = json.dumps({'count': 3, 'incident_id': 'INC-42',
        'content': 'On 2026-11-07, 25% of requests failed; cost $120. https://example.test/status ' + 'background ' * 2000})
    out = chat.shrink(helper, 's', 'web_extract', source)
    assert len(helper.calls) == 1 and (out is None or 'condensed_by' not in out)
    attempts = [row for row in store.chat_events('s') if row['helper_call']]
    assert len(attempts) == 1 and attempts[0]['status'] == 'rejected' and attempts[0]['cheap_cost'] == .001
    assert not any(row['chars'] and row['kind'] == 'summary' for row in store.chat_events('s'))


def test_summary_retaining_checkable_facts_is_accepted_without_reasoning_override(world):
    chat_policy.set_policy('s', '*', 'summary')
    summary = '3 incidents; INC-42 on 2026-11-07; 25%, $120. https://example.test/status'
    helper = Helper(summary)
    source = json.dumps({'count': 3, 'incident_id': 'INC-42', 'content': summary + ' context' * 2500})
    output = chat.shrink(helper, 's', 'web_extract', source)
    assert output is not None, (helper.calls, store.chat_events('s'), summary_checks.required_literals(source))
    result = json.loads(output)
    assert result['summary'] == summary
    assert chat._load_full(result['full_result_id'], 's') == source
    assert not set(helper.calls[0][1]) & {'reasoning', 'reasoning_effort', 'thinking'}


def test_uncheckable_large_literal_set_stays_exact_without_paying(world):
    chat_policy.set_policy('s', '*', 'summary')
    helper = Helper()
    source = json.dumps({'measurements': list(range(summary_checks.MAX_LITERALS + 1)), 'context': 'x' * 12000})
    result = chat.shrink(helper, 's', 'web_extract', source)
    assert result is None or 'condensed_by' not in result
    assert helper.calls == []
    assert not summary_checks.preserves('INC-420 and 15030', {'INC-42', '503'})
    assert not summary_checks.preserves('https://example.test/status/changed', {'https://example.test/status'})
    assert summary_checks.preserves('Cost $120. Next item.', {'$120'})


def test_duplicate_json_keys_and_nested_error_strings_are_uncertain():
    assert summary_checks.failed_result('{"error":"denied","error":false}')
    assert summary_checks.failed_result(json.dumps({'content': '{"status":"failed"}'}))


def test_public_buffering_contract_expands_placeholder_before_visible_delivery(world):
    registrations = {}
    class Context:
        def register_hook(self, name, callback, *, hold_stream=False):
            registrations[name] = (callback, hold_stream)
    ctx = Context()
    ctx.register_hook('transform_llm_output', capture.make_transform_llm_output(ctx), **output_delivery.registration_options(ctx))
    callback, held = registrations['transform_llm_output']
    original = 'Complete output with exact whitespace.\n\n終わり\n'
    result_id = chat._keep_full(original, 's')
    chunks = ['{{cry', 'stal:', result_id, '}}']
    visible = [callback(response_text=''.join(chunks), session_id='s')] if held else chunks
    assert held and visible == [original]
    assert '{{crystal:' not in ''.join(visible)


def test_legacy_public_hook_signature_never_receives_unknown_sdk_keywords():
    registrations = []
    class Legacy:
        def register_hook(self, name, callback):
            registrations.append((name, callback))
    callback = lambda **kwargs: None
    ctx = Legacy()
    ctx.register_hook('transform_llm_output', callback, **output_delivery.registration_options(ctx))
    assert registrations == [('transform_llm_output', callback)]


def test_plugin_registration_requests_the_exposed_public_buffering_capability(world, monkeypatch):
    from crystallize import dispatch_support, experiments, host_api, register, tools
    for module in (dispatch_support, experiments, host_api, tools):
        monkeypatch.setattr(module, '_ctx', None)
    hooks = {}
    class Context:
        def register_hook(self, name, callback, *, hold_stream=False):
            hooks[name] = (callback, hold_stream)
        def __getattr__(self, name):
            if name.startswith('register_'):
                return lambda *args, **kwargs: None
            raise AttributeError(name)
    register(Context())
    assert hooks['transform_llm_output'][1] is True
    assert all(not hold for name, (_, hold) in hooks.items() if name != 'transform_llm_output')


def test_failed_card_transaction_rolls_back_before_the_next_valid_write():
    store.set_setting(public_pricing.SETTING, [{'malformed': True}])
    card = dict(provider='p', model='m', currency='USD', unit='per_token', input=1, output=2,
                cache_read=0, cache_write=0, request_cost=0, tier_threshold_tokens=None)
    with pytest.raises(public_pricing.PricingError):
        public_pricing.save_rate_card(card)
    assert not store._db().in_transaction
    store.set_setting(public_pricing.SETTING, [])
    public_pricing.save_rate_card(card)
    assert public_pricing.exact_rate_card('m', 'p', config={}) is not None


@pytest.mark.parametrize('operation', ['save', 'delete'])
def test_separate_process_price_writers_do_not_lose_or_resurrect_cards(monkeypatch, tmp_path, operation):
    path = tmp_path / 'cards.sqlite'
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(store._SCHEMA)
    def card(model):
        return dict(provider='fixture', model=model, currency='USD', unit='per_million_tokens', input=1, output=2,
                    cache_read=0, cache_write=0, request_cost=0, tier_threshold_tokens=None)
    monkeypatch.setattr(store, '_conn', conn)
    if operation == 'delete':
        public_pricing.save_rate_card(card('first'))
    entered, release = threading.Event(), threading.Event()
    errors = []
    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor
        def fetchone(self):
            row = self.cursor.fetchone()
            self.cursor.close()
            entered.set()
            if not release.wait(10):
                raise RuntimeError('Offline transaction was not released')
            return row
    class Connection:
        def execute(self, sql, args=()):
            result = conn.execute(sql, args)
            return Cursor(result) if sql.startswith('SELECT value FROM settings') else result
        def __getattr__(self, name):
            return getattr(conn, name)
    monkeypatch.setattr(store, '_conn', Connection())
    def first():
        try:
            public_pricing.save_rate_card(card('first')) if operation == 'save' else public_pricing.delete_rate_card('fixture', 'first')
        except Exception as exc:
            errors.append(exc)
    writer = threading.Thread(target=first)
    writer.start()
    worker = None
    try:
        assert entered.wait(5)
        root = Path(__file__).resolve().parents[1]
        worker = subprocess.Popen([sys.executable, str(root / 'tests/helpers/rate_card_writer.py'), str(root), str(path), json.dumps(card('second'))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={'HERMES_HOME': str(tmp_path), 'PYTHONDONTWRITEBYTECODE': '1', 'PATH': os.environ.get('PATH', '')})
        with selectors.DefaultSelector() as ready:
            ready.register(worker.stdout, selectors.EVENT_READ)
            assert ready.select(8), 'Independent price writer did not reach its transaction'
            # Fixed code announces BEGIN; old code reaches SELECT first. Release
            # after either event, so the old lost-update race fails deterministically.
            assert worker.stdout.readline().strip() in {'transaction', 'read'}
        release.set()
        stdout, stderr = worker.communicate(timeout=10)
        writer.join(5)
        assert not writer.is_alive() and not errors
        assert worker.returncode == 0, stderr
        assert 'saved' in stdout
        rows = json.loads(conn.execute('SELECT value FROM settings WHERE key=?', (public_pricing.SETTING,)).fetchone()[0])
        assert {row['model'] for row in rows} == ({'first', 'second'} if operation == 'save' else {'second'})
    finally:
        release.set()
        writer.join(5)
        if worker is not None and worker.poll() is None:
            worker.kill()
            worker.communicate()
        conn.close()
