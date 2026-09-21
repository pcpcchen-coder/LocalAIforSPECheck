"""Single-user, loopback-only document comparison workspace."""
from __future__ import annotations
import copy
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import __version__
from .storage import Store, ConflictError, now, uid
from .engine import DEFAULT_SETTINGS, validate_settings, check_connection, compare_block, rank_results, ComparisonCancelled, PROMPT_VERSION, PROMPT_SHA256
from .ingestion import extract_document
from .exports import export_run

STATUSES = {'match','partial','mismatch','missing','uncertain'}
MAX_UPLOAD = 30 * 1024 * 1024


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class RunInput(BaseModel):
    standard_ids: list[str] = Field(default_factory=list)
    mode: Literal['local','demo'] = 'local'


class ReviewInput(BaseModel):
    decision: Literal['confirmed','changed','reopened']
    final_status: Literal['match','partial','mismatch','missing','uncertain'] | None = None
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(default='', max_length=10000)
    expected_version: int = Field(ge=0)


def public_settings(settings):
    return {k:v for k,v in settings.items() if k not in {'api_key','clear_api_key'}}


def public_doc(doc):
    return {k:v for k,v in doc.items() if not k.startswith('_')}


def create_app(data_dir=None):
    root = Path(data_dir or os.environ.get('SPEC_CHECK_DATA', 'data')).resolve()
    store = Store(root)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='spec-check')
    cancel_events = {}
    lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(app):
        for run in store.list('run'):
            if run['status'] in {'running','queued'}:
                run.update(status='interrupted', error='程式上次中斷。可繼續尚未完成的條目。')
                store.put('run',run,run['project_id'])
        yield
        for event in cancel_events.values():
            event.set()
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title='LocalAIforSPECheck', version=__version__, lifespan=lifespan)
    app.state.store = store

    @app.middleware('http')
    async def local_access(request: Request, call_next):
        host = request.url.hostname
        if host not in {'localhost','127.0.0.1','::1'}:
            return JSONResponse({'detail':'本工具僅接受 localhost 存取。'}, status_code=403)
        if request.method not in {'GET','HEAD','OPTIONS'}:
            origin = request.headers.get('origin')
            if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail':'拒絕跨來源寫入。請從本機工具頁面操作。'},status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail':'拒絕跨網站寫入。'},status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({'detail':'找不到指定資料。'}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail':str(exc)}, status_code=400)

    @app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return JSONResponse({'detail':str(exc)}, status_code=409)

    def get_settings():
        try:
            return store.get('settings','local')['values']
        except KeyError:
            return copy.deepcopy(DEFAULT_SETTINGS)

    def project_view(project_id):
        project = store.get('project',project_id)
        project['documents'] = [public_doc(d) for d in store.list('document',project_id) if not d.get('_deleted')]
        return project

    def full_run(run_id):
        run = store.run_snapshot(run_id)
        results = run['results']
        run['completed'] = len(results)
        run['rankings'] = rank_results(results,run['documents'])
        run['exports'] = [public_doc(e) for e in store.list('export',run_id)]
        return run

    def worker(run_id, settings, event):
        run = store.get('run',run_id)
        run.update(status='running', error=None, finished_at=None)
        store.put('run',run,run['project_id'])
        try:
            product = next(d for d in run['documents'] if d['role'] == 'product')
            done = {(r['standard_id'],r['block_id']) for r in store.list('result',run_id)}
            for doc in run['documents']:
                if doc['role'] != 'standard':
                    continue
                for block_index, block in enumerate(doc['blocks']):
                    if event.is_set():
                        raise ComparisonCancelled('使用者取消')
                    if (doc['id'],block['id']) in done:
                        continue
                    try:
                        contextual_block = dict(block, context=[b for i,b in enumerate(doc['blocks'][max(0,block_index-1):block_index+2],max(0,block_index-1)) if i != block_index])
                        payload = compare_block(contextual_block,product['blocks'],settings,mode=run['mode'],cancel_check=event.is_set)
                    except ComparisonCancelled:
                        raise
                    except Exception as exc:
                        # Do not store exception text: remote diagnostics could echo a credential.
                        payload = dict(requirement=block['text'],status='uncertain',explanation='此條目執行失敗，請檢查模型服務後另建比對或人工覆核。',differences=[],evidence=[],confidence=0,
                                       product_coverage={'scanned':0,'total':len(product['blocks'])},warnings=[type(exc).__name__])
                    result = dict(payload, id=uid(),run_id=run_id,standard_id=doc['id'],standard_name=doc['name'],block_id=block['id'],location=block['location'],requirement=block['text'],
                                  review={'decision':'pending','final_status':None,'reviewer':'','note':'','updated_at':None,'version':0})
                    store.put('result',result,run_id)
                    done.add((doc['id'],block['id']))
                    run['completed'] = len(done)
                    store.put('run',run,run['project_id'])
            run.update(status='completed',finished_at=now())
        except ComparisonCancelled:
            run.update(status='cancelled',finished_at=now(),error='已停止。可以從未完成條目繼續。')
        except Exception as exc:
            run.update(status='failed',finished_at=now(),error='執行中斷：'+type(exc).__name__+'。可嘗試繼續或另建比對。')
        finally:
            store.put('run',run,run['project_id'])

    def queue(run,settings):
        event = threading.Event()
        cancel_events[run['id']] = event
        executor.submit(worker,run['id'],copy.deepcopy(settings),event)

    @app.get('/api/health')
    def health():
        response = {'status':'ok','version':__version__,'app':'LocalAIforSPECheck'}
        if os.environ.get('SPEC_CHECK_INSTANCE'):
            response['instance_id'] = os.environ['SPEC_CHECK_INSTANCE']
        return response

    @app.get('/api/settings')
    def settings_read():
        settings = get_settings()
        return dict(public_settings(settings),has_api_key=bool(settings.get('api_key')))

    @app.put('/api/settings')
    def settings_save(values: dict):
        old = get_settings()
        values = {k:v for k,v in values.items() if k != 'has_api_key'}
        if values.pop('clear_api_key',False):
            values['api_key'] = ''
        elif not values.get('api_key'):
            values['api_key'] = old.get('api_key','')
        validated = validate_settings(dict(old,**values))
        store.put('settings',{'id':'local','values':validated})
        return dict(public_settings(validated),has_api_key=bool(validated.get('api_key')))

    @app.post('/api/connection')
    def connection(values: dict | None = None):
        settings = get_settings()
        override = (values or {}).get('settings',{})
        override = {k:v for k,v in override.items() if k not in {'has_api_key','clear_api_key'}}
        if not override.get('api_key'):
            override['api_key'] = settings.get('api_key','')
        try:
            models = check_connection(validate_settings(dict(settings,**override)))
        except ValueError:
            raise
        except Exception:
            raise HTTPException(502,'無法連線，請確認 LM Studio 已啟動本機 Server、位址及 Token。')
        return {'models':models,'message':'已讀取模型清單；仍需用合成範例執行正式模式，確認 JSON 輸出與內容品質。'}

    @app.get('/api/projects')
    def projects():
        return store.list('project')

    @app.post('/api/projects')
    def new_project(values: ProjectInput):
        name = values.name.strip()
        if not name:
            raise ValueError('請填寫專案名稱。')
        project = dict(id=uid(),name=name,created_at=now(),is_demo=False)
        store.put('project',project)
        return project_view(project['id'])

    @app.get('/api/projects/{project_id}')
    def read_project(project_id: str):
        return project_view(project_id)

    @app.post('/api/projects/{project_id}/documents')
    async def upload(project_id: str, file: UploadFile = File(...), role: str = Form(...)):
        project = store.get('project',project_id)
        if project.get('is_demo'):
            raise ValueError('示範專案保留合成文件。請建立新專案上傳自己的文件。')
        if role not in {'product','standard'}:
            raise ValueError('文件類型必須為 product 或 standard。')
        name = (file.filename or 'document.txt').replace('\\','/').split('/')[-1][:240]
        suffix = Path(name).suffix.lower()
        if suffix not in {'.pdf','.docx','.xlsx','.csv','.txt','.md'}:
            raise ValueError('支援 PDF、DOCX、XLSX、CSV、TXT、MD。舊版 DOC/XLS 請先另存新格式。')
        identifier = uid()
        path = root / 'uploads' / (identifier+suffix)
        count = 0
        try:
            with path.open('wb') as out:
                while chunk := await file.read(1024*1024):
                    count += len(chunk)
                    if count > MAX_UPLOAD:
                        raise ValueError('檔案超過 30 MB，請先拆分。')
                    out.write(chunk)
            try:
                extracted = await run_in_threadpool(extract_document,path,name,role)
            except ValueError:
                raise
            except Exception:
                raise ValueError('無法讀取文件。請確認檔案未加密、未損毀且格式正確。')
            doc = dict(extracted,id=identifier,created_at=now(),extraction_confirmed=False,_stored_name=path.name)
            with lock:
                if role == 'product' and any(d['role']=='product' for d in project_view(project_id)['documents']):
                    raise ValueError('每個專案只保留一份產品文件。請先移除舊文件或建立新專案。')
                store.put('document',doc,project_id)
            return public_doc(doc)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    def project_document(project_id,doc_id):
        store.get('project',project_id)
        for doc in store.list('document',project_id):
            if doc['id'] == doc_id:
                return doc
        raise KeyError(doc_id)

    @app.post('/api/projects/{project_id}/documents/{doc_id}/confirm')
    def confirm_document(project_id: str, doc_id: str):
        doc = project_document(project_id,doc_id)
        if not doc['blocks']:
            raise ValueError('此文件沒有可比對文字。請先 OCR，再上傳可搜尋文字的 PDF 或 TXT。')
        doc.update(extraction_confirmed=True,confirmed_at=now())
        store.put('document',doc,project_id)
        return public_doc(doc)

    @app.delete('/api/projects/{project_id}/documents/{doc_id}')
    def remove_document(project_id: str, doc_id: str):
        with lock:
            doc = project_document(project_id,doc_id)
            doc['_deleted'] = True
            store.put('document',doc,project_id)
        return {'removed':True,'message':'已移出專案；既有比對紀錄及原檔仍保留。'}

    @app.get('/api/projects/{project_id}/documents/{doc_id}/original')
    def original(project_id: str,doc_id: str):
        doc = project_document(project_id,doc_id)
        return FileResponse(root/'uploads'/doc['_stored_name'],filename=doc['name'],media_type='application/octet-stream')

    @app.post('/api/demo')
    def demo():
        project = dict(id=uid(),name='教學示範：儲能通訊模組',created_at=now(),is_demo=True)
        store.put('project',project)
        demo_docs = [
            ('product.txt','product','額定電壓：48 V\n通訊介面：CAN\n防護等級：IP54\n工作溫度：-20 至 60 °C\n'),
            ('規範_A.txt','standard','額定電壓：48 V\n通訊介面：CAN\n防護等級：IP65\n工作溫度：-20 至 60 °C\n認證：IEC 示範認證\n設計應考慮附錄例外條件\n'),
            ('規範_B.txt','standard','額定電壓：24 V\n通訊介面：RS485\n防護等級：IP54\n額定電壓：48 V；備援介面：Ethernet\n')]
        for name,role,content in demo_docs:
            identifier = uid()
            path = root/'uploads'/(identifier+'.txt')
            path.write_text(content,encoding='utf-8')
            doc = dict(extract_document(path,name,role),id=identifier,created_at=now(),extraction_confirmed=True,_stored_name=path.name)
            store.put('document',doc,project['id'])
        return project_view(project['id'])

    @app.post('/api/projects/{project_id}/runs')
    def start_run(project_id: str, values: RunInput):
        with lock:
            project = project_view(project_id)
            if values.mode == 'demo' and not project.get('is_demo'):
                raise ValueError('示範模式只可用於內建合成專案。正式文件請使用本地模型模式。')
            products = [d for d in project['documents'] if d['role']=='product']
            standards = [d for d in project['documents'] if d['role']=='standard' and (not values.standard_ids or d['id'] in values.standard_ids)]
            if values.standard_ids and set(values.standard_ids) != {d['id'] for d in standards}:
                raise ValueError('選取的規範不存在於此專案。')
            if len(products)!=1 or not standards:
                raise ValueError('請上傳一份產品文件及至少一份規範。')
            documents = products+standards
            if any(not d['blocks'] or not d['extraction_confirmed'] for d in documents):
                raise ValueError('請先預覽並確認每份文件的文字擷取結果。')
            settings = validate_settings(get_settings())
            if values.mode == 'local' and not settings.get('model'):
                raise ValueError('請先在模型設定讀取並選擇本地模型。')
            run = dict(id=uid(),project_id=project_id,project_name=project['name'],status='queued',created_at=now(),finished_at=None,mode=values.mode,settings=public_settings(settings),document_ids=[d['id'] for d in documents],documents=documents,total=sum(len(d['blocks']) for d in standards),completed=0,error=None,
                       app_version=__version__,prompt_version=PROMPT_VERSION,prompt_sha256=PROMPT_SHA256,scoring='(符合 + 0.5 × 部分符合) / 全部規範文字區塊 × 100',limitations=['文字區塊掃描完整不代表所有原子要求已辨識。','圖像、掃描頁、跨頁表格與上下文應人工確認。','文件符合度參考分數不是法規認證或安全合格證明。'])
            if (values.mode == 'local' and os.environ.get('SPEC_CHECK_PORTABLE') == '1'
                    and settings['base_url'] == os.environ.get('SPEC_CHECK_PORTABLE_BASE_URL')
                    and settings['model'] == os.environ.get('SPEC_CHECK_PORTABLE_MODEL')):
                run['portable_model_sha256'] = os.environ.get('SPEC_CHECK_MODEL_SHA256', '')
            store.put('run',run,project_id)
            queue(run,settings)
            return full_run(run['id'])

    @app.get('/api/projects/{project_id}/runs')
    def project_runs(project_id: str):
        store.get('project',project_id)
        return [{k:v for k,v in r.items() if k not in {'documents','results'}} for r in store.list('run',project_id)]

    @app.get('/api/runs/{run_id}')
    def read_run(run_id: str):
        return full_run(run_id)

    @app.post('/api/runs/{run_id}/cancel')
    def cancel(run_id: str):
        store.get('run',run_id)
        event = cancel_events.get(run_id)
        if event:
            event.set()
        return full_run(run_id)

    @app.post('/api/runs/{run_id}/resume')
    def resume(run_id: str):
        with lock:
            run = store.get('run',run_id)
            if run['status'] not in {'cancelled','interrupted','failed'}:
                raise ValueError('只有已停止或中斷的比對可以繼續。')
            # Preserve original model/config, using only the current locally stored token.
            current = get_settings()
            resume_settings = dict(run['settings'],api_key=current.get('api_key',''))
            if run['mode'] == 'local' and run.get('portable_model_sha256'):
                if (os.environ.get('SPEC_CHECK_PORTABLE') != '1'
                        or run['portable_model_sha256'] != os.environ.get('SPEC_CHECK_MODEL_SHA256')
                        or run['settings']['model'] != current.get('model')
                        or current.get('base_url') != os.environ.get('SPEC_CHECK_PORTABLE_BASE_URL')
                        or current.get('model') != os.environ.get('SPEC_CHECK_PORTABLE_MODEL')):
                    raise ValueError('請使用原本的可攜版 GGUF 模型重新啟動後再繼續；更換模型請建立新比對。')
                # A portable restart may choose a new free port. Preserve the original
                # snapshot while recording the actual endpoint used for this resume.
                resume_settings['base_url'] = current['base_url']
                run.setdefault('resume_events', []).append(dict(created_at=now(),base_url=current['base_url'],model=current['model'],model_sha256=run['portable_model_sha256']))
            settings = validate_settings(resume_settings)
            run.update(status='queued',finished_at=None,error=None)
            store.put('run',run,run['project_id'])
            queue(run,settings)
            return full_run(run_id)

    @app.post('/api/results/{result_id}/reviews')
    def review(result_id: str, values: ReviewInput):
        if not values.reviewer.strip():
            raise ValueError('請填寫確認人姓名。')
        if values.decision == 'changed' and (not values.final_status or not values.note.strip()):
            raise ValueError('改判須選擇判定並填寫原因。')
        if values.decision == 'reopened' and not values.note.strip():
            raise ValueError('重新開啟須填寫原因，保留覆核脈絡。')
        return store.review(result_id,values.model_dump())

    @app.get('/api/runs/{run_id}/export')
    def export(run_id: str, format: str = 'html'):
        if format not in {'html','xlsx','json'}:
            raise ValueError('匯出格式須為 html、xlsx 或 json。')
        snapshot = full_run(run_id)
        export_id = uid()
        exported_at = now()
        snapshot['export_id'] = export_id
        snapshot['exported_at'] = exported_at
        content,mime,name = export_run(snapshot,format)
        reports = root/'reports'
        reports.mkdir(exist_ok=True)
        stored_name = export_id+'.'+format
        (reports/stored_name).write_bytes(content)
        store.put('export',dict(id=export_id,run_id=run_id,format=format,created_at=exported_at,
                  sha256=hashlib.sha256(content).hexdigest(),filename=name,_stored_name=stored_name,_mime=mime),run_id)
        from urllib.parse import quote
        return Response(content,media_type=mime,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name)})

    @app.get('/api/runs/{run_id}/exports')
    def export_history(run_id: str):
        store.get('run',run_id)
        return [public_doc(e) for e in store.list('export',run_id)]

    @app.get('/api/exports/{export_id}/download')
    def archived_export(export_id: str):
        item = store.get('export',export_id)
        path = root/'reports'/item['_stored_name']
        if not path.exists():
            raise HTTPException(404,'封存報告檔案不存在，請從完整資料備份還原。')
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise HTTPException(409,'封存報告雜湊不符，可能已被外部修改。請檢查備份。')
        return FileResponse(path,filename=item['filename'],media_type=item['_mime'])

    app.mount('/static',StaticFiles(directory=Path(__file__).parent/'static'),name='assets')
    app.mount('/',StaticFiles(directory=Path(__file__).parent/'static',html=True),name='static')
    return app


app = create_app()
