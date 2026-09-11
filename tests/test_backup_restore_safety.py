"""Pure archive engine regressions; no gateway/backend import or live restore.
Run only via scripts/run_tests.py in the disposable offline snapshot.
"""
import gzip
import io
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts.backup_restore import RestoreLimits, UnsafeArchive, restore_archive


def member(name, content=b"new", kind=tarfile.REGTYPE, link=""):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = link
    info.size = len(content) if kind == tarfile.REGTYPE else 0
    return info, content


class RestoreSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "destination"
        (self.root / "data").mkdir(parents=True)
        self.old = self.root / "data" / "state.json"
        self.old.write_bytes(b"old")
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "state.json").write_bytes(b"outside")
        self.archive = self.base / "backup.tar.gz"

    def make(self, entries):
        with tarfile.open(self.archive, "w:gz") as tar:
            for info, content in entries:
                tar.addfile(info, io.BytesIO(content) if info.isfile() else None)
        return self.archive

    def rejected(self, entries, **kwargs):
        self.make([member("data/state.json"), *entries])
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        with self.assertRaises(UnsafeArchive):
            restore_archive(self.archive, self.root, **kwargs)
        self.assertEqual(self.old.read_bytes(), b"old")
        self.assertEqual((self.outside / "state.json").read_bytes(), b"outside")
        self.assertEqual(sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")), before)

    def test_all_existing_archive_scopes_restore_regular_files_only(self):
        names = ["data/state.json", "data/trading.db", "data/AI_TRADING_MEMORY.md",
                 "scripts/job.py", "dashboard/index.html", "okxquant_backend/app.py",
                 "okxquant_gateway/worker.py", "tests/test_example.py", "RECOVERY_GUIDE.md",
                 "SOUL.md", "PROFILE.md", "AGENTS.md", "MEMORY.md", "README.md",
                 "requirements.txt", "pyproject.toml", "docker-compose.yml", "Dockerfile", ".gitignore"]
        self.make([member("data", b"", tarfile.DIRTYPE), *[member(n) for n in names]])
        with patch.object(tarfile.TarFile, "extract", side_effect=AssertionError("no extract")), patch.object(tarfile.TarFile, "extractall", side_effect=AssertionError("no extractall")):
            result = restore_archive(self.archive, self.root)
        self.assertEqual(result["restored_count"], len(names))
        self.assertEqual(result["skipped_count"], 0)
        for name in names:
            self.assertEqual((self.root / name).read_bytes(), b"new")
        self.assertEqual(stat.S_IMODE(self.old.stat().st_mode), 0o600)

    def test_symlink_hardlink_devices_fifo_and_unknown_types_rejected_before_write(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE, b"X"):
            with self.subTest(kind=kind):
                self.rejected([member("scripts/escape", b"", kind, "../../outside")])

    def test_unsafe_paths_rejected_even_in_excluded_members(self):
        for name in ("/tmp/escape", "../escape", "data/../escape", "data/./escape", "./data/a", "data//a",
                     "C:/escape", "C:escape", "data/C:/escape", "data\\escape", "\\\\server\\share\\file",
                     "data/a:stream", "data/a?b", "data/a*b", "data/a|b", "data/name.", "data/name ", "data/CON", "data/com1.txt", "data/line\nname", ".git/../escape"):
            with self.subTest(name=name):
                self.rejected([member(name)])

    def test_links_in_secret_paths_are_not_silently_skipped(self):
        self.rejected([member(".env", b"", tarfile.SYMTYPE, "outside")])

    def test_duplicate_and_conflicting_members_rejected_in_both_orders(self):
        cases = [
            [member("scripts/a.py"), member("scripts/a.py")],
            [member("scripts/A.py"), member("scripts/a.py")],
            [member("scripts/Dir/a.py"), member("scripts/dir/b.py")],
            [member("scripts/caf\u00e9/a.py"), member("scripts/cafe\u0301/b.py")],
            [member("scripts/a"), member("scripts/a/b.py")],
            [member("scripts/a/b.py"), member("scripts/a")],
            [member("scripts/a", b"", tarfile.DIRTYPE), member("scripts/a")],
            [member("scripts/a", b"", tarfile.DIRTYPE), member("scripts/a", b"", tarfile.DIRTYPE)],
        ]
        for entries in cases:
            with self.subTest(names=[m.name for m, _ in entries]):
                self.rejected(entries)

    def test_existing_destination_symlink_is_rejected(self):
        (self.root / "data/link.json").symlink_to(self.outside / "state.json")
        self.rejected([member("data/link.json")])

    def test_existing_dangling_symlink_is_rejected(self):
        (self.root / "data/link.json").symlink_to(self.outside / "missing")
        self.rejected([member("data/link.json")])
        self.assertFalse((self.outside / "missing").exists())

    def test_existing_parent_symlink_even_inside_root_is_rejected(self):
        (self.root / "scripts").symlink_to(self.outside, target_is_directory=True)
        self.rejected([member("scripts/state.json")])
        (self.root / "scripts").unlink()
        (self.root / "scripts").symlink_to(self.root / "data", target_is_directory=True)
        self.rejected([member("scripts/state.json")])

    def test_destination_root_or_ancestor_symlink_is_rejected(self):
        link = self.base / "alias"
        link.symlink_to(self.root, target_is_directory=True)
        self.make([member("data/state.json")])
        for destination in (link, link / "data"):
            with self.subTest(destination=destination), self.assertRaises(UnsafeArchive):
                restore_archive(self.archive, destination)
        self.assertEqual(self.old.read_bytes(), b"old")

    def test_existing_hardlink_fifo_and_file_directory_conflicts_rejected(self):
        target = self.root / "data/link.json"
        os.link(self.outside / "state.json", target)
        self.rejected([member("data/link.json")])
        target.unlink()
        os.mkfifo(target)
        self.rejected([member("data/link.json")])
        target.unlink()
        target.mkdir()
        self.rejected([member("data/link.json")])
        self.rejected([member("data/state.json/child.json")])

    def test_existing_sqlite_sidecars_require_offline_clean_shutdown(self):
        for suffix in ("-wal", "-shm", "-journal"):
            path = self.root / ("data/trading.db" + suffix)
            path.write_bytes(b"keep-existing-journal")
            self.rejected([member("data/trading.db")])
            self.assertEqual(path.read_bytes(), b"keep-existing-journal")
            path.unlink()

    def test_archive_symlink_and_symlink_parent_rejected(self):
        self.make([member("data/state.json")])
        link = self.base / "link.tar.gz"
        link.symlink_to(self.archive)
        with self.assertRaises(UnsafeArchive):
            restore_archive(link, self.root)
        parent = self.base / "parent"
        parent.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(UnsafeArchive):
            restore_archive(parent / self.archive.name, self.root)
        self.assertEqual(self.old.read_bytes(), b"old")

    def test_secret_auth_runtime_and_unsupported_data_are_skipped(self):
        names = [".env", ".env.production", ".git/config", ".okx/auth.json", ".bypy/auth.json",
                 "data/.okxquant_secret_key", "data/okxquant_secrets.enc", "data/okxquant_admin.db", "data/okxquant_admin.db-wal",
                 "data/credentials/value.json", "data/oauth-connections/value.json", "data/session.json",
                 "data/nested/password.json", "data/private_key.txt", "data/llm_models.json", "data/llm_models.backup.json", "data/llm_providers.json",
                 "data/.okxquant_gateway.lock", "data/.ai_brain_cycle.lock", "data/okxquant_gateway.pid",
                 "data/trading.db-wal", "data/trading.db-shm", "data/trading.db-journal", "data/unknown.bin",
                 "scripts/.env.local", "scripts/.envrc", "scripts/secret/nested.json", "scripts/auth.json", "scripts/secrets.json", "scripts/llm_models.json",
                 "scripts/okxquant_admin.db", "scripts/api_key.json", "scripts/unknown.bin", "scripts/client.pem", "scripts/__pycache__/x.pyc"]
        # All fixtures below are synthetic: never read the checkout's credentials.
        for name in (".env", "data/okxquant_admin.db", "data/llm_models.json"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"keep-synthetic")
        self.make([member("data/state.json"), *[member(n, b"unwanted") for n in names]])
        result = restore_archive(self.archive, self.root)
        self.assertEqual(result["restored_count"], 1)
        self.assertEqual(result["skipped_count"], len(names))
        for name in names:
            path = self.root / name
            if name in {".env", "data/okxquant_admin.db", "data/llm_models.json"}:
                self.assertEqual(path.read_bytes(), b"keep-synthetic")
            else:
                self.assertFalse(path.exists(), name)

    def test_outside_archive_scope_is_rejected(self):
        for name in ("etc/passwd", "okxquant_frontend/src/main.ts", "backups/local/file.json", "logs/trader.log"):
            with self.subTest(name=name):
                self.rejected([member(name)])

    def test_file_total_count_path_depth_and_compressed_limits(self):
        cases = [
            (RestoreLimits(max_file_bytes=4), [member("data/large.json", b"12345")]),
            (RestoreLimits(max_total_bytes=4), [member("data/second.json", b"12")]),
            (RestoreLimits(max_members=1), [member("data/second.json")]),
            (RestoreLimits(max_path_bytes=15), [member("data/long-filename.json")]),
            (RestoreLimits(max_depth=2), [member("data/sub/x.json")]),
            (RestoreLimits(max_archive_bytes=1), []),
            (RestoreLimits(max_total_bytes=1, max_metadata_bytes=1), []),
            (RestoreLimits(max_file_bytes=4), [member(".env", b"12345")]),
        ]
        for limits, entries in cases:
            with self.subTest(limits=limits):
                self.rejected(entries, limits=limits)

    def test_truncated_gzip_bad_crc_and_truncated_tar_write_nothing(self):
        self.make([member("data/state.json"), member("scripts/last.py", b"x" * 1000)])
        original = self.archive.read_bytes()
        raw = gzip.decompress(original)
        variants = [original[:-5], original[:-8] + b"\0" * 8,
                    gzip.compress(raw[:1800]), b"not gzip"]
        for content in variants:
            with self.subTest(size=len(content)):
                self.archive.write_bytes(content)
                with self.assertRaises(UnsafeArchive):
                    restore_archive(self.archive, self.root)
                self.assertEqual(self.old.read_bytes(), b"old")
                self.assertFalse((self.root / "scripts").exists())

    def test_concatenated_hidden_tar_after_end_marker_is_rejected(self):
        self.make([member("data/state.json")])
        raw = gzip.decompress(self.archive.read_bytes())
        self.make([member("scripts/link", b"", tarfile.SYMTYPE, "../outside")])
        self.archive.write_bytes(gzip.compress(raw + gzip.decompress(self.archive.read_bytes())))
        with self.assertRaises(UnsafeArchive):
            restore_archive(self.archive, self.root)
        self.assertEqual(self.old.read_bytes(), b"old")

    def test_pax_overridden_path_and_sparse_metadata_are_rejected(self):
        info, content = member("scripts/safe.py")
        info.pax_headers = {"path": "../escape"}
        self.rejected([(info, content)])
        info, content = member("scripts/safe.py")
        info.pax_headers = {"GNU.sparse.name": "scripts/safe.py"}
        self.rejected([(info, content)])

    def test_ownership_setid_and_untrusted_mode_not_restored(self):
        info, content = member("scripts/job.py")
        info.mode, info.uid, info.gid = 0o6777, 1234, 1234
        self.make([(info, content)])
        restore_archive(self.archive, self.root)
        restored = self.root / "scripts/job.py"
        self.assertEqual(stat.S_IMODE(restored.stat().st_mode), 0o755)
        self.assertEqual(restored.stat().st_uid, os.getuid())

    def test_publish_does_not_follow_a_link_introduced_after_preflight(self):
        self.make([member("scripts/job.py")])
        from scripts import backup_restore as engine
        original = engine._directory_fd
        def swap(root_fd, parts):
            (self.root / "scripts").symlink_to(self.outside, target_is_directory=True)
            return original(root_fd, parts)
        with patch.object(engine, "_directory_fd", side_effect=swap), self.assertRaises(UnsafeArchive):
            restore_archive(self.archive, self.root)
        self.assertFalse((self.outside / "job.py").exists())
        self.assertEqual(self.old.read_bytes(), b"old")

    def test_extended_header_size_and_chain_are_bounded_before_tar_parsing(self):
        info, content = member("scripts/safe.py")
        info.pax_headers = {"comment": "x" * 3000}
        self.rejected([(info, content)], limits=RestoreLimits(max_metadata_bytes=1024))
        # Syntactically valid chained PAX headers can otherwise recurse in tarfile.
        info = tarfile.TarInfo("pax")
        info.type = tarfile.XHDTYPE
        info.size = 0
        self.make([member("data/state.json")])
        raw = gzip.decompress(self.archive.read_bytes())
        self.archive.write_bytes(gzip.compress(info.tobuf() * 40 + raw))
        with self.assertRaises(UnsafeArchive):
            restore_archive(self.archive, self.root)
        self.assertEqual(self.old.read_bytes(), b"old")

    def test_current_archive_creator_scope_contract_and_round_trip(self):
        # AST reads only constant source metadata; no backup_runtime/gateway import.
        import ast
        from scripts.backup_restore import SOURCE_DIRS, ROOT_FILES
        runtime = Path(__file__).resolve().parents[1] / "scripts/backup_runtime.py"
        tree = ast.parse(runtime.read_text(encoding="utf-8"))
        value = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(name, ast.Name) and name.id == "SCOPE_PATHS" for name in node.targets))
        scopes = ast.literal_eval(value)
        current_paths = {name for names in scopes.values() for name in names}
        self.assertEqual(current_paths, SOURCE_DIRS | ROOT_FILES | {"data"})
        source = self.base / "source-fixture"
        for scope in SOURCE_DIRS | {"data"}:
            (source / scope).mkdir(parents=True)
            (source / scope / ("example.json" if scope == "data" else "example.py")).write_bytes(b"fixture")
        for name in ROOT_FILES:
            (source / name).write_bytes(b"fixture")
        # Same archive.add(..., arcname=relative, recursive=True) as create_archive.
        with tarfile.open(self.archive, "w:gz") as tar:
            for name in sorted(current_paths):
                tar.add(source / name, arcname=name, recursive=True)
        result = restore_archive(self.archive, self.root)
        self.assertEqual(result["restored_count"], len(SOURCE_DIRS) + 1 + len(ROOT_FILES))

    def test_invalid_limits_fail_before_writes(self):
        with self.assertRaises(ValueError):
            restore_archive(self.archive, self.root, limits=RestoreLimits(max_members=0))


class ArchiveSecretExclusionTests(unittest.TestCase):
    """Only synthetic provider files; never inspect existing archives/configs."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        for name in ("llm_models.json", "llm_providers.json"):
            (self.root / "data" / name).write_bytes(b'{"api_key":"SYNTHETIC-NOT-A-REAL-KEY"}')
        (self.root / "data/trading_state.json").write_bytes(b'{"fixture":true}')

    def assert_safe_archive(self, archive):
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn("data/trading_state.json", names)
            self.assertNotIn("data/llm_models.json", names)
            self.assertNotIn("data/llm_providers.json", names)
        self.assertNotIn(b"SYNTHETIC-NOT-A-REAL-KEY", gzip.decompress(archive.read_bytes()))

    def test_default_data_scope_never_archives_inline_provider_keys(self):
        from scripts import backup_runtime as runtime
        from okxquant_backend.backup_store import _default_job
        with patch.object(runtime, "ROOT", self.root), patch.object(runtime, "BACKUPS", self.root / "backups"):
            archive, included = runtime.create_archive(_default_job(), "offline-fixture")
            self.assertIn("data", included)
            self.assert_safe_archive(archive)
        # Source fixtures stay intact; exclusion is not destructive cleanup.
        self.assertTrue((self.root / "data/llm_models.json").exists())
        self.assertTrue((self.root / "data/llm_providers.json").exists())

    def test_empty_custom_excludes_cannot_send_keys_to_local_or_remote_delivery(self):
        from scripts import backup_runtime as runtime
        from okxquant_backend.backup_store import _default_job
        job = _default_job()
        job.update({"exclude": [], "pre_backup_sync": False, "cleanup_local_on_success": False,
                    "targets": [{"id": kind, "type": kind, "enabled": True} for kind in ("local", "s3", "oss", "webdav", "baidu")]})
        def inspect_delivery(archive, target):
            self.assert_safe_archive(archive)
            return {"success": True, "attempts": 1, "destination": "offline-mock:" + target["type"]}
        with patch.object(runtime, "ROOT", self.root), patch.object(runtime, "BACKUPS", self.root / "backups"), patch.object(runtime, "MANIFEST_DIR", self.root / "backups/manifests"), patch.object(runtime, "deliver_target", side_effect=inspect_delivery) as delivery:
            result = runtime.run_backup_job(job)
        self.assertEqual(result["status"], "success", result["errors"])
        self.assertEqual(delivery.call_count, 5)


if __name__ == "__main__":
    unittest.main()
