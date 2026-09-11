"""Single-owner process supervisor for the OKXQuant Gateway worker."""
from __future__ import annotations
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / "data" / "okxquant_gateway.pid"
LOG_FILE = ROOT / "logs" / "okxquant_gateway_supervisor.log"
_stop = threading.Event()
_thread: threading.Thread | None = None
_owned_pid = 0


def _alive(pid: int) -> bool:
    if pid <= 0: return False
    try: os.kill(pid, 0); return True
    except OSError: return False


def _is_gateway_worker(pid: int) -> bool:
    if not _alive(pid): return False
    try:
        cmdline=(Path("/proc")/str(pid)/"cmdline").read_bytes().replace(b"\0",b" ").decode(errors="replace")
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


def ensure_worker() -> int:
    global _owned_pid
    pid=current_pid()
    if pid: return pid
    LOG_FILE.parent.mkdir(parents=True,exist_ok=True)
    with LOG_FILE.open("a",encoding="utf-8") as log:
        process=subprocess.Popen([sys.executable,"-m","okxquant_gateway.worker"],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    PID_FILE.write_text(str(process.pid),encoding="utf-8"); os.chmod(PID_FILE,0o600); _owned_pid=process.pid
    return process.pid


def _run() -> None:
    while not _stop.is_set(): ensure_worker(); _stop.wait(10)


def start_supervisor() -> None:
    # The worker requires POSIX flock and /proc ownership checks. A Windows
    # control plane can serve read-only APIs, but must not spawn a crash loop.
    if (os.getenv("OKXQUANT_TESTING") == "1" or sys.platform == "win32"
            or os.getenv("OKXQUANT_GATEWAY_AUTOSTART", "1") != "1"):
        return
    global _thread
    if _thread and _thread.is_alive(): return
    _stop.clear(); ensure_worker(); _thread=threading.Thread(target=_run,name="okxquant-gateway-supervisor",daemon=True); _thread.start()


def stop_supervisor() -> None:
    global _owned_pid
    _stop.set()
    pid=_owned_pid
    if pid and _is_gateway_worker(pid):
        try: os.kill(pid,signal.SIGTERM)
        except OSError: pass
        deadline=time.time()+8
        while _alive(pid) and time.time()<deadline: time.sleep(.1)
    if pid and not _alive(pid):
        try: PID_FILE.unlink(missing_ok=True)
        except OSError: pass
    _owned_pid=0
