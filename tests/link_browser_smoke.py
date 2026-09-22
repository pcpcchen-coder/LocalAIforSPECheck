"""Real browser + real API/storage; synthetic downloads replace only network I/O."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory(prefix='specheck-link-browser-') as tmp:
        temp = Path(tmp)
        sample = (ROOT/'examples/external-extraction/complete.standard.json').read_bytes()
        (temp/'result.json').write_bytes(sample)
        factory = '''import hashlib, time
from pathlib import Path
from spec_check.app import create_app
from spec_check import link_import
def download(url, **kwargs):
    time.sleep(1.2)
    if url == 'https://fixtures.example/standard.txt':
        raw, name = '連結匯入合成規範：必須為 48 V。'.encode(), 'standard.txt'
    elif url == 'https://fixtures.example/result.json':
        raw, name = Path(__file__).with_name('result.json').read_bytes(), 'result.json'
    else:
        raise ValueError('連結回傳網頁，請使用直接下載網址。')
    return link_import.Download(raw, name, dict(url=url, final_url=url, bytes=len(raw), download_sha256=hashlib.sha256(raw).hexdigest()))
link_import.download = download
app = create_app()
'''
        (temp/'browser_fixture.py').write_text(factory, encoding='utf-8')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        log=open(temp/'app.log','w+',encoding='utf-8')
        env=dict(os.environ,SPEC_CHECK_DATA=str(temp/'data'),PYTHONPATH=str(ROOT))
        process=subprocess.Popen([sys.executable,'-m','uvicorn','browser_fixture:app','--app-dir',tmp,
                                  '--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        base=f'http://127.0.0.1:{port}'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for _ in range(100):
                try:
                    with opener.open(base+'/api/health',timeout=1):break
                except OSError:time.sleep(.1)
            else:raise AssertionError('No app health')
            with sync_playwright() as pw:
                kwargs=dict(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
                if os.environ.get('SPEC_CHECK_BROWSER'):kwargs['executable_path']=os.environ['SPEC_CHECK_BROWSER']
                browser=pw.chromium.launch(**kwargs)
                page=browser.new_page(viewport={'width':1440,'height':1050},locale='zh-TW',accept_downloads=True)
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base)
                page.locator('#library-link-url').fill('https://fixtures.example/standard.txt')
                page.locator('#library-link-form button').click()
                expect(page.locator('#library-link-status')).to_contain_text('已等待')
                expect(page.locator('#document-dialog')).to_be_visible()
                expect(page.locator('#document-import-source')).to_contain_text('https://fixtures.example/standard.txt')
                page.locator('[data-close="document-dialog"]').click()
                with page.expect_download() as downloaded:
                    page.locator('a[href="/api/v2/library/standalone/prompt"]').click()
                downloaded.value.save_as(temp/'prompt.md')
                assert 'local-specheck-standard-package' in (temp/'prompt.md').read_text(encoding='utf-8')
                page.locator('#prepared-open').click()
                page.locator('#prepared-mode').select_option('link')
                page.locator('#prepared-url').fill('https://fixtures.example/result.json')
                page.locator('#prepared-reviewer').fill('合成驗收人')
                page.locator('#prepared-model').fill('ChatGPT（合成驗收）')
                partial=json.loads(sample);partial['coverage'].update(status='partial',description='尚缺附錄')
                (temp/'result.json').write_text(json.dumps(partial,ensure_ascii=False),encoding='utf-8')
                page.locator('#prepared-preview').click()
                expect(page.locator('#prepared-result')).to_contain_text('外部聲明尚未完成')
                expect(page.locator('#prepared-save')).to_be_disabled()
                (temp/'result.json').write_bytes(sample)
                page.locator('#prepared-preview').click()
                expect(page.locator('#prepared-status')).to_contain_text('已等待')
                expect(page.locator('#prepared-save')).to_be_enabled()
                expect(page.locator('#prepared-result')).to_contain_text('1 個原文區塊')
                page.locator('#prepared-model').fill('ChatGPT（版本未知）')
                expect(page.locator('#prepared-save')).to_be_disabled()
                page.locator('#prepared-preview').click()
                expect(page.locator('#prepared-save')).to_be_enabled()
                if os.environ.get('SPEC_CHECK_SCREENSHOTS')!='0':
                    page.screenshot(path=str(ROOT/'docs/screenshots/link-import.png'))
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.querySelector("#prepared-dialog").scrollWidth <= document.querySelector("#prepared-dialog").clientWidth + 1')
                page.locator('#prepared-ack').check();page.locator('#prepared-save').click()
                expect(page.locator('#prepared-dialog')).not_to_be_visible()
                expect(page.locator('#document-status')).to_contain_text('尚待人工確認')
                expect(page.locator('#document-original')).to_contain_text('成果 JSON')
                page.locator('#document-reviewer').fill('人工覆核人');page.locator('#document-ack').check()
                page.locator('#document-confirm').click()
                expect(page.locator('#document-status')).to_contain_text('已人工確認')
                page.locator('[data-close="document-dialog"]').click();page.reload()
                expect(page.locator('#library-list [data-document]')).to_have_count(2)
                page.locator('#prepared-open').click();page.locator('#prepared-mode').select_option('file')
                page.locator('#prepared-file').set_input_files(str(temp/'result.json'))
                page.locator('#prepared-model').fill('ChatGPT（版本未知）')
                page.locator('#prepared-preview').click();expect(page.locator('#prepared-result')).to_contain_text('相同成果')
                page.locator('#prepared-ack').check();page.locator('#prepared-save').click()
                expect(page.locator('#document-status')).to_contain_text('已人工確認')
                page.locator('[data-close="document-dialog"]').click()
                page.locator('#library-link-url').fill('https://fixtures.example/login')
                page.locator('#library-link-form button').click()
                expect(page.locator('#library-link-status')).to_contain_text('連結回傳網頁')
                assert not errors, errors
                print(json.dumps(dict(passed=True,checks=['link_download_status','prompt_download','result_link_preview',
                    'partial_preview_only','preview_invalidation','390px','manual_confirmation','reload','file_import_dedup','link_error'],page_errors=errors),ensure_ascii=False))
                browser.close()
        except Exception:
            log.flush();print((temp/'app.log').read_text(encoding='utf-8')[-5000:]);raise
        finally:
            process.terminate()
            try:process.wait(10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            log.close()


if __name__=='__main__':main()
