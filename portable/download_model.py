#!/usr/bin/env python3
"""Download or import the independently distributed, pinned starter GGUF.

Uses only the embedded Python standard library. No product/standard files are
read or uploaded. Existing models and the user's model selection are preserved.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request


CHUNK_SIZE = 1024 * 1024
MODEL_LINK = "https://github.com/pcpcchen-coder/LocalAIforSPECheck/blob/main/models/README.md"


def validate_https(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("模型下載網址必須是 HTTPS，且不能包含登入資訊。")


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_model_asset(root: Path) -> dict:
    lock_path = root / "build-lock.json"
    if not lock_path.is_file():
        lock_path = root / "portable" / "build-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1:
        raise ValueError("模型下載設定版本不支援。")
    asset = lock["model"]
    name = asset.get("filename")
    if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.gguf", name)
            or name.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
        raise ValueError("模型檔名無效。")
    if not re.fullmatch(r"[0-9a-f]{64}", asset.get("sha256", "")):
        raise ValueError("模型缺少固定 SHA-256 校驗碼。")
    if type(asset.get("size")) is not int or asset["size"] < 4:
        raise ValueError("模型缺少正確檔案大小。")
    validate_https(asset["url"])
    return asset


def verify_model(path: Path, asset: dict) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("模型必須是一般檔案，不能是連結或資料夾。")
    if path.stat().st_size != asset["size"]:
        raise ValueError("模型大小與固定版本不符。")
    with path.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise ValueError("檔案不是 GGUF 模型。")
        stream.seek(0)
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != asset["sha256"]:
        raise ValueError("模型 SHA-256 校驗失敗。")


@contextmanager
def download_lock(path: Path):
    """OS-owned lock is released even if the user closes the console."""
    if path.is_symlink():
        raise ValueError("下載鎖定檔不能是連結。")
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise RuntimeError("另一個模型下載正在執行，請等該視窗完成。") from None
            else:
                import fcntl
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise RuntimeError("另一個模型下載正在執行，請等該視窗完成。") from None
            acquired = True
            yield
        finally:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def publish_model(partial: Path, destination: Path) -> None:
    """Atomically publish a complete file; never overwrite an existing model."""
    if os.name == "nt":
        # Windows rename is atomic in this directory and fails if target exists.
        os.rename(partial, destination)
    else:
        # POSIX rename overwrites; an atomic hard link preserves no-overwrite.
        os.link(partial, destination)
        partial.unlink()


def acquire_model(root: Path, *, source: Path | None = None, progress=print) -> Path:
    root = root.resolve()
    asset = read_model_asset(root)
    models = root / "models"
    if models.is_symlink():
        raise ValueError("models 資料夾不能是連結。請使用解壓縮資料夾內的 models。")
    models.mkdir(parents=True, exist_ok=True)
    destination = models / asset["filename"]
    partial = models / (asset["filename"] + ".part")
    with download_lock(models / ".download.lock"):
        if destination.exists() or destination.is_symlink():
            try:
                progress("正在校驗既有模型；不需重新下載……")
                verify_model(destination, asset)
            except ValueError as exc:
                raise ValueError(f"既有模型未通過校驗，檔案已保留。請先移出或改名後重試。原因：{exc}") from None
            progress("模型校驗通過；既有模型與自訂選擇均已保留。")
            return destination
        if partial.is_symlink() or (partial.exists() and not partial.is_file()):
            raise ValueError("暫存模型路徑不是一般檔案，請先移出後重試。")
        if source is not None and source.resolve() == partial.resolve():
            raise ValueError("不能把下載暫存 .part 檔當成匯入來源。")
        progress(f"模型：{asset['filename']}；大小：{asset['size'] / 1024**3:.2f} GiB")
        progress("匯入本機模型……" if source is not None else "正在下載模型；只下載公開模型，不會傳送您的文件。")
        received, started, last_report = 0, time.monotonic(), 0.0
        try:
            if source is not None:
                if source.is_symlink() or not source.is_file():
                    raise ValueError("匯入來源必須是一般 GGUF 檔案。")
                input_stream = source.open("rb")
            else:
                request = urllib.request.Request(asset["url"], headers={
                    "User-Agent": "LocalAIforSPECheck-model-downloader/1",
                    "Accept-Encoding": "identity",
                })
                input_stream = urllib.request.build_opener(HTTPSRedirect()).open(request, timeout=120)
            with input_stream as incoming:
                if source is None:
                    validate_https(incoming.url)
                # A failed earlier attempt is restarted from zero, never appended.
                with partial.open("wb") as output:
                    while block := incoming.read(CHUNK_SIZE):
                        received += len(block)
                        if received > asset["size"]:
                            raise ValueError("收到的模型超過固定版本大小，已停止。")
                        output.write(block)
                        now = time.monotonic()
                        if now - last_report >= 1 or received == asset["size"]:
                            rate = received / max(now - started, .001) / 1024**2
                            progress(f"{received / asset['size'] * 100:5.1f}%  {received / 1024**2:.1f} / {asset['size'] / 1024**2:.1f} MiB  ({rate:.1f} MiB/s)")
                            last_report = now
                    output.flush()
                    os.fsync(output.fileno())
            progress("傳輸完成，正在校驗檔案大小、GGUF 格式與 SHA-256……")
            verify_model(partial, asset)
            publish_model(partial, destination)
        finally:
            # An interrupted transfer is never exposed with the .gguf suffix.
            if partial.is_file() and not partial.is_symlink():
                partial.unlink()
        progress("模型已就緒。請執行 Start.bat；若要切換模型，使用 Choose_model.bat。")
        return destination


def main() -> int:
    # Isolated Python (-I) ignores PYTHONUTF8/PYTHONIOENCODING. Make batch and
    # redirected console output deterministic even on non-Chinese Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source", type=Path, help="Import a manually downloaded pinned model, without network access")
    args = parser.parse_args()
    try:
        acquire_model(args.root, source=args.source, progress=lambda message: print(message, flush=True))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"\n模型尚未就緒：{exc}", file=sys.stderr, flush=True)
        print(f"可重新執行 Download_model.bat，或依模型頁面手動下載並複製至 models：\n{MODEL_LINK}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("\n已取消；既有模型及設定未變更。", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
