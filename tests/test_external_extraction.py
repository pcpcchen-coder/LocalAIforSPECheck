"""Manual external extraction contract, real API/storage, no cloud calls."""
import copy
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from spec_check.app import create_app
from spec_check import analysis_engine as ae
from spec_check.external_extraction import FORMAT, result_schema

BASE = '/api/v2'


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('SPEC_CHECK_DATA', str(tmp_path))
    monkeypatch.setattr(ae, 'extract_block', lambda *_a, **_k: pytest.fail('External import must not invoke a model'))
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8765') as client:
        yield client


def upload(client, text='额定直流电压必须为 48 V。\n工作温度应为 0–40 °C。', role='standard'):
    route = 'library' if role == 'standard' else 'products'
    doc = ok(client.post(BASE + '/' + route, files={'file': ('規範.txt', text.encode(), 'text/plain')}))
    return ok(client.get(f'{BASE}/documents/{doc["id"]}'))


def create(client, doc, size=4):
    return ok(client.post(f'{BASE}/documents/{doc["id"]}/external', json={
        'expected_version': doc['version'], 'reviewer': '工程師', 'acknowledge_public': True, 'blocks_per_batch': size}))


def bundle(client, doc, package):
    response = client.get(f'{BASE}/documents/{doc["id"]}/external/{package["id"]}/download')
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        files = {n: z.read(n) for n in z.namelist()}
    return files, [json.loads(files[n]) for n in sorted(files) if n.endswith('.input.json')]


def item(quote, **changes):
    return dict(name='額定電壓', parameter='額定直流電壓', value='48', unit='V', operator='=', conditions='',
                exceptions='', test_method='', criticality='medium', criticality_basis='明確性能要求。',
                quote=quote, kind='requirement', context_evidence=[], **changes)


def result(task):
    return {**{k: task[k] for k in ('schema_version', 'package_id', 'document_id', 'source_sha256', 'batch_id')},
            'format': FORMAT, 'blocks': [{'block_id': b['id'], 'items': [item(b['text'])]} for b in task['blocks']]}


def values(package, **extra):
    return dict(expected_version=package['version'], reviewer='工程師', note='已檢查外部結果，套用後人工確認。',
                model_label='ChatGPT（版本未知）', **extra)


def send(client, doc, package, results, operation='preview', extra=None, raw=None):
    files = [('files', (f'{n}.result.json', json.dumps(r, ensure_ascii=False).encode(), 'application/json')) for n, r in enumerate(results)]
    if raw is not None:
        files = [('files', ('result.json', raw, 'application/json'))]
    return client.post(f'{BASE}/documents/{doc["id"]}/external/{package["id"]}/{operation}',
                       files=files, data={'values': json.dumps(values(package, **(extra or {})), ensure_ascii=False)})


def stage(client, doc, package, results):
    preview = ok(send(client, doc, package, results))
    return ok(send(client, doc, package, results, 'import', {'preview_sha256': preview['preview_sha256']}))


def activate(client, doc, package):
    return client.post(f'{BASE}/documents/{doc["id"]}/external/{package["id"]}/activate',
                       json=values(package, acknowledge_warnings=True))


def test_bundle_contains_only_standard_and_pinned_contract(client):
    product = upload(client, 'SECRET_PRODUCT_DO_NOT_EXPORT', 'product')
    doc = upload(client)
    package = create(client, doc)
    files, tasks = bundle(client, doc, package)
    assert b'SECRET_PRODUCT_DO_NOT_EXPORT' not in b''.join(files.values())
    assert product['id'].encode() not in b''.join(files.values())
    source = json.loads(files['source.json'])
    assert source['blocks'] == doc['blocks'] and source['source_sha256'] == doc['sha256']
    assert json.loads(files['result.schema.json']) == result_schema()
    assert '逐字'.encode() in files['PROMPT.md']
    assert sum(len(t['blocks']) for t in tasks) == len(doc['blocks'])
    blocked = client.post(f'{BASE}/documents/{product["id"]}/external', json=values(product, acknowledge_public=True))
    assert blocked.status_code == 400


def test_preview_is_readonly_stage_then_activate_then_human_confirm(client):
    doc = upload(client)
    package = create(client, doc)
    _, tasks = bundle(client, doc, package)
    outputs = [result(t) for t in tasks]
    preview = ok(send(client, doc, package, outputs))
    assert preview['package']['received_count'] == 0
    assert ok(client.get(f'{BASE}/documents/{doc["id"]}'))['item_count'] == 0
    package = stage(client, doc, package, outputs)['package']
    assert package['received_count'] == package['batch_count']
    same = stage(client, doc, package, outputs)
    assert same['reused_count'] == len(outputs) and same['package']['version'] == package['version']
    assert ok(client.get(f'{BASE}/documents/{doc["id"]}'))['index_status'] == 'not_started'
    changed = ok(activate(client, doc, package))['document']
    assert changed['index_status'] == 'ready' and not changed['confirmed']
    rows = ok(client.get(f'{BASE}/documents/{doc["id"]}/items'))['items']
    assert rows and all(i['external_source']['model_identity'] == 'user_reported' for i in rows)
    confirmed = ok(client.post(f'{BASE}/documents/{doc["id"]}/confirm', json={
        'expected_version': changed['version'], 'reviewer': '覆核人', 'acknowledge_warnings': True}))
    assert confirmed['confirmed']
    actions = ok(client.get(f'{BASE}/documents/{doc["id"]}/audit'))['items']
    assert {a['action'] for a in actions} >= {'external_package_created','external_batch_imported','external_items_activated','items_confirmed'}
    assert activate(client, doc, package).status_code == 409


def test_partial_batches_do_not_replace_existing_items_or_silently_finish(client):
    doc = upload(client, '\n'.join(f'條文{i}要求：'+ '甲' * 1300 for i in range(4)))
    package = create(client, doc, 1)
    _, tasks = bundle(client, doc, package)
    assert len(tasks) >= 4
    package = stage(client, doc, package, [result(tasks[0])])['package']
    assert package['received_count'] == 1 and package['missing_batches']
    assert activate(client, doc, package).status_code == 400
    assert ok(client.get(f'{BASE}/documents/{doc["id"]}'))['item_count'] == 0
    package = stage(client, doc, package, [result(t) for t in tasks[1:]])['package']
    assert ok(activate(client, doc, package))['document']['item_count'] == len(doc['blocks'])


@pytest.mark.parametrize('mutate', [
    lambda r: r.update(source_sha256='wrong'),
    lambda r: r.update(document_id='other-document'),
    lambda r: r.update(package_id='other-package'),
    lambda r: r.update(batch_id='batch-9999'),
    lambda r: r['blocks'].clear(),
    lambda r: r['blocks'].append(copy.deepcopy(r['blocks'][0])),
    lambda r: r['blocks'][0]['items'][0].update(quote='不存在於原文的要求'),
    lambda r: r['blocks'][0]['items'][0].update(kind='specification'),
    lambda r: r['blocks'][0]['items'][0].update(value=48),
    lambda r: r['blocks'][0]['items'][0].update(context_evidence=[{'block_id':'unknown', 'quote':'48'}]),
    lambda r: r['blocks'][0]['items'][0].update(context_evidence=[{'block_id':r['blocks'][0]['block_id'], 'quote':'not present'}]),
    lambda r: r.update(extra='unknown field'),
    lambda r: r['blocks'][0]['items'].append(copy.deepcopy(r['blocks'][0]['items'][0])),
])
def test_invalid_external_results_reject_entire_upload(client, mutate):
    doc = upload(client); package = create(client, doc); _, tasks = bundle(client, doc, package)
    output = result(tasks[0]); mutate(output)
    assert send(client, doc, package, [output]).status_code == 400
    assert ok(client.get(f'{BASE}/documents/{doc["id"]}/external'))['items'][0]['received_count'] == 0


def test_uncovered_source_and_questionable_context_remain_unresolved(client):
    doc = upload(client); package = create(client, doc); _, tasks = bundle(client, doc, package)
    output = result(tasks[0]); output['blocks'][0]['items'][0].update(quote='48 V', kind='context')
    preview = ok(send(client, doc, package, [output]))
    assert preview['unresolved_count'] >= 2 and preview['warnings']
    package = stage(client, doc, package, [output])['package']
    changed = ok(activate(client, doc, package))['document']
    assert changed['unresolved_count'] >= 2
    rows = ok(client.get(f'{BASE}/documents/{doc["id"]}/items'))['items']
    for block in doc['blocks']:
        quotes = [i['quote'] for i in rows if i['block_id'] == block['id']]
        assert ''.join(sorted(quotes, key=block['text'].index)) == block['text']


def test_revision_preview_and_document_guards_prevent_overwrite(client):
    doc = upload(client); package = create(client, doc); _, tasks = bundle(client, doc, package)
    output = result(tasks[0]); preview = ok(send(client, doc, package, [output]))
    output['blocks'][0]['items'][0]['name'] = '已改動'
    assert send(client, doc, package, [output], 'import', {'preview_sha256':preview['preview_sha256']}).status_code == 409
    package2 = stage(client, doc, package, [output])['package']
    assert send(client, doc, package, [output]).status_code == 409
    updated = ok(client.patch(f'{BASE}/documents/{doc["id"]}', json={
        'expected_version':doc['version'], 'reviewer':'人工', 'note':'更新文件版本', 'metadata':{'version':'第二版'}}))
    assert updated['version'] > doc['version']
    assert activate(client, doc, package2).status_code == 409
    assert ok(client.get(f'{BASE}/documents/{doc["id"]}/external'))['items'][0]['stale']


def test_duplicate_json_keys_truncated_response_and_multiple_same_batch_rejected(client):
    doc = upload(client); package = create(client, doc); _, tasks = bundle(client, doc, package)
    output = result(tasks[0]); raw=json.dumps(output).encode()
    for bad in [b'{"format":"x","format":"y"}', raw[:-4], b'```json\n'+raw+b'\n```']:
        assert send(client, doc, package, [], raw=bad).status_code == 400
    assert send(client, doc, package, [output,output]).status_code == 400


def test_cross_chapter_references_and_import_provenance_survive_item_edit(client):
    doc = upload(client, '額定電壓 48 V。'+'甲'*1390+'\n適用範圍：戶內。')
    package = create(client, doc); _, tasks = bundle(client, doc, package)
    output = result(tasks[0]); ref={'block_id':doc['blocks'][-1]['id'],'quote':'戶內'}
    output['blocks'][0]['items'][0]['context_evidence']=[ref]
    package = stage(client, doc, package, [output])['package']
    changed = ok(activate(client, doc, package))['document']
    first=ok(client.get(f'{BASE}/documents/{doc["id"]}/items'))['items'][0]
    from spec_check.workflows import ITEM_FIELDS
    edited = {k:first[k] for k in ITEM_FIELDS};edited['name']='人工修正名稱'
    ok(client.put(f'{BASE}/documents/{doc["id"]}/items/{first["id"]}',json={
        'expected_version':changed['version'],'reviewer':'人工','note':'核對參數名稱','item':edited}))
    after=ok(client.get(f'{BASE}/documents/{doc["id"]}/items'))['items'][0]
    assert after['context_evidence']==[ref] and after['external_source']==first['external_source']
    context,_=ae._standard_context(doc,after,500)
    assert context[0]['block_id']==ref['block_id']


def test_documented_prompt_and_schema_match_packaged_contract():
    from pathlib import Path
    from spec_check.external_extraction import PROMPT_PATH
    root=Path(__file__).resolve().parents[1]
    assert (root/'docs/CHATGPT_STANDARD_EXTRACTION_PROMPT.md').read_bytes()==PROMPT_PATH.read_bytes()
    assert json.loads((root/'examples/external-extraction/result.schema.json').read_text(encoding='utf-8'))==result_schema()


def test_staged_package_survives_database_reopen(client):
    from spec_check.external_extraction import ExternalExtraction
    from spec_check.storage import Store
    doc=upload(client);package=create(client,doc);_,tasks=bundle(client,doc,package)
    package=stage(client,doc,package,[result(t) for t in tasks])['package']
    original=client.app.state.workflow
    original.store=Store(original.root)
    reopened=ExternalExtraction(original)
    assert reopened.view(reopened.package(doc['id'],package['id']))['received_count']==package['batch_count']
    assert reopened.activate(doc['id'],package['id'],values(package,acknowledge_warnings=True))['document']['item_count']>0


def test_portable_external_smoke_uses_distinct_source_and_preserves_prior_document(client, monkeypatch):
    from scripts import smoke_windows_portable as smoke
    previous = upload(client, '額定電壓必須為 48 V。\n')

    def request(_base, path, payload=None, *, method=None, raw=False, content_type='application/json', **_kwargs):
        method = method or ('POST' if payload is not None else 'GET')
        content = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        response = client.request(method, path, content=content, headers={'Content-Type':content_type})
        assert response.status_code == 200, response.text
        return response.content if raw else response.json()

    monkeypatch.setattr(smoke, 'request', request)
    external = smoke.run_external_smoke('http://127.0.0.1:8765')
    assert external['document_id'] != previous['id']
    assert ok(client.get(f'{BASE}/documents/{previous["id"]}')) == previous
    assert external['item_count'] >= 1
