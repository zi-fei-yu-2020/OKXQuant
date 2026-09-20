"""A10 operations regressions. Run only via scripts/run_tests.py in WSL."""
from __future__ import annotations
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
import importlib
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from okxquant_gateway import scheduler as sched
from okxquant_gateway.store import GatewayStore
from scripts import backup_runtime as backup, backup_restore as restore
from okxquant_backend import backup_store, backup_secrets


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def patched(self, target, name, value):
        return self.stack.enter_context(patch.object(target, name, value))


class BackupSafetyTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        for name, path in [('ROOT', self.root), ('BACKUPS', self.root/'backups'),
                           ('LOCAL_DIR', self.root/'backups/local'), ('SQLITE_DIR', self.root/'backups/sqlite'),
                           ('MANIFEST_DIR', self.root/'backups/manifests')]:
            self.patched(backup, name, path)
        (self.root/'data').mkdir()

    def test_secret_exclusions_preserve_leading_dot(self):
        for name in ['.env', './.env', '.env.production', 'scripts/.env.local',
                     'data/okxquant_backup_secrets.enc', 'data/.okxquant_backup_secret_key',
                     'data/llm_models.json', 'data/auth/session.json', 'data/okxquant_admin.db',
                     'scripts/credentials/token.json']:
            with self.subTest(name=name):
                self.assertTrue(backup._excluded(name, []))
        self.assertFalse(backup._excluded('data/trading_ledger.json', []))
        self.assertFalse(backup._excluded('scripts/backup_secrets.py', []))

    def test_archive_skips_nested_secret_and_symlink(self):
        scripts = self.root/'scripts'; scripts.mkdir()
        (scripts/'safe.py').write_text('safe')
        (scripts/'.env.production').write_text('synthetic fixture, not a credential')
        (scripts/'linked.py').symlink_to(scripts/'safe.py')
        path, _ = backup.create_archive({'id': 'safe', 'scope': ['scripts']}, 'stamp')
        with tarfile.open(path) as archive:
            self.assertEqual(set(archive.getnames()), {'scripts', 'scripts/safe.py'})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_runtime_target_cannot_escape_before_write(self):
        source = self.root/'okxquant_backup_fixture.tar.gz'; source.write_bytes(b'fixture')
        outside = self.root/'outside'
        with self.assertRaises(ValueError):
            backup.deliver_target(source, {'type': 'local', 'path': str(outside), 'retention': 1})
        self.assertFalse(outside.exists())

    def test_runtime_target_rejects_symlink_directory(self):
        (self.root/'backups').mkdir()
        outside = self.root/'outside'; outside.mkdir()
        (self.root/'backups/linked').symlink_to(outside, target_is_directory=True)
        source = self.root/'okxquant_backup_fixture.tar.gz'; source.write_bytes(b'fixture')
        with self.assertRaises(ValueError):
            backup.deliver_target(source, {'type': 'local', 'path': 'backups/linked', 'retention': 1})
        self.assertEqual(list(outside.iterdir()), [])

    def test_runtime_job_id_cannot_escape_manifest_or_sqlite_directory(self):
        for name in ['../outside', '/outside', 'a/b', 'a\\b', '..', 'bad\nname']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                backup.run_backup_job({'id': name, 'name': 'fixture'})
        self.assertFalse((self.root/'backups').exists())

    def test_local_archive_cannot_truncate_linked_destination(self):
        directory = self.root/'backups/local'; directory.mkdir(parents=True)
        source = self.root/'okxquant_backup_fixture.tar.gz'; source.write_bytes(b'new')
        original = self.root/'original'; original.write_bytes(b'old')
        os.link(original, directory/source.name)
        with self.assertRaises(ValueError):
            backup.retain_local_archive(source, 1)
        self.assertEqual(original.read_bytes(), b'old')

    def test_sqlite_retention_is_per_database(self):
        for name in ['one', 'two']:
            with sqlite3.connect(self.root/'data'/f'{name}.db') as connection:
                connection.execute('CREATE TABLE fixture (id INTEGER)')
        backup.sqlite_hot_backups('first', 1)
        backup.sqlite_hot_backups('second', 1)
        self.assertEqual({p.name for p in (self.root/'backups/sqlite').glob('*.db')},
                         {'one_second.db', 'two_second.db'})

    def test_decryption_bad_tag_does_not_replace_destination(self):
        source = self.root/'archive.gz'; source.write_bytes(b'fixture payload')
        with patch.dict(os.environ, {'TEST_A10_KEY': 'synthetic-test-key-only'}):
            encrypted = backup.encrypt_archive(source, 'TEST_A10_KEY')
            content = bytearray(encrypted.read_bytes()); content[-1] ^= 1
            encrypted.write_bytes(content)
            destination = self.root/'result.gz'; destination.write_bytes(b'keep original')
            with self.assertRaises(Exception):
                backup.decrypt_archive(encrypted, 'TEST_A10_KEY', destination)
            self.assertEqual(destination.read_bytes(), b'keep original')
            self.assertEqual(list(self.root.glob('.decrypt-*')), [])

    def test_retention_cannot_delete_another_jobs_archives(self):
        for job in ['one', 'two', 'one']:
            index = len(list(self.root.glob('okxquant_backup_*')))
            source = self.root/f'okxquant_backup_{job}_20260919_12000{index}.tar.gz'
            source.write_bytes(b'fixture')
            backup.retain_local_archive(source, 1)
        remaining = list((self.root/'backups/local').glob('okxquant_backup_*'))
        self.assertEqual(len(remaining), 2)
        self.assertTrue(any('two_' in p.name for p in remaining))

    def test_backup_failure_withholds_external_exception_detail(self):
        job = {'id':'fixture', 'name':'fixture', 'targets':[{'enabled':True}]}
        with patch.object(backup, 'create_archive', side_effect=RuntimeError('synthetic-sensitive-token')):
            result = backup.run_backup_job(job)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('synthetic-sensitive-token', str(result))
        self.assertNotIn('synthetic-sensitive-token', (self.root/result['manifest']).read_text())

    def test_verify_shares_restore_link_policy_without_writing_files(self):
        path = self.root/'fixture.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            link = tarfile.TarInfo('scripts/link.py'); link.type = tarfile.SYMTYPE; link.linkname = '../other'
            archive.addfile(link)
        with self.assertRaises(restore.UnsafeArchive):
            backup.verify_archive(path)

    def test_verify_valid_archive_and_crc_failure(self):
        scripts = self.root/'scripts'; scripts.mkdir(); (scripts/'safe.py').write_text('fixture')
        path, _ = backup.create_archive({'id':'fixture','scope':['scripts']}, 'stamp')
        self.assertEqual(backup.verify_archive(path)['members'], 2)
        path.write_bytes(path.read_bytes()[:-5])
        with self.assertRaises(restore.UnsafeArchive):
            backup.verify_archive(path)

    def test_archive_contains_committed_wal_data_without_raw_sidecars(self):
        database = self.root/'data/ledger.db'
        writer = sqlite3.connect(database)
        try:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('CREATE TABLE fixture (value TEXT)')
            writer.execute("INSERT INTO fixture VALUES ('committed-in-wal')")
            writer.commit()
            self.assertTrue(Path(str(database)+'-wal').exists())
            path, _ = backup.create_archive({'id':'fixture','scope':['data']}, 'stamp')
            with tarfile.open(path) as archive:
                self.assertNotIn('data/ledger.db-wal', archive.getnames())
                restored = self.root/'check.db'
                restored.write_bytes(archive.extractfile('data/ledger.db').read())
            with sqlite3.connect(restored) as check:
                self.assertEqual(check.execute('SELECT value FROM fixture').fetchone()[0], 'committed-in-wal')
        finally:
            writer.close()

    def test_archive_omits_hardlinked_source(self):
        scripts = self.root/'scripts'; scripts.mkdir()
        outside = self.root/'outside-fixture'; outside.write_bytes(b'synthetic')
        os.link(outside, scripts/'looks-like-source.py')
        path, _ = backup.create_archive({'id':'fixture','scope':['scripts']}, 'stamp')
        with tarfile.open(path) as archive:
            self.assertNotIn('scripts/looks-like-source.py', archive.getnames())


class RestoreSafetyTests(TemporaryTest):
    def archive(self, entries):
        archive_path = self.root/'fixture.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            for name, value in entries:
                member = tarfile.TarInfo(name)
                if isinstance(value, tarfile.TarInfo):
                    archive.addfile(value)
                else:
                    member.size = len(value)
                    archive.addfile(member, io.BytesIO(value))
        return archive_path

    def test_bad_member_rejects_entire_archive_before_write(self):
        destination = self.root/'restore'; destination.mkdir()
        archive = self.archive([('scripts/safe.py', b'safe'), ('../escape.py', b'bad')])
        with self.assertRaises(restore.UnsafeArchive):
            restore.restore_archive(archive, destination)
        self.assertEqual(list(destination.iterdir()), [])

    def test_restore_refuses_target_links(self):
        destination = self.root/'restore'; destination.mkdir()
        outside = self.root/'outside'; outside.mkdir()
        (destination/'scripts').symlink_to(outside, target_is_directory=True)
        archive = self.archive([('scripts/safe.py', b'bad')])
        with self.assertRaises(restore.UnsafeArchive):
            restore.restore_archive(archive, destination)
        self.assertEqual(list(outside.iterdir()), [])

    def test_restore_skips_auth_and_rejects_live_wal(self):
        destination = self.root/'restore'; (destination/'data').mkdir(parents=True)
        archive = self.archive([('data/okxquant_admin.db', b'synthetic'), ('scripts/good.py', b'ok')])
        result = restore.restore_archive(archive, destination)
        self.assertEqual(result['skipped_count'], 1)
        self.assertFalse((destination/'data/okxquant_admin.db').exists())
        (destination/'data/state.db-wal').touch()
        archive = self.archive([('data/state.db', b'fixture')])
        with self.assertRaises(restore.UnsafeArchive):
            restore.restore_archive(archive, destination)

    def test_restore_rejects_archive_links(self):
        destination = self.root/'restore'; destination.mkdir()
        link = tarfile.TarInfo('scripts/link.py'); link.type = tarfile.SYMTYPE; link.linkname = '../other'
        archive = self.archive([('scripts/link.py', link)])
        with self.assertRaises(restore.UnsafeArchive):
            restore.restore_archive(archive, destination)


class BackupConfigurationTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.patched(backup_store, 'ROOT', self.root)
        self.patched(backup_store, 'CONFIG_FILE', self.root/'config.json')
        self.patched(backup_secrets, 'KEY_FILE', self.root/'test.key')
        self.patched(backup_secrets, 'STORE_FILE', self.root/'test.enc')

    def test_concurrent_configuration_updates_are_not_lost(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = list(pool.map(lambda n: backup_store.create_job(f'fixture {n}'), range(6)))
        self.assertEqual(len(backup_store.list_jobs()), 7)
        self.assertEqual(len({j['id'] for j in jobs}), 6)

    def test_duplicate_job_ids_rejected(self):
        job = backup_store._default_job()
        with self.assertRaises(ValueError):
            backup_store.save_backup_config({'jobs': [job, job]})

    def test_concurrent_secret_updates_share_one_key(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda n: backup_secrets.save_credentials(f'fixture{n}', {'username': f'fake{n}'}), range(6)))
        for n in range(6):
            self.assertEqual(backup_secrets.load_credentials(f'fixture{n}'), {'username': f'fake{n}'})
        self.assertEqual(backup_secrets.KEY_FILE.stat().st_mode & 0o777, 0o600)

    def test_corrupt_secret_store_is_never_silently_replaced(self):
        backup_secrets.save_credentials('one', {'username': 'synthetic'})
        backup_secrets.STORE_FILE.write_bytes(b'invalid ciphertext fixture')
        with self.assertRaises(ValueError):
            backup_secrets.save_credentials('two', {'username': 'synthetic'})
        self.assertEqual(backup_secrets.STORE_FILE.read_bytes(), b'invalid ciphertext fixture')

    def test_missing_key_is_never_regenerated_over_old_store(self):
        backup_secrets.STORE_FILE.write_bytes(b'existing ciphertext fixture')
        with self.assertRaises(ValueError):
            backup_secrets.save_credentials('one', {'username': 'synthetic'})
        self.assertFalse(backup_secrets.KEY_FILE.exists())

    def test_corrupt_config_neither_enables_defaults_nor_overwrites_itself(self):
        backup_store.CONFIG_FILE.write_text('{broken fixture')
        with self.assertRaises(ValueError):
            backup_store.list_jobs()
        with self.assertRaises(ValueError):
            backup_store.create_job('fixture')
        self.assertEqual(backup_store.CONFIG_FILE.read_text(), '{broken fixture')


class RecordingExecutor:
    def __init__(self):
        self.calls = []
    def submit(self, function, *args):
        future = Future()
        self.calls.append((function, args, future))
        return future


class SchedulerFixture(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.store = GatewayStore(self.root/'gateway.db')
        self.scheduler = sched.GatewayScheduler(self.store)
        self.addCleanup(self.scheduler.shutdown)
        self.now = datetime(2026, 9, 19, 20, 0, 0, tzinfo=sched.BJ_TZ)


class SchedulerSafetyTests(SchedulerFixture):
    def test_trader_cadence_unchanged(self):
        trader = next(job for job in sched.JOBS if job.name == 'trader')
        self.assertEqual(trader.interval_seconds, 900)
        self.assertTrue(self.scheduler.due(trader, self.now, {}))
        self.assertFalse(self.scheduler.due(trader, self.now.replace(second=10), {}))
        self.store.set_state('job.last.trader', self.now.isoformat())
        self.assertFalse(self.scheduler.due(trader, self.now.replace(second=1), {}))

    def test_long_maintenance_does_not_queue_trader_or_guards(self):
        recorders = {name: RecordingExecutor() for name in ['executor','trader_executor','guard_executor','ledger_executor','scalp_executor']}
        jobs = tuple(job for job in sched.JOBS if job.name in {'trader','position_guard','self_improvement','demo_scalp'})
        with ExitStack() as stack:
            for name, executor in recorders.items():
                stack.enter_context(patch.object(self.scheduler, name, executor))
            stack.enter_context(patch.object(sched, 'current_jobs', return_value=jobs))
            stack.enter_context(patch.object(sched, 'load_schedule', return_value={}))
            self.scheduler.tick(self.now)
            for name in ['executor','trader_executor','guard_executor','scalp_executor']:
                self.assertEqual(len(recorders[name].calls), 1)

    def test_obsolete_queued_spec_does_not_spawn(self):
        spec = sched.JobSpec('backup:example', 'nightly_backup_and_clean.py', schedule_key='backup_job:example', default_times=('20:00',))
        with patch.object(sched, 'current_jobs', return_value=()), patch.object(sched, '_run_process') as run:
            self.scheduler._execute(spec)
        run.assert_not_called()
        self.assertEqual(self.store.job_runs(), [])

    def test_spawn_failure_retries_without_losing_schedule(self):
        spec = sched.JobSpec('fixture', 'fixture.py', 60)
        with patch.object(sched, 'current_jobs', return_value=(spec,)), patch.object(sched, 'load_schedule', return_value={}), patch.object(sched, '_run_process', side_effect=FileNotFoundError):
            self.scheduler._execute(spec)
            self.assertEqual(self.store.get_state('job.last.fixture'), '')
            self.assertEqual(self.scheduler.tick(self.now), [])
        self.assertEqual(self.store.job_runs()[0]['status'], 'failed')

    def test_legacy_naive_timestamp_does_not_crash(self):
        self.store.set_state('job.last.fixture', '2026-09-19T19:00:00')
        self.assertTrue(self.scheduler.due(sched.JobSpec('fixture','fixture.py',60), self.now, {}))

    def test_backup_names_stable_after_reorder(self):
        jobs = [{'id':'custom', 'enabled':True}, {'id':'nightly-default', 'enabled':True}]
        with patch.object(sched, 'list_backup_jobs', return_value=jobs):
            self.assertEqual([job.name for job in sched.backup_job_specs()], ['backup:custom','nightly_backup'])

    def test_tick_consumes_future_failure_and_recovers(self):
        future = Future(); future.set_exception(RuntimeError('synthetic'))
        self.scheduler.running['fixture'] = future
        with patch.object(sched, 'current_jobs', return_value=()), patch.object(sched, 'load_schedule', return_value={}):
            self.scheduler.tick(self.now)
        self.assertEqual(self.store.get_state('job.error.fixture'), 'RuntimeError')
        self.assertNotIn('fixture', self.scheduler.running)

    def test_queued_calendar_job_keeps_its_original_due_slot(self):
        spec = sched.JobSpec('fixture', 'fixture.py', schedule_key='fixture_times', default_times=('20:00',))
        with patch.object(sched, 'current_jobs', return_value=(spec,)), patch.object(sched, 'load_schedule', return_value={}), patch.object(sched, '_run_process', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            self.scheduler._execute(spec, self.now)
        run.assert_called_once()
        self.assertEqual(self.store.job_runs()[0]['status'], 'success')

    def test_schedule_change_invalidates_queued_calendar_work(self):
        spec = sched.JobSpec('fixture', 'fixture.py', schedule_key='fixture_times', default_times=('20:00',))
        with patch.object(sched, 'current_jobs', return_value=(spec,)), patch.object(sched, 'load_schedule', return_value={'fixture_times':['21:00']}), patch.object(sched, '_run_process') as run:
            self.scheduler._execute(spec, self.now)
        run.assert_not_called()

    def test_broken_job_does_not_block_later_jobs(self):
        jobs = (sched.JobSpec('bad','bad.py',60),sched.JobSpec('good','good.py',60))
        def due(spec, now, schedule):
            if spec.name == 'bad':
                raise ValueError('fixture')
            return True
        executor = RecordingExecutor()
        with patch.object(self.scheduler, 'executor', executor), patch.object(self.scheduler, 'due', side_effect=due), patch.object(sched, 'current_jobs', return_value=jobs), patch.object(sched, 'load_schedule', return_value={}):
            self.assertEqual(self.scheduler.tick(self.now), ['good'])
        self.assertEqual(self.store.get_state('job.error.bad'), 'ValueError')

    def test_backup_config_failure_isolated_from_core_job_specs(self):
        with patch.object(sched, 'list_backup_jobs', side_effect=ValueError('fixture')), self.assertLogs(sched.__name__, 'ERROR'):
            self.assertEqual(sched.backup_job_specs(), ())

    def test_completion_write_failure_never_rolls_back_started_job(self):
        spec = sched.JobSpec('fixture', 'fixture.py', 60)
        with patch.object(sched, 'current_jobs', return_value=(spec,)), patch.object(sched, 'load_schedule', return_value={}), patch.object(sched, '_run_process', return_value=subprocess.CompletedProcess([], 0, '', '')), patch.object(self.store, 'finish_job', side_effect=OSError('fixture disk')):
            self.scheduler._execute(spec)
        self.assertNotEqual(self.store.get_state('job.last.fixture'), '')
        self.assertTrue(self.scheduler.pending_completions)
        with patch.object(sched, '_run_process') as replay:
            self.scheduler._flush_completions()
            replay.assert_not_called()
        self.assertEqual(self.scheduler.pending_completions,{})
        self.assertEqual(self.store.job_runs()[0]['status'],'success')


class ManualReviewTests(SchedulerFixture):
    def test_manual_allowlist_refuses_trading(self):
        for name in ['trader', 'demo_scalp', 'position_guard', '../self_improvement']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.request_job(name, actor='fixture')
        self.assertEqual(self.store.job_requests(), [])

    def test_duplicate_concurrent_clicks_are_durable_and_idempotent(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            requests = list(pool.map(lambda _: self.store.request_job('self_improvement', actor='test-admin'), range(16)))
        self.assertEqual(len({r['request_id'] for r in requests}), 1)
        self.assertEqual(sum(not r['deduplicated'] for r in requests), 1)
        reopened = GatewayStore(self.store.path)
        self.assertEqual(len(reopened.pending_job_requests()), 1)
        request = reopened.claim_job_request(requests[0]['request_id'])
        self.assertIsNone(reopened.claim_job_request(request['request_id']))
        self.assertTrue(reopened.request_job('self_improvement')['deduplicated'])

    def test_manual_execution_forces_review_without_advancing_schedule(self):
        request = self.store.request_job('self_improvement', actor='fixture')
        last = self.now.replace(hour=14).isoformat()
        self.store.set_state('job.last.self_improvement', last)
        spec = next(job for job in sched.JOBS if job.name == 'self_improvement')
        result = subprocess.CompletedProcess([], 0, 'review only', '')
        with patch.object(sched, '_run_process', return_value=result) as run:
            self.scheduler._execute_manual(spec, request['request_id'])
        command = run.call_args.args[0]
        self.assertEqual(command[-1], '--force')
        self.assertTrue(command[1].endswith('self_improvement_engine.py'))
        self.assertNotIn('--publish', command)
        self.assertEqual(self.store.get_state('job.last.self_improvement'), last)
        self.assertEqual(self.store.job_runs()[0]['status'], 'success')
        final = self.store.job_requests()[0]
        self.assertEqual(final['status'], 'success')
        self.assertEqual(final['run_id'], self.store.job_runs()[0]['id'])
        self.assertFalse(self.store.request_job('self_improvement')['deduplicated'])

    def test_tick_dispatches_manual_outside_schedule_and_deduplicates(self):
        self.store.request_job('self_improvement')
        executor = RecordingExecutor()
        with patch.object(self.scheduler, 'executor', executor), patch.object(sched, 'current_jobs', return_value=()), patch.object(sched, 'load_schedule', return_value={}):
            self.assertEqual(self.scheduler.tick(self.now), ['self_improvement'])
            self.assertEqual(self.scheduler.tick(self.now), [])
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(self.store.job_requests()[0]['status'], 'pending')
        self.scheduler.running.clear()

    def test_scheduled_review_keeps_precedence_and_catches_up_after_manual(self):
        spec = next(job for job in sched.JOBS if job.name == 'self_improvement')
        self.store.set_state('job.last.self_improvement', self.now.replace(hour=14).isoformat())
        self.store.request_job('self_improvement')
        executor = RecordingExecutor()
        with patch.object(self.scheduler, 'executor', executor), patch.object(sched, 'current_jobs', return_value=(spec,)), patch.object(sched, 'load_schedule', return_value={}):
            self.scheduler.tick(self.now)
        self.assertEqual(executor.calls[0][0], self.scheduler._execute)
        self.assertEqual(len(executor.calls), 1)
        self.assertTrue(self.scheduler.due(spec, self.now.replace(minute=5), {}))
        self.scheduler.running.clear()

    def test_manual_timeout_recorded_without_automatic_replay(self):
        request = self.store.request_job('self_improvement')
        spec = next(job for job in sched.JOBS if job.name == 'self_improvement')
        with patch.object(sched, '_run_process', side_effect=subprocess.TimeoutExpired('fixture', 1200)):
            self.scheduler._execute_manual(spec, request['request_id'])
        self.assertEqual(self.store.job_runs()[0]['return_code'], 124)
        self.assertEqual(self.store.job_requests()[0]['status'], 'failed')
        self.assertEqual(self.store.pending_job_requests(), [])

    def test_restart_marks_inflight_unknown_but_preserves_pending(self):
        request = self.store.request_job('self_improvement')
        self.store.claim_job_request(request['request_id'])
        self.store.recover_processing()
        self.assertEqual(self.store.job_requests()[0]['status'], 'failed')
        self.assertEqual(self.store.job_runs()[0]['return_code'], 125)
        self.store.request_job('self_improvement')
        self.store.recover_processing()
        self.assertEqual(len(self.store.pending_job_requests()), 1)


class ProcessAndToolTests(TemporaryTest):
    def test_worker_lock_loser_never_overwrites_pid(self):
        import fcntl
        from okxquant_gateway import worker
        lock = self.root/'worker.lock'; pid = self.root/'worker.pid'; pid.write_text('123')
        with lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(worker, 'LOCK_FILE', lock), patch.object(worker, 'PID_FILE', pid), patch.object(worker, 'log'):
                worker.run()
        self.assertEqual(pid.read_text(), '123')

    def test_supervisor_does_not_spawn_while_worker_owns_lock(self):
        import fcntl
        from okxquant_gateway import supervisor
        pid = self.root/'worker.pid'; lock = self.root/'.okxquant_gateway.lock'
        with lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(supervisor, 'PID_FILE', pid), patch.object(supervisor, '_owned_process', None), patch.object(supervisor.subprocess, 'Popen') as spawn:
                self.assertEqual(supervisor.ensure_worker(), 0)
                spawn.assert_not_called()

    def test_scheduler_loop_recovers_tick_failure(self):
        from okxquant_gateway import worker
        stopped = threading.Event(); scheduler = Mock()
        def tick():
            stopped.set()
            raise ValueError('fixture')
        scheduler.tick.side_effect = tick
        with patch.object(worker, 'log') as log:
            worker.schedule_loop(scheduler, stopped)
        log.assert_called_once()
        self.assertIn('ValueError', log.call_args.args[0])

    def test_timeout_kills_entire_job_group(self):
        process = Mock(pid=987654)
        process.communicate.side_effect = [subprocess.TimeoutExpired('fixture', 3), ('', '')]
        with patch.object(sched.subprocess, 'Popen', return_value=process) as spawn, patch.object(sched.os, 'killpg') as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                sched._run_process(['fixture'], timeout=3)
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        kill.assert_called_once_with(process.pid, sched.signal.SIGKILL)
        self.assertEqual(process.communicate.call_count, 2)

    def test_log_failure_does_not_kill_scheduler_thread(self):
        from okxquant_gateway import worker
        with patch.object(worker, 'LOG_FILE', self.root/'worker.log'), patch.object(Path, 'open', side_effect=OSError):
            worker.log('fixture')

    def test_cleanup_never_runs_host_shell_or_follows_links(self):
        from scripts import cleanup_disk
        outside = self.root/'outside'; outside.mkdir(); old = outside/'old'; old.write_text('keep')
        (self.root/'backups').mkdir()
        (self.root/'backups/tmp').symlink_to(outside, target_is_directory=True)
        with patch.object(cleanup_disk, 'WORKSPACE_DIR', str(self.root)), patch.object(cleanup_disk.subprocess, 'run') as run:
            self.assertEqual(cleanup_disk.clean_system_caches(), [])
            run.assert_not_called()
        self.assertEqual(old.read_text(), 'keep')

    def test_diagnostic_imports_never_call_account(self):
        with patch('subprocess.run') as run:
            importlib.import_module('scripts.debug_aggregate_orders')
            importlib.import_module('scripts.debug_audit_bills')
        run.assert_not_called()

    def test_registry_does_not_claim_verified_health_or_sandbox(self):
        from okxquant_gateway import plugins
        with patch.object(plugins, '_env', return_value={}):
            rows = plugins.plugin_statuses()
        self.assertEqual(len({row['plugin_id'] for row in rows}), len(rows))
        for row in rows:
            self.assertFalse(row['health_verified'])
            self.assertFalse(row['permissions_enforced'])
            self.assertNotEqual(row['health'], 'healthy')

    def test_test_copy_filter_excludes_links_and_env(self):
        from scripts import run_tests
        (self.root/'linked').symlink_to(self.root/'absent')
        ignored = run_tests._source_ignore(str(self.root), ['linked','.env','.env.local','safe.py'])
        self.assertEqual(ignored, {'linked','.env','.env.local'})
        with patch.dict(os.environ, {'ARBITRARY_SECRET': 'fixture-only'}):
            self.assertNotIn('ARBITRARY_SECRET', run_tests.test_environment(self.root))

    def test_release_and_container_static_safety(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root/'.github/workflows/publish-release.yml').read_text()
        self.assertLess(workflow.index('python scripts/run_tests.py'), workflow.index('docker/login-action'))
        self.assertIn('persist-credentials: false', workflow)
        self.assertNotIn('id-token: write', workflow)
        entrypoint = (root/'docker/entrypoint.sh').read_text()
        self.assertIn('exec gosu okxquant:okxquant "$@"', entrypoint)
        self.assertIn('umask 077', entrypoint)
        legacy = (root/'deploy/okxquant-scheduler.service').read_text()
        self.assertIn('User=okxquant', legacy)
        self.assertIn('-m okxquant_gateway.worker', legacy)


if __name__ == '__main__':
    unittest.main()
