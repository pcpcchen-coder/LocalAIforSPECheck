"""The independently downloaded model is never visible until fully verified."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

import pytest

from portable import download_model as download


PAYLOAD = b"GGUF" + b"synthetic test model\x00" * 20


def portable_root(tmp_path, payload=PAYLOAD):
    root = tmp_path / "Portable 繁體中文 spaced path"
    root.mkdir()
    asset = {"filename": "test-model.gguf", "url": "https://example.com/fixed-revision/model.gguf",
             "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
    (root / "build-lock.json").write_text(json.dumps({"schema_version": 1, "model": asset}))
    return root, asset


def mocked_download(monkeypatch, response_bytes=PAYLOAD, *, exception_after_read=False, url=None):
    calls = []
    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(response_bytes)
            self.url = url or "https://cdn.example.com/model.gguf"

        def read(self, n=-1):
            if exception_after_read and self.tell():
                raise ConnectionResetError("Synthetic connection loss")
            return super().read(n)

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            return Response()

    monkeypatch.setattr(download.urllib.request, "build_opener", lambda *args: Opener())
    return calls


def test_verified_download_is_published_with_progress_and_never_touches_selection(tmp_path, monkeypatch):
    root, asset = portable_root(tmp_path)
    (root / "data").mkdir()
    configuration = root / "data/portable-config.json"
    configuration.write_bytes(b'{"model":"my-custom-model.gguf"}')
    (root / "models").mkdir()
    custom = root / "models/my-custom-model.gguf"
    custom.write_bytes(b"GGUF existing custom model")
    calls = mocked_download(monkeypatch)
    messages = []
    target = download.acquire_model(root, progress=messages.append)
    assert target.read_bytes() == PAYLOAD
    assert target.name == asset["filename"]
    assert len(calls) == 1 and calls[0].data is None
    assert "Authorization" not in calls[0].headers
    assert configuration.read_bytes() == b'{"model":"my-custom-model.gguf"}'
    assert custom.read_bytes() == b"GGUF existing custom model"
    assert not list((root / "models").glob("*.part"))
    assert any("100.0%" in message for message in messages)
    assert any("SHA-256" in message for message in messages)


def test_existing_valid_model_is_rehashed_and_reused_without_network_or_rewrite(tmp_path, monkeypatch):
    root, asset = portable_root(tmp_path)
    (root / "models").mkdir()
    target = root / "models" / asset["filename"]
    target.write_bytes(PAYLOAD)
    before = target.stat().st_mtime_ns
    def forbidden(*args, **kwargs):
        pytest.fail("Existing model must never make a network request")
    monkeypatch.setattr(download.urllib.request, "build_opener", forbidden)
    assert download.acquire_model(root, progress=lambda _: None) == target
    assert target.stat().st_mtime_ns == before


@pytest.mark.parametrize("bad", [b"partial", b"GGUF" + b"x" * (len(PAYLOAD) - 4)])
def test_invalid_existing_model_is_preserved_and_never_overwritten(tmp_path, monkeypatch, bad):
    root, asset = portable_root(tmp_path)
    (root / "models").mkdir()
    target = root / "models" / asset["filename"]
    target.write_bytes(bad)
    calls = mocked_download(monkeypatch)
    with pytest.raises(ValueError, match="檔案已保留"):
        download.acquire_model(root)
    assert target.read_bytes() == bad
    assert calls == []


@pytest.mark.parametrize("bad", [b"GGUF short", b"x" * len(PAYLOAD), b"GGUF" + b"x" * (len(PAYLOAD) - 4), PAYLOAD + b"too much"])
def test_failed_size_format_or_hash_never_exposes_gguf(tmp_path, monkeypatch, bad):
    root, asset = portable_root(tmp_path)
    mocked_download(monkeypatch, bad)
    with pytest.raises(ValueError):
        download.acquire_model(root)
    assert not (root / "models" / asset["filename"]).exists()
    assert not list((root / "models").glob("*.part"))


def test_connection_loss_discards_partial_and_retry_starts_from_zero(tmp_path, monkeypatch):
    root, asset = portable_root(tmp_path)
    mocked_download(monkeypatch, exception_after_read=True)
    with pytest.raises(ConnectionResetError):
        download.acquire_model(root)
    assert not list((root / "models").glob("*.gguf"))
    assert not list((root / "models").glob("*.part"))
    # A forced console close can leave a partial: it must not be appended to.
    (root / "models" / (asset["filename"] + ".part")).write_bytes(b"stale download")
    mocked_download(monkeypatch)
    assert download.acquire_model(root).read_bytes() == PAYLOAD


def test_manual_import_is_offline_and_does_not_modify_source(tmp_path, monkeypatch):
    root, asset = portable_root(tmp_path)
    source = tmp_path / "手動 模型.gguf"
    source.write_bytes(PAYLOAD)
    before = source.stat().st_mtime_ns
    def forbidden(*args, **kwargs):
        pytest.fail("Manual import must not access network")
    monkeypatch.setattr(download.urllib.request, "build_opener", forbidden)
    assert download.acquire_model(root, source=source).read_bytes() == PAYLOAD
    assert source.read_bytes() == PAYLOAD and source.stat().st_mtime_ns == before


def test_publishing_refuses_a_model_created_during_transfer(tmp_path):
    partial = tmp_path / "model.gguf.part"
    target = tmp_path / "model.gguf"
    partial.write_bytes(PAYLOAD)
    target.write_bytes(b"user model created while downloading")
    with pytest.raises(FileExistsError):
        download.publish_model(partial, target)
    assert target.read_bytes() == b"user model created while downloading"


def test_second_downloader_cannot_acquire_active_lock(tmp_path):
    with download.download_lock(tmp_path / "download.lock"):
        with pytest.raises(RuntimeError, match="另一個"):
            with download.download_lock(tmp_path / "download.lock"):
                pytest.fail("Concurrent downloader acquired lock")
    with download.download_lock(tmp_path / "download.lock"):
        pass


@pytest.mark.parametrize("name", ["../escape.gguf", "C:\\model.gguf", "file:stream.gguf", "CON.gguf", "model.txt"])
def test_model_lock_rejects_unsafe_filenames(tmp_path, name):
    root, asset = portable_root(tmp_path)
    asset["filename"] = name
    (root / "build-lock.json").write_text(json.dumps({"schema_version": 1, "model": asset}))
    with pytest.raises(ValueError, match="檔名"):
        download.acquire_model(root)


@pytest.mark.parametrize("url", ["http://example.com/model", "file:///local/model", "https://secret@example.com/model"])
def test_https_redirect_rejects_downgrade_and_credentials_before_request(url):
    handler = download.HTTPSRedirect()
    with pytest.raises(ValueError, match="HTTPS"):
        handler.redirect_request(urllib.request.Request("https://example.com/model"), None, 302, "", {}, url)


def test_network_response_final_url_must_still_be_https(tmp_path, monkeypatch):
    root, asset = portable_root(tmp_path)
    mocked_download(monkeypatch, url="http://example.com/unsafe")
    with pytest.raises(ValueError, match="HTTPS"):
        download.acquire_model(root)
    assert not (root / "models" / asset["filename"]).exists()


@pytest.mark.parametrize("linked_path", ["models", "partial", "target", "lock"])
def test_symlinks_cannot_overwrite_external_files(tmp_path, linked_path):
    root, asset = portable_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "preserve.txt"
    secret.write_bytes(b"do not overwrite")
    models = root / "models"
    try:
        if linked_path == "models":
            models.symlink_to(outside, target_is_directory=True)
        else:
            models.mkdir()
            names = {"partial": asset["filename"] + ".part", "target": asset["filename"], "lock": ".download.lock"}
            (models / names[linked_path]).symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable for this Windows account")
    with pytest.raises(ValueError):
        download.acquire_model(root)
    assert secret.read_bytes() == b"do not overwrite"


def test_cli_manual_import_uses_only_stdlib_and_unicode_paths(tmp_path):
    root, asset = portable_root(tmp_path)
    source = tmp_path / "手動 下載.gguf"
    source.write_bytes(PAYLOAD)
    result = subprocess.run([sys.executable, "-I", "-S", download.__file__, "--root", str(root),
                             "--source", str(source)], capture_output=True, encoding="utf-8",
                            env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"), timeout=20)
    assert result.returncode == 0, result.stderr
    assert (root / "models" / asset["filename"]).read_bytes() == PAYLOAD
    assert "模型已就緒" in result.stdout


def test_cli_reconfigures_redirected_non_unicode_streams_before_printing(tmp_path, monkeypatch):
    root, asset = portable_root(tmp_path)
    source = tmp_path / "手動 下載.gguf"
    source.write_bytes(PAYLOAD)
    stdout_bytes, stderr_bytes = io.BytesIO(), io.BytesIO()
    stdout = io.TextIOWrapper(stdout_bytes, encoding="cp1252", errors="strict")
    stderr = io.TextIOWrapper(stderr_bytes, encoding="cp1252", errors="strict")
    monkeypatch.setattr(download.sys, "stdout", stdout)
    monkeypatch.setattr(download.sys, "stderr", stderr)
    monkeypatch.setattr(download.sys, "argv", [download.__file__, "--root", str(root), "--source", str(source)])
    assert download.main() == 0
    stdout.flush()
    stderr.flush()
    assert stdout.encoding == stderr.encoding == "utf-8"
    assert "模型已就緒" in stdout_bytes.getvalue().decode("utf-8")
    assert (root / "models" / asset["filename"]).read_bytes() == PAYLOAD
