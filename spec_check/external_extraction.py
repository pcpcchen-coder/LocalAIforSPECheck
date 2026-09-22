"""Manual, standard-only external extraction exchange; never calls a network service."""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from .storage import ConflictError, now, uid
from .workflows import ITEM_FIELDS, check_version, reviewer

PROMPT_PATH = Path(__file__).with_name('prompts') / 'CHATGPT_STANDARD_EXTRACTION_PROMPT.md'
FORMAT = 'local-specheck-extraction-result'
MAX_RESULT_BYTES = 16 * 1024 * 1024
TEXT_FIELDS = sorted(ITEM_FIELDS - {'block_id'})


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def strict_json(raw):
    def pairs(entries):
        out = {}
        for key, value in entries:
            if key in out:
                raise ValueError('JSON 出現重複欄位，請重新產生完整結果。')
            out[key] = value
        return out
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('JSON 不可包含 NaN／Infinity。')))
        # Reject unpaired escaped surrogates before filenames, hashes or SQLite encoding.
        encoded(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('無法讀取完整 UTF-8 JSON；請勿加入 Markdown 圍欄、說明文字或截斷內容。') from exc


def result_schema():
    ref = {'type': 'object', 'properties': {'block_id': {'type': 'string'}, 'quote': {'type': 'string'}},
           'required': ['block_id', 'quote'], 'additionalProperties': False}
    fields = {k: {'type': 'string', 'maxLength': 20000} for k in TEXT_FIELDS}
    fields['kind']['enum'] = ['requirement', 'context', 'unresolved']
    fields['criticality']['enum'] = ['high', 'medium', 'low', 'unknown']
    fields['context_evidence'] = {'type': 'array', 'maxItems': 20, 'items': ref}
    item = {'type': 'object', 'properties': fields, 'required': TEXT_FIELDS + ['context_evidence'], 'additionalProperties': False}
    block = {'type': 'object', 'properties': {'block_id': {'type': 'string'}, 'items': {'type': 'array', 'maxItems': 200, 'items': item}},
             'required': ['block_id', 'items'], 'additionalProperties': False}
    props = {key: {'type': 'string'} for key in ('package_id', 'document_id', 'source_sha256', 'batch_id')}
    props.update(schema_version={'const': 1}, format={'const': FORMAT}, blocks={'type': 'array', 'items': block})
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': props,
            'required': list(props), 'additionalProperties': False}


class ExternalExtraction:
    def __init__(self, service):
        self.s = service

    def document(self, identifier):
        doc = self.s.doc(identifier)
        if doc['role'] != 'standard':
            raise ValueError('外部萃取只開放標準文件；產品文件不包含在外部萃取包。')
        return doc

    def package(self, identifier, package_id):
        package = self.s.store.get('v2_external_package', package_id)
        if package['document_id'] != identifier:
            raise ValueError('萃取包與目前文件不符。')
        return package

    def batch_metadata(self, package_id):
        with self.s.store.connect() as db:
            rows = db.execute("SELECT json_remove(data,'$.items') AS data FROM records WHERE kind=? AND parent=?",
                              ('v2_external_batch', package_id)).fetchall()
        return [json.loads(r['data']) for r in rows]

    def view(self, package, doc=None):
        records = self.batch_metadata(package['id'])
        received = {r['batch_id'] for r in records}
        doc = doc or self.document(package['document_id'])
        return {**{k: v for k, v in package.items() if k != 'batches'},
                'batch_count': len(package['batches']), 'received_count': len(received),
                'missing_batches': [b['batch_id'] for b in package['batches'] if b['batch_id'] not in received],
                'stale': doc['version'] != package['base_version'] and package['status'] != 'activated',
                'item_count': sum(r['item_count'] for r in records),
                'unresolved_count': sum(r['unresolved_count'] for r in records)}

    def listing(self, identifier):
        doc = self.document(identifier)
        return {'items': [self.view(p, doc) for p in self.s.store.list('v2_external_package', identifier)][::-1]}

    def create(self, identifier, values):
        actor, note = reviewer(values)
        size = values.get('blocks_per_batch', 4)
        if type(size) is not int or not 1 <= size <= 8:
            raise ValueError('每批區塊數必須為 1–8。')
        if values.get('acknowledge_public') is not True:
            raise ValueError('請先確認此份標準可交給外部模型處理。')
        with self.s.lock:
            doc = self.document(identifier)
            self.s._editable(doc)
            check_version(doc, values)
            if not doc['blocks']:
                raise ValueError('文件沒有可用文字；請先準備文字版或完成 OCR 後重新上傳。')
            batches = [dict(batch_id=f'batch-{n // size + 1:04d}', block_ids=[b['id'] for b in doc['blocks'][n:n + size]])
                       for n in range(0, len(doc['blocks']), size)]
            package = dict(id=uid(), document_id=identifier, source_sha256=doc['sha256'],
                           source_blocks_sha256=digest(doc['blocks']), base_version=doc['version'],
                           document_name=doc['name'], metadata=doc['metadata'], batches=batches,
                           version=1, status='collecting', created_at=now(), created_by=actor,
                           prompt_sha256=hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest())
            self.s.store.put_many([('v2_external_package', package, identifier),
                                   self.s.audit(identifier, 'external_package_created', dict(reviewer=actor, note=note),
                                                package_id=package['id'], batch_count=len(batches), source_sha256=doc['sha256'],
                                                prompt_sha256=package['prompt_sha256'])])
            return self.view(package)

    def archive(self, identifier, package_id):
        with self.s.lock:
            doc, package = self.document(identifier), self.package(identifier, package_id)
            if digest(doc['blocks']) != package['source_blocks_sha256']:
                raise ConflictError('原文區塊已改變，請建立新的外部萃取包。')
            # A prompt update must never silently change an older package's contract.
            if hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest() != package['prompt_sha256']:
                raise ConflictError('Prompt 版本已更新，請建立新的外部萃取包。')
            source = dict(format='local-specheck-extraction-source', schema_version=1, package_id=package_id,
                          document_id=identifier, source_sha256=doc['sha256'], name=package['document_name'],
                          metadata=package['metadata'], blocks=doc['blocks'], warnings=doc.get('warnings', []))
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('PROMPT.md', PROMPT_PATH.read_bytes())
                archive.writestr('source.json', encoded(source))
                archive.writestr('result.schema.json', encoded(result_schema()))
                archive.writestr('README.txt', '先閱讀 PROMPT.md。將 source.json、result.schema.json 和本次 batch input 交給 ChatGPT。\n每批產生一個 .result.json；在原系統同一份標準的「外部模型萃取」中預覽、保存。\n收齊全部批次後套用，再人工確認。這個 ZIP 不含產品資料、模型金鑰、人工歷程或分析結果。\n')
                by_id = {b['id']: i for i, b in enumerate(doc['blocks'])}
                for batch in package['batches']:
                    positions = [by_id[identifier] for identifier in batch['block_ids']]
                    context_ids = {i for p in positions for i in (p - 1, p + 1) if 0 <= i < len(doc['blocks'])} - set(positions)
                    task = dict(format='local-specheck-extraction-input', schema_version=1, package_id=package_id,
                                document_id=identifier, source_sha256=doc['sha256'], batch_id=batch['batch_id'],
                                blocks=[doc['blocks'][i] for i in positions],
                                neighbor_context=[doc['blocks'][i] for i in sorted(context_ids)])
                    archive.writestr(batch['batch_id'] + '.input.json', encoded(task))
            return data.getvalue()

    def _collecting(self, doc, package):
        self.s._editable(doc)
        if package['status'] != 'collecting':
            raise ConflictError('這個萃取包已套用；若要重新萃取，請建立新包。')
        if doc['version'] != package['base_version'] or digest(doc['blocks']) != package['source_blocks_sha256']:
            raise ConflictError('文件或項目已在匯出後更新。為保留人工修改，請建立新包再萃取。')

    def _validate(self, doc, package, raw, filename):
        value = strict_json(raw)
        keys = {'schema_version', 'format', 'package_id', 'document_id', 'source_sha256', 'batch_id', 'blocks'}
        if not isinstance(value, dict) or set(value) != keys or type(value['schema_version']) is not int or value['schema_version'] != 1 or value['format'] != FORMAT:
            raise ValueError('結果格式不符；請使用包內 PROMPT.md 與 result.schema.json。')
        for key, expected in [('package_id', package['id']), ('document_id', doc['id']), ('source_sha256', doc['sha256'])]:
            if value[key] != expected:
                raise ValueError(f'{key} 不符；不能匯入其他文件或其他萃取包的結果。')
        batch = next((b for b in package['batches'] if b['batch_id'] == value['batch_id']), None)
        if batch is None:
            raise ValueError('批次編號不屬於此萃取包。')
        blocks = value['blocks']
        if not isinstance(blocks, list) or any(not isinstance(b, dict) or set(b) != {'block_id', 'items'} or not isinstance(b['block_id'], str) for b in blocks):
            raise ValueError('blocks 必須列出本批每一個 block_id 與 items。')
        if len(blocks) != len(batch['block_ids']) or {b['block_id'] for b in blocks} != set(batch['block_ids']):
            raise ValueError('本批區塊有遺漏、重複或多列，請讓 ChatGPT 補齊整批 JSON。')
        source = {b['id']: b for b in doc['blocks']}
        items, warnings = [], []
        for block in blocks:
            block_id = block['block_id']
            item_source = {'id': doc['id'], 'blocks': [source[block_id]]}
            if not isinstance(block['items'], list) or len(block['items']) > 200:
                raise ValueError(f'{block_id} 的 items 必須是陣列，每區塊最多 200 項。')
            entries, seen = [], set()
            for item in block['items']:
                if not isinstance(item, dict) or set(item) != set(TEXT_FIELDS) | {'context_evidence'}:
                    raise ValueError(f'{block_id} 的項目欄位不完整或含未知欄位。')
                candidate = self.s._validate_item({k: item[k] for k in TEXT_FIELDS} | {'block_id': block_id}, item_source)
                if candidate['kind'] == 'specification':
                    raise ValueError('標準萃取不可使用產品 specification 類型。')
                refs = item['context_evidence']
                if not isinstance(refs, list) or len(refs) > 20:
                    raise ValueError('上下文引文必須是陣列，每項最多 20 筆。')
                for ref in refs:
                    if not isinstance(ref, dict) or set(ref) != {'block_id', 'quote'} or not isinstance(ref['block_id'], str) or not isinstance(ref['quote'], str):
                        raise ValueError('上下文引文格式無效。')
                    if ref['block_id'] not in source or not ref['quote'].strip() or ref['quote'] not in source[ref['block_id']]['text']:
                        raise ValueError('上下文引文必須逐字存在於同份標準的指定區塊。')
                candidate['context_evidence'] = refs
                if candidate['kind'] == 'context' and re.search(r'\d|應|应|必須|必须|不得|shall|must|minimum|maximum', candidate['quote'], re.I):
                    candidate.update(kind='unresolved', criticality='unknown', criticality_basis='背景分類中含數字或要求用語，需人工確認。')
                    warnings.append(f'{block_id}：背景分類可能含要求，已改為待處理。')
                fingerprint = digest(candidate)
                if fingerprint in seen:
                    raise ValueError(f'{block_id} 包含完全重複項目，請移除重複後再匯入。')
                seen.add(fingerprint)
                entries.append(candidate)
            for fragment in self.s.uncovered(source[block_id]['text'], [i['quote'] for i in entries]):
                candidate = {k: '' for k in ITEM_FIELDS}
                candidate.update(block_id=block_id, quote=fragment, name='外部萃取未涵蓋原文', kind='unresolved',
                                 criticality='unknown', criticality_basis='外部結果沒有涵蓋這段原文，需人工確認。')
                entries.append(self.s._validate_item(candidate, item_source))
                warnings.append(f'{block_id}：未涵蓋原文已保留為待處理項目。')
            items.extend(entries)
        return dict(id=package['id'] + ':' + batch['batch_id'], batch_id=batch['batch_id'], package_id=package['id'],
                    filename=Path(filename.replace('\\', '/')).name[:200], raw_sha256=hashlib.sha256(raw).hexdigest(),
                    content_sha256=digest(value), items=items, item_count=len(items),
                    unresolved_count=sum(i['kind'] == 'unresolved' for i in items), warnings=list(dict.fromkeys(warnings)))

    def preview(self, identifier, package_id, files, values, commit=False):
        actor, note = reviewer(values, reason=True)
        model = values.get('model_label', '')
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError('請填寫實際使用的外部模型名稱；不知道版本可填 ChatGPT（版本未知）。')
        model = model.strip()
        if not 1 <= len(files) <= 50 or sum(len(raw) for _, raw in files) > MAX_RESULT_BYTES:
            raise ValueError('每次選擇 1–50 個 JSON，合計不可超過 16 MB。')
        with self.s.lock:
            doc, package = self.document(identifier), self.package(identifier, package_id)
            self._collecting(doc, package)
            check_version(package, values)
            entries = [self._validate(doc, package, raw, filename) for filename, raw in files]
            if len({r['batch_id'] for r in entries}) != len(entries):
                raise ValueError('本次選取了相同批次的多個檔案，請每批只保留一份。')
            previous = {r['batch_id']: r for r in self.batch_metadata(package_id)}
            changes = [r for r in entries if previous.get(r['batch_id'], {}).get('content_sha256') != r['content_sha256']
                       or previous.get(r['batch_id'], {}).get('model_label') != model]
            replaced = [r['batch_id'] for r in changes if r['batch_id'] in previous]
            fingerprint = digest(dict(package_id=package_id, package_version=package['version'], document_version=doc['version'],
                                      records=[(r['batch_id'], r['raw_sha256']) for r in entries], model=model, reviewer=actor, note=note))
            output = dict(preview_sha256=fingerprint, expected_version=package['version'], batches=[r['batch_id'] for r in entries],
                          reused_count=len(entries) - len(changes), replaced_batches=replaced,
                          item_count=sum(len(r['items']) for r in entries),
                          unresolved_count=sum(i['kind'] == 'unresolved' for r in entries for i in r['items']),
                          warnings=[w for r in entries for w in r['warnings']],
                          sample_items=[{k: i.get(k) for k in ('name', 'kind', 'quote', 'location')} for r in entries for i in r['items']][:10])
            if commit:
                if values.get('preview_sha256') != fingerprint:
                    raise ConflictError('檔案、模型名稱或操作資料已變更，請重新預覽後保存。')
                if changes:
                    events = []
                    for entry in changes:
                        entry.update(model_label=model, imported_by=actor, imported_at=now())
                        events.append(self.s.audit(identifier, 'external_batch_imported', dict(reviewer=actor, note=note),
                                      package_id=package_id, batch_id=entry['batch_id'], model_label=model,
                                      model_identity='user_reported', raw_sha256=entry['raw_sha256'], content_sha256=entry['content_sha256'],
                                      previous_sha256=previous.get(entry['batch_id'], {}).get('content_sha256'),
                                      item_count=len(entry['items']), warnings=entry['warnings']))
                    package['version'] += 1
                    self.s.store.put_many([*[('v2_external_batch', r, package_id) for r in changes],
                                           ('v2_external_package', package, identifier), *events])
            return {**output, 'package': self.view(package)}

    def activate(self, identifier, package_id, values):
        actor, note = reviewer(values, reason=True)
        if values.get('acknowledge_warnings') is not True:
            raise ValueError('請確認這會建立新的項目版本，並須重新人工確認。')
        with self.s.lock:
            doc, package = self.document(identifier), self.package(identifier, package_id)
            self._collecting(doc, package)
            check_version(package, values)
            records = {r['batch_id']: r for r in self.s.store.list('v2_external_batch', package_id)}
            if set(records) != {b['batch_id'] for b in package['batches']}:
                raise ValueError('尚未收齊全部批次，不能套用為完整項目版本。')
            index_id, items = uid(), []
            for batch in package['batches']:
                record = records[batch['batch_id']]
                for source in record['items']:
                    items.append(dict(source, id=uid(), index_id=index_id, external_source=dict(package_id=package_id,
                                      batch_id=record['batch_id'], content_sha256=record['content_sha256'],
                                      model_label=record['model_label'], model_identity='user_reported')))
            if not items:
                raise ValueError('沒有可套用的項目。')
            prior_index = doc.get('index_id')
            for key in ('job_id', 'extraction_model_sha256', 'confirmed_at', 'confirmed_by'):
                doc.pop(key, None)
            models = sorted({r['model_label'] for r in records.values()})
            doc.update(index_id=index_id, index_status='ready', confirmed=False, version=doc['version'] + 1,
                       item_count=len(items), unresolved_count=sum(i['kind'] == 'unresolved' for i in items),
                       indexed_at=now(), index_engine='external-extraction-v1', external_package_id=package_id,
                       extraction_model={'provider': 'external_manual', 'models': models, 'identity': 'user_reported'},
                       extraction_prompt_sha256=package['prompt_sha256'],
                       extraction_warnings=['外部模型身分由操作者填寫；引文與格式核對不等於語意正確，請人工確認。'] +
                                           list(dict.fromkeys(w for r in records.values() for w in r['warnings'])))
            package.update(status='activated', version=package['version'] + 1, activated_at=now(), index_id=index_id)
            self.s.store.put_many([*[('v2_item', i, index_id) for i in items], ('v2_document', doc, doc['role']),
                                   ('v2_external_package', package, identifier),
                                   self.s.audit(identifier, 'external_items_activated', dict(reviewer=actor, note=note),
                                     package_id=package_id, prior_index_id=prior_index, index_id=index_id, models=models,
                                     prompt_sha256=package['prompt_sha256'], version=doc['version'],
                                     item_count=len(items), unresolved_count=doc['unresolved_count'])])
            return {'document': self.s.summary_doc(doc), 'package': self.view(package)}
