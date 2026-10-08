"""Small read-only projections cached by the exact source-file generation.

No last-good fallback on deletion/corruption. Atomic replacements, even with the
same mtime/size, invalidate via inode/ctime. Readers must not mutate projections.
"""
import json
import os
from pathlib import Path
import threading


def _signature(stat):
    return (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)


class VersionedJsonProjection:
    def __init__(self, project):
        self.project=project
        self.lock=threading.Lock()
        self.key=None
        self.value=None

    def read(self, path):
        path=Path(path).absolute()
        with self.lock:
            try:
                key=(str(path),_signature(path.stat()))
                if key==self.key:return self.value
                # A writer may atomically replace the path between stat/open.
                # Retry once, never publish an older generation as current.
                for _ in range(2):
                    with path.open('r',encoding='utf-8') as stream:
                        before=_signature(os.fstat(stream.fileno()))
                        value=json.load(stream)
                        after=_signature(os.fstat(stream.fileno()))
                    if before==after==_signature(path.stat()):
                        projected=self.project(value)
                        self.key=(str(path),after)
                        self.value=projected
                        return projected
                raise OSError('JSON source changed while reading')
            except Exception:
                self.key=None
                self.value=None
                raise
