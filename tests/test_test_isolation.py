"""Regression coverage for offline tests and explicit background lifecycles."""
from __future__ import annotations

import os
import io
import subprocess
from contextlib import redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dashboard.app as dashboard
import okxquant_gateway.supervisor as supervisor
from scripts import run_tests


class TestIsolationTests(unittest.TestCase):
    def test_dashboard_import_does_not_start_background_thread(self):
        self.assertIsNone(dashboard._BG_WORKER_THREAD)
        self.assertFalse(dashboard._BG_WORKER_RUNNING)

    def test_test_mode_disables_background_startup(self):
        with patch.dict(os.environ, {"OKXQUANT_TESTING": "1"}), \
             patch.object(dashboard.threading, "Thread") as thread, \
             patch.object(supervisor, "ensure_worker") as worker:
            dashboard.start_dashboard_background_worker()
            supervisor.start_supervisor()
            thread.assert_not_called()
            worker.assert_not_called()

    def test_dashboard_production_startup_is_preserved(self):
        with patch.dict(os.environ, {"OKXQUANT_TESTING": "0"}), \
             patch.object(dashboard, "_BG_WORKER_THREAD", None), \
             patch.object(dashboard, "_BG_WORKER_RUNNING", False), \
             patch.object(dashboard.threading, "Thread") as thread:
            dashboard.start_dashboard_background_worker()
            thread.return_value.start.assert_called_once()

    def test_gateway_production_startup_is_preserved(self):
        with patch.dict(os.environ, {"OKXQUANT_TESTING": "0"}), \
             patch.object(supervisor, "_thread", None), \
             patch.object(supervisor, "ensure_worker") as worker, \
             patch.object(supervisor.threading, "Thread") as thread:
            supervisor.start_supervisor()
            worker.assert_called_once()
            thread.return_value.start.assert_called_once()

    def test_environment_does_not_inherit_credentials_or_config_paths(self):
        with patch.dict(os.environ, {
            "OKX_LIVE_API_KEY": "not-a-real-key",
            "LLM_API_KEY": "not-a-real-key",
            "CUSTOM_BACKUP_PASSWORD": "not-a-real-key",
            "OKXQUANT_ENV_FILE": "/live/config/.env",
            "OKXQUANT_GATEWAY_DB": "/live/data/gateway.db",
        }):
            env = run_tests.test_environment(Path("/sandbox"))
        for name in ("OKX_LIVE_API_KEY", "LLM_API_KEY", "CUSTOM_BACKUP_PASSWORD", "OKXQUANT_ENV_FILE", "OKXQUANT_GATEWAY_DB"):
            self.assertNotIn(name, env)
        self.assertEqual(env["HOME"], "/sandbox/home")
        self.assertEqual(env["OKXQUANT_OKX_ENV"], "demo")
        self.assertEqual(env["OKXQUANT_TESTING"], "1")

    def test_source_snapshot_excludes_runtime_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()
            for name in run_tests.SOURCE_DIRS:
                directory = root / name
                directory.mkdir()
                (directory / "source.py").write_text("# fixture", encoding="utf-8")
            for name in ("README.md", "STANDALONE.md", "requirements.txt", "env.example"):
                (root / name).write_text("fixture", encoding="utf-8")
            (root / ".env").write_text("SECRET=private", encoding="utf-8")
            (root / "data").mkdir()
            (root / "data" / "private.db").write_text("private", encoding="utf-8")
            (root / "okxquant_frontend" / "node_modules").mkdir()
            snapshot = Path(temporary) / "snapshot"
            snapshot.mkdir()
            with patch.object(run_tests, "ROOT", root):
                run_tests.copy_sources(snapshot)
            self.assertFalse((snapshot / ".env").exists())
            self.assertEqual(list((snapshot / "data").iterdir()), [])
            self.assertFalse((snapshot / "okxquant_frontend" / "node_modules").exists())
            self.assertTrue((snapshot / "scripts" / "source.py").exists())

    def test_asgi_lifespans_do_not_start_workers_in_test_mode(self):
        from fastapi.testclient import TestClient
        import okxquant_backend.app as backend
        with patch.dict(os.environ, {"OKXQUANT_TESTING": "1"}), \
             patch.object(dashboard.threading, "Thread") as thread, \
             patch.object(supervisor, "ensure_worker") as worker, \
             patch.object(backend.admin_auth, "initialize_from_legacy"):
            with TestClient(backend.app):
                pass
            with TestClient(dashboard.app):
                pass
            thread.assert_not_called()
            worker.assert_not_called()

    def test_caught_external_call_still_fails_the_test_command(self):
        class CatchesProcessError(unittest.TestCase):
            def runTest(self):
                try:
                    subprocess.run(["never-execute-this-command"])
                except AssertionError:
                    pass

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".okxquant-test-sandbox").touch()
            suite = unittest.TestSuite([CatchesProcessError()])
            output = io.StringIO()
            with patch.object(run_tests, "ROOT", root), \
                 patch.dict(os.environ, {"OKXQUANT_TEST_ROOT": str(root), "OKXQUANT_TESTING": "1"}), \
                 patch.object(unittest.defaultTestLoader, "discover", return_value=suite), \
                 redirect_stderr(output):
                code = run_tests.run_isolated("test_*.py", 1)
            self.assertEqual(code, 1)
            self.assertIn("Blocked 1 unmocked external operation", output.getvalue())

    def test_internal_mode_refuses_non_sandbox_checkout(self):
        with patch.dict(os.environ, {"OKXQUANT_TEST_ROOT": "/not-this-checkout"}):
            with self.assertRaises(RuntimeError):
                run_tests.run_isolated("test_*.py", 1)


if __name__ == "__main__":
    unittest.main()
