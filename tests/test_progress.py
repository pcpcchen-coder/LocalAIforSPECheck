"""Observable execution progress without fake model activity or network calls."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from spec_check import engine
from spec_check.app import create_app


BASE_URL = 'http://127.0.0.1:8765'


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def response():
    return {'choices': [{'message': {'content': json.dumps({
        'status': 'missing', 'explanation': '沒有相關產品證據。',
        'differences': [], 'evidence': [], 'confidence': 0.8,
    })}, 'finish_reason': 'stop'}]}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path), base_url=BASE_URL) as value:
        yield value


def setup_project(client, windows=1):
    project = ok(client.post('/api/projects', json={'name': '進度驗收'}))
    for name, role, content in [
        ('產品.txt', 'product', '\n'.join(f'產品段落 {i}：' + '說明'*600 for i in range(windows))),
        ('規範.txt', 'standard', '電壓應為 48 V。'),
    ]:
        document = ok(client.post(f'/api/projects/{project["id"]}/documents',
            files={'file': (name, content.encode(), 'text/plain')}, data={'role': role}))
        ok(client.post(f'/api/projects/{project["id"]}/documents/{document["id"]}/confirm'))
    ok(client.put('/api/settings', json={'model': 'test-model', 'context_chars': 2000, 'timeout': 30}))
    return project


def start(client, project):
    return ok(client.post(f'/api/projects/{project["id"]}/runs', json={}))


def progress(client, run):
    return ok(client.get(f'/api/runs/{run["id"]}/progress'))


def finished(client, run, status='completed'):
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        state = progress(client, run)
        if state['status'] == status:
            return state
        time.sleep(.01)
    pytest.fail(f'Expected {status}, got {state}')


def test_polling_remains_responsive_during_blocked_model_and_tracks_windows(client, monkeypatch):
    project = setup_project(client, windows=3)
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    calls = []
    writes = []
    store = client.app.state.store
    original_put_many = store.put_many
    def record_writes(items):
        writes.append({kind for kind, _, _ in items})
        return original_put_many(items)
    monkeypatch.setattr(store, 'put_many', record_writes)

    def request(*args):
        index = len(calls)
        calls.append(args)
        if index < 2:
            entered[index].set()
            assert release[index].wait(8)
        return response()

    monkeypatch.setattr(engine, '_request_json', request)
    run = start(client, project)
    try:
        assert entered[0].wait(3)
        initial_run_writes = sum('run' in kinds for kinds in writes)
        # A slow synchronous model request occupies only the comparison worker.
        with ThreadPoolExecutor(max_workers=1) as pool:
            state = pool.submit(progress, client, run).result(timeout=2)
        p = state['progress']
        assert state['completed'] == 0 and state['total'] == 1
        assert p['stage'] == 'waiting_model'
        assert p['window_index'] == 1 and p['window_total'] == 3
        assert p['request_started_at'] and p['started_at']
        assert p['requests_completed'] == 0 and p['last_response_at'] is None
        assert p['standard_name'] == '規範.txt' and p['block_index'] == 1
        assert state['server_time'] >= p['updated_at']
        # Polling reads neither the immutable documents nor accumulated results.
        original_get = store.get
        def get_lightweight(kind, identifier):
            assert kind != 'run', 'polling loaded the source snapshot'
            return original_get(kind, identifier)
        with monkeypatch.context() as patch:
            patch.setattr(store, 'get', get_lightweight)
            again = progress(client, run)
        assert again['progress'] == p  # server heartbeat must not invent activity
        assert not {'documents', 'results', 'settings'} & state.keys()
        release[0].set()
        assert entered[1].wait(3)
        halfway = progress(client, run)
        assert halfway['completed'] == 0  # first row needs every window
        assert halfway['progress']['window_index'] == 2
        assert halfway['progress']['windows_completed'] == 1
        assert halfway['progress']['requests_completed'] == 1
        assert halfway['progress']['last_response_at']
        assert halfway['progress']['request_started_at'] > p['request_started_at']
        assert sum('run' in kinds for kinds in writes) == initial_run_writes
        full = ok(client.get(f'/api/runs/{run["id"]}'))
        assert full['progress'] == halfway['progress']
    finally:
        for event in release:
            event.set()
    terminal = finished(client, run)
    assert terminal['completed'] == terminal['total'] == 1
    assert terminal['progress']['stage'] == 'completed'
    assert terminal['progress']['requests_completed'] == 3
    assert terminal['progress']['windows_completed'] == 3
    assert terminal['progress']['request_started_at'] is None
    assert terminal['progress']['finished_at']
    assert progress(client, run)['progress'] == terminal['progress']
    full = ok(client.get(f'/api/runs/{run["id"]}'))
    assert len(full['results']) == terminal['completed']
    assert {'run_progress','result'} in writes
    assert not any('result' in kinds and 'run_progress' not in kinds for kinds in writes)


def test_cancel_waits_for_outstanding_request_and_resume_resets_session(client, monkeypatch):
    project = setup_project(client, windows=2)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def request(*args):
        calls.append(args)
        if len(calls) == 1:
            entered.set()
            assert release.wait(8)
        return response()
    monkeypatch.setattr(engine, '_request_json', request)
    run = start(client, project)
    try:
        assert entered.wait(3)
        before = progress(client, run)
        cancelled = ok(client.post(f'/api/runs/{run["id"]}/cancel'))
        assert cancelled['status'] == 'running'
        assert cancelled['progress']['cancel_requested'] is True
        assert cancelled['progress']['stage'] == 'waiting_model'
        assert cancelled['progress']['last_response_at'] is None
        assert cancelled['progress']['finished_at'] is None
    finally:
        release.set()
    stopped = finished(client, run, 'cancelled')
    assert stopped['completed'] == 0
    assert stopped['progress']['requests_completed'] == 1
    assert stopped['progress']['last_response_at']
    assert stopped['progress']['request_started_at'] is None
    ok(client.post(f'/api/runs/{run["id"]}/resume'))
    done = finished(client, run)
    assert done['progress']['started_at'] > before['progress']['started_at']
    assert done['progress']['requests_completed'] == 2
    assert done['progress']['cancel_requested'] is False
    assert len(calls) == 3


def test_queued_cancel_can_resume_without_old_future_overwriting_state(client, monkeypatch):
    project = setup_project(client)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def request(*args):
        calls.append(args)
        if len(calls) == 1:
            entered.set()
            assert release.wait(8)
        return response()
    monkeypatch.setattr(engine, '_request_json', request)
    first = start(client, project)
    try:
        assert entered.wait(3)
        second = start(client, project)
        queued = progress(client, second)
        assert queued['status'] == 'queued'
        assert queued['progress']['stage'] == 'queued'
        assert queued['progress']['started_at'] is None
        stopped = ok(client.post(f'/api/runs/{second["id"]}/cancel'))
        assert stopped['status'] == 'cancelled'
        assert stopped['progress']['requests_completed'] == 0
        resumed = ok(client.post(f'/api/runs/{second["id"]}/resume'))
        assert resumed['status'] == 'queued'
        assert resumed['progress']['cancel_requested'] is False
    finally:
        release.set()
    finished(client, first)
    done = finished(client, second)
    assert done['completed'] == done['total'] == 1
    assert done['progress']['requests_completed'] == 1
    assert len(calls) == 2


def test_failures_do_not_claim_response_or_expose_secrets(client, monkeypatch):
    project = setup_project(client)
    secret = 'private-token-not-progress'
    ok(client.put('/api/settings', json={'api_key': secret}))
    monkeypatch.setattr(engine, '_request_json', lambda *args: (_ for _ in ()).throw(TimeoutError(secret)))
    run = start(client, project)
    done = finished(client, run)
    assert done['completed'] == 1
    p = done['progress']
    assert p['requests_completed'] == 1 and p['windows_completed'] == 0
    assert p['last_response_at'] is None and p['request_started_at'] is None
    assert '失敗' in json.dumps(p, ensure_ascii=False)
    assert any(event['stage'] == 'handling_error' for event in p['events'])
    assert not any(event['stage'] == 'checking_response' for event in p['events'])
    assert secret not in json.dumps(done)
    full = ok(client.get(f'/api/runs/{run["id"]}'))
    assert full['results'][0]['status'] == 'uncertain'
    assert secret not in json.dumps(full)


def test_engine_callbacks_distinguish_received_invalid_and_failed_requests(monkeypatch):
    blocks = [{'id': str(i), 'text': '文字'*700} for i in range(3)]
    replies = iter([response(), {'choices': [{'message': {'content': '{bad json'}}]}, TimeoutError('secret')])
    def request(*args):
        value = next(replies)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(engine, '_request_json', request)
    events = []
    result = engine.compare_block({'id': 'r', 'text': '條款'}, blocks,
        dict(engine.DEFAULT_SETTINGS, model='test', context_chars=2000),progress_callback=events.append)
    assert [(e['stage'], e['window_index']) for e in events] == [
        ('preparing',0), ('waiting_model',1), ('checking_response',1), ('window_completed',1),
        ('waiting_model',2), ('checking_response',2), ('window_failed',2),
        ('waiting_model',3), ('window_failed',3),
    ]
    assert all(e['window_total'] == 3 for e in events)
    assert result['status'] == 'uncertain'
    assert 'secret' not in json.dumps(events)


def test_legacy_database_progress_counts_saved_results_and_marks_interrupted(tmp_path, monkeypatch):
    application = create_app(tmp_path)
    monkeypatch.setattr(engine, '_request_json', lambda *args: response())
    with TestClient(application, base_url=BASE_URL) as client:
        project = setup_project(client)
        run = start(client, project)
        finished(client, run)
    store = application.state.store
    stored = store.get('run',run['id'])
    stored.update(status='running',completed=0,finished_at=None)
    store.put('run',stored,project['id'])
    with store.connect() as db:
        db.execute("DELETE FROM records WHERE kind='run_progress' AND id=?",(run['id'],))
    with TestClient(create_app(tmp_path), base_url=BASE_URL) as client:
        state = progress(client,run)
        assert state['status'] == state['progress']['stage'] == 'interrupted'
        assert state['completed'] == 1
        assert state['progress']['request_started_at'] is None
        assert state['progress']['finished_at']
        assert client.get('/api/runs/not-found/progress').status_code == 404


def test_progress_event_tail_is_bounded_for_long_scans(client, monkeypatch):
    project = setup_project(client, windows=20)
    monkeypatch.setattr(engine, '_request_json', lambda *args: response())
    run = start(client, project)
    done = finished(client, run)
    assert done['progress']['requests_completed'] == 20
    assert len(done['progress']['events']) == 30
    assert done['progress']['events'][-1]['stage'] == 'completed'
