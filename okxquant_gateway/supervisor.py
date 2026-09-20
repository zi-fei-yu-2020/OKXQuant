"""Single-owner process supervisor for the OKXQuant Gateway worker."""
from __future__ import annotations
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from scripts.config_lock import configuration_write

ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / "data" / "okxquant_gateway.pid"
LOG_FILE = ROOT / "logs" / "okxquant_gateway_supervisor.log"
_stop = threading.Event()
_thread: threading.Thread | None = None
_owned_pid = 0
_owned_process: subprocess.Popen | None = None
_lifecycle_lock = threading.RLock()


def _alive(pid: int) -> bool:
    if pid <= 0: return False
    try: os.kill(pid, 0); return True
    except OSError: return False


def _is_gateway_worker(pid: int) -> bool:
    if not _alive(pid): return False
    try:
        cmdline=(Path("/proc")/str(pid)/"cmdline").read_bytes().decode(errors="replace").split("\0")
        cwd=(Path("/proc")/str(pid)/"cwd").resolve()
        return "okxquant_gateway.worker" in cmdline and cwd == ROOT.resolve()
    except OSError: return False


def current_pid() -> int:
    try: pid=int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError,ValueError): return 0
    if _is_gateway_worker(pid): return pid
    try: PID_FILE.unlink(missing_ok=True)
    except OSError: pass
    return 0


def _worker_lock_held() -> bool:
    import fcntl
    path = PID_FILE.parent / ".okxquant_gateway.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


def ensure_worker() -> int:
    global _owned_pid, _owned_process
    with configuration_write(PID_FILE.with_name(".gateway-supervisor")):
        if _owned_process is not None:
            _owned_process.poll()  # reap an exited owned child
        pid = current_pid()
        if pid:
            return pid
        if _worker_lock_held():
            return 0  # worker startup/shutdown owns the lock; never race it
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "okxquant_gateway.worker"], cwd=ROOT,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        _owned_process = process
        _owned_pid = process.pid
        # PID is authoritative only after the worker has acquired its flock.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            pid = current_pid()
            if pid:
                return pid
            if process.poll() is not None:
                break
            time.sleep(.02)
        return 0


def _run() -> None:
    while not _stop.is_set():
        try:
            ensure_worker()
        except Exception:
            # A transient spawn/filesystem error must not kill supervision.
            pass
        _stop.wait(10)


def start_supervisor() -> None:
    # The worker requires POSIX flock and /proc ownership checks. A Windows
    # control plane can serve read-only APIs, but must not spawn a crash loop.
    if (os.getenv("OKXQUANT_TESTING") == "1" or sys.platform == "win32"
            or os.getenv("OKXQUANT_GATEWAY_AUTOSTART", "1") != "1"):
        return
    global _thread
    with _lifecycle_lock:
        if _thread and _thread.is_alive(): return
        _stop.clear()
        try:
            ensure_worker()
        except Exception:
            pass  # the background loop retries transient startup failures
        _thread=threading.Thread(target=_run,name="okxquant-gateway-supervisor",daemon=True)
        _thread.start()


def stop_supervisor() -> None:
    global _owned_pid
    _stop.set()
    if _thread and _thread is not threading.current_thread():
        _thread.join(timeout=12)
    pid=_owned_pid
    if pid and _is_gateway_worker(pid):
        try: os.kill(pid,signal.SIGTERM)
        except OSError: pass
        deadline=time.time()+8
        while _alive(pid) and time.time()<deadline:
            if _owned_process is not None and _owned_process.poll() is not None: break
            time.sleep(.1)
    if pid and not _alive(pid):
        try:
            if PID_FILE.read_text(encoding="utf-8").strip() == str(pid):
                PID_FILE.unlink(missing_ok=True)
        except OSError: pass
    _owned_pid=0
