"""Portable safety behavior; uses real locks, subprocesses and loopback HTTP only."""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


LAUNCHER_PATH = Path(__file__).resolve().parents[1] / "portable" / "launcher.py"
_spec = importlib.util.spec_from_file_location("portable_launcher_under_test", LAUNCHER_PATH)
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)


def portable_folder(tmp_path: Path) -> Path:
    root = tmp_path / "規格 比對 Portable"
    for name in ("data", "logs", "models", "app", "runtime", "engine"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    pytest.fail("Timed out waiting for the test subprocess")


@contextmanager
def lock_owner(root: Path, *, nonce=None):
    """A separate interpreter owns the lock; the test never imitates lock results."""
    ready = root / "ready.json"
    release = root / "release"
    script = """
import importlib.util, json, pathlib, sys, time
spec = importlib.util.spec_from_file_location('portable_child', sys.argv[1])
l = importlib.util.module_from_spec(spec)
spec.loader.exec_module(l)
root = pathlib.Path(sys.argv[2])
nonce = json.loads(sys.argv[3])
lock = l.FolderLock(root / 'data' / 'portable.lock')
assert lock.acquire()
try:
    if nonce:
        l.write_json(root / 'data' / 'portable-session.json', {'nonce': nonce, 'pid': __import__('os').getpid()})
    (root / 'ready.json').write_text('true')
    while not (root / 'release').exists():
        if nonce and l.stop_requested(root, nonce):
            break
        time.sleep(.02)
finally:
    lock.close()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(LAUNCHER_PATH), str(root), json.dumps(nonce)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        wait_for(lambda: ready.exists() or child.poll() is not None)
        assert child.poll() is None, child.communicate()[1].decode("utf-8", errors="replace")
        yield child
    finally:
        release.touch()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()


def test_folder_lock_excludes_second_process_and_releases_on_process_death(tmp_path):
    root = portable_folder(tmp_path)
    with lock_owner(root) as child:
        contender = launcher.FolderLock(root / "data" / "portable.lock")
        try:
            assert contender.acquire() is False
        finally:
            contender.close()
        child.kill()  # No application cleanup: the operating system must release it.
        child.wait(timeout=5)
        recovered = launcher.FolderLock(root / "data" / "portable.lock")
        try:
            assert recovered.acquire() is True
        finally:
            recovered.close()


def test_stop_ignores_stale_session_even_if_pid_now_belongs_to_another_process(tmp_path):
    root = portable_folder(tmp_path)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        launcher.write_json(root / "data" / "portable-session.json", {"pid": other.pid, "nonce": "old-run"})
        assert launcher.stop(root) == 0
        assert other.poll() is None
        assert not (root / "data" / "portable-stop.json").exists()
    finally:
        other.terminate()
        other.wait(timeout=5)


def test_old_stop_request_cannot_stop_current_session_and_stop_waits_for_release(tmp_path):
    root = portable_folder(tmp_path)
    launcher.write_json(root / "data" / "portable-stop.json", {"nonce": "previous-session"})
    with lock_owner(root, nonce="current-session") as child:
        assert not launcher.stop_requested(root, "current-session")
        assert child.poll() is None
        assert launcher.stop(root) == 0
        child.wait(timeout=5)
        assert child.returncode == 0
        assert launcher.read_json(root / "data" / "portable-stop.json")["nonce"] == "current-session"
        lock = launcher.FolderLock(root / "data" / "portable.lock")
        try:
            assert lock.acquire()
        finally:
            lock.close()


def test_port_collision_chooses_different_port_without_disturbing_existing_service():
    import socket

    with socket.socket() as occupied:
        occupied.bind((launcher.HOST, 0))
        occupied.listen(1)
        old_port = occupied.getsockname()[1]
        new_port = launcher.free_port(old_port)
        assert new_port != old_port
        with socket.create_connection((launcher.HOST, old_port), timeout=1):
            connection, _ = occupied.accept()
            connection.close()
        with socket.socket() as available:
            available.bind((launcher.HOST, new_port))


def test_valid_unicode_model_in_a_folder_with_spaces(tmp_path):
    root = portable_folder(tmp_path)
    model = root / "models" / "中文 模型.gguf"
    model.write_bytes(b"GGUF" + b"\x00" * 32)
    launcher.write_json(root / "data" / "portable-config.json", {"model": model.name})
    assert launcher.model_path(root) == model.resolve()


@pytest.mark.parametrize("name", ["../outside.gguf", "/outside.gguf", "model.txt", "", None, 42])
def test_model_configuration_cannot_select_outside_models_or_non_gguf_files(tmp_path, name):
    root = portable_folder(tmp_path)
    (tmp_path / "outside.gguf").write_bytes(b"GGUF")
    launcher.write_json(root / "data" / "portable-config.json", {"model": name})
    with pytest.raises(ValueError):
        launcher.model_path(root)


def test_model_symlink_cannot_escape_models_folder(tmp_path):
    root = portable_folder(tmp_path)
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"GGUF")
    selected = root / "models" / launcher.DEFAULT_MODEL
    try:
        selected.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("This Windows account cannot create symbolic links")
    with pytest.raises(ValueError):
        launcher.model_path(root)


def test_incomplete_download_or_html_error_page_is_not_loaded_as_a_model(tmp_path):
    root = portable_folder(tmp_path)
    (root / "models" / launcher.DEFAULT_MODEL).write_bytes(b"<html>download failed</html>")
    with pytest.raises(ValueError, match="GGUF"):
        launcher.model_path(root)


@contextmanager
def local_server(handler):
    server = ThreadingHTTPServer((launcher.HOST, 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_local_health_request_ignores_proxy_and_refuses_redirects(monkeypatch):
    proxy_calls = []

    class Trap(BaseHTTPRequestHandler):
        def do_GET(self):
            proxy_calls.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"unexpected-proxy-or-redirect"}')

        def log_message(self, *_):
            pass

    with local_server(Trap) as trap_port:
        class Local(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{trap_port}/leak")
                else:
                    self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def log_message(self, *_):
                pass

        monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{trap_port}")
        monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{trap_port}")
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        with local_server(Local) as port:
            assert launcher.http_json(port, "/health", "secret-not-for-proxy") == {"status": "ok"}
            assert launcher.http_json(port, "/redirect", "secret-not-for-redirect") == {}
        assert proxy_calls == []


def test_failed_start_releases_folder_lock_and_cleans_only_its_session_files(tmp_path):
    root = portable_folder(tmp_path)
    (root / "models" / launcher.DEFAULT_MODEL).write_bytes(b"GGUF")
    unrelated = root / "data" / "customer-document.txt"
    unrelated.write_text("Keep this file", encoding="utf-8")
    for name in ("portable-session.json", "portable-api-key.txt", "portable-stop.json"):
        (root / "data" / name).write_text("stale", encoding="utf-8")
    assert launcher.launch(root, no_browser=True) == 1
    assert unrelated.read_text(encoding="utf-8") == "Keep this file"
    assert not (root / "data" / "portable-session.json").exists()
    assert not (root / "data" / "portable-api-key.txt").exists()
    lock = launcher.FolderLock(root / "data" / "portable.lock")
    try:
        assert lock.acquire()
    finally:
        lock.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are OS-specific")
def test_closing_windows_job_terminates_owned_child_and_preserves_other_process(tmp_path):
    ready = tmp_path / "owned-ready"
    completed = tmp_path / "owned-finished-naturally"
    owned = subprocess.Popen([
        sys.executable, "-c",
        "from pathlib import Path; import sys, time; "
        "Path(sys.argv[1]).touch(); time.sleep(60); Path(sys.argv[2]).touch()",
        str(ready), str(completed),
    ])
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    job = launcher.ChildJob()
    try:
        wait_for(lambda: ready.exists())
        assert owned.poll() is None
        job.add(owned)
        job.close()
        owned.wait(timeout=10)
        # Closing a Windows Job Object can report exit code 0. The child must
        # exit before its 60-second sleep completes, without reaching its end.
        assert not completed.exists()
        assert unrelated.poll() is None
    finally:
        job.close()
        for child in (owned, unrelated):
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)


def _api_success(response):
    assert response.status_code == 200, response.text
    return response.json()


def _run_at_status(client, run_id, expected):
    result = {}

    def done():
        result.clear()
        result.update(_api_success(client.get(f"/api/runs/{run_id}")))
        return result["status"] == expected

    wait_for(done)
    return result


def _prepare_cancelled_portable_run(root, monkeypatch):
    from fastapi.testclient import TestClient
    import spec_check.app as app_module
    from spec_check.engine import ComparisonCancelled

    original_url = "http://127.0.0.1:1235/v1"
    model_name = "portable-test-model"
    model_hash = "a" * 64
    monkeypatch.setenv("SPEC_CHECK_PORTABLE", "1")
    monkeypatch.setenv("SPEC_CHECK_MODEL_SHA256", model_hash)
    # These identify the actual bundled service even if settings are editable.
    monkeypatch.setenv("SPEC_CHECK_PORTABLE_BASE_URL", original_url)
    monkeypatch.setenv("SPEC_CHECK_PORTABLE_MODEL", model_name)

    def cancelled(*args, **kwargs):
        raise ComparisonCancelled("simulated portable shutdown")

    monkeypatch.setattr(app_module, "compare_block", cancelled)
    application = app_module.create_app(root)
    with TestClient(application, base_url="http://127.0.0.1:8765") as client:
        project = _api_success(client.post("/api/demo"))
        _api_success(client.put("/api/settings", json={
            "model": model_name, "base_url": original_url, "api_key": "old-session-token",
        }))
        run = _api_success(client.post(f"/api/projects/{project['id']}/runs", json={"mode": "local"}))
        run = _run_at_status(client, run["id"], "cancelled")
        assert run["portable_model_sha256"] == model_hash
    return run, application.state.store


def test_portable_resume_uses_new_local_port_and_token_but_preserves_original_snapshot(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import spec_check.app as app_module

    original, store = _prepare_cancelled_portable_run(tmp_path, monkeypatch)
    current = store.get("settings", "local")
    current["values"].update(base_url="http://127.0.0.1:54321/v1", api_key="new-session-token")
    store.put("settings", current)
    monkeypatch.setenv("SPEC_CHECK_PORTABLE_BASE_URL", current["values"]["base_url"])
    received_settings = []

    def compare(requirement, product_blocks, settings, **kwargs):
        received_settings.append(dict(settings))
        return {
            "status": "uncertain", "explanation": "Test resume only.", "differences": [],
            "evidence": [], "confidence": 0, "product_coverage": {"scanned": 0, "total": 1},
            "warnings": ["synthetic response"],
        }

    monkeypatch.setattr(app_module, "compare_block", compare)
    with TestClient(app_module.create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        _api_success(client.post(f"/api/runs/{original['id']}/resume"))
        completed = _run_at_status(client, original["id"], "completed")
        assert received_settings
        assert {s["base_url"] for s in received_settings} == {"http://127.0.0.1:54321/v1"}
        assert {s["api_key"] for s in received_settings} == {"new-session-token"}
        assert completed["settings"] == original["settings"]
        assert completed["portable_model_sha256"] == original["portable_model_sha256"]
        assert len(completed["resume_events"]) == 1
        assert completed["resume_events"][0]["base_url"] == "http://127.0.0.1:54321/v1"
        exported = _api_success(client.get(f"/api/runs/{original['id']}/export?format=json"))
        assert exported["resume_events"] == completed["resume_events"]
        assert "new-session-token" not in json.dumps(exported)
        assert "old-session-token" not in json.dumps(exported)


@pytest.mark.parametrize("change", ["hash", "model", "not-portable"])
def test_portable_resume_rejects_changed_model_or_nonportable_runtime(tmp_path, monkeypatch, change):
    from fastapi.testclient import TestClient
    import spec_check.app as app_module

    original, store = _prepare_cancelled_portable_run(tmp_path, monkeypatch)
    if change == "hash":
        monkeypatch.setenv("SPEC_CHECK_MODEL_SHA256", "b" * 64)
    elif change == "model":
        current = store.get("settings", "local")
        current["values"]["model"] = "different-model"
        store.put("settings", current)
    else:
        monkeypatch.delenv("SPEC_CHECK_PORTABLE")
    calls = []
    monkeypatch.setattr(app_module, "compare_block", lambda *args, **kwargs: calls.append(args))
    with TestClient(app_module.create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        response = client.post(f"/api/runs/{original['id']}/resume")
        assert response.status_code == 400
        unchanged = _api_success(client.get(f"/api/runs/{original['id']}"))
        assert unchanged["status"] == "cancelled"
        assert unchanged["settings"] == original["settings"]
        assert not unchanged.get("resume_events")
        assert calls == []


def test_health_exposes_only_current_instance_identity(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from spec_check.app import create_app

    monkeypatch.setenv("SPEC_CHECK_INSTANCE", "this-folder-launch-nonce")
    monkeypatch.setenv("SPEC_CHECK_MODEL_SHA256", "a" * 64)
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        health = _api_success(client.get("/api/health"))
        assert health["app"] == launcher.APP_ID
        assert health["instance_id"] == "this-folder-launch-nonce"
        assert "api_key" not in health


@pytest.mark.parametrize("same_instance", [False, True])
def test_second_start_only_opens_browser_for_the_same_portable_instance(tmp_path, monkeypatch, same_instance):
    root = portable_folder(tmp_path)
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok", "app": launcher.APP_ID,
                "instance_id": "this-folder" if same_instance else "different-folder",
            }).encode("utf-8"))

        def log_message(self, *_):
            pass

    with local_server(Health) as port, lock_owner(root) as child:
        launcher.write_json(root / "data" / "portable-session.json", {
            "nonce": "this-folder", "app_port": port,
        })
        assert launcher.launch(root, no_browser=False) == 0
        assert opened == ([f"http://127.0.0.1:{port}"] if same_instance else [])
        assert child.poll() is None


@pytest.mark.parametrize("change", ["base_url", "model"])
def test_manually_selected_service_is_not_mislabeled_with_bundled_model_fingerprint(tmp_path, monkeypatch, change):
    from fastapi.testclient import TestClient
    import spec_check.app as app_module

    original, store = _prepare_cancelled_portable_run(tmp_path, monkeypatch)
    current = store.get("settings", "local")
    current["values"][change] = "http://127.0.0.1:54321/v1" if change == "base_url" else "manually-selected-model"
    store.put("settings", current)
    with TestClient(app_module.create_app(tmp_path), base_url="http://127.0.0.1:8765") as client:
        run = _api_success(client.post(f"/api/projects/{original['project_id']}/runs", json={"mode": "local"}))
        run = _run_at_status(client, run["id"], "cancelled")
        assert "portable_model_sha256" not in run
        assert run["settings"][change] == current["values"][change]
        # The original fingerprinted run cannot resume against the edited service.
        response = client.post(f"/api/runs/{original['id']}/resume")
        assert response.status_code == 400
