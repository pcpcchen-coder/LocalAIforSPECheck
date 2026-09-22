#!/usr/bin/env python3
"""Build a Windows x64 runtime ZIP; the inference model is downloaded separately.

Run on Windows with a build-time Python matching portable/build-lock.json.
Build dependencies need internet and pip. Users download a model once, then run offline.
Nothing installs on a user's PC.
"""
from __future__ import annotations

import argparse
from email.parser import Parser
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile


REPOSITORY = Path(__file__).resolve().parents[1]
BUNDLE_NAME = "LocalAIforSPECheck-Windows-x64"
MAX_RELEASE_BYTES = 2 * 1024 ** 3 - 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PINNED_REQUIREMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9_.+!-]+\Z")
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_asset(asset: dict) -> None:
    url = urllib.parse.urlsplit(asset.get("url", ""))
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("Every downloadable asset must use a public HTTPS URL")
    if not _SHA256.fullmatch(asset.get("sha256", "")):
        raise ValueError("Every downloadable asset requires a fixed lowercase SHA-256")


def download_asset(asset: dict, cache_dir: Path) -> Path:
    """Use a content-addressed cache, checking bytes on every cache hit."""
    validate_asset(asset)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / asset["sha256"]
    if cached.is_file() and sha256_file(cached) == asset["sha256"]:
        return cached
    if cached.exists():
        cached.unlink()
    request = urllib.request.Request(asset["url"], headers={"User-Agent": "LocalAIforSPECheck-builder/1"})
    temporary: Path | None = None
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            if urllib.parse.urlsplit(response.url).scheme != "https":
                raise ValueError("Refusing a download redirected outside HTTPS")
            with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False) as output:
                temporary = Path(output.name)
                shutil.copyfileobj(response, output, 1024 * 1024)
        actual = sha256_file(temporary)
        if actual != asset["sha256"]:
            raise ValueError(f"SHA-256 mismatch for {asset['url']}: expected {asset['sha256']}, got {actual}")
        if "size" in asset and temporary.stat().st_size != asset["size"]:
            raise ValueError(f"Size mismatch for {asset['url']}")
        os.replace(temporary, cached)
        return cached
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def safe_archive_path(name: str) -> PurePosixPath:
    """Reject POSIX and Windows traversal, device names, ADS, and aliases."""
    normalized = name.replace("\\", "/")
    parts = normalized.rstrip("/").split("/")
    if not parts or any(not part or part in {".", ".."} for part in parts):
        raise ValueError(f"Unsafe ZIP path: {name!r}")
    for part in parts:
        if (part[-1:] in {" ", "."} or any(c in part for c in ':<>"|?*\x00')
                or any(ord(c) < 32 for c in part)
                or part.split(".", 1)[0].upper() in _RESERVED_NAMES):
            raise ValueError(f"Unsafe Windows ZIP path: {name!r}")
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise ValueError(f"Absolute ZIP path: {name!r}")
    return path


def safe_extract_zip(archive: Path, destination: Path, *, max_bytes: int = MAX_RELEASE_BYTES) -> None:
    """Validate the entire central directory before writing any member."""
    destination.mkdir(parents=True, exist_ok=True)
    target_root = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        checked: list[tuple[zipfile.ZipInfo, Path]] = []
        seen: set[str] = set()
        expanded = 0
        for member in source.infolist():
            path = safe_archive_path(member.filename)
            key = path.as_posix().rstrip("/").casefold()
            if key in seen:
                raise ValueError(f"Duplicate Windows ZIP path: {member.filename}")
            seen.add(key)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise ValueError(f"Special file is not permitted: {member.filename}")
            expanded += member.file_size
            if expanded > max_bytes:
                raise ValueError("ZIP expanded size exceeds the permitted limit")
            target = destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(target_root):
                raise ValueError(f"ZIP path escapes destination: {member.filename}")
            checked.append((member, target))
        for member, target in checked:
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as input_stream, target.open("wb") as output:
                    shutil.copyfileobj(input_stream, output)


def configure_embedded_python(runtime: Path, version: str) -> None:
    tag = "".join(version.split(".")[:2])
    if not (runtime / "python.exe").is_file() or not (runtime / f"python{tag}.zip").is_file():
        raise ValueError("Embedded Python archive is missing python.exe or its standard library")
    (runtime / f"python{tag}._pth").write_text(
        f"python{tag}.zip\n.\nLib/site-packages\n../app\nimport site\n", encoding="ascii"
    )


def validate_requirements(path: Path) -> list[str]:
    requirements = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not _PINNED_REQUIREMENT.fullmatch(line):
            raise ValueError(f"Portable dependencies must be fully pinned, without URLs or extras: {line}")
        requirements.append(line)
    if not requirements:
        raise ValueError("Portable dependency lock is empty")
    names = [re.sub(r"[-_.]+", "-", item.split("==")[0]).lower() for item in requirements]
    if len(names) != len(set(names)):
        raise ValueError("Portable dependency lock contains duplicate packages")
    return requirements


def install_dependencies(runtime: Path, requirements: Path) -> None:
    validate_requirements(requirements)
    target = runtime / "Lib" / "site-packages"
    subprocess.run([
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
        "--only-binary=:all:", "--no-compile", "--no-deps", "--target", str(target),
        "-r", str(requirements),
    ], check=True)
    # Installed console scripts refer to the build Python and are not needed.
    shutil.rmtree(target / "bin", ignore_errors=True)
    validate_installed_dependencies(target)
    with tempfile.TemporaryDirectory(prefix="specheck-import-check-") as temporary:
        environment = dict(os.environ, SPEC_CHECK_DATA=temporary)
        subprocess.run([
            str(runtime / "python.exe"), "-I", "-B", "-c",
            "import fastapi,uvicorn,multipart,pypdf,docx,openpyxl,pydantic_core;"
            "import spec_check.app;print('Portable runtime imports OK')",
        ], check=True, cwd=runtime.parent, env=environment)


def validate_installed_dependencies(target: Path, environment: dict | None = None) -> None:
    """Validate all dependencies, including Windows/Python conditional markers.

    packaging comes from build-time pip and is not shipped as a target dependency.
    Imports alone cannot catch lazily loaded Windows-only dependencies.
    """
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.markers import default_environment

    marker_environment = default_environment()
    marker_environment.update(environment or {})
    marker_environment["extra"] = ""
    normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    distributions = list(importlib.metadata.distributions(path=[str(target)]))
    installed = {normalize(dist.metadata["Name"]): dist.version for dist in distributions}
    if not installed:
        raise ValueError("No Python dependency distributions were installed")
    errors = []
    for dist in distributions:
        for entry in dist.requires or []:
            requirement = Requirement(entry)
            if requirement.marker and not requirement.marker.evaluate(marker_environment):
                continue
            version = installed.get(normalize(requirement.name))
            if version is None or not requirement.specifier.contains(version, prereleases=True):
                errors.append(f"{dist.metadata['Name']} requires {entry}; bundled version: {version or 'missing'}")
    if errors:
        raise ValueError("Incomplete or incompatible Windows dependency lock:\n" + "\n".join(errors))


def copy_file(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Required regular source file missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_application(repository: Path, bundle: Path) -> None:
    """Explicit file/type allowlist: never copy a checkout or user data wholesale."""
    source_package = repository / "spec_check"
    for source in sorted(source_package.rglob("*")):
        relative = source.relative_to(source_package)
        if "__pycache__" in relative.parts:
            continue
        if source.is_symlink():
            raise ValueError(f"Symlink is not allowed in application sources: {source}")
        if not source.is_file():
            continue
        if source.suffix == ".py" or (relative.parts[0] == "prompts" and source.suffix == ".md") or (relative.parts[0] == "static" and source.suffix in {".html", ".css", ".js", ".svg", ".png", ".ico"}):
            copy_file(source, bundle / "app" / "spec_check" / relative)
    copy_file(repository / "portable" / "launcher.py", bundle / "app" / "portable_launcher.py")
    copy_file(repository / "portable" / "download_model.py", bundle / "app" / "download_model.py")
    copy_file(repository / "models" / "README.md", bundle / "models" / "README.md")
    for name in ("Start.bat", "Stop.bat", "Choose_model.bat", "Download_model.bat"):
        copy_file(repository / "portable" / name, bundle / name)
    for name in ("product_48v_controller.txt", "standard_a_48v.txt", "standard_b_400v.txt", "gold_template.json", "demo_report.html"):
        copy_file(repository / "examples" / name, bundle / "app" / "examples" / name)
    for source in sorted((repository / "examples" / "external-extraction").glob("*")):
        if source.is_file() and source.suffix in {".json", ".md"}:
            copy_file(source, bundle / "examples" / "external-extraction" / source.name)
    copy_file(repository / "README.md", bundle / "README.md")
    for source in sorted((repository / "docs").glob("*.md")):
        copy_file(source, bundle / "docs" / source.name)
    for source in sorted((repository / "docs" / "screenshots").glob("*.png")):
        copy_file(source, bundle / "docs" / "screenshots" / source.name)
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md"):
        if (repository / name).is_file():
            copy_file(repository / name, bundle / name)
    for name in ("data", "logs", "models", "licenses"):
        (bundle / name).mkdir(parents=True, exist_ok=True)


def install_engine(archive: Path, engine: Path, executable: str = "llama-server.exe") -> None:
    if Path(executable).name != executable or executable != "llama-server.exe":
        raise ValueError("Only the llama-server.exe engine is supported")
    with tempfile.TemporaryDirectory(prefix="specheck-engine-") as temporary:
        extracted = Path(temporary)
        safe_extract_zip(archive, extracted)
        candidates = list(extracted.rglob(executable))
        if len(candidates) != 1:
            raise ValueError("Engine archive must contain exactly one llama-server.exe")
        binary_dir = candidates[0].parent
        for source in sorted(binary_dir.iterdir()):
            if source.is_file() and (source.name == executable or source.suffix.lower() == ".dll"):
                copy_file(source, engine / source.name)
        for source in sorted(extracted.rglob("*")):
            if source.is_file() and source.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                copy_file(source, engine.parent / "licenses" / "engine" / source.relative_to(extracted))


def copy_visual_cpp_runtime(bundle: Path, required: bool = True) -> dict:
    """App-local deployment from Microsoft's redistributable VC143 CRT directory.

    This uses only DLLs from the official redistribution directory of the build
    runner. It never installs a redistributable or copies arbitrary System32 DLLs.
    """
    roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
             Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))]
    candidates: list[Path] = []
    for root in roots:
        candidates.extend(root.glob("Microsoft Visual Studio/2022/*/VC/Redist/MSVC/*/x64/Microsoft.VC143.CRT"))
    if not candidates:
        if required:
            raise RuntimeError("Visual Studio 2022 VC143 x64 redistribution directory was not found on the build machine")
        return {"source": "not-required", "files": []}
    source = max(candidates, key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.parts[-3])))
    files = sorted(source.glob("*.dll"))
    if not files:
        raise RuntimeError("The Visual C++ redistribution directory contains no DLLs")
    for dll in files:
        copy_file(dll, bundle / "engine" / dll.name)
        copy_file(dll, bundle / "runtime" / dll.name)
    notice = (
        "Microsoft Visual C++ Runtime DLLs\n\n"
        "These files come from the build machine's Visual Studio 2022\n"
        "VC/Redist/MSVC distribution directory, and are deployed beside the\n"
        "application executables. No redistributable installer runs on users' PCs.\n\n"
        "The DLLs remain subject to Microsoft's license terms; this notice does\n"
        "not replace or modify those terms. Official license and redistribution information:\n"
        "https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/\n"
        "https://visualstudio.microsoft.com/wp-content/uploads/2021/09/Visual-C-Runtime-2015-2022-License-1.docx\n"
        "https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution\n\n"
        f"Build-time VC143 CRT version: {source.parts[-3]}\n"
        "File SHA-256 values are recorded in MANIFEST.json.\n\n"
        + "\n".join(dll.name for dll in files) + "\n"
    )
    (bundle / "licenses").mkdir(parents=True, exist_ok=True)
    (bundle / "licenses" / "MICROSOFT-CRT-NOTICE.txt").write_text(notice, encoding="utf-8")
    return {"source": "Microsoft Visual Studio 2022 VC143 x64 redistributable CRT",
            "version": source.parts[-3], "files": [p.name for p in files]}


def collect_dependency_notices(bundle: Path) -> list[dict]:
    """Retain wheel licenses and provide an index; do not invent license terms."""
    packages = []
    site = bundle / "runtime" / "Lib" / "site-packages"
    for dist in sorted(site.glob("*.dist-info")):
        metadata_file = dist / "METADATA"
        if not metadata_file.is_file():
            raise ValueError(f"Dependency metadata is missing: {dist.name}")
        metadata = Parser().parsestr(metadata_file.read_text(encoding="utf-8"))
        licenses = []
        for source in sorted(dist.rglob("*")):
            if source.is_file() and (source.name.lower().startswith(("license", "copying", "notice")) or "licenses" in source.relative_to(dist).parts):
                destination = bundle / "licenses" / "python-packages" / dist.name / source.relative_to(dist)
                copy_file(source, destination)
                licenses.append(destination.relative_to(bundle).as_posix())
        packages.append({"name": metadata.get("Name"), "version": metadata.get("Version"),
                         "license_expression": metadata.get("License-Expression", metadata.get("License", "See package metadata")),
                         "license_files": licenses,
                         "metadata": metadata_file.relative_to(bundle).as_posix()})
    return packages


def write_manifest(bundle: Path, metadata: dict) -> Path:
    files = []
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path != bundle / "MANIFEST.json":
            files.append({"path": path.relative_to(bundle).as_posix(), "size": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = bundle / "MANIFEST.json"
    manifest.write_text(json.dumps({"schema_version": 1, "manifest_self_excluded": True,
                                    **metadata, "files": files}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def create_release_zip(bundle: Path, destination: Path, *, max_bytes: int = MAX_RELEASE_BYTES) -> None:
    """Sorted members and fixed timestamps make subsequent content checks simple."""
    if any(path.suffix.lower() in {".gguf", ".part"} for path in bundle.rglob("*")):
        raise ValueError("Portable release ZIP must not include model binaries or partial downloads")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as output:
        for path in sorted([bundle, *bundle.rglob("*")]):
            if path.is_symlink():
                raise ValueError(f"Refusing to package symlink: {path}")
            relative = path.relative_to(bundle.parent).as_posix()
            info = zipfile.ZipInfo(relative + ("/" if path.is_dir() else ""), date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = ((stat.S_IFDIR | 0o755) if path.is_dir() else (stat.S_IFREG | 0o644)) << 16
            if path.is_dir():
                output.writestr(info, b"")
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as source, output.open(info, "w", force_zip64=True) as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
    if destination.stat().st_size > max_bytes:
        size = destination.stat().st_size
        destination.unlink()
        raise ValueError(f"Release ZIP is {size:,} bytes; it must be smaller than 2 GiB for GitHub Releases")


def build(args: argparse.Namespace) -> Path:
    lock = json.loads(args.lock_file.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1:
        raise ValueError("Unsupported portable build lock schema")
    if sys.platform != "win32" or sys.maxsize <= 2 ** 32:
        raise RuntimeError("Build the Windows x64 portable release on 64-bit Windows")
    required_python = tuple(int(part) for part in lock["python"]["version"].split(".")[:2])
    if sys.version_info[:2] != required_python:
        raise RuntimeError(f"Build Python must be {required_python[0]}.{required_python[1]} to select compatible Windows wheels")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    requirements = REPOSITORY / "portable" / "requirements.lock.txt"
    with tempfile.TemporaryDirectory(prefix="specheck-build-") as temporary:
        bundle = Path(temporary) / BUNDLE_NAME
        bundle.mkdir()
        copy_application(REPOSITORY, bundle)
        safe_extract_zip(download_asset(lock["python"], args.cache_dir), bundle / "runtime")
        configure_embedded_python(bundle / "runtime", lock["python"]["version"])
        install_dependencies(bundle / "runtime", requirements)
        install_engine(download_asset(lock["engine"], args.cache_dir), bundle / "engine", lock["engine"].get("executable", "llama-server.exe"))
        vc_runtime = copy_visual_cpp_runtime(bundle, required=lock.get("vc_runtime", {}).get("required", True))
        # The model stays outside every app ZIP; Download_model.bat uses this lock.
        validate_asset(lock["model"])
        for item in lock.get("licenses", []):
            filename = item["filename"]
            if safe_archive_path(filename).name != filename:
                raise ValueError("License filename must be a basename")
            copy_file(download_asset(item, args.cache_dir), bundle / "licenses" / filename)
        python_license = bundle / "runtime" / "LICENSE.txt"
        if python_license.is_file():
            copy_file(python_license, bundle / "licenses" / "Python-LICENSE.txt")
        packages = collect_dependency_notices(bundle)
        copy_file(args.lock_file, bundle / "build-lock.json")
        copy_file(requirements, bundle / "requirements.lock.txt")
        notices = {"python": lock["python"], "engine": lock["engine"],
                   "model": lock["model"], "model_distributed_separately": True,
                   "visual_cpp_runtime": vc_runtime, "python_packages": packages,
                   "licenses": lock.get("licenses", [])}
        (bundle / "licenses" / "THIRD_PARTY_NOTICES.json").write_text(json.dumps(notices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Smoke imports can create application data or bytecode; release always starts clean.
        for name in ("data", "logs"):
            shutil.rmtree(bundle / name)
            (bundle / name).mkdir()
        for cache in list(bundle.rglob("__pycache__")):
            shutil.rmtree(cache)
        write_manifest(bundle, {"platform": "windows-x64", "offline_model_included": False,
                                "model_download": lock["model"],
                                "python_version": lock["python"]["version"],
                                "dependency_lock_sha256": sha256_file(requirements),
                                "build_lock_sha256": sha256_file(args.lock_file)})
        archive = args.output_dir / f"{BUNDLE_NAME}.zip"
        create_release_zip(bundle, archive)
    (args.output_dir / "SHA256SUMS.txt").write_text(f"{sha256_file(archive)}  {archive.name}\n", encoding="ascii")
    print(f"Created {archive} ({archive.stat().st_size:,} bytes)")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=REPOSITORY / "dist")
    parser.add_argument("--cache-dir", type=Path, default=REPOSITORY / ".portable-cache")
    parser.add_argument("--lock-file", type=Path, default=REPOSITORY / "portable" / "build-lock.json")
    parser.add_argument("--skip-model", action="store_true", help=argparse.SUPPRESS)  # compatibility: model is always separate
    args = parser.parse_args()
    for name in ("output_dir", "cache_dir", "lock_file"):
        setattr(args, name, getattr(args, name).resolve())
    try:
        build(args)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Build failed: {exc}\n")


if __name__ == "__main__":
    main()
