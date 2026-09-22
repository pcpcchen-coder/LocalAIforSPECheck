"""Real Chromium round trip for manual ChatGPT extraction (synthetic JSON, no cloud)."""
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def reply(task):
    return {**{k:task[k] for k in ('schema_version','package_id','document_id','source_sha256','batch_id')},
            'format':'local-specheck-extraction-result', 'blocks':[
                {'block_id':b['id'],'items':[dict(name='額定電壓要求',parameter='額定電壓',value='48',unit='V',operator='=',
                    conditions='',exceptions='',test_method='',criticality='medium',criticality_basis='合成性能要求。',
                    quote=b['text'],kind='requirement',context_evidence=[])]} for b in task['blocks']]}


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory(prefix='specheck-external-browser-') as tmp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        log=open(Path(tmp)/'app.log','w+',encoding='utf-8')
        process=subprocess.Popen([sys.executable,'launcher.py','--no-browser','--port',str(port)],cwd=ROOT,
                    env=dict(os.environ,SPEC_CHECK_DATA=str(Path(tmp)/'data')),stdout=log,stderr=subprocess.STDOUT)
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
                text='\n'.join(f'合成條文 {n}：額定電壓必須為 48 V。'+ '甲'*1250 for n in range(3))
                page.locator('#library-files').set_input_files({'name':'外部萃取合成標準.txt','mimeType':'text/plain','buffer':text.encode()})
                page.locator('#library-upload button[type=submit]').click()
                expect(page.locator('#library-list [data-document]')).to_have_count(1)
                page.locator('#library-list [data-document]').click()
                page.locator('#document-external').click()
                page.locator('#external-create-reviewer').fill('合成驗收人')
                page.locator('#external-batch-size').select_option('1')
                page.locator('#external-public').check()
                with page.expect_download() as download:
                    page.locator('#external-create-form button[type=submit]').click()
                path=Path(tmp)/'package.zip';download.value.save_as(path)
                with zipfile.ZipFile(path) as archive:
                    tasks=[json.loads(archive.read(n)) for n in sorted(archive.namelist()) if n.endswith('.input.json')]
                assert len(tasks)>=3
                outputs=[reply(t) for t in tasks]
                def choose(values):
                    page.locator('#external-files').set_input_files([{'name':f'{r["batch_id"]}.result.json','mimeType':'application/json',
                        'buffer':json.dumps(r,ensure_ascii=False).encode()} for r in values])
                page.locator('#external-reviewer').fill('合成驗收人')
                page.locator('#external-model').fill('ChatGPT（合成回覆，非真模型）')
                choose(outputs[:1]);page.locator('#external-preview').click()
                expect(page.locator('#external-save')).to_be_enabled()
                page.locator('#external-model').fill('合成模型標籤修正')
                expect(page.locator('#external-save')).to_be_disabled()
                page.locator('#external-preview').click()
                expect(page.locator('#external-save')).to_be_enabled()
                page.locator('#external-save').click()
                expect(page.locator('#external-progress')).to_contain_text(f'已保存 1/{len(tasks)} 批')
                expect(page.locator('#external-save')).to_be_disabled()
                expect(page.locator('#external-activate')).to_be_disabled()
                page.reload();page.locator('#library-list [data-document]').click();page.locator('#document-external').click()
                expect(page.locator('#external-progress')).to_contain_text(f'已保存 1/{len(tasks)} 批')
                page.locator('#external-model').fill('ChatGPT（合成回覆，非真模型）')
                bad=json.loads(json.dumps(outputs[1]));bad['blocks'][0]['items'][0]['quote']='原文不存在的引文'
                choose([bad]);page.locator('#external-preview').click()
                expect(page.locator('#external-dialog .dialog-notice')).to_contain_text('來源引文必須逐字存在')
                expect(page.locator('#external-save')).to_be_disabled()
                choose(outputs[1:]);page.locator('#external-preview').click()
                expect(page.locator('#external-save')).to_be_enabled()
                if os.environ.get('SPEC_CHECK_SCREENSHOTS','1')!='0':
                    page.screenshot(path=str(ROOT/'docs/screenshots/external-extraction.png'),full_page=True)
                page.locator('#external-save').click()
                expect(page.locator('#external-progress')).to_contain_text(f'已保存 {len(tasks)}/{len(tasks)} 批')
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.querySelector("#external-dialog").scrollWidth <= document.querySelector("#external-dialog").clientWidth + 1')
                page.locator('#external-ack').check();page.locator('#external-activate').click()
                expect(page.locator('#external-dialog')).not_to_be_visible()
                expect(page.locator('#document-status')).to_contain_text('尚待人工確認')
                expect(page.locator('#document-confirm')).to_be_enabled()
                page.locator('#document-reviewer').fill('正式覆核人')
                page.locator('#document-ack').check();page.locator('#document-confirm').click()
                expect(page.locator('#document-status')).to_contain_text('已人工確認')
                page.locator('#document-dialog summary').filter(has_text='抽取與人工修改歷程').click()
                page.locator('#document-audit-load').click()
                expect(page.locator('#document-audit')).to_contain_text('套用外部萃取項目')
                assert not errors, errors
                print(json.dumps({'passed':True,'batch_count':len(tasks),'checks':['download','partial_stage','reload',
                    'preview_invalidation','bad_quote_rejected','all_batches_apply','manual_confirm','audit','390px'],
                    'page_errors':errors},ensure_ascii=False))
                browser.close()
        except Exception:
            log.flush();print(Path(tmp,'app.log').read_text(encoding='utf-8')[-5000:]);raise
        finally:
            process.terminate()
            try:process.wait(10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            log.close()


if __name__=='__main__':main()
