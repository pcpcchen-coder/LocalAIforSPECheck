#!/usr/bin/env python3
"""Exercise the released runtime ZIP and independently obtained real local model.

Run on Windows after build_windows_portable.py. All documents are synthetic.
This is an integration check, not a model-accuracy benchmark or a substitute
for an organization's application-control acceptance test.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile


ARCHIVE_NAME = "LocalAIforSPECheck-Windows-x64.zip"
ROOT_NAME = "LocalAIforSPECheck-Windows-x64"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def request(base: str, path: str, payload=None, *, method=None, raw=False,
            content_type="application/json", timeout=30):
    body = payload
    if payload is not None and not isinstance(payload, bytes):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=body, method=method,
                                 headers={"Content-Type": content_type})
    try:
        with OPENER.open(req, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        # Requests contain synthetic fixtures only. Do not print request headers.
        raise AssertionError(f"HTTP {exc.code} at {path}: {exc.read(2048)!r}") from None
    return data if raw else json.loads(data)


def wait_for(predicate, *, timeout, description, process=None):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise AssertionError(f"Launcher exited {process.returncode} while waiting for {description}")
        try:
            result = predicate()
            if result:
                return result
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = type(exc).__name__
        time.sleep(0.4)
    raise TimeoutError(f"Timed out waiting for {description}; last error: {last_error}")


def socket_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def verify_and_extract(archive: Path, destination: Path) -> Path:
    check(archive.stat().st_size < 2 * 1024**3, "ZIP exceeds GitHub's per-asset size limit")
    expected = None
    for line in (archive.parent / "SHA256SUMS.txt").read_text(encoding="utf-8-sig").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].lstrip(" *") == archive.name:
            expected = parts[0].lower()
    check(expected is not None, "SHA256SUMS.txt does not cover the ZIP")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    check(digest == expected, "ZIP checksum differs from SHA256SUMS.txt")
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            path = PurePosixPath(member.filename.replace("\\", "/"))
            check(not path.is_absolute() and ".." not in path.parts,
                  f"Unsafe ZIP member: {member.filename}")
            check(path.parts and path.parts[0] == ROOT_NAME,
                  f"Unexpected ZIP root: {member.filename}")
            check(not any(":" in part for part in path.parts), "ZIP contains a drive/stream path")
            check((member.external_attr >> 16) & 0o170000 != 0o120000, "ZIP contains a symlink")
            check(path.suffix.lower() not in {".gguf", ".part"}, "App ZIP contains a model binary")
        bundle.extractall(destination)
    root = destination / ROOT_NAME
    for relative in ("runtime/python.exe", "app/portable_launcher.py", "engine/llama-server.exe",
                     "Start.bat", "Stop.bat", "Choose_model.bat", "Download_model.bat",
                     "app/download_model.py", "models/README.md"):
        check((root / relative).is_file(), f"Missing portable file: {relative}")
    check(not any((root / "models").glob("*.gguf")), "App ZIP must not contain a model")
    check(not (root / "data/spec_check.sqlite3").exists(), "ZIP contains a populated user database")
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    check(manifest.get("offline_model_included") is False, "Manifest must describe a model-free runtime ZIP")
    check(manifest.get("model_download", {}).get("sha256"), "Manifest lacks the separately pinned model")
    check(manifest.get("platform") == "windows-x64", "Unexpected platform in build manifest")
    for entry in manifest["files"]:
        path = (root / entry["path"]).resolve()
        check(path.is_relative_to(root) and path.is_file(), f"Invalid manifest member: {entry['path']}")
        check(path.stat().st_size == entry["size"], f"Manifest size mismatch: {entry['path']}")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        check(actual == entry["sha256"], f"Manifest checksum mismatch: {entry['path']}")
    return root


def import_separate_model(root: Path, source: Path, env: dict) -> dict:
    """Exercise the actual user's batch file, entirely offline after CI caching."""
    python = root / "runtime/python.exe"
    missing = subprocess.run([str(python), str(root / "app/portable_launcher.py"),
                              "--root", str(root), "--no-browser"],
                             cwd=root, env=env, capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
    check(missing.returncode == 1 and "Download_model.bat" in missing.stdout + missing.stderr,
          "First launch without a model must give an actionable download instruction")
    command = [str(Path(env.get("SYSTEMROOT", env.get("SystemRoot", r"C:\Windows"))) / "System32/cmd.exe"),
               "/d", "/c", "call", str(root / "Download_model.bat"), "--source", str(source.resolve())]
    imported = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=240)
    check(imported.returncode == 0, "Separate model import failed: " + imported.stdout + imported.stderr)
    asset = json.loads((root / "build-lock.json").read_text(encoding="utf-8"))["model"]
    model = root / "models" / asset["filename"]
    check(model.stat().st_size == asset["size"], "Imported model has an unexpected size")
    with model.open("rb") as stream:
        check(hashlib.file_digest(stream, "sha256").hexdigest() == asset["sha256"], "Imported model checksum differs")
    check(not list((root / "models").glob("*.part")), "Partial model remains after successful import")
    previous_stat = model.stat()
    # No --source: valid pre-existing model must succeed despite the invalid
    # proxy in clean_environment, proving the repeat run needs no network.
    repeated = subprocess.run([str(python), str(root / "app/download_model.py")],
                              cwd=root, env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
    check(repeated.returncode == 0, "Existing model verification attempted an unnecessary download")
    check(model.stat().st_mtime_ns == previous_stat.st_mtime_ns, "Existing model was rewritten")
    return {"filename": asset["filename"], "sha256": asset["sha256"], "size": asset["size"]}


def clean_environment(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.upper().startswith(("PYTHON", "CONDA", "VIRTUAL_ENV")) or key == "SPEC_CHECK_DATA":
            env.pop(key, None)
    windows = Path(env.get("SYSTEMROOT", env.get("SystemRoot", r"C:\Windows")))
    env["PATH"] = os.pathsep.join(str(windows / part) for part in ("System32", "", "System32/Wbem"))
    env["PYTHONUTF8"] = "1"
    # Ensure runtime does not need a working configured proxy for localhost.
    env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    env["NO_PROXY"] = ""
    return env


def upload(base: str, project_id: str, role: str, filename: str, text: str):
    boundary = "specheck-" + uuid.uuid4().hex
    payload = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"role\"\r\n\r\n{role}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n{text}\r\n--{boundary}--\r\n"
    ).encode("utf-8")
    document = request(base, f"/api/projects/{project_id}/documents", payload,
                       content_type=f"multipart/form-data; boundary={boundary}")
    check(document["blocks"], f"No extracted blocks from {filename}")
    return request(base, f"/api/projects/{project_id}/documents/{document['id']}/confirm", {})


def upload_v2(base: str, role: str, filename: str, text: str):
    boundary = "specheck-v2-" + uuid.uuid4().hex
    payload = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
               f"Content-Type: text/plain; charset=utf-8\r\n\r\n{text}\r\n--{boundary}--\r\n").encode("utf-8")
    route = "products" if role == "product" else "library"
    return request(base, f"/api/v2/{route}", payload, content_type=f"multipart/form-data; boundary={boundary}")


def v2_wait(base: str, path: str, expected: str, process, timeout: int):
    def reached():
        current = request(base, "/api/v2" + path)
        check("documents" not in current and "results" not in current,
              "v0.3 progress response includes full source snapshots or result rows")
        if current["status"] in {"failed", "cancelled", "interrupted"}:
            raise AssertionError(f"v0.3 job failed at {path}: {current.get('error') or current['status']}")
        return current if current["status"] == expected else None
    return wait_for(reached, timeout=timeout, description=f"v0.3 {path} -> {expected}", process=process)


def verify_v2_model_response(state: dict, phase: str):
    progress = state["progress"]
    check(progress["requests_completed"] >= 1 and progress.get("last_response_at"),
          f"v0.3 {phase} did not record an actual model response")


def run_v2_smoke(base: str, process, timeout: int) -> dict:
    """Actual new API + real local model; conservative uncertain results are valid."""
    documents = []
    extraction_jobs = []
    source_map = {}
    for role, filename, text in [("product", "v03-product.txt", "額定電壓：48 V\n"),
                                 ("standard", "v03-standard.txt", "額定電壓必須為 48 V。\n")]:
        document = upload_v2(base, role, filename, text)
        job = request(base, f"/api/v2/documents/{document['id']}/extract", {})
        finished = v2_wait(base, f"/jobs/{job['id']}", "completed", process, timeout)
        verify_v2_model_response(finished, role + " extraction")
        extraction_jobs.append(finished)
        document = request(base, f"/api/v2/documents/{document['id']}")
        page = request(base, f"/api/v2/documents/{document['id']}/items?limit=200")
        check(page["total"] == len(page["items"]) == document["item_count"] and page["total"] >= 1,
              "v0.3 extraction lost source items")
        document["items"] = page["items"]
        for block in document["blocks"]:
            source_map[(document["id"], block["id"])] = block["text"]
            mask = [False] * len(block["text"])
            for item in document["items"]:
                if item["block_id"] != block["id"]:
                    continue
                quote = item["quote"]
                check(quote and quote in block["text"], "v0.3 extraction quote is not in the original source")
                start = block["text"].find(quote)
                mask[start:start + len(quote)] = [True] * len(quote)
            check(all(covered or character.isspace() for character, covered in zip(block["text"], mask)),
                  "v0.3 extraction discarded an original source span instead of preserving an unresolved item")
        confirmed = request(base, f"/api/v2/documents/{document['id']}/confirm", {
            "expected_version": document["version"], "reviewer": "CI 合成案例驗收",
            "note": "僅確認合成來源及保留未解決項目，不作實際產品合規判定。", "acknowledge_warnings": True,
        })
        check(confirmed["confirmed"], "v0.3 source confirmation was not saved")
        documents.append(document)
    product, standard = documents
    analysis = request(base, "/api/v2/analyses", {
        "name": "Portable v0.3 合成完整流程", "product_id": product["id"], "library_ids": [standard["id"]],
        "context": {}, "comparison_mode": "exhaustive",
    })
    identifier = analysis["id"]
    screened = v2_wait(base, f"/analyses/{identifier}", "awaiting_selection", process, timeout)
    verify_v2_model_response(screened, "standard screening")
    screenings = request(base, f"/api/v2/analyses/{identifier}/screening?limit=200")
    check(screenings["total"] == len(screenings["items"]) == 1, "v0.3 did not retain the selected standard")
    check(screenings["items"][0]["standard_id"] == standard["id"] and screenings["items"][0]["selected"] is True,
          "v0.3 silently excluded the standard")
    for evidence in screenings["items"][0]["evidence"]:
        key = (evidence["document_id"], evidence["block_id"])
        check(key in source_map and evidence["quote"] in source_map[key], "v0.3 screening citation is invalid")
    request(base, f"/api/v2/analyses/{identifier}/compare", {"expected_version": screened["version"]})
    completed = v2_wait(base, f"/analyses/{identifier}", "completed", process, timeout)
    verify_v2_model_response(completed, "item comparison")
    results = request(base, f"/api/v2/analyses/{identifier}/results?limit=200")
    expected = {item["id"] for item in standard["items"] if item["kind"] != "context"}
    check(len(expected) >= 1 and results["total"] == len(results["items"]) == len(expected),
          "v0.3 did not persist one result for every selected non-context item")
    check({row["item"]["id"] for row in results["items"]} == expected,
          "v0.3 result records omit or duplicate selected source items")
    for row in results["items"]:
        check(row["status"] in {"match", "partial", "mismatch", "missing", "uncertain"}, "v0.3 invalid result status")
        check(row.get("risk", {}).get("level") in {"high", "medium", "low", "unknown", "none"},
              "v0.3 result lacks its risk priority")
        for evidence in row["evidence"]:
            key = (evidence["document_id"], evidence["block_id"])
            check(key in source_map and evidence["quote"] in source_map[key], "v0.3 product citation is invalid")
    exported = request(base, f"/api/v2/analyses/{identifier}/export?format=json")
    check(exported.get("kind") == "analysis" and exported.get("schema_version") == 2,
          "v0.3 JSON export lost the analysis schema")
    check(len(exported["results"]) == len(expected) and all("risk" in row and "ai_risk" in row for row in exported["results"]),
          "v0.3 JSON export lost risk findings")
    check({d["id"] for d in exported["documents"]} == {product["id"], standard["id"]},
          "v0.3 JSON export lost original source snapshots")
    check(exported["screenings"][0]["selected"] is True and exported["audit"],
          "v0.3 JSON export lost retained standards or workflow history")
    return dict(analysis_id=identifier, product_id=product["id"], standard_id=standard["id"],
                source_item_ids=sorted(expected), result_ids=sorted(row["id"] for row in results["items"]),
                statuses=[row["status"] for row in results["items"]],
                risk_levels=[row["risk"]["level"] for row in results["items"]],
                extraction_requests=[job["progress"]["requests_completed"] for job in extraction_jobs],
                screening_requests=screened["progress"]["requests_completed"],
                comparison_requests=completed["progress"]["requests_completed"])


def verify_v2_restart(base: str, previous: dict):
    identifier = previous["analysis_id"]
    restored = request(base, f"/api/v2/analyses/{identifier}")
    check(restored["status"] == "completed", "v0.3 analysis status did not survive restart")
    verify_v2_model_response(restored, "restored comparison")
    rows = request(base, f"/api/v2/analyses/{identifier}/results?limit=200")["items"]
    check(sorted(row["id"] for row in rows) == previous["result_ids"], "v0.3 results did not survive restart")
    check(sorted(row["item"]["id"] for row in rows) == previous["source_item_ids"], "v0.3 source item identities changed after restart")
    for document_id in [previous["product_id"], previous["standard_id"]]:
        document = request(base, f"/api/v2/documents/{document_id}")
        check(document["confirmed"] and document["index_status"] == "ready", "v0.3 indexed source did not survive restart")
    exports = request(base, f"/api/v2/analyses/{identifier}/exports")
    check(exports["total"] == 1 and exports["items"][0]["format"] == "json", "v0.3 export archive did not survive restart")


def start(root: Path, env: dict, log, timeout: int, *, batch=False):
    python = root / "runtime/python.exe"
    launcher = root / "app/portable_launcher.py"
    command = ([str(Path(env.get("SYSTEMROOT", env.get("SystemRoot", r"C:\Windows"))) / "System32/cmd.exe"), "/d", "/c", "call",
                str(root / "Start.bat"), "--no-browser"] if batch else
               [str(python), str(launcher), "--root", str(root), "--no-browser"])
    process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
    session_path = root / "data/portable-session.json"
    try:
        session = wait_for(lambda: json.loads(session_path.read_text(encoding="utf-8")),
                           timeout=timeout, description="portable session metadata", process=process)
        check(isinstance(session.get("pid"), int) and session["pid"] > 0, "Invalid supervisor PID")
        if not batch:
            check(session["pid"] == process.pid, "Portable session does not belong to the launched process")
        app_port, model_port = int(session["app_port"]), int(session["model_port"])
        base = f"http://127.0.0.1:{app_port}"
        health = wait_for(lambda: request(base, "/api/health", timeout=2),
                          timeout=timeout, description="application health", process=process)
        check(health.get("app") == "LocalAIforSPECheck", "Unexpected service at application port")
        # Launcher may publish session before the model has fully loaded.
        wait_for(lambda: request(base, "/api/connection", {}, timeout=5).get("models"),
                 timeout=timeout, description="loaded local model", process=process)
        return process, session, base
    except BaseException:
        process.terminate()
        process.wait(timeout=30)
        raise


def stop(root: Path, env: dict, process, session):
    completed = subprocess.run([str(Path(env.get("SYSTEMROOT", env.get("SystemRoot", r"C:\Windows"))) / "System32/cmd.exe"),
                                "/d", "/c", "call", str(root / "Stop.bat")], cwd=root, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
    check(completed.returncode == 0, f"Portable stop failed: {completed.stdout.decode('utf-8', 'replace')}")
    process.wait(timeout=90)
    check(process.returncode == 0, f"Portable supervisor exited {process.returncode} after stop")
    wait_for(lambda: not socket_open(int(session["app_port"])) and not socket_open(int(session["model_port"])),
             timeout=30, description="both owned services to stop")
    check(not (root / "data/portable-session.json").exists(), "Stale portable session remains after stop")


def run_smoke(args):
    archive = args.archive.resolve()
    destination = Path(tempfile.mkdtemp(prefix="SPE Check 繁體中文 ", dir=args.work_dir))
    root = verify_and_extract(archive, destination)
    env = clean_environment(root)
    separate_model = import_separate_model(root, args.model_source, env)
    python = root / "runtime/python.exe"
    check_result = subprocess.run([
        str(python), "-c",
        "import json,sys,fastapi,pydantic_core,lxml.etree,openpyxl,pypdf,uvicorn,sqlite3; "
        "print(json.dumps({'executable':sys.executable,'path':sys.path,'version':sys.version}))",
    ], cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    check(check_result.returncode == 0, "Bundled runtime import failed: " + check_result.stderr)
    interpreter = json.loads(check_result.stdout)
    for path in interpreter["path"]:
        check(Path(path).resolve().is_relative_to(root), f"Interpreter uses external package path: {path}")

    report = {
        "passed": False, "archive": archive.name,
        "platform": sys.platform, "runtime_version": interpreter["version"],
        "separate_model": separate_model,
        "checks": ["zip_excludes_model", "missing_model_guidance", "separate_model_batch_import",
                   "separate_model_hash_and_size", "existing_model_reuse_offline"],
        "limits": ["GitHub Windows runner has preinstalled system components; this is not a pristine Windows VM.",
                   "Synthetic classic and v0.3 workflows verify integration, not document-comparison accuracy."],
    }
    process = session = None
    with ExitStack() as stack:
        # Exercise fallback ports without stopping or reusing an unrelated listener.
        reserved = []
        for port in (8765, 1235):
            sock = stack.enter_context(socket.socket())
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
            reserved.append(sock)
        log = stack.enter_context((destination / "smoke-launcher.log").open("wb"))
        try:
            process, session, base = start(root, env, log, args.startup_timeout)
            check(int(session["app_port"]) != 8765 and int(session["model_port"]) != 1235,
                  "Launcher did not choose alternate ports when preferred ports were occupied")
            report["checks"].extend(["archive_checksum", "file_manifest", "bundled_imports", "unicode_spaced_path",
                                     "clean_path", "occupied_port_fallback", "model_connection"])
            settings = request(base, "/api/settings")
            check(settings["base_url"] == f"http://127.0.0.1:{session['model_port']}/v1",
                  "App does not use the portable model service")
            request(base, "/api/settings", {"timeout": 600, "max_tokens": 900, "temperature": 0}, method="PUT")
            project = request(base, "/api/projects", {"name": "Portable 繁體中文驗收"})
            product = upload(base, project["id"], "product", "product.txt", "額定電壓：48 V\n")
            standard = upload(base, project["id"], "standard", "standard.txt", "額定電壓：48 V\n")
            run = request(base, f"/api/projects/{project['id']}/runs",
                          {"mode": "local", "standard_ids": [standard["id"]]})
            observed_stages = set()

            def completed_run():
                current = request(base, f"/api/runs/{run['id']}/progress")
                check("documents" not in current and "results" not in current,
                      "Progress polling unexpectedly returns full source snapshots")
                check(current.get("server_time"), "Progress response lacks application heartbeat time")
                observed_stages.add(current["progress"]["stage"])
                if current["status"] in {"failed", "cancelled", "interrupted"}:
                    raise AssertionError(f"Real model run failed: {current['status']}")
                return request(base, f"/api/runs/{run['id']}") if current["status"] == "completed" else None

            result = wait_for(completed_run, timeout=args.inference_timeout,
                              description="real local-model comparison", process=process)
            check(result["mode"] == "local" and result["total"] == result["completed"] == 1,
                  "Real comparison did not complete exactly one requirement")
            progress = result["progress"]
            check(progress["stage"] == "completed" and progress["requests_completed"] == 1,
                  "Completed real model request was not reflected in execution progress")
            check(progress["last_response_at"] and progress["events"],
                  "Actual model response time or execution events were not saved")
            row = result["results"][0]
            check(row["status"] == "match", f"Expected cited exact match; got: {json.dumps(row, ensure_ascii=False)}")
            check(row["evidence"], "Real model produced no validated product evidence")
            source_blocks = {block["id"]: block["text"] for block in product["blocks"]}
            for evidence in row["evidence"]:
                check(evidence["block_id"] in source_blocks and evidence["quote"] in source_blocks[evidence["block_id"]],
                      "Model evidence is not an exact source quotation")
            check(row["product_coverage"]["scanned"] == row["product_coverage"]["total"] == 1,
                  "Product coverage is incomplete")
            reviewed = request(base, f"/api/results/{row['id']}/reviews", {
                "decision": "confirmed", "reviewer": "CI 驗收", "note": "已核對合成的 48 V 原文。",
                "expected_version": 0,
            })
            check(reviewed["review"]["version"] == 1 and len(reviewed["history"]) == 1,
                  "Review audit trail was not preserved")
            for fmt in ("html", "xlsx", "json"):
                output = request(base, f"/api/runs/{run['id']}/export?format={fmt}", raw=True)
                check(len(output) > 100, f"Empty {fmt} export")
            check(len(request(base, f"/api/runs/{run['id']}/exports")) == 3, "Export archive is incomplete")
            report["checks"].extend(["real_json_inference", "exact_cited_evidence", "source_coverage",
                                     "review_audit", "html_xlsx_json_exports", "lightweight_execution_progress"])
            report["observed_execution_stages"] = sorted(observed_stages)
            report["model"] = settings["model"]
            report["comparison_status"] = row["status"]
            report["v03_workflow"] = run_v2_smoke(base, process, args.workflow_phase_timeout)
            report["checks"].extend(["v03_real_model_extraction", "v03_original_span_retention", "v03_no_silent_standard_exclusion",
                                     "v03_real_model_item_comparison", "v03_risk_and_cited_evidence", "v03_json_analysis_export"])
            stop(root, env, process, session)
            process = session = None
            process, session, base = start(root, env, log, args.startup_timeout, batch=True)
            restored = request(base, f"/api/runs/{run['id']}")
            check(restored["results"][0]["review"]["version"] == 1, "Review did not survive restart")
            check(len(restored["exports"]) == 3, "Export history did not survive restart")
            restored_progress = request(base, f"/api/runs/{run['id']}/progress")["progress"]
            check(restored_progress["stage"] == "completed" and restored_progress["last_response_at"],
                  "Execution progress history did not survive restart")
            verify_v2_restart(base, report["v03_workflow"])
            report["checks"].append("v03_restart_persistence")
            stop(root, env, process, session)
            process = session = None
            for sock in reserved:
                check(sock.fileno() >= 0, "Launcher closed an unrelated listener")
            report["checks"].extend(["batch_start_stop", "restart_persistence", "owned_services_stop"])
            report["passed"] = True
        finally:
            if process is not None and process.poll() is None:
                try:
                    stop(root, env, process, session)
                except Exception:
                    process.terminate()
                    process.wait(timeout=30)
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if not report["passed"]:
                log.flush()
                for path in [destination / "smoke-launcher.log", *sorted((root / "logs").glob("*.log"))]:
                    if path.is_file():
                        print(f"Diagnostic tail: {path.name}", flush=True)
                        print(path.read_bytes()[-14000:].decode("utf-8", "replace"), flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=Path("dist") / ARCHIVE_NAME)
    parser.add_argument("--report", type=Path, default=Path("dist/portable-smoke-report.json"))
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--model-source", type=Path, required=True,
                        help="Verified independently downloaded/cached starter GGUF; never part of the ZIP")
    parser.add_argument("--startup-timeout", type=int, default=360)
    parser.add_argument("--inference-timeout", type=int, default=900)
    parser.add_argument("--workflow-phase-timeout", type=int, default=660,
                        help="Seconds per new extraction/screening/comparison phase (model timeout is 600 seconds)")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This integration check must run on Windows x64.")
    run_smoke(args)


if __name__ == "__main__":
    main()
