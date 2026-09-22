"""Durable local standard library and auditable, staged product assessments.

The legacy workspace remains independent. Documents and item sets are copied into
an assessment once; progress and paginated findings never carry source snapshots.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from . import __version__
from . import analysis_engine as ae
from .engine import ComparisonCancelled, validate_settings
from .exports import export_run
from .ingestion import MAX_FILE_BYTES, SUPPORTED_EXTENSIONS, extract_document
from .storage import ConflictError, now, uid

ACTIVE = {'queued', 'running'}
STATUSES = {'match', 'partial', 'mismatch', 'missing', 'uncertain'}
LEVELS = {'high', 'medium', 'low', 'unknown', 'none'}
ITEM_KINDS = {'requirement', 'specification', 'context', 'unresolved'}
ITEM_FIELDS = {'name', 'parameter', 'value', 'unit', 'operator', 'conditions',
               'exceptions', 'test_method', 'criticality', 'criticality_basis',
               'quote', 'block_id', 'kind'}


def public(obj):
    return {k: v for k, v in obj.items() if not k.startswith('_') and k != 'api_key'}


def paging(offset=0, limit=50):
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError('分頁參數無效：每頁 1–200 筆。')
    return offset, limit


def reviewer(values, reason=False):
    name = str(values.get('reviewer', '')).strip()
    note = str(values.get('note', '')).strip()
    if not name or len(name) > 100:
        raise ValueError('請填寫確認人姓名（最多 100 字）。')
    if len(note) > 10000 or (reason and not note):
        raise ValueError('請填寫操作原因（最多 10,000 字）。')
    return name, note


def check_version(obj, values, field='version'):
    if values.get('expected_version') != obj.get(field, 0):
        raise ConflictError('資料已更新，請重新讀取後確認，避免覆蓋別人的紀錄。')


def effective(row):
    review = row.get('review', {})
    if review.get('decision') in {'confirmed', 'changed'}:
        return review.get('final_status') or row['status']
    return row['status']


class WorkflowService:
    def __init__(self, store, root, get_settings, executor, lock):
        self.store, self.root, self.get_settings = store, Path(root), get_settings
        self.executor, self.lock = executor, lock
        self.events = {}

    def _list(self, kind, parent=None, where='', args=(), offset=0, limit=50, sort='created'):
        offset, limit = paging(offset, limit)
        clause, params = 'kind=?', [kind]
        if parent is not None:
            clause += ' AND parent=?'
            params.append(parent)
        if where:
            clause += ' AND (' + where + ')'
            params.extend(args)
        order = {
            'created': 'rowid',
            'priority': "CASE json_extract(data,'$.risk.level') WHEN 'high' THEN 0 WHEN 'unknown' THEN 1 "
                        "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,rowid",
            'relevance': "CASE json_extract(data,'$.relevance') WHEN 'high' THEN 0 WHEN 'medium' THEN 1 "
                         "WHEN 'unknown' THEN 2 ELSE 3 END,rowid",
        }[sort]
        with self.store.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM records WHERE ' + clause, params).fetchone()[0]
            rows = db.execute('SELECT data FROM records WHERE ' + clause + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?',
                              [*params, limit, offset]).fetchall()
        return dict(items=[json.loads(r['data']) for r in rows], total=total, offset=offset, limit=limit)

    def audit(self, parent, action, values=None, **detail):
        values = values or {}
        event = dict(id=uid(), parent_id=parent, action=action, reviewer=values.get('reviewer', ''),
                     note=values.get('note', ''), created_at=now(), **detail)
        return ('v2_audit', event, parent)

    def doc(self, identifier):
        return self.store.get('v2_document', identifier)

    def summary_doc(self, doc):
        return {k: v for k, v in public(doc).items() if k not in {'blocks'}}

    def _document_refs(self, role):
        with self.store.connect() as db:
            rows = db.execute("SELECT json_remove(data,'$.blocks','$._stored_name') AS data "
                              'FROM records WHERE kind=? AND parent=? ORDER BY rowid', ('v2_document', role)).fetchall()
        return [json.loads(r['data']) for r in rows]

    @staticmethod
    def _write(db, records):
        db.executemany('INSERT INTO records(kind,id,parent,data) VALUES(?,?,?,?) '
                       'ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data,parent=excluded.parent',
                       [(kind, obj['id'], parent, json.dumps(obj, ensure_ascii=False)) for kind, obj, parent in records])

    def documents(self, role, q='', offset=0, limit=50):
        where, args = "json_extract(data,'$.role')=?", [role]
        if q:
            where += " AND instr(lower(json_extract(data,'$.name')),lower(?))>0"
            args.append(q)
        # SQLite removes the potentially large source text before returning rows.
        offset, limit = paging(offset, limit)
        with self.store.connect() as db:
            params = ['v2_document', *args]
            total = db.execute('SELECT COUNT(*) FROM records WHERE kind=? AND ' + where, params).fetchone()[0]
            rows = db.execute("SELECT json_remove(data,'$.blocks','$._stored_name') AS data FROM records WHERE kind=? AND "
                              + where + ' ORDER BY rowid DESC LIMIT ? OFFSET ?', [*params, limit, offset]).fetchall()
        return dict(items=[json.loads(r['data']) for r in rows], total=total, offset=offset, limit=limit)

    async def upload(self, file, role):
        name = (file.filename or 'document.txt').replace('\\', '/').split('/')[-1][:240]
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            await file.close()

            raise ValueError('支援 PDF、DOCX、XLSX、CSV、TXT、MD。')
        identifier = uid()
        path = self.root / 'uploads' / (identifier + suffix)
        try:
            size = 0
            with path.open('wb') as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise ValueError('檔案超過 30 MB，請拆分後上傳。')
                    stream.write(chunk)
            parsed = await run_in_threadpool(extract_document, path, name, role)
            document = dict(parsed, id=identifier, created_at=now(), version=1,
                            block_count=len(parsed['blocks']),
                            metadata=dict(category='', scope='', version='', region=''),
                            index_status='not_started', confirmed=False, item_count=0, unresolved_count=0,
                            _stored_name=path.name)
            with self.lock:
                # The same immutable bytes/role reuse the existing index and review.
                duplicate = self._list('v2_document', where="json_extract(data,'$.sha256')=? AND json_extract(data,'$.role')=?",
                                       args=(document['sha256'], role), limit=1)['items']
                if duplicate:
                    path.unlink(missing_ok=True)
                    return dict(self.summary_doc(duplicate[0]), reused=True)
                self.store.put_many([('v2_document', document, role), self.audit(identifier, 'uploaded', sha256=document['sha256'])])
            return self.summary_doc(document)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    def import_project(self, identifier):
        with self.lock:
            project = self.store.get('project', identifier)
            if project.get('is_demo'):
                raise ValueError('教學示範文件不匯入正式文件庫。')
            imported, reused = [], 0
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                refs = db.execute("SELECT id FROM records WHERE kind='document' AND parent=? "
                                  "AND coalesce(json_extract(data,'$._deleted'),0)=0 ORDER BY rowid", (identifier,)).fetchall()
                for reference in refs:
                    legacy = self.store.get('document', reference['id'])
                    existing = db.execute("SELECT data FROM records WHERE kind='v2_document' AND "
                                          "json_extract(data,'$.sha256')=? AND json_extract(data,'$.role')=? LIMIT 1",
                                          (legacy['sha256'], legacy['role'])).fetchone()
                    if existing:
                        imported.append(self.summary_doc(json.loads(existing['data'])))
                        reused += 1
                        continue
                    if not legacy.get('_stored_name') or not (self.root / 'uploads' / legacy['_stored_name']).is_file():
                        raise ValueError('舊版專案缺少原始檔案，請先還原資料備份。')
                    doc = {k: legacy[k] for k in ('name', 'role', 'sha256', 'blocks', 'warnings', '_stored_name')}
                    doc.update(id=uid(), created_at=now(), version=1, metadata=dict(category='', scope='', version='', region=''),
                               block_count=len(doc['blocks']), index_status='not_started', confirmed=False, item_count=0,
                               unresolved_count=0, legacy_project_id=identifier, legacy_document_id=legacy['id'])
                    self._write(db, [('v2_document', doc, doc['role']), self.audit(doc['id'], 'legacy_imported',
                                     project_id=identifier, document_id=legacy['id'], sha256=doc['sha256'])])
                    imported.append(self.summary_doc(doc))
            return dict(items=imported, total=len(imported), reused=reused)

    def items(self, document):
        index_id = document.get('index_id')
        return self.store.list('v2_item', index_id) if index_id else []

    def update_metadata(self, identifier, values):
        name, note = reviewer(values)
        metadata = values.get('metadata', {})
        if not isinstance(metadata, dict) or set(metadata) - {'category', 'scope', 'version', 'region'}:
            raise ValueError('標準資料欄位無效。')
        if any(not isinstance(v, str) or len(v) > 5000 for v in metadata.values()):
            raise ValueError('資料欄位必須是文字，每欄最多 5,000 字。')
        with self.lock:
            doc = self.doc(identifier)
            check_version(doc, values)
            self._editable(doc)
            before = copy.deepcopy(doc['metadata'])
            doc['metadata'].update(metadata)
            doc.update(version=doc['version'] + 1, confirmed=False)
            self.store.put_many([('v2_document', doc, doc['role']), self.audit(identifier, 'metadata_changed',
                                 dict(reviewer=name, note=note), before=before, after=doc['metadata'], version=doc['version'])])
            return self.summary_doc(doc)

    def _editable(self, doc):
        if doc.get('index_status') in {'queued', 'running'}:
            raise ValueError('項目仍在抽取中，請完成或停止後再修改。')

    def _validate_item(self, item, doc):
        if not isinstance(item, dict) or set(item) - ITEM_FIELDS - {'id', 'index_id', 'document_id', 'location'}:
            raise ValueError('規格項目欄位無效。')
        output = {}
        for key in ITEM_FIELDS:
            value = item.get(key, '')
            if not isinstance(value, str) or len(value) > 20000:
                raise ValueError('項目各欄必須是文字，最多 20,000 字。')
            output[key] = value
        if output['kind'] not in ITEM_KINDS or output['criticality'] not in LEVELS - {'none'}:
            raise ValueError('請選擇有效項目類型與重要性。')
        block = next((b for b in doc['blocks'] if b['id'] == output['block_id']), None)
        if block is None or not output['quote'].strip() or output['quote'] not in block['text']:
            raise ValueError('來源引文必須逐字存在於指定的文件區塊，不可新增不存在的規格。')
        output.update(document_id=doc['id'], location=block['location'])
        return output

    @staticmethod
    def uncovered(text, quotes):
        covered = [False] * len(text)
        for quote_text in quotes:
            start = 0
            while quote_text and (start := text.find(quote_text, start)) >= 0:
                covered[start:start+len(quote_text)] = [True] * len(quote_text)
                start += len(quote_text)
        spans, start = [], None
        for index in range(len(text) + 1):
            if index < len(text) and not covered[index]:
                if start is None:
                    start = index
            elif start is not None:
                fragment = text[start:index]
                if fragment.strip():
                    spans.append(fragment)
                start = None
        return spans

    def edit_item(self, identifier, item_id, values, split=False):
        name, note = reviewer(values, reason=True)
        with self.lock:
            doc = self.doc(identifier)
            self._editable(doc)
            check_version(doc, values)
            item = self.store.get('v2_item', item_id)
            if item.get('index_id') != doc.get('index_id') or item['document_id'] != identifier:
                raise KeyError(item_id)
            candidates = values.get('items') if split else [values.get('item')]
            if not isinstance(candidates, list) or not 1 <= len(candidates) <= 100:
                raise ValueError('拆分必須提供 1–100 個項目。')
            new_items = [self._validate_item(candidate, doc) for candidate in candidates]
            if any(i['block_id'] != item['block_id'] for i in new_items):
                raise ValueError('項目來源區塊不能移動；請在對應原文的項目中修改，保留來源對應。')
            if split and any(i['quote'] not in item['quote'] for i in new_items):
                raise ValueError('拆分後的引文必須來自原項目引文。')
            # Any replaced source span not represented by another row remains explicit.
            siblings = [i for i in self.items(doc) if i['id'] != item_id and i['block_id'] == item['block_id']]
            fragments = self.uncovered(item['quote'], [i['quote'] for i in siblings + new_items])
            for fragment in fragments:
                new_items.append(dict(item, id=uid(), name='未歸類原文', parameter='', value='', unit='', operator='',
                                      conditions='', exceptions='', test_method='', quote=fragment, kind='unresolved',
                                      criticality='unknown', criticality_basis='編輯或拆分後尚未歸類的來源文字。'))
            for index, value in enumerate(new_items):
                value.update(id=item_id if index == 0 else uid(), index_id=doc['index_id'])
            all_items = [i for i in self.items(doc) if i['id'] != item_id] + new_items
            doc.update(version=doc['version'] + 1, confirmed=False, item_count=len(all_items),
                       unresolved_count=sum(i['kind'] == 'unresolved' for i in all_items))
            self.store.put_many([*[('v2_item', i, doc['index_id']) for i in new_items], ('v2_document', doc, doc['role']),
                                 self.audit(identifier, 'item_split' if split else 'item_changed', dict(reviewer=name, note=note),
                                            before=item, after=new_items, version=doc['version'])])
            return dict(document=self.summary_doc(doc), items=new_items)

    def confirm(self, values, identifiers):
        name, note = reviewer(values)
        if values.get('acknowledge_warnings') is not True:
            raise ValueError('請先確認原文、抽取項目及未解決提示。')
        with self.lock:
            docs = []
            for reference in identifiers:
                doc = self.doc(reference['id'])
                check_version(doc, reference)
                if doc['index_status'] != 'ready' or not doc['item_count']:
                    raise ValueError('請先完成文件的規格項目抽取。')
                doc.update(confirmed=True, confirmed_at=now(), confirmed_by=name, version=doc['version'] + 1)
                docs.append(doc)
            self.store.put_many([entry for doc in docs for entry in [
                ('v2_document', doc, doc['role']), self.audit(doc['id'], 'items_confirmed', dict(reviewer=name, note=note),
                                                           version=doc['version'], unresolved_count=doc['unresolved_count'])]])
            return [self.summary_doc(d) for d in docs]

    def _settings(self):
        settings = validate_settings(self.get_settings())
        if not settings.get('model'):
            raise ValueError('請先設定本地模型並測試連線。')
        return settings

    def _new_job(self, phase, total, settings, **extra):
        stamp = now()
        job = dict(id=uid(), status='queued', phase=phase, completed=0, total=total, created_at=stamp, error=None,
                   settings=public(settings), engine_version=ae.ENGINE_VERSION, prompt_sha256=ae.PROMPT_SHA256,
                   progress=dict(stage='queued', started_at=None, updated_at=stamp, request_started_at=None,
                                 last_response_at=None, finished_at=None, requests_completed=0,
                                 window_index=0, window_total=0, timeout_seconds=settings['timeout'], events=[]), **extra)
        if (os.environ.get('SPEC_CHECK_PORTABLE') == '1' and settings['base_url'] == os.environ.get('SPEC_CHECK_PORTABLE_BASE_URL')
                and settings['model'] == os.environ.get('SPEC_CHECK_PORTABLE_MODEL')):
            job['model_sha256'] = os.environ.get('SPEC_CHECK_MODEL_SHA256')
        return job

    def _queue(self, job, settings):
        event = threading.Event()
        self.events[job['id']] = event
        self.store.put('v2_job', job, job.get('analysis_id', 'extraction'))
        self.executor.submit(self._work, job['id'], settings, event)

    def extract(self, identifiers, force=False):
        if not isinstance(identifiers, list) or not identifiers:
            raise ValueError('請選擇至少一份文件。')
        settings = self._settings()
        with self.lock:
            docs = [self.summary_doc(self.doc(i)) for i in dict.fromkeys(identifiers)]
            if any(not d.get('block_count', len(self.doc(d['id'])['blocks'])) for d in docs):
                raise ValueError('文件沒有可用文字，請先提供文字版或完成 OCR。')
            active = [d for d in docs if d['index_status'] in ACTIVE]
            if active:
                if len(docs) == 1:
                    return self.job_view(active[0]['job_id'])
                raise ValueError('部分文件仍在抽取中，請完成或停止後再開始。')
            targets = [d for d in docs if force or d['index_status'] != 'ready']
            job = self._new_job('extraction', sum(d.get('block_count', 0) or len(self.doc(d['id'])['blocks']) for d in targets), settings,
                                targets=[dict(document_id=d['id'], index_id=uid()) for d in targets])
            with self.store.connect() as db:
                for reference in targets:
                    doc = self.doc(reference['id'])
                    doc.update(index_status='queued', job_id=job['id'], confirmed=False, version=doc['version'] + 1)
                    self._write(db, [('v2_document', doc, doc['role'])])
            self._queue(job, settings)
            return self.job_view(job['id'])

    def job_view(self, identifier):
        job = self.store.get('v2_job', identifier)
        return {k: v for k, v in public(job).items() if k not in {'targets', 'settings'}} | {'server_time': now()}

    def _progress(self, identifier, stage=None, message=None, **fields):
        with self.lock:
            job = self.store.get('v2_job', identifier)
            p = job['progress']
            p.update(fields, updated_at=now())
            if stage:
                p['stage'] = stage
            if message:
                p['events'] = (p['events'] + [dict(at=now(), stage=p['stage'], message=message)])[-30:]
            self.store.put('v2_job', job, job.get('analysis_id', 'extraction'))

    def _callback(self, identifier):
        def on_progress(update):
            stage = update.get('stage', 'preparing')
            fields = {k: v for k, v in update.items() if k in {'window_index', 'window_total'}}
            with self.lock:
                job = self.store.get('v2_job', identifier)
                if stage == 'waiting_model':
                    fields['request_started_at'] = now()
                    message = '已送出本地模型請求，等待回覆。'
                elif stage == 'checking_response':
                    fields.update(last_response_at=now(), request_started_at=None)
                    message = '已收到模型回覆，正在驗證原文與格式。'
                elif stage in {'window_completed', 'window_failed'}:
                    fields.update(request_started_at=None, requests_completed=job['progress']['requests_completed'] + 1)
                    message = '請求已完成驗證。' if stage == 'window_completed' else '請求或格式驗證失敗，保留待確認資訊。'
                else:
                    message = None
                self._progress(identifier, stage, message, **fields)
        return on_progress

    def _check(self, event):
        if event.is_set():
            raise ComparisonCancelled('已停止，可從未完成項目繼續。')

    def _work(self, identifier, settings, event):
        with self.lock:
            if event.is_set() or self.events.get(identifier) is not event:
                return
            job = self.store.get('v2_job', identifier)
            job.update(status='running', error=None)
            job['progress'].update(started_at=now(), stage='preparing', finished_at=None, updated_at=now())
            self.store.put('v2_job', job, job.get('analysis_id', 'extraction'))
            if job.get('analysis_id'):
                analysis = self.store.get('v2_analysis', job['analysis_id'])
                analysis['status'] = 'screening' if job['phase'] == 'screening' else 'comparing'
                self.store.put('v2_analysis', analysis)
        try:
            {'extraction': self._extract_work, 'screening': self._screen_work, 'comparison': self._compare_work}[job['phase']](job, settings, event)
            self._check(event)
            status, error = 'completed', None
        except ComparisonCancelled:
            status, error = 'cancelled', '已停止，可從未完成項目繼續。'
        except Exception as exc:
            status, error = 'failed', f'本階段執行失敗（{type(exc).__name__}），已保存完成項目，請檢查模型設定後繼續。'
        with self.lock:
            if self.events.get(identifier) is not event:
                return
            job = self.store.get('v2_job', identifier)
            job.update(status=status, error=error)
            job['progress'].update(stage=status, request_started_at=None, finished_at=now(), updated_at=now())
            changes = [('v2_job', job, job.get('analysis_id', 'extraction'))]
            if job.get('analysis_id'):
                analysis = self.store.get('v2_analysis', job['analysis_id'])
                analysis.update(status=('awaiting_selection' if job['phase'] == 'screening' else 'completed')
                                if status == 'completed' else status, updated_at=now(), error=error,
                                version=analysis['version'] + 1)
                changes.extend([('v2_analysis', analysis, None), self.audit(analysis['id'], 'phase_' + status, phase=job['phase'], error=error)])
            elif status != 'completed':
                for target in job['targets']:
                    doc = self.doc(target['document_id'])
                    if doc.get('job_id') == identifier and doc['index_status'] != 'ready':
                        doc['index_status'] = status
                        changes.append(('v2_document', doc, doc['role']))
            self.store.put_many(changes)
            self.events.pop(identifier, None)

    def _checkpoint(self, job_id, records, **fields):
        with self.lock:
            job = self.store.get('v2_job', job_id)
            job.update(fields)
            job['completed'] += 1
            job['progress'].update(updated_at=now(), request_started_at=None)
            self.store.put_many([*records, ('v2_job', job, job.get('analysis_id', 'extraction'))])

    def _extract_work(self, job, settings, event):
        for target in job['targets']:
            self._check(event)
            doc = self.doc(target['document_id'])
            if doc.get('index_id') == target['index_id'] and doc['index_status'] == 'ready':
                continue
            with self.lock:
                doc.update(index_status='running')
                self.store.put('v2_document', doc, doc['role'])
            finished = {entry['block_id'] for entry in self.store.list('v2_extracted_block', target['index_id'])}
            for block in doc['blocks']:
                self._check(event)
                if block['id'] in finished:
                    continue
                self._progress(job['id'], 'extracting', '正在抽取獨立規格項目。', document_name=doc['name'], document_id=doc['id'], location=block['location'])
                result = ae.extract_block(block, doc['role'], settings, cancel_check=event.is_set,
                                          progress_callback=self._callback(job['id']))
                self._check(event)
                entries = []
                for value in result.get('items', []):
                    candidate = {k: value.get(k, '') for k in ITEM_FIELDS}
                    candidate.setdefault('block_id', block['id'])
                    candidate['block_id'] = block['id']
                    try:
                        item = self._validate_item(candidate, doc)
                    except ValueError:
                        continue
                    item.update(id=uid(), index_id=target['index_id'])
                    entries.append(item)
                # Defence in depth: model validation failure never drops a source span.
                for fragment in self.uncovered(block['text'], [i['quote'] for i in entries]):
                    item = {k: '' for k in ITEM_FIELDS}
                    item.update(id=uid(), index_id=target['index_id'], document_id=doc['id'], name='未歸類原文',
                                block_id=block['id'], location=block['location'], quote=fragment,
                                kind='unresolved', criticality='unknown', criticality_basis='來源尚未可靠拆解，需人工確認。')
                    entries.append(item)
                checkpoint = dict(id=target['index_id'] + ':' + block['id'], block_id=block['id'],
                                  coverage=result.get('coverage', 'needs_review'), warnings=result.get('warnings', []))
                self._checkpoint(job['id'], [*[('v2_item', i, target['index_id']) for i in entries],
                                             ('v2_extracted_block', checkpoint, target['index_id'])],
                                 document_id=doc['id'], document_name=doc['name'])
            with self.lock:
                doc = self.doc(doc['id'])
                items = self.store.list('v2_item', target['index_id'])
                block_records = self.store.list('v2_extracted_block', target['index_id'])
                doc.update(index_id=target['index_id'], index_status='ready', confirmed=False,
                           item_count=len(items), unresolved_count=sum(i['kind'] == 'unresolved' for i in items),
                           version=doc['version'] + 1, indexed_at=now(), index_engine=ae.ENGINE_VERSION,
                           extraction_warnings=list(dict.fromkeys(w for b in block_records for w in b['warnings'])),
                           extraction_model=public(settings), extraction_prompt_sha256=ae.PROMPT_SHA256)
                if job.get('model_sha256'):
                    doc['extraction_model_sha256'] = job['model_sha256']
                self.store.put_many([('v2_document', doc, doc['role']), self.audit(doc['id'], 'items_extracted',
                                     item_count=len(items), unresolved_count=doc['unresolved_count'], index_id=target['index_id'],
                                     model=settings['model'], model_sha256=job.get('model_sha256'),
                                     resume_events=self.store.get('v2_job', job['id']).get('resume_events', []),
                                     prompt_sha256=ae.PROMPT_SHA256, version=doc['version'])])

    def create_analysis(self, values):
        name = str(values.get('name', '')).strip()
        if not name or len(name) > 160:
            raise ValueError('請填寫分析名稱（最多 160 字）。')
        mode = values.get('comparison_mode', 'focused')
        if mode not in {'focused', 'exhaustive'}:
            raise ValueError('請選擇重點比對或完整比對。')
        context = values.get('context', {})
        if not isinstance(context, dict) or set(context) - {'purpose', 'environment', 'market', 'notes'}:
            raise ValueError('產品用途欄位無效。')
        if any(not isinstance(v, str) or len(v) > 5000 for v in context.values()):
            raise ValueError('用途與環境欄位每欄最多 5,000 字。')
        settings = self._settings()
        with self.lock:
            product = self.summary_doc(self.doc(values.get('product_id', '')))
            if product['role'] != 'product':
                raise ValueError('請選擇產品規格文件。')
            identifiers = values.get('library_ids', [])
            if not isinstance(identifiers, list):
                raise ValueError('標準文件清單無效。')
            standards = [self.summary_doc(self.doc(i)) for i in dict.fromkeys(identifiers)] if identifiers else self._document_refs('standard')
            if not standards or any(d['role'] != 'standard' for d in standards):
                raise ValueError('請先匯入標準文件庫。')
            docs = [product, *standards]
            if any(d['index_status'] != 'ready' or not d['confirmed'] for d in docs):
                raise ValueError('請先完成並確認產品與選定標準的規格項目；尚未確認的文件不會被靜默略過。')
            identifier = uid()
            analysis = dict(id=identifier, name=name, project_name=name, product_id=product['id'], product_name=product['name'],
                            created_at=now(), updated_at=now(), status='queued', phase='screening', version=1,
                            comparison_mode=mode, context=context, standard_count=len(standards), total_items=0,
                            unresolved_items=sum(d['unresolved_count'] for d in docs),
                            settings=public(settings), app_version=__version__, engine_version=ae.ENGINE_VERSION,
                            prompt_sha256=ae.PROMPT_SHA256, error=None)
            job = self._new_job('screening', len(standards), settings, analysis_id=identifier)
            analysis['job_id'] = job['id']
            # Snapshot one document at a time in one transaction, keeping the
            # whole standards corpus out of the worker's in-memory document list.
            with self.store.connect() as db:
                for reference in docs:
                    doc = self.doc(reference['id'])
                    source = dict(public(doc), items=self.items(doc), extraction_audit=self.store.list('v2_audit', doc['id']))
                    snapshot = dict(id=identifier + ':' + doc['id'], document_id=doc['id'], document=source)
                    self._write(db, [('v2_source', snapshot, identifier)])
                self._write(db, [('v2_analysis', analysis, None), self.audit(identifier, 'analysis_created',
                            standard_count=len(standards), context=context, comparison_mode=mode,
                            source_versions={d['id']: d['version'] for d in docs})])
            self._queue(job, settings)
            return self.analysis_view(identifier)

    def _sources(self, identifier):
        return [s['document'] for s in self.store.list('v2_source', identifier)]

    def _source_ids(self, identifier):
        with self.store.connect() as db:
            rows = db.execute("SELECT json_extract(data,'$.document_id') AS document_id "
                              'FROM records WHERE kind=? AND parent=? ORDER BY rowid', ('v2_source', identifier)).fetchall()
        return [r['document_id'] for r in rows]

    def _source(self, analysis_id, document_id):
        return self.store.get('v2_source', analysis_id + ':' + document_id)['document']

    def _screen_work(self, job, settings, event):
        analysis = self.store.get('v2_analysis', job['analysis_id'])
        product = self._source(analysis['id'], analysis['product_id'])
        done = {r['standard_id'] for r in self.store.list('v2_screening', job['analysis_id'])}
        for document_id in self._source_ids(analysis['id']):
            self._check(event)
            if document_id == product['id'] or document_id in done:
                continue
            doc = self._source(analysis['id'], document_id)
            self._progress(job['id'], 'screening', '正在檢查標準相關性與適用條件。', document_name=doc['name'], document_id=doc['id'])
            try:
                result = ae.screen_standard(product, doc, analysis['context'], settings, cancel_check=event.is_set,
                                            progress_callback=self._callback(job['id']))
            except ComparisonCancelled:
                raise
            except Exception:
                result = dict(relevance='unknown', relevance_score=0, applicability='unknown', reason='本次初篩失敗，保留文件待確認。',
                              evidence=[], warnings=['初篩未完成，不能據此排除標準。'])
            self._check(event)
            result.update(id=uid(), analysis_id=analysis['id'], standard_id=doc['id'], standard_name=doc['name'],
                          selected=True, review=dict(version=0, decision='pending', reviewer='', note=''),
                          item_count=sum(i['kind'] != 'context' for i in doc['items']))
            self._checkpoint(job['id'], [('v2_screening', result, analysis['id'])])

    def _counts(self, identifier):
        counts = dict(results=0, pending=0, high=0, medium=0, low=0, unknown=0, none=0,
                      match=0, partial=0, mismatch=0, missing=0, uncertain=0, exhaustive_results=0)
        # Aggregate rows in SQLite without reading source text or every result body.
        with self.store.connect() as db:
            rows = db.execute("SELECT json_extract(data,'$.review.decision') AS decision, "
                              "CASE WHEN json_extract(data,'$.review.decision') IN ('confirmed','changed') "
                              "THEN coalesce(json_extract(data,'$.review.final_status'),json_extract(data,'$.status')) "
                              "ELSE json_extract(data,'$.status') END AS status, "
                              "json_extract(data,'$.risk.level') AS risk, json_extract(data,'$.retrieval.complete') AS complete, "
                              "COUNT(*) AS n FROM records WHERE kind='v2_result' AND parent=? GROUP BY decision,status,risk,complete",
                              (identifier,)).fetchall()
            screenings = db.execute("SELECT json_extract(data,'$.selected') AS selected,COUNT(*) AS n "
                                    "FROM records WHERE kind='v2_screening' AND parent=? GROUP BY selected", (identifier,)).fetchall()
        for row in rows:
            n = row['n']
            counts['results'] += n
            if row['decision'] not in {'confirmed', 'changed'}:
                counts['pending'] += n
            if row['status'] in STATUSES:
                counts[row['status']] += n
            if row['risk'] in LEVELS:
                counts[row['risk']] += n
            if row['complete']:
                counts['exhaustive_results'] += n
        counts.update(screened=sum(r['n'] for r in screenings), selected_standards=sum(r['n'] for r in screenings if r['selected']),
                      excluded_standards=sum(r['n'] for r in screenings if not r['selected']))
        return counts

    def analysis_view(self, identifier):
        analysis = self.store.get('v2_analysis', identifier)
        job = self.job_view(analysis['job_id'])
        counts = self._counts(identifier)
        selected_complete = (analysis['status'] == 'completed' and counts['results'] == analysis['total_items']
                             and counts['exhaustive_results'] == counts['results'])
        coverage = dict(screened=counts['screened'], total_standards=analysis['standard_count'],
                        selected_standards=counts['selected_standards'], excluded_standards=counts['excluded_standards'],
                        compared=counts['results'], total_items=analysis['total_items'], exhaustive_results=counts['exhaustive_results'],
                        selected_scope_complete=selected_complete,
                        coverage_complete=selected_complete and counts['excluded_standards'] == 0,
                        unresolved_items=analysis.get('unresolved_items', 0),
                        note='覆蓋表示來源與請求處理範圍，不能保證全部語意差異已找出。')
        summary = {k: v for k, v in public(analysis).items() if k != 'settings'}
        return dict(summary, counts=counts, coverage=coverage, progress=dict(job['progress'], completed=job['completed'], total=job['total']),
                    server_time=now(), job_status=job['status'])

    def select(self, identifier, values):
        name, note = reviewer(values)
        decisions = values.get('decisions')
        if not isinstance(decisions, list) or not decisions:
            raise ValueError('請提供要確認的標準選取清單。')
        with self.lock:
            analysis = self.store.get('v2_analysis', identifier)
            check_version(analysis, values)
            if analysis['status'] != 'awaiting_selection':
                raise ValueError('只能在初篩完成、尚未開始比對時調整標準。')
            rows = {r['standard_id']: r for r in self.store.list('v2_screening', identifier)}
            changes, seen = [], set()
            for value in decisions:
                standard_id = value.get('standard_id')
                if standard_id not in rows or standard_id in seen or not isinstance(value.get('selected'), bool):
                    raise ValueError('標準選取資料無效。')
                seen.add(standard_id)
                reason = str(value.get('reason', '')).strip() or note
                if len(reason) > 10000 or (not value['selected'] and not reason):
                    raise ValueError('排除標準必須填寫理由，且保留排除紀錄。')
                row = rows[standard_id]
                before = row['selected']
                row['selected'] = value['selected']
                row['review'] = dict(version=row['review']['version'] + 1, decision='included' if row['selected'] else 'excluded',
                                     reviewer=name, note=reason, updated_at=now())
                changes.extend([('v2_screening', row, identifier), self.audit(identifier, 'standard_selected',
                                 dict(reviewer=name, note=reason), standard_id=standard_id, before=before, selected=row['selected'])])
            analysis.update(version=analysis['version'] + 1, updated_at=now())
            self.store.put_many([*changes, ('v2_analysis', analysis, None)])
            return self.analysis_view(identifier)

    def compare(self, identifier, values):
        with self.lock:
            analysis = self.store.get('v2_analysis', identifier)
            check_version(analysis, values)
            if analysis['status'] != 'awaiting_selection':
                raise ValueError('請先完成標準初篩及選取。')
            selected_rows = [r for r in self.store.list('v2_screening', identifier) if r['selected']]
            selected = {r['standard_id'] for r in selected_rows}
            if not selected:
                raise ValueError('請至少選擇一份標準。')
            total = sum(r['item_count'] for r in selected_rows)
            if not total:
                raise ValueError('所選標準只有背景文字，沒有可比對要求；請先確認抽取項目。')
            settings = self._resume_settings(self.store.get('v2_job', analysis['job_id']))
            job = self._new_job('comparison', total, settings, analysis_id=identifier)
            analysis.update(status='queued', phase='comparison', job_id=job['id'], total_items=total,
                            version=analysis['version'] + 1, updated_at=now())
            self.store.put_many([('v2_analysis', analysis, None), self.audit(identifier, 'comparison_started', selected_standards=sorted(selected),
                                 total_items=total, comparison_mode=analysis['comparison_mode'])])
            self._queue(job, settings)
            return self.analysis_view(identifier)

    def _compare_work(self, job, settings, event):
        identifier = job['analysis_id']
        analysis = self.store.get('v2_analysis', identifier)
        product = self._source(identifier, analysis['product_id'])
        selected = {r['standard_id'] for r in self.store.list('v2_screening', identifier) if r['selected']}
        done = {r['item']['id'] for r in self.store.list('v2_result', identifier)}
        for document_id in self._source_ids(identifier):
            if document_id not in selected:
                continue
            doc = self._source(identifier, document_id)
            for item in doc['items']:
                self._check(event)
                if item['kind'] == 'context' or item['id'] in done:
                    continue
                self._progress(job['id'], 'comparing', '正在比對獨立規格要求。', document_name=doc['name'], document_id=doc['id'],
                               item_name=item.get('name', ''), location=item['location'])
                try:
                    result = ae.compare_item(item, product, doc, settings, exhaustive=analysis['comparison_mode'] == 'exhaustive',
                                             cancel_check=event.is_set, progress_callback=self._callback(job['id']))
                except ComparisonCancelled:
                    raise
                except Exception:
                    result = dict(status='uncertain', explanation='本項目請求或格式驗證失敗，保留待確認。', differences=[], evidence=[],
                                  confidence=0, warnings=['執行失敗，不能據此判定符合或未載明。'], product_coverage={'scanned': 0, 'total': len(product['blocks'])},
                                  retrieval={'mode': 'failed', 'candidate_count': 0, 'total': len(product['blocks']), 'complete': False}, matched_product_item_ids=[])
                    result['risk'] = ae.assess_risk(result, item)
                self._check(event)
                result.update(id=uid(), analysis_id=identifier, run_id=identifier, standard_id=doc['id'], standard_name=doc['name'],
                              item=copy.deepcopy(item), block_id=item['block_id'], location=item['location'], requirement=item['quote'],
                              created_at=now(), review=dict(version=0, decision='pending', final_status=None, reviewer='', note=''))
                result['ai_risk'] = copy.deepcopy(result.get('risk', {}))
                self._checkpoint(job['id'], [('v2_result', result, identifier)])

    def results(self, identifier, offset=0, limit=50, status='', risk='', standard_id='', review='', q=''):
        self.store.get('v2_analysis', identifier)
        where, args = [], []
        if status:
            if status not in STATUSES:
                raise ValueError('判定篩選值無效。')
            where.append("CASE WHEN json_extract(data,'$.review.decision') IN ('confirmed','changed') THEN "
                         "coalesce(json_extract(data,'$.review.final_status'),json_extract(data,'$.status')) ELSE json_extract(data,'$.status') END=?")
            args.append(status)
        for field, value in [('risk.level', risk), ('standard_id', standard_id), ('review.decision', review)]:
            if value:
                where.append("json_extract(data,'$." + field + "')=?")
                args.append(value)
        if q:
            where.append("instr(lower(json_extract(data,'$.requirement') || json_extract(data,'$.explanation') || json_extract(data,'$.differences')),lower(?))>0")
            args.append(q)
        data = self._list('v2_result', identifier, ' AND '.join(where), args, offset, limit, sort='priority')
        for row in data['items']:
            row['effective_status'] = effective(row)
        return data

    def result(self, identifier, result_id):
        row = self.store.get('v2_result', result_id)
        if row['analysis_id'] != identifier:
            raise KeyError(result_id)
        row['history'] = self.store.list('v2_result_review', result_id)
        row['effective_status'] = effective(row)
        return row

    def review(self, identifier, result_id, values):
        decision = values.get('decision')
        if decision not in {'confirmed', 'changed', 'reopened'}:
            raise ValueError('覆核動作無效。')
        name, note = reviewer(values, reason=decision in {'changed', 'reopened'} or values.get('risk_level') is not None)
        level = values.get('risk_level')
        if level is not None and level not in LEVELS:
            raise ValueError('覆核優先度無效。')
        with self.lock:
            row = self.result(identifier, result_id)
            check_version(row['review'], values)
            row.pop('history', None)
            row.pop('effective_status', None)
            final_status = row['status'] if decision == 'confirmed' else values.get('final_status')
            if decision == 'reopened':
                final_status = None
            elif final_status not in STATUSES:
                raise ValueError('請選擇有效的最終判定。')
            event = dict(id=uid(), result_id=result_id, decision=decision, final_status=final_status,
                         risk_level=level if decision != 'reopened' else None, reviewer=name, note=note,
                         created_at=now(), version=row['review']['version'] + 1)
            row['review'] = {k: v for k, v in event.items() if k not in {'id', 'result_id', 'created_at'}} | {'updated_at': event['created_at']}
            if decision == 'reopened':
                row['risk'] = copy.deepcopy(row['ai_risk'])
            else:
                adjusted = dict(row, status=final_status)
                row['risk'] = ae.assess_risk(adjusted, row['item'])
                if level:
                    row['risk'].update(level=level, basis='人工覆核：' + note, reviewed=True)
            self.store.put_many([('v2_result', row, identifier), ('v2_result_review', event, result_id),
                                 self.audit(identifier, 'result_reviewed', dict(reviewer=name, note=note), result_id=result_id,
                                            decision=decision, final_status=final_status, risk_level=row['risk']['level'], version=event['version'])])
            return self.result(identifier, result_id)

    def extras(self, identifier):
        analysis = self.analysis_view(identifier)
        product = self._source(identifier, analysis['product_id'])
        mapped = set()
        for row in self.store.list('v2_result', identifier):
            mapped.update(row.get('matched_product_item_ids', []))
        return dict(items=[i for i in product['items'] if i['kind'] != 'context' and i['id'] not in mapped],
                    coverage_complete=analysis['coverage']['coverage_complete'],
                    note='此清單只表示尚未建立對應；不代表所有標準都沒有要求。未完成、排除標準及檢索未涵蓋內容仍須確認。')

    def snapshot(self, identifier):
        with self.lock:
            analysis = self.analysis_view(identifier)
            result = dict(analysis, settings=self.store.get('v2_analysis', identifier)['settings'],
                          schema_version=2, kind='analysis', documents=self._sources(identifier),
                          results=self.store.list('v2_result', identifier), screenings=self.store.list('v2_screening', identifier),
                          product_extras=self.extras(identifier), audit=self.store.list('v2_audit', identifier), rankings=[],
                          execution_jobs=[public(job) for job in self.store.list('v2_job', identifier)])
            for row in result['results']:
                row['history'] = self.store.list('v2_result_review', row['id'])
                row['effective_status'] = effective(row)
            return result

    def export(self, identifier, format):
        if format not in {'html', 'xlsx', 'json'}:
            raise ValueError('匯出格式須為 html、xlsx 或 json。')
        snapshot = self.snapshot(identifier)
        export_id, exported_at = uid(), now()
        snapshot.update(export_id=export_id, exported_at=exported_at)
        content, mime, name = export_run(snapshot, format)
        directory = self.root / 'reports'
        directory.mkdir(exist_ok=True)
        filename = export_id + '.' + format
        (directory / filename).write_bytes(content)
        record = dict(id=export_id, analysis_id=identifier, format=format, created_at=exported_at,
                      sha256=hashlib.sha256(content).hexdigest(), filename=name, size=len(content), _stored_name=filename, _mime=mime)
        self.store.put_many([('export', record, identifier), self.audit(identifier, 'report_exported', format=format,
                             export_id=export_id, sha256=record['sha256'])])
        return Response(content, media_type=mime, headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name)})

    def _resume_settings(self, job):
        current = self._settings()
        settings = dict(job['settings'], api_key=current.get('api_key', ''))
        if job.get('engine_version') != ae.ENGINE_VERSION or job.get('prompt_sha256') != ae.PROMPT_SHA256:
            raise ValueError('分析引擎版本已變更。請用原版繼續，或建立新分析以保留一致的判讀方法。')
        if job.get('model_sha256'):
            if (job['model_sha256'] != os.environ.get('SPEC_CHECK_MODEL_SHA256')
                    or current['model'] != settings['model']
                    or current['base_url'] != os.environ.get('SPEC_CHECK_PORTABLE_BASE_URL')):
                raise ValueError('請選用本次分析原本的 GGUF 模型後再繼續。')
            settings['base_url'] = current['base_url']
        elif current['model'] != settings['model'] or current['base_url'] != settings['base_url']:
            raise ValueError('請恢復原模型與連線位址再繼續；更換模型請建立新分析。')
        return validate_settings(settings)

    def cancel(self, identifier):
        with self.lock:
            job = self.store.get('v2_job', identifier)
            event = self.events.get(identifier)
            if job['status'] not in ACTIVE or event is None:
                return self.job_view(identifier)
            event.set()
            job['progress']['cancel_requested'] = True
            if job['status'] == 'queued':
                job.update(status='cancelled', error='已取消排隊。')
                job['progress'].update(stage='cancelled', finished_at=now(), updated_at=now())
                changes = [('v2_job', job, job.get('analysis_id', 'extraction'))]
                if job.get('analysis_id'):
                    analysis = self.store.get('v2_analysis', job['analysis_id'])
                    analysis.update(status='cancelled', version=analysis['version'] + 1)
                    changes.append(('v2_analysis', analysis, None))
                else:
                    for target in job['targets']:
                        doc = self.doc(target['document_id'])
                        doc['index_status'] = 'cancelled'
                        changes.append(('v2_document', doc, doc['role']))
                self.store.put_many(changes)
                self.events.pop(identifier, None)
            else:
                self.store.put('v2_job', job, job.get('analysis_id', 'extraction'))
            return self.job_view(identifier)

    def resume(self, identifier):
        with self.lock:
            job = self.store.get('v2_job', identifier)
            if job['status'] not in {'cancelled', 'interrupted', 'failed'}:
                raise ValueError('只能繼續已停止、中斷或失敗的工作。')
            settings = self._resume_settings(job)
            if job['phase'] == 'extraction':
                for target in job['targets']:
                    doc = self.doc(target['document_id'])
                    if doc.get('job_id') != identifier:
                        raise ValueError('文件已開始新的抽取工作，不能恢復舊工作。')
                for target in job['targets']:
                    doc = self.doc(target['document_id'])
                    if doc['index_status'] != 'ready':
                        doc['index_status'] = 'queued'
                        self.store.put('v2_document', doc, doc['role'])
            else:
                analysis = self.store.get('v2_analysis', job['analysis_id'])
                if analysis['job_id'] != identifier:
                    raise ValueError('分析已进入下一階段，不能恢復舊工作。')
                analysis.update(status='queued', version=analysis['version'] + 1)
                self.store.put('v2_analysis', analysis)
            job.update(status='queued', error=None)
            job['progress'].update(stage='queued', finished_at=None, request_started_at=None, cancel_requested=False)
            job.setdefault('resume_events', []).append(dict(at=now(), base_url=settings['base_url'], model=settings['model']))
            self._queue(job, settings)
            return self.job_view(identifier)

    def startup(self):
        with self.lock:
            for job in self.store.list('v2_job'):
                if job['status'] not in ACTIVE:
                    continue
                job.update(status='interrupted', error='上次程式中斷，已完成項目保留，可繼續。')
                job['progress'].update(stage='interrupted', request_started_at=None, finished_at=now(), updated_at=now())
                changes = [('v2_job', job, job.get('analysis_id', 'extraction'))]
                if job.get('analysis_id'):
                    analysis = self.store.get('v2_analysis', job['analysis_id'])
                    analysis.update(status='interrupted', version=analysis['version'] + 1)
                    changes.append(('v2_analysis', analysis, None))
                else:
                    for target in job['targets']:
                        doc = self.doc(target['document_id'])
                        if doc.get('job_id') == job['id'] and doc['index_status'] != 'ready':
                            doc['index_status'] = 'interrupted'
                            changes.append(('v2_document', doc, doc['role']))
                self.store.put_many(changes)

    def shutdown(self):
        with self.lock:
            for event in self.events.values():
                event.set()


def install_routes(app, service):
    router = APIRouter(prefix='/api/v2')

    @router.post('/import-project')
    def import_project(values: dict = Body(...)):
        return service.import_project(values.get('project_id', ''))

    @router.get('/library')
    def library(q: str = '', offset: int = 0, limit: int = 50):
        return service.documents('standard', q, offset, limit)

    @router.post('/library')
    async def add_standard(file: UploadFile = File(...)):
        return await service.upload(file, 'standard')

    @router.get('/products')
    def products(q: str = '', offset: int = 0, limit: int = 50):
        return service.documents('product', q, offset, limit)

    @router.post('/products')
    async def add_product(file: UploadFile = File(...)):
        return await service.upload(file, 'product')

    @router.post('/library/extract')
    def batch_extract(values: dict = Body(...)):
        identifiers = values.get('document_ids', [])
        if any(service.doc(i)['role'] != 'standard' for i in identifiers):
            raise ValueError('批次抽取僅適用標準文件。')
        return service.extract(identifiers)

    @router.post('/library/confirm')
    def batch_confirm(values: dict = Body(...)):
        documents = values.get('documents', [])
        if not documents or any(service.doc(d['id'])['role'] != 'standard' for d in documents):
            raise ValueError('請選擇要確認的標準文件。')
        return dict(items=service.confirm(values, documents))

    @router.get('/documents/{identifier}')
    def document(identifier: str):
        return public(service.doc(identifier))

    @router.patch('/documents/{identifier}')
    def metadata(identifier: str, values: dict = Body(...)):
        return service.update_metadata(identifier, values)

    @router.get('/documents/{identifier}/original')
    def original(identifier: str):
        doc = service.doc(identifier)
        path = service.root / 'uploads' / doc['_stored_name']
        if not path.exists():
            raise HTTPException(404, '找不到原始檔案，請確認資料備份。')
        return FileResponse(path, filename=doc['name'])

    @router.post('/documents/{identifier}/extract')
    def extract(identifier: str, values: dict = Body(default={})):
        return service.extract([identifier], force=values.get('force') is True)

    @router.get('/documents/{identifier}/items')
    def items(identifier: str, offset: int = 0, limit: int = 50, kind: str = '', q: str = ''):
        doc = service.doc(identifier)
        clauses, args = [], []
        if kind:
            clauses.append("json_extract(data,'$.kind')=?")
            args.append(kind)
        if q:
            clauses.append("instr(lower(json_extract(data,'$.quote') || json_extract(data,'$.name')),lower(?))>0")
            args.append(q)
        result = service._list('v2_item', doc.get('index_id', 'not-indexed'), ' AND '.join(clauses), args, offset, limit)
        return dict(result, document=service.summary_doc(doc))

    @router.put('/documents/{identifier}/items/{item_id}')
    def edit(identifier: str, item_id: str, values: dict = Body(...)):
        return service.edit_item(identifier, item_id, values)

    @router.post('/documents/{identifier}/items/{item_id}/split')
    def split(identifier: str, item_id: str, values: dict = Body(...)):
        return service.edit_item(identifier, item_id, values, split=True)

    @router.post('/documents/{identifier}/confirm')
    def confirm(identifier: str, values: dict = Body(...)):
        return service.confirm(values, [dict(id=identifier, expected_version=values.get('expected_version'))])[0]

    @router.get('/documents/{identifier}/audit')
    def document_audit(identifier: str, offset: int = 0, limit: int = 50):
        service.doc(identifier)
        return service._list('v2_audit', identifier, offset=offset, limit=limit)

    @router.get('/jobs/{identifier}')
    def job(identifier: str):
        return service.job_view(identifier)

    @router.post('/jobs/{identifier}/cancel')
    def cancel_job(identifier: str):
        return service.cancel(identifier)

    @router.post('/jobs/{identifier}/resume')
    def resume_job(identifier: str):
        return service.resume(identifier)

    @router.get('/analyses')
    def analyses(offset: int = 0, limit: int = 50):
        return service._list('v2_analysis', offset=offset, limit=limit)

    @router.post('/analyses')
    def create(values: dict = Body(...)):
        return service.create_analysis(values)

    @router.get('/analyses/{identifier}')
    def analysis(identifier: str):
        return service.analysis_view(identifier)

    @router.get('/analyses/{identifier}/screening')
    def screening(identifier: str, offset: int = 0, limit: int = 50):
        service.store.get('v2_analysis', identifier)
        return service._list('v2_screening', identifier, offset=offset, limit=limit, sort='relevance')

    @router.post('/analyses/{identifier}/selection')
    def selection(identifier: str, values: dict = Body(...)):
        return service.select(identifier, values)

    @router.post('/analyses/{identifier}/compare')
    def compare(identifier: str, values: dict = Body(...)):
        return service.compare(identifier, values)

    @router.post('/analyses/{identifier}/cancel')
    def cancel_analysis(identifier: str):
        service.cancel(service.store.get('v2_analysis', identifier)['job_id'])
        return service.analysis_view(identifier)

    @router.post('/analyses/{identifier}/resume')
    def resume_analysis(identifier: str):
        service.resume(service.store.get('v2_analysis', identifier)['job_id'])
        return service.analysis_view(identifier)

    @router.get('/analyses/{identifier}/results')
    def results(identifier: str, offset: int = 0, limit: int = 50, status: str = '', risk: str = '',
                standard_id: str = '', review: str = '', q: str = ''):
        return service.results(identifier, offset, limit, status, risk, standard_id, review, q)

    @router.get('/analyses/{identifier}/results/{result_id}')
    def result(identifier: str, result_id: str):
        return service.result(identifier, result_id)

    @router.post('/analyses/{identifier}/results/{result_id}/reviews')
    def review(identifier: str, result_id: str, values: dict = Body(...)):
        return service.review(identifier, result_id, values)

    @router.get('/analyses/{identifier}/product-extras')
    def extras(identifier: str, offset: int = 0, limit: int = 50):
        paging(offset, limit)
        result = service.extras(identifier)
        return dict(result, items=result['items'][offset:offset+limit], total=len(result['items']), offset=offset, limit=limit)

    @router.get('/analyses/{identifier}/audit')
    def analysis_audit(identifier: str, offset: int = 0, limit: int = 50):
        service.store.get('v2_analysis', identifier)
        return service._list('v2_audit', identifier, offset=offset, limit=limit)

    @router.get('/analyses/{identifier}/export')
    def export(identifier: str, format: str = 'html'):
        return service.export(identifier, format)

    @router.get('/analyses/{identifier}/exports')
    def exports(identifier: str, offset: int = 0, limit: int = 50):
        service.store.get('v2_analysis', identifier)
        page = service._list('export', identifier, offset=offset, limit=limit)
        page['items'] = [public(e) for e in page['items']]
        return page

    app.include_router(router)
