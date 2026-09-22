"""Bounded public HTTPS downloads, with DNS addresses pinned to the TLS socket."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from email.message import Message
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from .ingestion import MAX_FILE_BYTES, SUPPORTED_EXTENSIONS


@dataclass
class Download:
    data: bytes
    filename: str
    source: dict


def parse_url(value):
    if not isinstance(value, str) or not value or len(value) > 8192 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError('請輸入完整的公開 HTTPS 檔案下載連結。')
    try:
        url = urlsplit(value)
        if url.scheme != 'https' or not url.hostname or url.username is not None or url.password is not None or url.port not in (None, 443):
            raise ValueError()
        host = url.hostname.encode('idna').decode('ascii')
        if '%' in host or '\\' in value:
            raise ValueError()
    except (ValueError, UnicodeError) as exc:
        raise ValueError('僅支援 HTTPS、443 連接埠及不含帳號密碼的公開下載連結。') from exc
    return url, host


def public_addresses(host):
    addresses = list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses:
        raise ValueError('無法解析下載主機。')
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast or (isinstance(ip, ipaddress.IPv6Address) and
                (ip.ipv4_mapped or ip.sixtofour or ip.teredo or ip in ipaddress.ip_network('64:ff9b::/96'))):
            raise ValueError('連結必須指向公開網路，不能使用本機、內網或特殊用途位址。')
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Do not resolve the hostname again: redirects and DNS rebinding cannot reach LAN services.
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def safe_source(value):
    url, host = parse_url(value)
    netloc = '[' + host + ']' if ':' in host else host
    return urlunsplit(('https', netloc, url.path or '/', '', ''))


def download(value, *, result=False, filename=''):
    """No cookies, credentials, environment proxy, JavaScript or browser session."""
    max_bytes = 16 * 1024 * 1024 if result else MAX_FILE_BYTES
    allowed = {'.json'} if result else SUPPORTED_EXTENSIONS
    if not isinstance(filename, str) or len(filename) > 240:
        raise ValueError('下載檔名最多 240 字。')
    original, current, deadline = value, value, time.monotonic() + 60
    try:
        for redirects in range(4):
            url, host = parse_url(current)
            addresses = public_addresses(host)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            connection = PinnedHTTPS(host, addresses[0], min(10, remaining))
            try:
                target = quote(url.path or '/', safe='/%:@!$&\'()*+,;=-._~')
                if url.query:
                    target += '?' + quote(url.query, safe='/%?:@!$&\'()*+,;=-._~')
                connection.request('GET', target, headers={'User-Agent': 'LocalAIforSPECheck/file-import',
                                                           'Accept-Encoding': 'identity'})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader('Location')
                    if not location or redirects == 3:
                        raise ValueError('下載連結重新導向過多或缺少目標，請使用直接下載網址。')
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    raise ValueError(f'下載站台回傳 HTTP {response.status}；請檢查連結是否需登入或已失效。')
                mime = response.getheader('Content-Type', '').split(';')[0].strip().lower()
                if mime in {'text/html', 'application/xhtml+xml'}:
                    raise ValueError('連結回傳網頁，並非檔案；請改用直接下載網址或下載成果後選擇檔案匯入。')
                if response.getheader('Content-Encoding', 'identity').lower() not in {'', 'identity'}:
                    raise ValueError('下載站台回傳壓縮傳輸，請改用未壓縮的直接檔案連結。')
                length = response.getheader('Content-Length')
                if length is not None and (not length.isdigit() or int(length) > max_bytes):
                    raise ValueError('檔案大小無效或超過上限（規範 30 MB，成果 16 MB）。')
                header = Message()
                header['Content-Disposition'] = response.getheader('Content-Disposition', '')
                name = filename.strip() or header.get_filename() or unquote(PurePosixPath(url.path).name)
                name = name.replace('\\', '/').split('/')[-1][:240]
                if not name or any(ord(c) < 32 for c in name) or PurePosixPath(name).suffix.lower() not in allowed:
                    raise ValueError('無法辨識檔案類型；請填寫含正確副檔名的檔名（成果須為 .json）。')
                chunks, total = [], 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    if connection.sock is not None:
                        connection.sock.settimeout(min(10, remaining))
                    chunk = response.read1(min(65536, max_bytes + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError('檔案超過上限（規範 30 MB，成果 16 MB）。')
                    chunks.append(chunk)
                raw = b''.join(chunks)
                if length is not None and total != int(length):
                    raise ValueError('檔案下載不完整，請重新下載。')
                if not raw:
                    raise ValueError('下載檔案是空白的。')
                prefix = raw[:1024].lstrip(b'\xef\xbb\xbf \r\n\t').lower()
                if prefix.startswith((b'<!doctype html', b'<html', b'<head', b'<body')):
                    raise ValueError('下載內容是網頁，請使用檔案直接下載連結。')
                return Download(raw, name, dict(url=safe_source(original), final_url=safe_source(current),
                                                download_sha256=hashlib.sha256(raw).hexdigest(), bytes=total))
            finally:
                connection.close()
    except (OSError, http.client.HTTPException) as exc:
        raise ValueError('無法下載檔案：連線、憑證或逾時問題。請在可連線的環境下載成果，再選擇檔案匯入。') from exc
    raise ValueError('下載失敗，請改用直接檔案連結。')
