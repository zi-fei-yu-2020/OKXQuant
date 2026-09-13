"""Compile only allowed app functions: no backend startup, gateway or live I/O.
Run via scripts/run_tests.py. Credential status/config and /proc are fake fixtures.
"""
import ast
import copy
import fcntl
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException


def functions(root):
    source = Path(__file__).resolve().parents[1] / "okxquant_backend/app.py"
    names = {"simple_backup_config", "restore_backup_archive", "_save_memory_items"}
    selected = []
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    assert len(selected) == len(names)
    def path(value):
        return root / "fake-proc" if str(value) == "/proc" else Path(value)
    namespace = dict(ROOT=root, Path=path, Any=object, json=json, os=os, sys=sys,
                     HTTPException=HTTPException, Header=lambda **_: None,
                     BackupRestoreRequest=SimpleNamespace, MEMORY_FILE=root / "data/AI_TRADING_MEMORY.md",
                     require_admin_header=Mock(), require_superadmin=Mock(), refresh_settings=Mock(),
                     list_backup_jobs=Mock(), validate_backup_job=Mock(return_value={"valid": True, "errors": [], "warnings": []}),
                     backup_credential_status=Mock(return_value={"configured": False, "fields": [], "count": 0}),
                     audit_record=Mock())
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


class BackupStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ns = functions(self.root)
        self.job = {"id": "nightly-default", "enabled": True, "schedule_times": ["02:00"],
                    "targets": [{"id": "local-default", "type": "local", "enabled": True, "path": "backups/local", "retention": 3}]}
        self.ns["list_backup_jobs"].return_value = [self.job]

    def config(self):
        return self.ns["simple_backup_config"]("fake-admin")

    def test_local_needs_no_credentials_and_does_not_claim_directory_writable(self):
        self.job["targets"][0]["credential_status"] = {"configured": False}
        before = copy.deepcopy(self.job)
        result = self.config()
        self.assertTrue(result["configured"])
        self.assertFalse(result["directory_write_verified"])
        self.assertFalse(result["connection_verified"])
        self.assertNotIn("credential_status", result["target"])
        self.assertIn("无需凭据", result["configuration_note"])
        self.ns["backup_credential_status"].assert_not_called()
        self.assertEqual(self.job, before)
        self.assertFalse((self.root / "backups").exists())

    def test_local_configuration_is_distinct_from_validation_and_enabled(self):
        self.job["targets"][0]["enabled"] = False
        self.ns["validate_backup_job"].return_value = {"valid": False, "errors": ["invalid local path"]}
        result = self.config()
        self.assertTrue(result["configured"])
        self.assertFalse(result["validation"]["valid"])
        self.assertFalse(result["directory_write_verified"])

    def test_remote_types_require_real_status_all_credentials_and_target_fields(self):
        cases = [
            ("s3", "native", {"access_key_id", "secret_access_key"}, {"endpoint": "https://storage.invalid", "bucket": "test"}),
            ("oss", "native", {"access_key_id", "secret_access_key"}, {"endpoint": "https://storage.invalid", "bucket": "test"}),
            ("webdav", "webdav", {"username", "password"}, {"endpoint": "https://storage.invalid"}),
            ("aliyundrive", "webdav", {"username", "password"}, {"endpoint": "https://storage.invalid"}),
            ("quark", "webdav", {"username", "password"}, {"endpoint": "https://storage.invalid"}),
            ("baidu", "oauth", {"app_key", "app_secret", "refresh_token"}, {}),
        ]
        for kind, mode, required, config in cases:
            with self.subTest(kind=kind):
                target = {"id": "remote-test", "type": kind, "auth_mode": mode, "enabled": True,
                          "credential_ref": "backup:fake", **config}
                self.job["targets"] = [target]
                for fields in [set(), *[required - {field} for field in required], required, required | {"session_token"}]:
                    self.ns["backup_credential_status"].return_value = {"configured": bool(fields), "fields": sorted(fields), "count": len(fields)}
                    result = self.config()
                    self.assertEqual(result["configured"], required <= fields)
                    self.assertEqual(result["target"]["credential_status"]["configured"], required <= fields)
                    self.assertFalse(result["connection_verified"])
                    self.assertEqual(set(result["missing_fields"]), required - fields)
                    self.ns["backup_credential_status"].assert_called_with("backup:fake")
                for name in config:
                    incomplete = dict(target)
                    incomplete[name] = " "
                    self.job["targets"] = [incomplete]
                    self.assertFalse(self.config()["configured"])
                self.assertNotIn("credential_status", target)

    def test_stale_inline_presence_cannot_override_real_store_and_default_ref_matches_runtime(self):
        self.job["targets"] = [{"id": "s3-test", "type": "s3", "enabled": True, "endpoint": "https://storage.invalid",
                                "bucket": "test", "credential_status": {"configured": True, "fields": ["access_key_id", "secret_access_key"]}}]
        result = self.config()
        self.assertFalse(result["configured"])
        self.ns["backup_credential_status"].assert_called_with("backup:s3-test")
        self.assertEqual(result["missing_fields"], ["access_key_id", "secret_access_key"])

    def test_legacy_bypy_and_unsupported_oauth_not_reported_configured(self):
        for kind, mode in (("baidu", "bypy"), ("quark", "oauth"), ("unknown", "native")):
            self.job["targets"] = [{"id": "fake", "type": kind, "enabled": True, "auth_mode": mode, "endpoint": "https://storage.invalid"}]
            self.ns["backup_credential_status"].return_value = {"configured": True, "fields": ["username", "password", "app_key", "app_secret", "refresh_token"]}
            self.assertFalse(self.config()["configured"])

    def test_manifests_use_started_finished_and_never_default_to_success(self):
        directory = self.root / "backups/manifests"
        directory.mkdir(parents=True)
        for index, status in enumerate(("success", "failed", "partial", "running", "skipped", "unknown", None, "garbage", {"bad": True})):
            item = {"job_id": self.job["id"], "started_at": "2026-09-08 02:00:00", "finished_at": "2026-09-08 02:01:00", "status": status}
            path = directory / "current.json"
            path.write_text(json.dumps(item), encoding="utf-8")
            os.utime(path, (1000 + index, 1000 + index))
            result = self.config()["latest"]
            self.assertEqual(result["started_at"], item["started_at"])
            self.assertEqual(result["finished_at"], item["finished_at"])
            self.assertEqual(result["status"], status if isinstance(status, str) and status in {"success", "failed", "partial", "running", "skipped"} else "unknown")
        for name, content in (("invalid.json", "broken"), ("array.json", "[]"), ("other.json", '{"job_id":"other","status":"success"}')):
            path = directory / name
            path.write_text(content, encoding="utf-8")
            os.utime(path, (9999, 9999))
        self.assertEqual(self.config()["latest"]["status"], "unknown")
        (directory / "current.json").write_text('{"job_id":"nightly-default"}', encoding="utf-8")
        latest = self.config()["latest"]
        self.assertEqual(latest["status"], "unknown")
        self.assertNotIn("created_at", latest)

    def test_no_job_and_no_manifest(self):
        self.assertIsNone(self.config()["latest"])
        self.ns["list_backup_jobs"].return_value = []
        with self.assertRaises(HTTPException) as error:
            self.config()
        self.assertEqual(error.exception.status_code, 404)

    def test_legacy_memory_writer_cannot_bypass_reviewed_publication(self):
        path = self.ns["MEMORY_FILE"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("保留当前实际模型记忆", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "候选审核发布"):
            self.ns["_save_memory_items"](["  人工采用心法  ", "", "第二条"])
        self.assertEqual(path.read_text(encoding="utf-8"), "保留当前实际模型记忆")


class RestoreAPIGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        (self.root / "fake-proc").mkdir()
        (self.root / "backups/local").mkdir(parents=True)
        self.ns = functions(self.root)
        self.payload = SimpleNamespace(archive_name="offline.tar.gz", confirmation="RESTORE OKXQUANT")
        self.archive = self.root / "backups/local/offline.tar.gz"
        with tarfile.open(self.archive, "w:gz") as tar:
            item = tarfile.TarInfo("data/state.json")
            item.size = 4
            tar.addfile(item, io.BytesIO(b"safe"))

    def restore(self):
        return self.ns["restore_backup_archive"](self.payload, None, "fake-session")

    def test_api_success_only_in_temp_root_without_gateway_startup(self):
        result = self.restore()
        self.assertTrue(result["restored"])
        self.assertEqual(result["restored_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual((self.root / "data/state.json").read_bytes(), b"safe")
        self.ns["require_superadmin"].assert_called_once_with("fake-session")
        self.ns["audit_record"].assert_called_once()

    def test_active_supervisor_is_rejected_without_importing_or_stopping_gateway(self):
        supervisor = SimpleNamespace(_thread=Mock())
        supervisor._thread.is_alive.return_value = True
        with patch.dict(sys.modules, {"okxquant_gateway.supervisor": supervisor}), patch("scripts.backup_restore.restore_archive") as engine, self.assertRaises(HTTPException) as error:
            self.restore()
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("自动拉起线程", error.exception.detail)
        engine.assert_not_called()
        self.assertFalse((self.root / "data/state.json").exists())

    def test_each_active_runtime_lock_returns_409_before_restore(self):
        for name in (".okxquant_gateway.lock", ".ai_factor_trader.lock", ".ai_brain_cycle.lock", ".okxquant_scheduler.lock"):
            with self.subTest(name=name), (self.root / "data" / name).open("a+") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch("scripts.backup_restore.restore_archive") as engine, self.assertRaises(HTTPException) as error:
                    self.restore()
                self.assertEqual(error.exception.status_code, 409)
                self.assertIn("请先", error.exception.detail)
                self.assertIn("云端保护", error.exception.detail)
                engine.assert_not_called()
        self.assertFalse((self.root / "data/state.json").exists())
        self.ns["audit_record"].assert_not_called()

    def test_all_locks_held_through_engine_and_released_even_on_failure(self):
        from scripts.backup_restore import UnsafeArchive
        def engine(*_):
            for name in (".okxquant_gateway.lock", ".ai_factor_trader.lock", ".ai_brain_cycle.lock", ".okxquant_scheduler.lock"):
                with (self.root / "data" / name).open("a+") as handle:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raise UnsafeArchive("synthetic unsafe archive")
        with patch("scripts.backup_restore.restore_archive", side_effect=engine), self.assertRaises(HTTPException) as error:
            self.restore()
        self.assertEqual(error.exception.status_code, 400)
        for name in (".okxquant_gateway.lock", ".ai_factor_trader.lock", ".ai_brain_cycle.lock", ".okxquant_scheduler.lock"):
            with (self.root / "data" / name).open("a+") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.ns["audit_record"].assert_not_called()

    def test_idle_same_instance_trader_or_gateway_returns_409_without_signals(self):
        process = self.root / "fake-proc/99999999"
        process.mkdir()
        process.joinpath("cwd").symlink_to(self.root, target_is_directory=True)
        for command in (b"python\0scripts/ai_factor_trader.py\0", b"python\0scripts/ai_brain_trader.py\0", b"python\0-m\0okxquant_gateway.worker\0",
                        b"python\0-m\0scripts.ai_factor_trader\0", b"python\0-m\0scripts.ai_brain_trader\0",
                        b"python\0-m\0okxquant_backend.scheduler\0", b"python\0okxquant_backend/scheduler.py\0"):
            process.joinpath("cmdline").write_bytes(command)
            with patch("os.kill", side_effect=AssertionError("never signal")), patch("scripts.backup_restore.restore_archive") as engine, self.assertRaises(HTTPException) as error:
                self.restore()
            self.assertEqual(error.exception.status_code, 409)
            engine.assert_not_called()

    def test_other_instance_does_not_block_but_absolute_same_instance_script_does(self):
        process = self.root / "fake-proc/99999999"
        process.mkdir()
        process.joinpath("cwd").symlink_to(self.root.parent, target_is_directory=True)
        process.joinpath("cmdline").write_bytes(b"python\0scripts/ai_factor_trader.py\0")
        self.assertTrue(self.restore()["restored"])
        command = f"python\0{self.root}/scripts/ai_factor_trader.py\0".encode()
        process.joinpath("cmdline").write_bytes(command)
        with self.assertRaises(HTTPException) as error:
            self.restore()
        self.assertEqual(error.exception.status_code, 409)

    def test_process_inspection_failure_fails_closed(self):
        process = self.root / "fake-proc/99999999"
        process.mkdir()
        with patch.object(Path, "read_bytes", side_effect=PermissionError), patch("scripts.backup_restore.restore_archive") as engine, self.assertRaises(HTTPException) as error:
            self.restore()
        self.assertEqual(error.exception.status_code, 409)
        engine.assert_not_called()

    def test_unsafe_lock_and_data_symlink_return_409(self):
        lock = self.root / "data/.okxquant_gateway.lock"
        lock.symlink_to(self.root / "missing")
        with self.assertRaises(HTTPException) as error:
            self.restore()
        self.assertEqual(error.exception.status_code, 409)
        self.assertFalse((self.root / "missing").exists())
        lock.unlink()
        (self.root / "data").rmdir()
        (self.root / "data").symlink_to(self.root / "fake-proc", target_is_directory=True)
        with self.assertRaises(HTTPException) as error:
            self.restore()
        self.assertEqual(error.exception.status_code, 409)

    def test_confirmation_filename_missing_file_and_platform_guard(self):
        self.payload.confirmation = "NO"
        with self.assertRaises(HTTPException) as error:
            self.restore()
        self.assertEqual(error.exception.status_code, 400)
        self.payload.confirmation = "RESTORE OKXQUANT"
        for name in ("../offline.tar.gz", "C:\\offline.tar.gz", "/offline.tar.gz", "file.enc", "data:stream.tgz"):
            self.payload.archive_name = name
            with self.assertRaises(HTTPException) as error:
                self.restore()
            self.assertEqual(error.exception.status_code, 400)
        self.payload.archive_name = "missing.tar.gz"
        with self.assertRaises(HTTPException) as error:
            self.restore()
        self.assertEqual(error.exception.status_code, 404)
        self.payload.archive_name = "offline.tar.gz"
        with patch.object(sys, "platform", "win32"), self.assertRaises(HTTPException) as error:
            self.restore()
        self.assertEqual(error.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
