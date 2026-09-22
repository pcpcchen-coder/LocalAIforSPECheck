"""Optional real-browser workflow smoke test (not part of default pytest).

pip install playwright
python -m playwright install chromium
python tests/browser_smoke.py
Set SPEC_CHECK_BROWSER to use an existing Chromium executable.
"""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory(prefix='specheck-browser-') as temporary:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port = sock.getsockname()[1]
        env = dict(os.environ,SPEC_CHECK_DATA=str(Path(temporary)/'data'))
        log = open(Path(temporary)/'server.log','w+',encoding='utf-8')
        process = subprocess.Popen([sys.executable,'launcher.py','--no-browser','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        base = f'http://127.0.0.1:{port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for _ in range(100):
                try:
                    with opener.open(base+'/api/health',timeout=1) as r:
                        if json.load(r)['status']=='ok': break
                except OSError:
                    time.sleep(.05)
            else:
                raise RuntimeError('App did not start')
            with sync_playwright() as pw:
                kwargs = dict(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
                if os.environ.get('SPEC_CHECK_BROWSER'):
                    kwargs['executable_path']=os.environ['SPEC_CHECK_BROWSER']
                browser = pw.chromium.launch(**kwargs)
                page = browser.new_page(viewport={'width':1440,'height':1100},locale='zh-TW',accept_downloads=True)
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base + '/classic')
                expect(page.locator('#welcome')).to_be_visible()
                page.locator('#welcome-demo').click()
                expect(page.locator('#result-rows tr[data-result]')).to_have_count(10,timeout=15000)
                expect(page.locator('#run-monitor')).to_contain_text('已完成')
                assert page.locator('.ranking-card').count()==2
                screenshots=ROOT/'docs'/'screenshots'
                screenshots.mkdir(exist_ok=True)
                page.screenshot(path=str(screenshots/'comparison.png'),full_page=True)
                page.locator('#filter-status').select_option('mismatch')
                expect(page.locator('#result-rows tr[data-result]')).to_have_count(3)
                page.locator('#result-rows .row-open').first.click()
                expect(page.locator('#review-dialog')).to_be_visible()
                page.locator('#reviewer').fill('示範確認人')
                page.locator('#review-decision').select_option('changed')
                page.locator('#review-final-status').select_option('partial')
                page.locator('#review-note').fill('操作驗收：已核對原文，保留部分差異待補資料。')
                page.locator('#review-form button[type=submit]').click()
                expect(page.locator('#detail-version')).to_contain_text('v1',timeout=10000)
                expect(page.locator('#detail-meta')).to_contain_text('人工修正')
                expect(page.locator('#detail-meta')).to_contain_text('部分符合')
                expect(page.locator('#review-history')).to_contain_text('操作驗收')
                page.screenshot(path=str(screenshots/'review.png'),full_page=True)
                page.locator('[data-close="review-dialog"]').click()
                page.locator('[data-step="reports"]').click()
                with page.expect_download() as info:
                    page.locator('#export-html').click()
                downloaded=info.value
                report=Path(temporary)/'report.html'
                downloaded.save_as(report)
                assert '操作驗收' in report.read_text(encoding='utf-8')
                (ROOT/'examples'/'demo_report.html').write_bytes(report.read_bytes())
                if page.locator('#refresh-exports').count():
                    page.locator('#refresh-exports').click()
                    expect(page.locator('#export-history')).to_contain_text('HTML')
                page.locator('#new-project').click()
                page.locator('#project-name').fill('瀏覽器驗收專案')
                page.locator('#project-form button[type=submit]').click()
                expect(page.locator('#project-title')).to_have_text('瀏覽器驗收專案')
                page.locator('#product-file').set_input_files(str(ROOT/'examples/product_48v_controller.txt'))
                page.locator('#product-upload button[type=submit]').click()
                expect(page.locator('.document-row')).to_have_count(1)
                page.locator('#standard-files').set_input_files([str(ROOT/'examples/standard_a_48v.txt'),str(ROOT/'examples/standard_b_400v.txt')])
                page.locator('#standard-upload button[type=submit]').click()
                expect(page.locator('.document-row')).to_have_count(3)
                for index in range(3):
                    page.locator('[data-preview]').nth(index).click()
                    page.locator('#preview-check').check()
                    page.locator('#confirm-document').click()
                    expect(page.locator('#preview-confirmed')).to_be_visible()
                    page.locator('[data-close="document-dialog"]').click()
                page.locator('#settings-button').click()
                page.locator('#setting-model').fill('browser-test-model')
                page.locator('#settings-form button[type=submit]').click()
                expect(page.locator('#settings-dialog')).not_to_be_visible()
                page.reload()
                expect(page.locator('#project-title')).to_have_text('瀏覽器驗收專案')
                expect(page.locator('#model-name')).to_have_text('browser-test-model')
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                assert not errors, errors
                browser.close()
                print('PASS: real Chromium demo, 10 rows, filters, review, export, upload, confirm, settings, persistence, narrow layout, zero page errors')
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait()
            log.close()


if __name__=='__main__':
    main()
