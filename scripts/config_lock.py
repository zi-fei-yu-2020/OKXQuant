"""Reentrant cross-process configuration lock, separate from trading locks."""
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time

_registry_lock = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def configuration_write(path, timeout=10):
    key = str(Path(path).resolve())
    with _registry_lock:
        lock = _locks.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=timeout):
        raise TimeoutError('Configuration is being updated; retry the save')
    depths = getattr(_local, 'depths', {})
    _local.depths = depths
    handle = None
    acquired = False
    try:
        if not depths.get(key):
            target = Path(key + '.lock')
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = target.open('a+b')
            os.chmod(target, 0o600)
            if os.name == 'nt' and target.stat().st_size == 0:
                handle.write(b'0'); handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, PermissionError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Configuration is being updated; retry the save') from None
                    time.sleep(.02)
        depths[key] = depths.get(key, 0) + 1
        try:
            yield
        finally:
            depths[key] -= 1
            if not depths[key]: depths.pop(key)
    finally:
        if handle:
            if acquired:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
        lock.release()
