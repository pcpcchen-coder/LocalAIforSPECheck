"""Optional real Chromium execution-monitor acceptance test.

Run ``python tests/progress_browser_smoke.py`` after installing Playwright and
Chromium; SPEC_CHECK_BROWSER may point to an existing Chromium executable.
Uses a real application server and a deliberately delayed, synthetic model HTTP
server. No model download or model-accuracy claim is involved. Screenshots use
only synthetic documents. Both servers and the browser run in this process tree.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class DelayedModel:
    """Gate individual real HTTP replies, making first-request waiting testable."""

    def __init__(self):
        self.calls = []
        self.gates = []
        self.lock = threading.Lock()
        self.unblocked = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.reply({"data": [{"id": "synthetic-progress-test"}]})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                gate = threading.Event()
                with owner.lock:
                    owner.calls.append(payload)
                    owner.gates.append(gate)
                    if owner.unblocked:
                        gate.set()
                if not gate.wait(120):
                    self.send_error(504)
                    return
                inputs = json.loads(payload["messages"][1]["content"])
                block = inputs["product_blocks"][0]
                answer = {
                    "status": "match", "explanation": "合成驗收：電壓原文相符。",
                    "differences": [], "confidence": 0.8,
                    "evidence": [{"block_id": block["id"], "quote": "額定電壓：48 V。"}],
                }
                self.reply({"choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}, "finish_reason": "stop"}]})

            def reply(self, value):
                body = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def release(self, index):
        with self.lock:
            self.gates[index].set()

    def release_all(self):
        with self.lock:
            self.unblocked = True
            for gate in self.gates:
                gate.set()

    def block_new_calls(self):
        with self.lock:
            self.unblocked = False

    def close(self):
        self.release_all()
        self.server.shutdown()
        self.server.server_close()


def checked(response):
    assert response.ok, (response.status, response.text())
    return response.json()


def seed_project(request, base, name, standard_name="合成技術規範：直流通訊模組.txt"):
    project = checked(request.post(base + "/api/projects", data={"name": name}))
    url = base + "/api/projects/" + project["id"]
    # Three separately extracted blocks exceed the 2,000-character window size.
    product = "\n".join("額定電壓：48 V。" + f"合成第 {index} 段產品說明。" * 95 for index in range(1, 4))
    for role, filename, content in (
        ("product", "合成產品：直流通訊模組.txt", product),
        ("standard", standard_name, "額定電壓：48 V。"),
    ):
        document = checked(request.post(url + "/documents", multipart={
            "role": role,
            "file": {"name": filename, "mimeType": "text/plain", "buffer": content.encode("utf-8")},
        }))
        checked(request.post(url + f"/documents/{document['id']}/confirm"))
    return project["id"]


def open_project(page, base, project_id):
    from playwright.sync_api import expect

    page.goto(base + '/classic')
    page.wait_for_load_state("networkidle")
    target = page.locator(f'[data-project="{project_id}"]')
    target.click()
    expect(target).to_have_class(re.compile(r"\bactive\b"))
    page.locator('[data-step="compare"]').click()
    page.locator("#start-run").click()


def main():
    from playwright.sync_api import sync_playwright, expect

    with tempfile.TemporaryDirectory(prefix="specheck-progress-") as temporary:
        model = DelayedModel()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = dict(os.environ, SPEC_CHECK_DATA=str(Path(temporary) / "data"))
        log = open(Path(temporary) / "server.log", "w+", encoding="utf-8")
        process = subprocess.Popen([sys.executable, "launcher.py", "--no-browser", "--port", str(port)],
                                   cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for _ in range(150):
                try:
                    with opener.open(base + "/api/health", timeout=1) as response:
                        if json.load(response)["status"] == "ok":
                            break
                except OSError:
                    time.sleep(.05)
            else:
                raise RuntimeError("Application did not start")

            with sync_playwright() as pw:
                options = dict(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
                if os.environ.get("SPEC_CHECK_BROWSER"):
                    options["executable_path"] = os.environ["SPEC_CHECK_BROWSER"]
                browser = pw.chromium.launch(**options)
                page = browser.new_page(viewport={"width": 1440, "height": 1100}, locale="zh-TW")
                errors, requests = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text)
                        if message.type == "error" and "net::ERR_FAILED" not in message.text
                        and "net::ERR_CONNECTION_FAILED" not in message.text else None)
                page.on("request", lambda request: requests.append(request.url))
                checked(page.request.put(base + "/api/settings", data={
                    "base_url": model.base_url, "model": "synthetic-progress-test",
                    "context_chars": 2000, "timeout": 120,
                }))
                project_id = seed_project(page.request, base, "執行現況驗收｜合成文件")
                open_project(page, base, project_id)
                expect(page.locator("#monitor-stage")).to_contain_text("等待本機模型回覆", timeout=15000)
                expect(page.locator("#monitor-completed")).to_contain_text("0 / 1")
                expect(page.locator("#monitor-current-standard")).to_contain_text("合成技術規範")
                expect(page.locator("#monitor-current-block")).to_contain_text("1")
                expect(page.locator("#monitor-window")).to_contain_text("1 / 3")
                expect(page.locator("#monitor-activity")).to_have_attribute("data-active", "true")
                assert len(model.calls) == 1, "The first real HTTP model request should be held open"
                run_id = page.locator("#review-run-select").input_value()
                progress_url = base + f"/api/runs/{run_id}/progress"
                initial_progress = checked(page.request.get(progress_url))
                assert not {"documents", "results", "rankings"}.intersection(initial_progress)
                assert initial_progress["progress"]["last_response_at"] is None

                initial_clock = page.locator("#monitor-request-elapsed").inner_text()
                initial_response = page.locator("#monitor-last-response").inner_text()
                full_run_pattern = re.compile(r"/api/runs/[^/]+$")
                full_count = sum(bool(full_run_pattern.search(url)) for url in requests)
                progress_count = sum(url.endswith("/progress") for url in requests)
                page.wait_for_timeout(4300)
                assert page.locator("#monitor-request-elapsed").inner_text() != initial_clock
                assert page.locator("#monitor-last-response").inner_text() == initial_response, "A local timer must not invent model responses"
                later_progress = checked(page.request.get(progress_url))
                assert later_progress["progress"]["updated_at"] == initial_progress["progress"]["updated_at"], "Polling cannot manufacture model activity timestamps"
                expect(page.locator("#monitor-completed")).to_contain_text("0 / 1")
                assert sum(url.endswith("/progress") for url in requests) >= progress_count + 2
                assert sum(bool(full_run_pattern.search(url)) for url in requests) == full_count, "Unchanged progress must not reload all document/result payloads"
                screenshots = ROOT / "docs" / "screenshots"
                screenshots.mkdir(exist_ok=True)
                if os.environ.get("SPEC_CHECK_SCREENSHOTS", "1") != "0":
                    page.screenshot(path=str(screenshots / "execution-progress.png"), full_page=True)

                # The model request remains in flight; only browser-to-app polling fails.
                page.route("**/api/runs/*/progress", lambda route: route.abort("connectionfailed"))
                expect(page.locator("#monitor-stage")).to_contain_text("無法確認目前執行狀態", timeout=10000)
                expect(page.locator("#monitor-activity")).to_have_attribute("data-active", "false")
                if os.environ.get("SPEC_CHECK_SCREENSHOTS", "1") != "0":
                    page.screenshot(path=str(screenshots / "execution-offline.png"), full_page=True)
                page.unroute("**/api/runs/*/progress")
                page.locator("#refresh-progress").click()
                expect(page.locator("#monitor-stage")).to_contain_text("等待本機模型回覆", timeout=10000)

                # A genuine first response advances the window, while no row is saved yet.
                model.release(0)
                expect(page.locator("#monitor-window")).to_contain_text("2 / 3", timeout=15000)
                expect(page.locator("#monitor-completed")).to_contain_text("0 / 1")
                assert page.locator("#monitor-last-response").inner_text() != initial_response
                assert len(model.calls) == 2
                # A completed lightweight status remains truthful even if fetching
                # the full result payload fails. The UI must allow a manual retry.
                full_run_url = base + f"/api/runs/{run_id}"
                page.route(full_run_url, lambda route: route.abort("connectionfailed"))
                model.release_all()
                expect(page.locator("#monitor-stage")).to_contain_text("已完成 · 結果清單更新未完成", timeout=15000)
                expect(page.locator("#monitor-wait-note")).to_contain_text("結果清單讀取失敗")
                expect(page.locator("#monitor-completed")).to_contain_text("1 / 1")
                expect(page.locator("#monitor-activity")).to_have_attribute("data-active", "false")
                expect(page.locator("#result-rows tr[data-result]")).to_have_count(0)
                finished_clock = page.locator("#monitor-elapsed").inner_text()
                page.wait_for_timeout(2300)
                assert page.locator("#monitor-elapsed").inner_text() == finished_clock, "Terminal clocks must stop"
                page.unroute(full_run_url)
                page.locator("#refresh-progress").click()
                expect(page.locator("#result-rows tr[data-result]")).to_have_count(1, timeout=10000)
                expect(page.locator("#monitor-stage")).to_have_text("本批次比對已完成")
                expect(page.locator("#monitor-wait-note")).not_to_be_visible()

                # Cancel while a second run is inside an actual outstanding model request.
                model.block_new_calls()
                hostile_name = "規範_<img src=x onerror=alert(1)>.txt"
                project_id = seed_project(page.request, base, "停止與安全顯示驗收", hostile_name)
                open_project(page, base, project_id)
                expect(page.locator("#monitor-stage")).to_contain_text("等待本機模型回覆", timeout=15000)
                expect(page.locator("#monitor-current-standard")).to_contain_text(hostile_name)
                assert page.locator("#run-monitor img").count() == 0, "Source filenames must remain text"
                page.locator("#cancel-run").click()
                expect(page.locator("#monitor-stage")).to_contain_text("已要求停止", timeout=10000)
                expect(page.locator("#monitor-completed")).to_contain_text("0 / 1")
                model.release_all()
                expect(page.locator("#run-monitor")).to_contain_text("已停止", timeout=15000)
                expect(page.locator("#resume-run")).to_be_visible()
                expect(page.locator("#result-rows tr[data-result]")).to_have_count(0)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Narrow layout must not overflow"
                assert not errors, errors
                browser.close()
                print("PASS: real app + delayed HTTP model; first-request 0% activity; live timers; lightweight polling; disconnect/recovery; actual window progress; completed-result fetch failure/retry with frozen timer; pending cancellation; escaped filenames; narrow layout; zero page errors")
        except Exception:
            log.flush()
            log.seek(0)
            print(log.read()[-10000:], file=sys.stderr)
            raise
        finally:
            model.close()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            log.close()


if __name__ == "__main__":
    main()
