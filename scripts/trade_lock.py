"""One reentrant process/thread-safe writer gate, independent of HTTP read admission."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import threading
import time

PATH = Path(__file__).resolve().parents[1] / 'data' / '.position-writer.lock'
_local = threading.local()
_thread_lock = threading.RLock()

@contextmanager
def writer(timeout=60, *, account=None, inst_id=None, side=None):
    import fcntl
    key = '|'.join(str(v or '') for v in (account, inst_id, side))
    lock_path = PATH if not key else PATH.with_name(PATH.name + '.' + hashlib.sha256(key.encode()).hexdigest()[:20])
    if not _thread_lock.acquire(timeout=timeout):
        raise TimeoutError('Position writer busy; no order sent')
    handle = None
    try:
        depth = getattr(_local, 'depth', 0)
        if not depth:
            PATH.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open('a+')
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Position writer busy; no order sent')
                    time.sleep(.05)
            _local.handle = handle
        _local.depth = depth + 1
        try:
            yield
        finally:
            _local.depth -= 1
    finally:
        if handle is not None:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
            _local.handle = None
        _thread_lock.release()

@contextmanager
def inference_window():
    """Release only the outer portfolio lock while the model runs; reacquire before use."""
    import fcntl
    if getattr(_local, 'depth', 0) != 1:
        raise RuntimeError('Inference window requires exactly one portfolio writer lock')
    handle = _local.handle
    fcntl.flock(handle, fcntl.LOCK_UN)
    _local.depth = 0
    _thread_lock.release()
    try:
        yield
    finally:
        _thread_lock.acquire()
        fcntl.flock(handle, fcntl.LOCK_EX)
        _local.depth = 1
        _local.handle = handle

def serialized(fn):
    from functools import wraps
    @wraps(fn)
    def call(*args, **kwargs):
        with writer():
            return fn(*args, **kwargs)
    return call
