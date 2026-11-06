"""Authoritative zeroes and durable execution IDs must survive accounting."""
import sqlite3

from crystallize import chat, history, modelinfo, overview


def make_run():
    return history.Run('e', 'j', 'job', 's', True, 1.)


def test_actual_zero_is_not_replaced_with_estimate(monkeypatch):
    run = make_run()
    info = {'actual_cost_usd': 0., 'estimated_cost_usd': .5, 'model': 'm', 'api_call_count': 1}
    history._apply_session(run, info)
    monkeypatch.setattr(history, '_session_costs', lambda ids: {'s': info})
    assert run.cost_usd == 0 and run.cost_provenance == 'actual'
    assert overview._chat_spent('s') == 0
    assert chat.analyse('s')['spent_usd'] == 0


def test_aggregate_price_estimate_is_explicitly_uncertain(monkeypatch):
    monkeypatch.setattr(modelinfo, 'api_cost', lambda *args: .8)
    run = make_run()
    history._apply_session(run, {'cost_status': 'included', 'model': 'm', 'api_call_count': 3})
    assert run.cost_usd == 0 and run.pricing_uncertain
    assert run.api_cost_provenance == 'aggregate_usage_estimate'


def test_overlapping_unlinked_session_is_not_a_baseline():
    db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
    db.executescript('CREATE TABLE sessions (id TEXT, source TEXT, started_at REAL, ended_at REAL, api_call_count INTEGER);'
                     "INSERT INTO sessions VALUES ('other-job','cron',100,200,2);")
    assert history._ledger_session(db, 'job-A', 'exec-A', 100, 200) is None
    db.close()


def test_exact_scheduler_admission_wins_over_nearby_job():
    db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
    db.executescript('CREATE TABLE sessions (id TEXT, source TEXT, started_at REAL, ended_at REAL, api_call_count INTEGER);'
                     'CREATE TABLE session_admissions (target_session_id TEXT, principal_id TEXT, request_id TEXT);'
                     "INSERT INTO sessions VALUES ('A','cron',100,199,2),('B','cron',101,200,2);"
                     "INSERT INTO session_admissions VALUES ('A','cron-owner','cron:job-A:exec-A');")
    assert history._ledger_session(db, 'job-A', 'exec-A', 100, 200)['id'] == 'A'
    db.close()


def test_legacy_job_timestamp_cannot_prove_an_execution():
    db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
    db.executescript('CREATE TABLE sessions (id TEXT, source TEXT, started_at REAL, ended_at REAL, api_call_count INTEGER);'
                     "INSERT INTO sessions VALUES ('cron_job-A_20261106_010101','cron',100,200,2);")
    assert history._ledger_session(db, 'job-A', 'exec-A', 100, 200) is None
    db.close()


def test_ambiguous_scheduler_admission_is_not_resolved_by_time():
    db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
    db.executescript('CREATE TABLE sessions (id TEXT, source TEXT, started_at REAL);'
                     'CREATE TABLE session_admissions (target_session_id TEXT, principal_id TEXT, request_id TEXT);'
                     "INSERT INTO sessions VALUES ('A','cron',100),('B','cron',500);"
                     "INSERT INTO session_admissions VALUES ('A','cron-owner','cron:job-A:exec-A'),"
                     "('B','cron-owner','cron:job-A:exec-A');")
    assert history._ledger_session(db, 'job-A', 'exec-A', 100, 200) is None
    db.close()


def test_recreated_job_name_does_not_merge_baselines(monkeypatch):
    rows = [history.Run('a', 'job-old', 'same name', 'sa', True, 1),
            history.Run('b', 'job-new', 'same name', 'sb', True, 2)]
    monkeypatch.setattr(history, 'load_runs', lambda: rows)
    assert {row['job_id'] for row in history.job_summaries()} == {'job-old', 'job-new'}


def test_known_no_ai_runs_have_zero_api_equivalent_cost():
    rows = overview._skipped_runs({'skips': [1.]}, 'job')
    assert rows[0].api_cost_usd == 0 and rows[0].total_tokens == 0
