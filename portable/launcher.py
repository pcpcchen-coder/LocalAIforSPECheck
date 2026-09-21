"""Portable supervisor. Uses only the bundled Python and local child services."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

HOST = "127.0.0.1"
APP_ID = "LocalAIforSPECheck"
DEFAULT_MODEL = "Qwen3.5-2B-Q6_K.gguf"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def http_json(port: int, path: str, key: str = "") -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(f"http://{HOST}:{port}{path}")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with opener.open(request, timeout=2) as response:
            value = json.loads(response.read(1024 * 1024))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((HOST, port))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("找不到可用的本機連接埠。")


class FolderLock:
    """OS-owned lock automatically released on process death; stale PID is harmless."""
    def __init__(self, path: Path):
        self.file = path.open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        self.held = False

    def acquire(self) -> bool:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.held = True
            return True
        except OSError:
            return False

    def close(self) -> None:
        if self.held:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()


class ChildJob:
    """Kill supervised children even when the Windows console is closed abruptly."""
    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IOCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def add(self, child: subprocess.Popen) -> None:
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, int(child._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def model_path(root: Path) -> Path:
    name = read_json(root / "data" / "portable-config.json").get("model", DEFAULT_MODEL)
    if not isinstance(name, str) or Path(name).name != name or not name.lower().endswith(".gguf"):
        raise ValueError("模型設定無效。請使用 Choose_model.bat 重新選擇。")
    path = (root / "models" / name).resolve()
    if path.parent != (root / "models").resolve() or not path.is_file():
        raise ValueError("找不到模型。請完整解壓 ZIP，或用 Choose_model.bat 選擇 models 內的檔案。")
    with path.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise ValueError("模型不是完整 GGUF 檔案，請重新取得模型。")
    return path


def choose_model(root: Path) -> int:
    candidates = sorted(p for p in (root / "models").glob("*.gguf") if p.is_file())
    if not candidates:
        print("找不到模型。請將 GGUF 檔案複製到 models 資料夾後再執行。")
        return 1
    print("請選擇下次啟動使用的模型：")
    for index, path in enumerate(candidates, 1):
        print(f"  {index}. {path.name} ({path.stat().st_size / 1024**3:.2f} GiB)")
    answer = input("輸入編號（直接按 Enter 取消）：").strip()
    if not answer:
        return 0
    if not answer.isdecimal() or not 1 <= int(answer) <= len(candidates):
        print("編號無效，設定未變更。")
        return 1
    selected = candidates[int(answer) - 1]
    write_json(root / "data" / "portable-config.json", {"model": selected.name})
    print(f"已選擇 {selected.name}。若程式正在執行，請先 Stop.bat，再 Start.bat。")
    return 0


def stop_requested(root: Path, nonce: str) -> bool:
    return read_json(root / "data" / "portable-stop.json").get("nonce") == nonce


def stop(root: Path) -> int:
    lock = FolderLock(root / "data" / "portable.lock")
    try:
        if lock.acquire():
            print("目前沒有執行中的可攜版服務。")
            return 0
    finally:
        lock.close()
    session = read_json(root / "data" / "portable-session.json")
    nonce = session.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        print("程式仍在啟動，請稍後再按 Stop.bat。")
        return 1
    write_json(root / "data" / "portable-stop.json", {"nonce": nonce})
    print("正在停止服務；進行中的比對會在下次啟動時標示中斷，可繼續。", flush=True)
    for _ in range(120):
        probe = FolderLock(root / "data" / "portable.lock")
        try:
            if probe.acquire():
                print("已停止，可以關閉瀏覽器或搬移資料夾。")
                return 0
        finally:
            probe.close()
        time.sleep(0.25)
    print("服務尚未停止，請回到啟動視窗按 Ctrl+C，並查看 logs。")
    return 1


def wait_ready(root: Path, nonce: str, children: list, port: int, path: str, key: str = "", timeout: int = 300) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop_requested(root, nonce):
            raise KeyboardInterrupt
        for child in children:
            if child.poll() is not None:
                raise RuntimeError(f"本機服務提前結束（代碼 {child.returncode}），請查看 logs。")
        value = http_json(port, path, key)
        if value.get("status") == "ok":
            return value
        time.sleep(0.5)
    raise RuntimeError("本機服務啟動逾時。請檢查可用記憶體，或查看 logs。")


def launch(root: Path, no_browser: bool) -> int:
    lock = FolderLock(root / "data" / "portable.lock")
    if not lock.acquire():
        lock.close()
        session = read_json(root / "data" / "portable-session.json")
        port = session.get("app_port")
        health = http_json(port, "/api/health") if isinstance(port, int) else {}
        if health.get("app") == APP_ID and health.get("instance_id") == session.get("nonce"):
            url = f"http://{HOST}:{port}"
            print(f"程式已開啟：{url}")
            if not no_browser:
                webbrowser.open(url)
        else:
            print("程式正在啟動，請等候原本的啟動視窗。")
        return 0
    children, handles = [], []
    job = None
    nonce = secrets.token_hex(24)
    state_path = root / "data" / "portable-session.json"
    key_path = root / "data" / "portable-api-key.txt"
    try:
        model = model_path(root)
        engine = root / "engine" / "llama-server.exe"
        python = root / "runtime" / "python.exe"
        if not engine.is_file() or not python.is_file():
            raise ValueError("缺少可攜式執行檔。請下載完整 Portable ZIP 並先解壓縮。")
        app_port, model_port = free_port(8765), free_port(1235)
        while model_port == app_port:
            model_port = free_port(0)
        key = secrets.token_urlsafe(32)
        key_path.write_text(key + "\n", encoding="utf-8")
        state = dict(pid=os.getpid(), nonce=nonce, app_port=app_port, model_port=model_port,
                     model=model.name, status="starting")
        write_json(state_path, state)
        os.environ["SPEC_CHECK_DATA"] = str(root / "data")
        os.environ["SPEC_CHECK_PORTABLE"] = "1"
        os.environ["SPEC_CHECK_INSTANCE"] = nonce
        os.environ["PYTHONUTF8"] = "1"
        print("正在讀取模型指紋，以記錄本次比對使用的模型……", flush=True)
        with model.open("rb") as model_stream:
            os.environ["SPEC_CHECK_MODEL_SHA256"] = hashlib.file_digest(model_stream, "sha256").hexdigest()
        # Store settings before the web child starts; the secret is excluded from all exports.
        from spec_check.storage import Store
        from spec_check.engine import DEFAULT_SETTINGS
        store = Store(root / "data")
        settings = dict(DEFAULT_SETTINGS, base_url=f"http://{HOST}:{model_port}/v1",
                        model=model.stem, api_key=key, timeout=600, temperature=0)
        os.environ["SPEC_CHECK_PORTABLE_BASE_URL"] = settings['base_url']
        os.environ["SPEC_CHECK_PORTABLE_MODEL"] = settings['model']
        store.put("settings", {"id": "local", "values": settings})
        job = ChildJob()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        def spawn(command: list[str], log_name: str) -> subprocess.Popen:
            log_path = root / "logs" / log_name
            if log_path.exists():
                log_path.replace(log_path.with_suffix(".previous.log"))
            handle = log_path.open("wb")
            handles.append(handle)
            child = subprocess.Popen(command, cwd=root / "app", stdin=subprocess.DEVNULL,
                                     stdout=handle, stderr=subprocess.STDOUT, creationflags=flags)
            children.append(child)
            job.add(child)
            return child

        print(f"正在載入本機模型：{model.name}", flush=True)
        print("首次載入可能需要數分鐘。請保留此視窗；Ctrl+C 或 Stop.bat 可停止。", flush=True)
        # Upstream's API-key reader uses a narrow Windows file stream. Relative
        # ASCII paths preserve support for a bundle inside a Chinese user folder.
        model_argument = str(Path("..") / "models" / model.name)
        key_argument = str(Path("..") / "data" / key_path.name)
        model_child = spawn([str(engine), "--model", model_argument, "--host", HOST,
                            "--port", str(model_port), "--alias", model.stem,
                            "--ctx-size", "16384", "--parallel", "1", "--n-gpu-layers", "0",
                            "--batch-size", "256", "--ubatch-size", "128", "--reasoning", "off",
                            "--api-key-file", key_argument], "model.log")
        state["model_pid"] = model_child.pid
        write_json(state_path, state)
        wait_ready(root, nonce, children, model_port, "/health", key)
        models = http_json(model_port, "/v1/models", key).get("data", [])
        if not any(item.get("id") == model.stem for item in models):
            raise RuntimeError("無法確認此資料夾的模型服務，請重新啟動。")
        app_child = spawn([str(python), "-m", "uvicorn", "spec_check.app:app", "--host", HOST,
                          "--port", str(app_port), "--workers", "1", "--timeout-graceful-shutdown", "5"], "app.log")
        state["app_pid"] = app_child.pid
        write_json(state_path, state)
        health = wait_ready(root, nonce, children, app_port, "/api/health", timeout=60)
        if health.get("app") != APP_ID or health.get("instance_id") != nonce:
            raise RuntimeError("啟動位址已被其他程式使用，請重新啟動。")
        state["status"] = "ready"
        write_json(state_path, state)
        url = f"http://{HOST}:{app_port}"
        print(f"已就緒：{url}\n若瀏覽器未自動開啟，請複製上方網址。", flush=True)
        if not no_browser:
            webbrowser.open(url)
        while not stop_requested(root, nonce):
            if any(child.poll() is not None for child in children):
                raise RuntimeError("本機服務意外停止，請查看 logs 後重新啟動。")
            time.sleep(0.4)
        return 0
    except KeyboardInterrupt:
        print("正在停止本機服務……", flush=True)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"無法啟動：{exc}", flush=True)
        print(f"請查看 {root / 'logs'}，並參考 docs/WINDOWS_PORTABLE.md。", flush=True)
        return 1
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in reversed(children):
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        if job:
            job.close()
        for handle in handles:
            handle.close()
        for path in (state_path, key_path, root / "data" / "portable-stop.json"):
            path.unlink(missing_ok=True)
        lock.close()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="LocalAIforSPECheck Windows Portable")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--no-browser", action="store_true")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--stop", action="store_true")
    actions.add_argument("--choose-model", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        (root / "data").mkdir(exist_ok=True)
        (root / "logs").mkdir(exist_ok=True)
    except OSError:
        print("此資料夾無法寫入。請將完整資料夾搬到您可寫入的位置，再按 Start.bat。")
        return 1
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        if args.stop:
            return stop(root)
        if args.choose_model:
            return choose_model(root)
        return launch(root, args.no_browser)
    except (OSError, ValueError) as exc:
        print(f"無法完成操作：{exc}。請檢查資料夾是否可寫入。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
