"""Self-contained external standards; no database IDs or local model required."""
from __future__ import annotations

import hashlib
import re

from .external_extraction import ExternalExtraction, FORMAT, MAX_RESULT_BYTES, digest, encoded, strict_json
from .link_import import safe_source
from .storage import ConflictError, now, uid
from .workflows import reviewer

PACKAGE_FORMAT = 'local-specheck-standard-package'


def text_field(value, label, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f'{label} 必須是文字，最多 {maximum} 字，必要欄位不可空白。')
    return value


class StandardPackage:
    def __init__(self, service):
        self.s = service

    def validate(self, raw):
        if not raw or len(raw) > MAX_RESULT_BYTES:
            raise ValueError('完整成果 JSON 不可空白或超過 16 MB。')
        value = strict_json(raw)
        if not isinstance(value, dict) or set(value) != {'schema_version', 'format', 'standard', 'coverage', 'blocks'} or \
                type(value['schema_version']) is not int or value['schema_version'] != 1 or value['format'] != PACKAGE_FORMAT:
            raise ValueError('請使用外網完整成果 Prompt 產生 .standard.json；舊版 .result.json 請在原文件的外部萃取視窗匯入。')
        standard, coverage, blocks = value['standard'], value['coverage'], value['blocks']
        if not isinstance(standard, dict) or set(standard) != {'name', 'source_url', 'version', 'category', 'scope', 'region'}:
            raise ValueError('standard 欄位不完整。')
        for k, v in standard.items():
            text_field(v, 'standard.' + k, 240 if k == 'name' else 5000, k == 'name')
        if any(ord(c) < 32 for c in standard['name']):
            raise ValueError('文件名稱不可包含控制字元。')
        source_url = safe_source(standard['source_url']) if standard['source_url'] else ''
        if not isinstance(coverage, dict) or set(coverage) != {'status', 'description'} or coverage['status'] not in ('complete', 'partial'):
            raise ValueError('coverage 須提供 status（complete／partial）及 description。')
        text_field(coverage['description'], 'coverage.description', 8000, True)
        if not isinstance(blocks, list) or not 1 <= len(blocks) <= 2000:
            raise ValueError('成果必須含 1–2,000 個原文區塊；請勿僅交摘要。')
        sources, results, seen, characters, item_count = [], [], set(), 0, 0
        for block in blocks:
            if not isinstance(block, dict) or set(block) != {'id', 'location', 'text', 'items'}:
                raise ValueError('每個區塊須包含 id、location、text、items。')
            block_id = text_field(block['id'], 'block.id', 64, True)
            if not re.fullmatch(r'[A-Za-z0-9_-]+', block_id) or block_id in seen:
                raise ValueError('區塊 id 須唯一，僅使用英文、數字、底線與連字號。')
            seen.add(block_id)
            text_field(block['location'], 'block.location', 500, True)
            text_field(block['text'], 'block.text', 12000, True)
            characters += len(block['text'])
            if not isinstance(block['items'], list):
                raise ValueError('items 必須是陣列。')
            item_count += len(block['items'])
            if characters > 12_000_000 or item_count > 20000:
                raise ValueError('成果原文或項目過多；上限為 1,200 萬字及 20,000 項。')
            sources.append({k: block[k] for k in ('id', 'location', 'text')})
            results.append(dict(block_id=block_id, items=block['items']))
        doc = dict(id='standalone', sha256=digest(sources), blocks=sources)
        package = dict(id='standalone', batches=[dict(batch_id='all', block_ids=[b['id'] for b in sources])])
        exchange = dict(schema_version=1, format=FORMAT, package_id='standalone', document_id='standalone',
                        source_sha256=doc['sha256'], batch_id='all', blocks=results)
        checked = ExternalExtraction(self.s)._validate(doc, package, encoded(exchange), 'complete.standard.json')
        warnings = ['來源為外部提供的原文轉錄；系統只核對成果內引文，未驗證與原始 PDF 的一致性或全文完整性。',
                    '涵蓋範圍由外部填報：' + coverage['status'] + '；' + coverage['description']]
        if coverage['status'] == 'partial':
            warnings.append('此成果尚未涵蓋完整文件，只能預覽；請在外網補齊後再匯入，避免未完成文件阻擋全庫分析。')
        warnings.extend(checked['warnings'])
        return value, sources, checked['items'], warnings, source_url

    def preview(self, raw, values, source=None, commit=False):
        actor, note = reviewer(values, reason=True)
        model = text_field(values.get('model_label'), '外部模型名稱', 200, True).strip()
        value, blocks, items, warnings, source_url = self.validate(raw)
        raw_hash, content_hash = hashlib.sha256(raw).hexdigest(), digest(value)
        fingerprint = digest(dict(raw_sha256=raw_hash, reviewer=actor, note=note, model=model, source=source))
        with self.s.lock:
            duplicate = self.s._list('v2_document', where="json_extract(data,'$.external_content_sha256')=?",
                                     args=(content_hash,), limit=1)['items']
            output = dict(preview_sha256=fingerprint, name=value['standard']['name'], block_count=len(blocks),
                          item_count=len(items), unresolved_count=sum(i['kind'] == 'unresolved' for i in items),
                          coverage=value['coverage'], warnings=warnings, reused=bool(duplicate),
                          sample_items=[{k: i.get(k) for k in ('name', 'kind', 'quote', 'location')} for i in items[:10]])
            if not commit:
                return output
            if values.get('preview_sha256') != fingerprint:
                raise ConflictError('成果內容、連結或操作資料已變更，請重新預覽後匯入。')
            if value['coverage']['status'] != 'complete':
                raise ValueError('成果尚未完整，只能預覽。請在外網補齊原文與項目後重新匯入。')
            if values.get('acknowledge_warnings') is not True:
                raise ValueError('請先確認外部原文仍須人工核對。')
            if duplicate:
                self.s.store.put_many([self.s.audit(duplicate[0]['id'], 'standard_package_reused',
                    dict(reviewer=actor, note=note), raw_sha256=raw_hash, content_sha256=content_hash, source=source)])
                return dict(output, document=self.s.summary_doc(duplicate[0]))
            identifier, index_id, stamp = uid(), uid(), now()
            document = dict(id=identifier, name=value['standard']['name'], role='standard', version=1, created_at=stamp,
                            sha256=raw_hash, blocks=blocks, block_count=len(blocks), warnings=warnings,
                            metadata={k: value['standard'][k] for k in ('category', 'scope', 'version', 'region')},
                            index_id=index_id, index_status='ready', confirmed=False, indexed_at=stamp,
                            item_count=len(items), unresolved_count=output['unresolved_count'],
                            index_engine='external-standard-package-v1', source_kind='external_transcription',
                            external_coverage=value['coverage'], external_content_sha256=content_hash,
                            source_url=source_url, import_source=source,
                            extraction_model=dict(provider='external_manual', models=[model], identity='user_reported'),
                            _stored_name=identifier + '.json')
            for item in items:
                item.update(id=uid(), document_id=identifier, index_id=index_id,
                            external_source=dict(content_sha256=content_hash, raw_sha256=raw_hash,
                                                 model_label=model, model_identity='user_reported',
                                                 source_kind='external_transcription'))
            path = self.s.root / 'uploads' / document['_stored_name']
            try:
                path.write_bytes(raw)
                self.s.store.put_many([('v2_document', document, 'standard'),
                    *[('v2_item', item, index_id) for item in items],
                    self.s.audit(identifier, 'standard_package_imported', dict(reviewer=actor, note=note),
                                 raw_sha256=raw_hash, content_sha256=content_hash, model_label=model,
                                 coverage=value['coverage'], source=source, item_count=len(items))])
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return dict(output, document=self.s.summary_doc(document))
