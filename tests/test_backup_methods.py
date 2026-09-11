"""Backup method configuration and local backend tests."""
from __future__ import annotations
import sqlite3
import tempfile
import unittest
from pathlib import Path

import okxquant_backend.backup_store as store
import scripts.backup_runtime as runtime


class BackupMethodTests(unittest.TestCase):
    def test_open_source_defaults_keep_local_enabled_and_cloud_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            original = store.CONFIG_FILE
            store.CONFIG_FILE = Path(td) / "backup.json"
            try:
                methods = store.load_backup_methods()
                self.assertFalse(methods["baidu"]["enabled"])
                self.assertTrue(methods["local"]["enabled"])
                self.assertFalse(methods["sqlite"]["enabled"])
            finally:
                store.CONFIG_FILE = original

    def test_sqlite_hot_backup_is_consistent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data, backups = root / "data", root / "backups" / "sqlite"
            data.mkdir(parents=True)
            source = data / "sample.db"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE x(value TEXT)")
            connection.execute("INSERT INTO x VALUES ('ok')")
            connection.commit(); connection.close()
            old_root, old_dir = runtime.ROOT, runtime.SQLITE_DIR
            runtime.ROOT, runtime.SQLITE_DIR = root, backups
            try:
                created = runtime.sqlite_hot_backups("20260901_020000", 2)
                check = sqlite3.connect(created[0])
                self.assertEqual(check.execute("SELECT value FROM x").fetchone()[0], "ok")
                check.close()
            finally:
                runtime.ROOT, runtime.SQLITE_DIR = old_root, old_dir


if __name__ == "__main__":
    unittest.main()
