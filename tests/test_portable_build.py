"""Portable distribution integrity checks; real Windows startup runs separately in CI."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

from scripts import build_windows_portable as build


def archive_at(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


@pytest.mark.parametrize("name", [
    "../escape", "one/../../escape", r"..\escape", "/absolute", r"C:\Windows\file",
    "folder/file:stream", "folder/CON", "folder/NUL.txt", "folder/COM1.log", "file.",
    "file ", "one//two", "one/./two", "folder/<name>", "folder/line\nfeed",
])
def test_rejects_windows_and_posix_archive_escapes(tmp_path, name):
    archive = archive_at(tmp_path / "bad.zip", {"valid.txt": b"valid", name: b"bad"})
    with pytest.raises(ValueError):
        build.safe_extract_zip(archive, tmp_path / "target")
    assert not (tmp_path / "target" / "valid.txt").exists()
    assert not (tmp_path / "escape").exists()


def test_safe_extract_preserves_nested_binary_contents(tmp_path):
    archive = archive_at(tmp_path / "safe.zip", {"folder/a.dll": b"\x00\xff", "folder/readme.txt": b"notice"})
    build.safe_extract_zip(archive, tmp_path / "target")
    assert (tmp_path / "target/folder/a.dll").read_bytes() == b"\x00\xff"


def test_rejects_case_collisions_and_zip_bombs(tmp_path):
    archive = archive_at(tmp_path / "collision.zip", {"engine.DLL": b"a", "ENGINE.dll": b"b"})
    with pytest.raises(ValueError, match="Duplicate"):
        build.safe_extract_zip(archive, tmp_path / "out")
    large = archive_at(tmp_path / "large.zip", {"file": b"12345"})
    with pytest.raises(ValueError, match="expanded size"):
        build.safe_extract_zip(large, tmp_path / "out2", max_bytes=4)


def test_rejects_archive_symlinks(tmp_path):
    archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive, "w") as output:
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(link, "../outside")
    with pytest.raises(ValueError, match="Special file"):
        build.safe_extract_zip(archive, tmp_path / "out")


def test_embedded_runtime_search_paths_are_relative(tmp_path):
    runtime = tmp_path / "space path 繁體中文" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"test-exe")
    (runtime / "python313.zip").write_bytes(b"test-stdlib")
    build.configure_embedded_python(runtime, "3.13.15")
    assert (runtime / "python313._pth").read_text().splitlines() == [
        "python313.zip", ".", "Lib/site-packages", "../app", "import site"
    ]


@pytest.mark.parametrize("text", ["fastapi>=0.115", "-r requirements.txt", "pkg @ https://example.com/pkg.whl", "pkg==1\nPKG==1", "# empty"])
def test_dependency_lock_rejects_unpinned_or_duplicate_entries(tmp_path, text):
    lock = tmp_path / "requirements.txt"
    lock.write_text(text)
    with pytest.raises(ValueError):
        build.validate_requirements(lock)


def test_checked_in_build_inputs_are_fully_locked():
    lock = json.loads((build.REPOSITORY / "portable/build-lock.json").read_text())
    assert lock["schema_version"] == 1
    for asset in [lock["python"], lock["engine"], lock["model"], *lock["licenses"]]:
        build.validate_asset(asset)
    assert lock["model"]["revision"] in lock["model"]["url"]
    assert lock["engine"]["version"] in lock["engine"]["url"]
    assert lock["model"]["size"] < build.MAX_RELEASE_BYTES
    requirements = build.validate_requirements(build.REPOSITORY / "portable/requirements.lock.txt")
    assert {item.split("==")[0] for item in requirements} >= {"fastapi", "uvicorn", "pypdf", "python-docx", "openpyxl"}


def test_cache_hit_rehashes_content_and_corrupt_download_is_never_used(tmp_path, monkeypatch):
    payload = b"trusted bytes"
    digest = hashlib.sha256(payload).hexdigest()
    asset = {"url": "https://example.com/file.zip", "sha256": digest}
    cached = tmp_path / digest
    cached.write_bytes(payload)
    assert build.download_asset(asset, tmp_path) == cached
    cached.write_bytes(b"tampered cached bytes")
    class Response(io.BytesIO):
        url = asset["url"]
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *a, **kw: Response(b"wrong download"))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        build.download_asset(asset, tmp_path)
    assert not cached.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_checks_hash_and_expected_size(tmp_path, monkeypatch):
    payload = b"download"
    asset = {"url": "https://example.com/file.zip", "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
    class Response(io.BytesIO):
        url = asset["url"]
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *a, **kw: Response(payload))
    assert build.download_asset(asset, tmp_path).read_bytes() == payload


@pytest.mark.parametrize("asset", [
    {"url": "http://example.com/asset", "sha256": "a" * 64},
    {"url": "https://secret@example.com/asset", "sha256": "a" * 64},
    {"url": "https://example.com/asset", "sha256": "replace-this-later"},
])
def test_download_rejects_unsafe_or_unpinned_assets(asset):
    with pytest.raises(ValueError):
        build.validate_asset(asset)


def fake_repository(path):
    files = {
        "spec_check/__init__.py": "version = 1",
        "spec_check/app.py": "# app",
        "spec_check/static/index.html": "<html></html>",
        "spec_check/__pycache__/app.pyc": "private cache",
        "spec_check/private.db": "private database",
        "data/projects/private.json": "private project",
        "logs/private.log": "private log",
        ".env": "SECRET=private",
        "portable/launcher.py": "# portable",
        "portable/Start.bat": "@echo off",
        "portable/Stop.bat": "@echo off",
        "portable/Choose_model.bat": "@echo off",
        "README.md": "readme",
        "docs/USER_GUIDE.md": "guide",
    }
    for name in ("product_48v_controller.txt", "standard_a_48v.txt", "standard_b_400v.txt", "gold_template.json", "demo_report.html"):
        files[f"examples/{name}"] = "example"
    for name, content in files.items():
        destination = path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    return path


def test_application_allowlist_excludes_private_data_and_build_artifacts(tmp_path):
    repository = fake_repository(tmp_path / "repo")
    bundle = tmp_path / "bundle"
    build.copy_application(repository, bundle)
    assert (bundle / "app/spec_check/app.py").is_file()
    assert (bundle / "app/portable_launcher.py").is_file()
    assert (bundle / "app/examples/product_48v_controller.txt").is_file()
    assert not list((bundle / "data").iterdir())
    assert not list((bundle / "logs").iterdir())
    assert not any(p.suffix in {".pyc", ".db"} for p in bundle.rglob("*"))
    assert not (bundle / ".env").exists()


def test_engine_copies_runtime_dlls_and_licenses_but_no_other_programs(tmp_path):
    archive = archive_at(tmp_path / "engine.zip", {
        "build/bin/llama-server.exe": b"exe",
        "build/bin/llama-server-impl.dll": b"required server dll",
        "build/bin/ggml-cpu.dll": b"cpu dll",
        "build/bin/llama-cli.exe": b"unnecessary exe",
        "LICENSE-LLVM-OpenMP": b"upstream license",
    })
    engine = tmp_path / "bundle/engine"
    build.install_engine(archive, engine)
    assert {p.name for p in engine.iterdir()} == {"llama-server.exe", "llama-server-impl.dll", "ggml-cpu.dll"}
    assert (engine.parent / "licenses/engine/LICENSE-LLVM-OpenMP").read_bytes() == b"upstream license"


def test_vc_runtime_uses_redistributable_directory_and_app_local_copies(tmp_path, monkeypatch):
    program_files = tmp_path / "Program Files"
    source = program_files / "Microsoft Visual Studio/2022/Enterprise/VC/Redist/MSVC/14.44.000/x64/Microsoft.VC143.CRT"
    source.mkdir(parents=True)
    (source / "msvcp140.dll").write_bytes(b"redistributable")
    (source / "not-a-runtime.exe").write_bytes(b"no")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "empty"))
    bundle = tmp_path / "bundle"
    result = build.copy_visual_cpp_runtime(bundle)
    assert result["version"] == "14.44.000"
    assert (bundle / "engine/msvcp140.dll").read_bytes() == b"redistributable"
    assert (bundle / "runtime/msvcp140.dll").read_bytes() == b"redistributable"
    assert not list(bundle.rglob("*.exe"))


def test_manifest_and_zip_cover_every_release_file_and_preserve_empty_data(tmp_path):
    bundle = tmp_path / build.BUNDLE_NAME
    (bundle / "app").mkdir(parents=True)
    (bundle / "data").mkdir()
    (bundle / "logs").mkdir()
    (bundle / "app/file.txt").write_text("測試", encoding="utf-8")
    (bundle / "app/MANIFEST.json").write_text("nested application file")
    manifest = build.write_manifest(bundle, {"offline_model_included": True})
    records = json.loads(manifest.read_text())["files"]
    assert {item["path"] for item in records} == {"app/file.txt", "app/MANIFEST.json"}
    for item in records:
        assert build.sha256_file(bundle / item["path"]) == item["sha256"]
        assert (bundle / item["path"]).stat().st_size == item["size"]
    archive = tmp_path / "release.zip"
    build.create_release_zip(bundle, archive)
    with zipfile.ZipFile(archive) as source:
        assert all(name.startswith(build.BUNDLE_NAME + "/") for name in source.namelist())
        assert build.BUNDLE_NAME + "/data/" in source.namelist()
        assert build.BUNDLE_NAME + "/logs/" in source.namelist()
    digest = build.sha256_file(archive)
    build.create_release_zip(bundle, archive)
    assert build.sha256_file(archive) == digest


def test_oversize_release_is_deleted_instead_of_published(tmp_path):
    bundle = tmp_path / build.BUNDLE_NAME
    bundle.mkdir()
    (bundle / "file").write_bytes(b"content")
    archive = tmp_path / "oversize.zip"
    with pytest.raises(ValueError, match="2 GiB"):
        build.create_release_zip(bundle, archive, max_bytes=1)
    assert not archive.exists()


def test_dependency_install_is_binary_only_and_does_not_bootstrap_pip(tmp_path, monkeypatch):
    lock = tmp_path / "requirements.txt"
    lock.write_text("fastapi==0.141.1\n")
    calls = []
    monkeypatch.setattr(build.subprocess, "run", lambda command, **kw: calls.append(command))
    monkeypatch.setattr(build, "validate_installed_dependencies", lambda target: None)
    build.install_dependencies(tmp_path / "runtime", lock)
    pip_command = calls[0]
    assert pip_command[:3] == [build.sys.executable, "-m", "pip"]
    assert "--only-binary=:all:" in pip_command and "--no-deps" in pip_command
    assert calls[1][0] == str(tmp_path / "runtime/python.exe")
    assert not any("get-pip" in item or "ensurepip" in item for command in calls for item in command)


def test_dependency_closure_checks_windows_markers_and_versions(tmp_path):
    first = tmp_path / "first-1.0.dist-info"
    first.mkdir()
    (first / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: first\nVersion: 1.0\n"
        "Requires-Dist: windows-only>=2; sys_platform == 'win32'\n"
        "Requires-Dist: unused-extra; extra == 'test'\n"
    )
    build.validate_installed_dependencies(tmp_path, {"sys_platform": "linux"})
    with pytest.raises(ValueError, match="windows-only.*missing"):
        build.validate_installed_dependencies(tmp_path, {"sys_platform": "win32"})
    second = tmp_path / "windows_only-2.0.dist-info"
    second.mkdir()
    metadata = second / "METADATA"
    metadata.write_text("Metadata-Version: 2.1\nName: windows-only\nVersion: 2.0\n")
    build.validate_installed_dependencies(tmp_path, {"sys_platform": "win32"})
    metadata.write_text("Metadata-Version: 2.1\nName: windows-only\nVersion: 1.0\n")
    with pytest.raises(ValueError, match="bundled version: 1.0"):
        build.validate_installed_dependencies(tmp_path, {"sys_platform": "win32"})
