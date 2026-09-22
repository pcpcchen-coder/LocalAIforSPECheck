"""v0.3 real Chromium acceptance using synthetic, deterministic HTTP model replies.

Run: python tests/workspace_browser_smoke.py
SPEC_CHECK_BROWSER may point to Chromium; SPEC_CHECK_SCREENSHOTS=0 avoids
rewriting synthetic screenshots. Servers and browser run in one process tree.
This validates the workflow and UI, not a real model's document accuracy.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class SyntheticModel:
    def __init__(self):
        self.calls = []
        self.screen_entered = threading.Event()
        self.screen_release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send(self, result):
                data = json.dumps(result, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.send({"data": [{"id": "synthetic-workspace-test"}]})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                data = json.loads(payload["messages"][-1]["content"])
                if "source" in data:
                    kind = "extract"
                    source = data["source"]
                    quote = source["text"]
                    parameter, _, value = quote.strip().partition("：")
                    result = {"items": [{
                        "name": parameter, "parameter": parameter, "value": value, "unit": "", "operator": "=",
                        "conditions": "", "exceptions": "", "test_method": "",
                        "criticality": "high" if "安全" in quote else "medium",
                        "criticality_basis": "合成案例：安全測試要求需優先覆核。" if "安全" in quote else "合成案例：明確性能規格。",
                        "quote": quote, "kind": "specification" if data["role"] == "product" else "requirement",
                    }]}
                elif "standard_excerpts" in data:
                    kind = "screen"
                    owner.screen_entered.set()
                    owner.screen_release.wait(120)
                    aviation = "航空" in data["standard_name"]
                    result = {"relevance": "low" if aviation else "high", "applicability": "conditional" if aviation else "likely",
                              "reason": "合成案例：航空用途與室內控制器不同，請人工確認排除條件。" if aviation else "合成案例：電氣與防護要求對應產品功能，應逐項查核。",
                              "evidence": [{"document_id": source["document_id"], "block_id": source["block_id"], "quote": source["text"]}
                                           for source in (data["product_excerpts"][0], data["standard_excerpts"][0])]}
                else:
                    kind = "compare"
                    requirement = data["requirement"]
                    parameter = requirement["parameter"]
                    block = next((b for b in data["product_blocks"] if parameter in b["text"]), None)
                    status = "missing" if block is None else "mismatch" if "防護" in parameter else "match"
                    result = {"status": status, "explanation": {
                        "match": "合成案例：產品額定電壓與要求一致。",
                        "mismatch": "合成案例：標準要求 IP65，產品原文為 IP54，須確認防護差異。",
                        "missing": "合成案例：產品文件沒有安全耐壓測試證據，需補測試報告。",
                    }[status], "differences": [] if status == "match" else ["防護等級 IP54 與要求 IP65 不同。" if status == "mismatch" else "缺少安全耐壓測試報告。"],
                        "evidence": [] if block is None else [{"block_id": block["id"], "quote": block["text"]}],
                        "confidence": 0.9, "parameter_relation": "not_found" if block is None else "same", "all_conditions_checked": block is not None}
                owner.calls.append(kind)
                self.send({"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}, "finish_reason": "stop"}]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.screen_release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


def checked(response):
    assert response.ok, (response.status, response.text())
    return response.json()


def file_payload(name, text):
    return {"name": name, "mimeType": "text/plain", "buffer": text.encode()}


def wait_api(request, base, path, statuses, timeout=15):
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        state = checked(request.get(base + path))
        if state["status"] in statuses:
            return state
        time.sleep(.05)
    raise AssertionError(state)


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory(prefix="specheck-workspace-browser-") as temporary:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, SPEC_CHECK_DATA=str(Path(temporary) / "data"))
        log = open(Path(temporary) / "app.log", "w+", encoding="utf-8")
        process = subprocess.Popen([sys.executable, "launcher.py", "--no-browser", "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        model = SyntheticModel()
        base = f"http://127.0.0.1:{port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for _ in range(100):
                try:
                    with opener.open(base + "/api/health", timeout=1) as response:
                        if json.load(response)["status"] == "ok":
                            break
                except OSError:
                    time.sleep(.05)
            else:
                raise RuntimeError("App did not start")
            with sync_playwright() as pw:
                kwargs = dict(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
                if os.environ.get("SPEC_CHECK_BROWSER"):
                    kwargs["executable_path"] = os.environ["SPEC_CHECK_BROWSER"]
                browser = pw.chromium.launch(**kwargs)
                page = browser.new_page(viewport={"width": 1440, "height": 1050}, locale="zh-TW", accept_downloads=True)
                errors, requests = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" and "net::ERR_CONNECTION_FAILED" not in message.text and "Failed to load resource" not in message.text else None)
                page.on("request", lambda request: requests.append(request.url))
                checked(page.request.put(base + "/api/settings", data={"base_url": model.base_url, "model": "synthetic-workspace-test", "context_chars": 2000, "timeout": 120}))
                page.goto(base)
                expect(page.locator("#panel-library")).to_be_visible()
                page.locator("#library-files").set_input_files([
                    file_payload("合成電氣與防護標準.txt", "額定電壓：48 V\n防護等級：IP65\n"),
                    file_payload("合成安全測試標準.txt", "安全耐壓：500 V，須提供測試報告\n"),
                    file_payload("合成航空規範.txt", "飛行高度：8000 m\n"),
                ])
                page.locator("#library-upload button[type=submit]").click()
                expect(page.locator("#library-list [data-select-doc]")).to_have_count(3)
                page.locator("#library-select-page").check()
                page.locator("#library-extract").click()
                expect(page.locator("#execution-title")).to_contain_text("完成", timeout=15000)
                page.locator("#library-refresh").click()
                page.locator("#library-select-page").check()
                # Inspect each source before explicitly acknowledging batch confirmation.
                for index in range(3):
                    page.locator("#library-list [data-document]").nth(index).click()
                    expect(page.locator("#document-sources")).not_to_be_empty()
                    expect(page.locator("#document-items")).not_to_be_empty()
                    page.locator('[data-close="document-dialog"]').click()
                page.locator("#library-confirm").click()
                page.locator("#batch-reviewer").fill("合成驗收工程師")
                page.locator("#batch-note").fill("已核對三份合成文件與抽取項目。")
                page.locator("#batch-ack").check()
                page.locator("#batch-form button[type=submit]").click()
                expect(page.locator("#batch-dialog")).not_to_be_visible()
                page.locator('[data-panel="product"]').click()
                page.locator("#product-file").set_input_files(file_payload("合成室內控制器.txt", "額定電壓：48 V\n防護等級：IP54\n維修介面：USB\n"))
                page.locator("#product-upload button[type=submit]").click()
                expect(page.locator("#product-list [data-document]")).to_have_count(1)
                expect(page.locator("#document-dialog")).to_be_visible()
                page.locator("#document-extract").click()
                expect(page.locator("#execution-title")).to_contain_text("完成", timeout=15000)
                page.locator("#product-list [data-document]").click()
                page.locator("#document-reviewer").fill("合成驗收工程師")
                page.locator("#document-note").fill("合成產品的 48 V、IP54、USB 與原文一致。")
                page.locator("#document-ack").check()
                page.locator("#document-confirm").click()
                expect(page.locator("#document-status")).to_contain_text("已人工確認")
                page.locator('[data-close="document-dialog"]').click()
                page.locator("[data-use-product]").click()
                page.locator("#analysis-name").fill("室內控制器 · 全庫風險檢查（合成案例）")
                page.locator("#context-purpose").fill("室內儲能控制器")
                page.locator("#context-environment").fill("室內工業場域")
                page.locator("#comparison-mode").select_option("exhaustive")
                page.locator("#analysis-start").click()
                assert model.screen_entered.wait(10), "The actual screening request must reach the HTTP model"
                expect(page.locator("#execution-title")).to_contain_text("等待", timeout=15000)
                analyses = checked(page.request.get(base + "/api/v2/analyses"))["items"]
                analysis_id = analyses[0]["id"]
                status_path = f"/api/v2/analyses/{analysis_id}"
                initial = checked(page.request.get(base + status_path))
                assert not {"documents", "results", "settings"}.intersection(initial)
                source_reads = sum("/documents/" in url for url in requests)
                initial_wait = page.locator("#execution-wait").inner_text()
                last_response = initial["progress"]["last_response_at"]
                page.wait_for_timeout(3900)
                assert page.locator("#execution-wait").inner_text() != initial_wait
                assert checked(page.request.get(base + status_path))["progress"]["last_response_at"] == last_response
                assert sum("/documents/" in url for url in requests) == source_reads
                page.route(base + status_path, lambda route: route.abort("connectionfailed"))
                expect(page.locator("#execution-title")).to_contain_text("連線中斷", timeout=10000)
                expect(page.locator("#execution-spinner")).to_have_attribute("data-active", "false")
                page.unroute(base + status_path)
                page.locator("#job-refresh").click()
                expect(page.locator("#execution-title")).to_contain_text("等待本機模型", timeout=10000)
                model.screen_release.set()
                wait_api(page.request, base, status_path, {"awaiting_selection"})
                expect(page.locator("#screening-list [data-screen-select]")).to_have_count(3, timeout=10000)
                screening = checked(page.request.get(base + status_path + "/screening"))["items"]
                assert all(row["selected"] for row in screening)
                aviation = next(row for row in screening if "航空" in row["standard_name"])
                page.locator(f'[data-screen-select="{aviation["standard_id"]}"]').uncheck()
                page.locator(f'[data-screen-reason="{aviation["standard_id"]}"]').fill("合成案例：本產品供室內使用，不涉及航空用途。")
                page.locator("#selection-reviewer").fill("合成驗收工程師")
                page.locator("#selection-save").click()
                page.locator("#comparison-start").click()
                wait_api(page.request, base, status_path, {"completed"})
                expect(page.locator("#results-list [data-result]")).to_have_count(3, timeout=10000)
                results = checked(page.request.get(base + status_path + "/results"))["items"]
                assert {row["status"] for row in results} == {"match", "mismatch", "missing"}
                screenshots = ROOT / "docs" / "screenshots"
                screenshots.mkdir(exist_ok=True)
                if os.environ.get("SPEC_CHECK_SCREENSHOTS", "1") != "0":
                    page.evaluate("window.scrollTo(0, 0)")
                    page.screenshot(path=str(screenshots / "workspace-risks.png"), full_page=True)
                page.locator("#result-status").select_option("missing")
                expect(page.locator("#results-list [data-result]")).to_have_count(1)
                page.locator("#results-list [data-result]").click()
                expect(page.locator("#result-dialog")).to_be_visible()
                expect(page.locator("#result-requirement")).to_contain_text("500 V")
                page.locator("#review-decision").select_option("changed")
                page.locator("#review-status").select_option("uncertain")
                page.locator("#review-risk").select_option("high")
                page.locator("#review-reviewer").fill("合成驗收工程師")
                page.locator("#review-note").fill("合成驗收：文件缺少耐壓證據，請補測試報告後再判定。")
                page.locator("#review-form button[type=submit]").click()
                expect(page.locator("#review-history")).to_contain_text("請補測試報告")
                if os.environ.get("SPEC_CHECK_SCREENSHOTS", "1") != "0":
                    page.set_viewport_size({"width": 1440, "height": 1400})
                    page.evaluate("window.scrollTo(0, 0)")
                    page.locator("#result-dialog").evaluate("element => element.scrollTop = 0")
                    page.screenshot(path=str(screenshots / "workspace-review.png"), full_page=False)
                page.locator('[data-close="result-dialog"]').click()
                page.locator('[data-panel="exports"]').click()
                with page.expect_download() as download:
                    page.locator('[data-export="html"]').click()
                path = Path(temporary) / "analysis.html"
                download.value.save_as(path)
                report = path.read_text(encoding="utf-8")
                assert "請補測試報告" in report and "航空" in report and "不涉及航空用途" in report
                page.locator("#exports-refresh").click()
                expect(page.locator("#exports-list")).to_contain_text(".html")
                page.reload()
                page.locator('[data-panel="product"]').click()
                page.locator(f'[data-analysis="{analysis_id}"]').click()
                page.locator('[data-panel="exports"]').click()
                expect(page.locator("#exports-analysis")).to_contain_text("室內控制器")
                page.set_viewport_size({"width": 390, "height": 844})
                for panel in ("library", "product", "screening", "results", "exports"):
                    page.locator(f'[data-panel="{panel}"]').click()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"Narrow overflow: {panel}"
                assert not errors, errors
                assert {"extract", "screen", "compare"}.issubset(model.calls)
                browser.close()
                print("PASS: v0.3 real Chromium + synthetic HTTP model; upload/batch extraction/source confirmation; full-library screening with no silent exclusion; lightweight live progress; disconnect/recovery; explicit exclusions; atomic comparison and evidence gap; human risk review; archived HTML; reload; all five narrow panels; zero page errors")
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
