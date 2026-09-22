"""Network policy and complete external-result imports, no external service required."""
import copy
import hashlib
import io
import json
import socket
from pathlib import Path

import pytest

from spec_check import link_import as links
from spec_check.external_extraction import encoded
from test_external_extraction import client, ok

ROOT = Path(__file__).resolve().parents[1]


def sample():
    return json.loads((ROOT / 'examples/external-extraction/complete.standard.json').read_text(encoding='utf-8'))


def settings(**changes):
    return dict(reviewer='外網成果驗收', model_label='ChatGPT（版本未知）', note='人工核對前匯入', **changes)


def submit(client, value, operation='preview', **changes):
    return client.post('/api/v2/library/standalone/' + operation,
                       files={'file': ('example.standard.json', encoded(value), 'application/json')},
                       data={'values': json.dumps(settings(**changes), ensure_ascii=False)})


class FakeResponse:
    def __init__(self, raw=b'public standard', status=200, **headers):
        self.status, self.headers, self.stream = status, headers, io.BytesIO(raw)

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, count):
        return self.stream.read(count)


@pytest.fixture
def network(monkeypatch):
    calls, responses = [], []

    def resolve(host, *_args, **_kwargs):
        address = '127.0.0.1' if host == 'private.example' else '93.184.216.34'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]

    class Connection:
        sock = None

        def __init__(self, host, address, timeout):
            calls.append(dict(host=host, address=address, timeout=timeout))

        def request(self, method, target, headers):
            calls[-1].update(method=method, target=target, headers=headers)

        def getresponse(self):
            return responses.pop(0)

        def close(self):
            calls[-1]['closed'] = True

    monkeypatch.setattr(links.socket, 'getaddrinfo', resolve)
    monkeypatch.setattr(links, 'PinnedHTTPS', Connection)
    return calls, responses


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://example.org/a.pdf', 'https://user:secret@example.org/a.pdf',
    'https://example.org:8765/a.pdf', 'https://example.org/\r\nprivate', 'https://[fe80::1%25eth0]/a.pdf',
    'https://example.org\\@127.0.0.1/a.pdf', '', None])
def test_reject_bad_url(url):
    with pytest.raises(ValueError):
        links.parse_url(url)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '192.168.1.1', '100.64.0.1',
    '::1', 'fc00::1', 'fe80::1', '::ffff:127.0.0.1', '2002:7f00:1::', '224.0.0.1', '64:ff9b::7f00:1'])
def test_reject_private_or_mixed_dns(monkeypatch, address):
    monkeypatch.setattr(links.socket, 'getaddrinfo', lambda *_a, **_k: [(2,1,6,'',('93.184.216.34',443)), (2,1,6,'',(address,443))])
    with pytest.raises(ValueError, match='公開網路'):
        links.public_addresses('example.org')


def test_redirect_validated_before_opening_private_socket(network):
    calls, responses = network
    responses.append(FakeResponse(status=302, Location='https://private.example/internal.pdf'))
    with pytest.raises(ValueError, match='公開網路'):
        links.download('https://public.example/start.pdf')
    assert len(calls) == 1 and calls[0]['closed']


def test_download_redirect_filename_provenance_and_no_cookies(network):
    calls, responses = network
    responses.extend([FakeResponse(status=302, Location='https://cdn.example/download?token=secret'),
        FakeResponse(raw=b'voltage 48 V', **{'Content-Disposition': "attachment; filename*=UTF-8''%E8%A6%8F%E7%AF%84.txt", 'Content-Length':'12'})])
    data = links.download('https://public.example/start.txt?secret=value#fragment')
    assert data.filename == '規範.txt' and data.data == b'voltage 48 V'
    assert data.source['url'] == 'https://public.example/start.txt'
    assert data.source['final_url'] == 'https://cdn.example/download'
    assert data.source['download_sha256'] == hashlib.sha256(data.data).hexdigest()
    assert calls[1]['address'] == '93.184.216.34' and calls[1]['target'] == '/download?token=secret'
    assert 'Cookie' not in calls[1]['headers'] and 'Authorization' not in calls[1]['headers']
    assert all(c['closed'] for c in calls)


@pytest.mark.parametrize('response', [
    FakeResponse(status=403), FakeResponse(**{'Content-Type':'text/html'}),
    FakeResponse(raw=b'  <!DOCTYPE html><html>login</html>'), FakeResponse(raw=b''),
    FakeResponse(**{'Content-Encoding':'gzip'}), FakeResponse(raw=b'short', **{'Content-Length':'100'}),
    FakeResponse(**{'Content-Length':str(31*1024*1024)})])
def test_bad_response_has_actionable_error_and_closes(network, response):
    calls, responses = network;responses.append(response)
    with pytest.raises(ValueError):
        links.download('https://example.org/a.txt')
    assert calls[0]['closed']


def test_stream_limit_without_content_length(network, monkeypatch):
    calls, responses = network;responses.append(FakeResponse(raw=b'x'*100))
    monkeypatch.setattr(links, 'MAX_FILE_BYTES', 50)
    with pytest.raises(ValueError, match='上限'):
        links.download('https://example.org/a.txt')
    assert calls[0]['closed']


def test_tls_connect_uses_validated_ip_but_original_sni(monkeypatch):
    sock = object();seen = {}
    def connect(address, timeout):
        seen['address'] = address
        return sock
    class Context:
        def wrap_socket(self, value, server_hostname):
            seen.update(sock=value, sni=server_hostname)
            return object()
    monkeypatch.setattr(links.socket, 'create_connection', connect)
    connection = links.PinnedHTTPS('example.org', '93.184.216.34', 10)
    connection._context = Context();connection.connect()
    assert seen == dict(address=('93.184.216.34',443), sock=sock, sni='example.org')


def test_library_url_import_is_standard_only_and_deduplicates(client, network):
    calls, responses = network
    responses.extend([FakeResponse(raw='必須為 48 V。'.encode()) for _ in range(2)])
    body=dict(url='https://example.org/standard.txt?token=secret')
    first=ok(client.post('/api/v2/library/link',json=body))
    second=ok(client.post('/api/v2/library/link',json=body))
    assert first['id']==second['id'] and second['reused'] and first['role']=='standard'
    assert first['import_source']['url']=='https://example.org/standard.txt'
    assert ok(client.get('/api/v2/library'))['total']==1
    assert client.post('/api/v2/products/link',json=body).status_code in {404, 405}
    audit=ok(client.get(f'/api/v2/documents/{first["id"]}/audit'))['items']
    assert {a['action'] for a in audit} >= {'link_imported','link_import_reused'}


def test_standalone_readonly_preview_atomic_import_confirm_and_dedup(client):
    value=sample();preview=ok(submit(client,value))
    assert ok(client.get('/api/v2/library'))['total']==0
    assert submit(client,value,'import',preview_sha256=preview['preview_sha256']).status_code==400
    imported=ok(submit(client,value,'import',preview_sha256=preview['preview_sha256'],acknowledge_warnings=True))
    doc=imported['document']
    assert doc['item_count']==1 and not doc['confirmed'] and doc['source_kind']=='external_transcription'
    original=client.get(f'/api/v2/documents/{doc["id"]}/original')
    assert original.content==encoded(value) and '.standard.json' in original.headers['content-disposition']
    items=ok(client.get(f'/api/v2/documents/{doc["id"]}/items'))['items']
    assert items[0]['external_source']['model_identity']=='user_reported'
    confirmed=ok(client.post(f'/api/v2/documents/{doc["id"]}/confirm',json=settings(expected_version=1,acknowledge_warnings=True)))
    repeated=ok(submit(client,value,'import',preview_sha256=preview['preview_sha256'],acknowledge_warnings=True))
    assert repeated['document']['id']==doc['id'] and repeated['document']['confirmed'] and confirmed['version']==2
    assert ok(client.get('/api/v2/library'))['total']==1


def test_partial_cannot_be_confirmed_even_in_batch(client):
    value=sample();value['coverage'].update(status='partial',description='尚缺附錄')
    preview=ok(submit(client,value));doc=ok(submit(client,value,'import',preview_sha256=preview['preview_sha256'],acknowledge_warnings=True))['document']
    response=client.post('/api/v2/library/confirm',json=settings(acknowledge_warnings=True,documents=[{'id':doc['id'],'expected_version':doc['version']}]))
    assert response.status_code==400 and '完整' in response.json()['detail']


@pytest.mark.parametrize('mutate', [
    lambda v:v['blocks'][0]['items'][0].update(quote='不存在的引文'),
    lambda v:v['blocks'].append(copy.deepcopy(v['blocks'][0])),
    lambda v:v['blocks'][0]['items'][0].update(value=48),
    lambda v:v['blocks'][0].update(location=''),
    lambda v:v['blocks'][0]['items'][0].update(context_evidence=[{'block_id':'UNKNOWN','quote':'48'}]),
    lambda v:v.update(schema_version=True),lambda v:v.update(extra='unknown'),
    lambda v:v['standard'].update(source_url='javascript:alert(1)'),lambda v:v['coverage'].update(status='done')])
def test_invalid_package_does_not_write_documents(client,mutate):
    value=sample();mutate(value)
    assert submit(client,value).status_code==400
    assert ok(client.get('/api/v2/library'))['total']==0
    assert not list((client.app.state.workflow.root/'uploads').iterdir())


def test_changed_download_cannot_be_saved_under_old_preview(client,network):
    calls,responses=network;value=sample()
    responses.append(FakeResponse(raw=encoded(value)))
    body=settings(url='https://example.org/complete.standard.json')
    preview=ok(client.post('/api/v2/library/standalone-link/preview',json=body))
    value['blocks'][0]['items'][0]['name']='內容已更新';responses.append(FakeResponse(raw=encoded(value)))
    response=client.post('/api/v2/library/standalone-link/import',json=dict(body,preview_sha256=preview['preview_sha256'],acknowledge_warnings=True))
    assert response.status_code==409 and ok(client.get('/api/v2/library'))['total']==0


def test_prompt_download_and_documentation_match(client):
    response=client.get('/api/v2/library/standalone/prompt')
    assert response.status_code==200
    assert response.content==(ROOT/'docs/CHATGPT_EXTERNAL_LINK_PROMPT.md').read_bytes()
    assert sample()['format'].encode() in response.content


def test_invalid_unicode_is_rejected_before_storage(client):
    value=sample();value['standard']['name']='\ud800'
    response=client.post('/api/v2/library/standalone/preview',
        files={'file':('bad.json',json.dumps(value).encode(),'application/json')},
        data={'values':json.dumps(settings())})
    assert response.status_code==400 and ok(client.get('/api/v2/library'))['total']==0


@pytest.mark.parametrize('line_ending', [b'\n', b'\r\n'])
def test_portable_standalone_smoke_helper_uses_real_api(client,monkeypatch,line_ending):
    from scripts import smoke_windows_portable as smoke
    def request(_base,path,payload=None,*,method=None,raw=False,content_type='application/json',**_kwargs):
        response=client.request(method or ('POST' if payload is not None else 'GET'),path,
            content=payload if isinstance(payload,bytes) else json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type':content_type})
        assert response.status_code==200,response.text
        return response.content.replace(b'\r\n',b'\n').replace(b'\n',line_ending) if raw else response.json()
    monkeypatch.setattr(smoke,'request',request)
    result=smoke.run_standalone_smoke('http://127.0.0.1:8765')
    assert result['item_count']==1
    from spec_check.storage import Store
    reopened=Store(client.app.state.workflow.root)
    assert reopened.get('v2_document',result['document_id'])['external_content_sha256']==result['content_sha256']
