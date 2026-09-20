"""Second-pass ops review. Run ONLY through scripts/run_tests.py.

Normative regression assertions intentionally fail for confirmed release blockers;
no production scripts/containers/networks are executed by these tests.
"""
from __future__ import annotations
from concurrent.futures import Future
from contextlib import ExitStack
from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import signal
import sys
import threading
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from okxquant_gateway import scheduler as scheduler_module
from okxquant_gateway.store import GatewayStore

ROOT = Path(__file__).resolve().parents[1]


class ManualCompletionCrossReview(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=GatewayStore(self.root/"gateway.db")
        self.scheduler=scheduler_module.GatewayScheduler(self.store)
        self.addCleanup(self.scheduler.shutdown)
        self.spec=next(job for job in scheduler_module.JOBS if job.name=="self_improvement")

    def _assert_completion_recovers(self, error, process_error=None):
        request=self.store.request_job("self_improvement",actor="cross-review")
        future=Future()
        with patch.object(scheduler_module,"_run_process",return_value=subprocess.CompletedProcess([],0,"done",""),side_effect=process_error) as run,patch.object(self.store,"finish_job",side_effect=error):
            try:
                self.scheduler._execute_manual(self.spec,request["request_id"])
            except Exception as exc:
                future.set_exception(exc)
            else:
                future.set_result(None)
        run.assert_called_once()
        self.scheduler.running["self_improvement"]=future
        # Storage is healthy again, worker remains alive, and normal ticks resume.
        with patch.object(scheduler_module,"current_jobs",return_value=()),patch.object(scheduler_module,"load_schedule",return_value={}),patch.object(scheduler_module,"_run_process") as replay:
            for _ in range(3):self.scheduler.tick(datetime(2026,9,19,21,0,tzinfo=scheduler_module.BJ_TZ))
            replay.assert_not_called()  # Completed model work must not be blindly replayed.
        row=self.store.job_requests()[0]
        self.assertIn(row["status"],("success","failed"),
            f"CROSS-OPS-02: completion Future was removed but durable request remains {row['status']}; no future/pending claim can finish it until worker restart")

    def test_completion_persistence_failure_must_not_leave_manual_request_running_forever(self):
        self._assert_completion_recovers(OSError("temporary completion storage failure"))

    def test_sqlite_completion_failure_must_reconcile_without_reexecuting(self):
        self._assert_completion_recovers(sqlite3.OperationalError("database is locked"))

    def test_post_spawn_io_failure_also_retries_terminal_metadata(self):
        self._assert_completion_recovers(sqlite3.OperationalError("database is locked"),
            process_error=RuntimeError("Job communication failed: OSError"))

    def test_slow_manual_completion_retry_does_not_consume_trader_window(self):
        # A deterministic clock replaces sleeping for SQLite's ten-second timeout.
        # The completion write is contended; ordinary WAL reads remain available.
        request=self.store.request_job("self_improvement")
        with patch.object(scheduler_module,"_run_process",return_value=subprocess.CompletedProcess([],0,"done","")),patch.object(self.store,"finish_job",side_effect=sqlite3.OperationalError("database is locked")):
            self.scheduler._execute_manual(self.spec,request["request_id"])
        clock=[datetime(2026,9,19,20,0,0,tzinfo=scheduler_module.BJ_TZ)]
        class Clock:
            @staticmethod
            def now(tz=None):return clock[0]
            fromisoformat=staticmethod(datetime.fromisoformat)
        def finish(*args, **kwargs):
            # Respect an implementation that uses a genuinely nonblocking retry.
            # GatewayStore's default SQLite timeout is ten seconds.
            if kwargs.get("timeout", 10) > 0:
                clock[0]=clock[0].replace(second=10)
            raise sqlite3.OperationalError("database is locked")
        class RecordingExecutor:
            def __init__(self):self.calls=[]
            def submit(self,*args):
                self.calls.append(args);return Future()
        trader=next(job for job in scheduler_module.JOBS if job.name=="trader")
        executor=RecordingExecutor()
        with patch.object(scheduler_module,"datetime",Clock),patch.object(scheduler_module,"current_jobs",return_value=(trader,)),patch.object(scheduler_module,"load_schedule",return_value={}),patch.object(self.store,"finish_job",side_effect=finish),patch.object(self.scheduler,"trader_executor",executor):
            launched=self.scheduler.tick()
        self.assertIn("trader",launched,
            "CROSS-OPS-03: a manual completion retry blocked tick past the unchanged 10-second trader admission window")

    def test_duplicate_clicks_create_one_atomic_request_and_claim(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as pool:
            requests=list(pool.map(lambda _:self.store.request_job("self_improvement"),range(10)))
        self.assertEqual(len({r["request_id"] for r in requests}),1)
        with ThreadPoolExecutor(max_workers=6) as pool:
            claims=list(pool.map(lambda _:self.store.claim_job_request(requests[0]["request_id"]),range(10)))
        self.assertEqual(sum(row is not None for row in claims),1)
        self.assertEqual(len(self.store.job_runs()),1)

    def test_restart_fails_unknown_execution_and_preserves_unstarted_work(self):
        request=self.store.request_job("self_improvement")
        self.store.claim_job_request(request["request_id"])
        self.store.recover_processing()
        self.assertEqual(self.store.job_requests()[0]["status"],"failed")
        self.assertEqual(self.store.job_runs()[0]["return_code"],125)
        pending=self.store.request_job("self_improvement")
        self.store.recover_processing()
        self.assertEqual(self.store.pending_job_requests()[0]["request_id"],pending["request_id"])

    def test_manual_review_does_not_advance_schedule_or_dispatch_trades(self):
        self.store.set_state("job.last.self_improvement","2026-09-19T14:00:00+08:00")
        for name in ("trader","position_guard","demo_scalp","nightly_backup"):
            with self.subTest(name=name),self.assertRaises(ValueError):self.store.request_job(name)
        request=self.store.request_job("self_improvement")
        with patch.object(scheduler_module,"_run_process",return_value=subprocess.CompletedProcess([],0,"done","")) as run:
            self.scheduler._execute_manual(self.spec,request["request_id"])
        self.assertEqual(self.store.get_state("job.last.self_improvement"),"2026-09-19T14:00:00+08:00")
        self.assertIn("--force",run.call_args.args[0])
        self.assertEqual(self.store.job_requests()[0]["status"],"success")

    def test_manual_timeout_is_terminal_and_not_replayed(self):
        request=self.store.request_job("self_improvement")
        with patch.object(scheduler_module,"_run_process",side_effect=subprocess.TimeoutExpired("fake",1200)):
            self.scheduler._execute_manual(self.spec,request["request_id"])
        self.assertEqual(self.store.job_requests()[0]["status"],"failed")
        self.assertEqual(self.store.job_runs()[0]["return_code"],124)
        self.assertEqual(self.store.pending_job_requests(),[])

    def test_maintenance_saturation_does_not_occupy_trader_or_guard_executors(self):
        from concurrent.futures import ThreadPoolExecutor
        busy=threading.Event();release=threading.Event();trader=threading.Event();guard=threading.Event()
        general=ThreadPoolExecutor(max_workers=1)
        def occupy():
            busy.set();release.wait(4)
        occupied=general.submit(occupy)
        self.assertTrue(busy.wait(1))
        request=self.store.request_job("self_improvement")
        jobs=tuple(job for job in scheduler_module.JOBS if job.name in {"trader","position_guard"})
        def execute(spec,*_):
            (trader if spec.name=="trader" else guard).set()
        try:
            with patch.object(self.scheduler,"executor",general),patch.object(self.scheduler,"_execute",side_effect=execute),patch.object(scheduler_module,"current_jobs",return_value=jobs),patch.object(scheduler_module,"load_schedule",return_value={}),patch.object(scheduler_module,"_run_process",return_value=subprocess.CompletedProcess([],0,"done","")):
                self.scheduler.tick(datetime(2026,9,19,20,0,tzinfo=scheduler_module.BJ_TZ))
                self.assertTrue(trader.wait(1));self.assertTrue(guard.wait(1))
                self.assertEqual(self.store.job_requests()[0]["status"],"pending")
                release.set();self.scheduler.running["self_improvement"].result(timeout=3)
                self.assertEqual(self.store.job_requests()[0]["status"],"success")
        finally:
            release.set();general.shutdown(wait=True,cancel_futures=True)

    def test_entry_cadences_and_live_pause_does_not_disable_guard(self):
        specs={job.name:job for job in scheduler_module.JOBS}
        self.assertEqual(specs["trader"].interval_seconds,900)
        self.assertEqual(specs["position_guard"].interval_seconds,60)
        self.assertEqual(specs["demo_scalp"].interval_seconds,60)
        with patch("scripts.okx_runtime._load_dotenv",return_value={"OKXQUANT_AUTOTRADE_ENABLED":"1"}),patch.object(scheduler_module,"backup_job_specs",return_value=()):
            self.assertIn("trader",{job.name for job in scheduler_module.current_jobs()})
        with patch("scripts.okx_runtime._load_dotenv",return_value={"OKXQUANT_AUTOTRADE_ENABLED":"0"}),patch.object(scheduler_module,"backup_job_specs",return_value=()):
            names={job.name for job in scheduler_module.current_jobs()}
            self.assertNotIn("trader",names);self.assertIn("position_guard",names)


class DockerPersistenceCrossReview(unittest.TestCase):
    def test_custom_pipeline_directory_is_on_a_compose_persistent_volume(self):
        # Inspect only its directory configuration, not the custom AST interpreter.
        from okxquant_backend import interceptor_manager
        write_directory=getattr(interceptor_manager,"CUSTOM_PLUGINS_DIR",interceptor_manager.PLUGINS_DIR)
        relative=write_directory.resolve().relative_to(ROOT.resolve())
        image_path=PurePosixPath('/app').joinpath(*relative.parts)
        compose=(ROOT/'compose.yaml').read_text(encoding='utf-8')
        targets=[PurePosixPath(match.group(1)) for match in re.finditer(r'^\s*-\s+[^:#\s]+:(/[^\s:#]+)',compose,re.M)]
        self.assertTrue(any(image_path==target or target in image_path.parents for target in targets),
            f"CROSS-OPS-01: custom files are written to {image_path}, outside persistent volume targets {targets}; container recreation loses files while /app/data registration survives")

    def test_pipeline_overlay_writes_and_image_replacement_keep_files_and_metadata(self):
        # Only storage is exercised. A07 owns validation/interpreter semantics.
        from okxquant_backend import interceptor_manager as manager
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);image=root/'image-one';image.mkdir();data=root/'data';data.mkdir()
            overlay=data/'interceptors';config=data/'interceptor_plugins.json'
            (image/'builtin.py').write_text('read-only source original')
            validator=lambda code,**kwargs:SimpleNamespace(source=code)
            with patch.object(manager,'PLUGINS_DIR',image),patch.object(manager,'CUSTOM_PLUGINS_DIR',overlay),patch.object(manager,'CONFIG_FILE',config),patch.object(manager,'validate_rule',side_effect=validator),patch.object(manager,'get_plugin_detail',return_value={}):
                manager.create_plugin('custom.py','custom saved source')
                manager.save_plugin_code('builtin.py','administrator overlay')
                self.assertEqual((image/'builtin.py').read_text(),'read-only source original')
                self.assertFalse((image/'custom.py').exists())
                self.assertEqual((overlay/'custom.py').read_text(),'custom saved source')
                self.assertEqual((overlay/'builtin.py').read_text(),'administrator overlay')
                self.assertEqual((overlay/'custom.py').stat().st_mode&0o777,0o600)
                second=root/'image-two';second.mkdir();(second/'builtin.py').write_text('new image builtin')
                with patch.object(manager,'PLUGINS_DIR',second):
                    self.assertEqual(manager._resolve('custom.py'),overlay/'custom.py')
                    self.assertEqual(manager._resolve('builtin.py'),overlay/'builtin.py')
                    self.assertIn('custom.py',manager.load_config()['pipeline_order'])

    def test_compose_image_and_entrypoint_agree_on_writable_env_location(self):
        compose=(ROOT/'compose.yaml').read_text();image=(ROOT/'Dockerfile').read_text();entry=(ROOT/'docker/entrypoint.sh').read_text()
        self.assertIn('OKXQUANT_ENV_FILE: /app/config/.env',compose)
        self.assertIn('OKXQUANT_ENV_FILE=/app/config/.env',image)
        self.assertIn('ENV_PATH=${OKXQUANT_ENV_FILE:-/app/config/.env}',entry)
        self.assertIn('okxquant_config:/app/config',compose)
        self.assertIn('env_file:',compose);self.assertIn('- ./.env',compose)
        self.assertIn('[ "$ENV_PATH" = /app/config/.env ]',entry)

    def test_root_volume_migration_precedes_privilege_drop_for_every_mount(self):
        entry=(ROOT/'docker/entrypoint.sh').read_text();compose=(ROOT/'compose.yaml').read_text()
        targets=re.findall(r'^\s*-\s+[^:#\s]+:(/[^\s:#]+)',compose,re.M)
        loop=re.search(r'for directory in (.+?); do',entry).group(1).split()
        self.assertTrue(set(targets)<=set(loop))
        self.assertLess(entry.index('chown -hR okxquant:okxquant "$directory"'),entry.index('exec gosu okxquant:okxquant "$@"'))
        self.assertIn('chown okxquant:okxquant "$ENV_PATH"',entry)
        self.assertIn('umask 077',entry)
        self.assertIn('chmod 0600 "$ENV_PATH"',entry)
        self.assertIn('no-new-privileges:true',compose)
        self.assertIn('init: true',compose)

    def test_persisted_env_overrides_stale_compose_environment_in_all_loaders(self):
        from okxquant_backend import config,notifications
        from scripts import okx_runtime
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runtime.env';path.write_text('OKXQUANT_AUTOTRADE_ENABLED=1\nLLM_MODEL=saved-model\n')
            with patch.dict(os.environ,{"OKXQUANT_ENV_FILE":str(path),"OKXQUANT_AUTOTRADE_ENABLED":"0","LLM_MODEL":"inherited-old"}),patch("okxquant_gateway.secrets.load_secrets",return_value={}),patch.object(notifications,"SECRET_LOADER",lambda:{}):
                self.assertEqual(config.environment_file(),path)
                self.assertEqual(okx_runtime._load_dotenv()["OKXQUANT_AUTOTRADE_ENABLED"],"1")
                self.assertEqual(notifications._env()["LLM_MODEL"],"saved-model")
                config.load_dotenv(config.environment_file())
                self.assertEqual(os.environ["LLM_MODEL"],"saved-model")

    def test_ci_and_release_gate_use_isolated_full_library_discovery(self):
        ci=(ROOT/'.github/workflows/ci.yml').read_text();release=(ROOT/'.github/workflows/publish-release.yml').read_text()
        for workflow in (ci,release):
            self.assertIn('python scripts/run_tests.py --verbose',workflow)
            self.assertNotIn('unittest discover',workflow)
            self.assertNotIn('--pattern test_audit_',workflow)
        self.assertLess(release.index('python scripts/run_tests.py --verbose'),release.index('docker/login-action'))
        self.assertIn('npm run test:unit',ci)
        runner=(ROOT/'scripts/run_tests.py').read_text()
        self.assertIn('default="test_*.py"',runner)
        for token in ('socket.socket.connect','subprocess.Popen','OKXQUANT_TEST_ROOT','.okxquant-test-sandbox'):
            self.assertIn(token,runner)


class WorkerLifecycleCrossReview(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_flock_loser_cannot_publish_pid_or_recover_someone_elses_jobs(self):
        import fcntl
        from okxquant_gateway import worker
        lock=self.root/'worker.lock';pid=self.root/'worker.pid';pid.write_text('777')
        with lock.open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(worker,'LOCK_FILE',lock),patch.object(worker,'PID_FILE',pid),patch.object(worker,'log'),patch.object(worker,'GatewayStore') as store:
                worker.run();store.assert_not_called()
        self.assertEqual(pid.read_text(),'777')

    def test_supervisor_uses_file_not_unread_pipe_and_reaps_owned_exit(self):
        from okxquant_gateway import supervisor
        old=Mock();process=Mock(pid=7654321);process.poll.return_value=1
        with patch.object(supervisor,'PID_FILE',self.root/'worker.pid'),patch.object(supervisor,'LOG_FILE',self.root/'worker.log'),patch.object(supervisor,'current_pid',return_value=0),patch.object(supervisor,'_worker_lock_held',return_value=False),patch.object(supervisor,'_owned_process',old),patch.object(supervisor,'_owned_pid',0),patch.object(supervisor.subprocess,'Popen',return_value=process) as spawn:
            self.assertEqual(supervisor.ensure_worker(),0)
        old.poll.assert_called_once()
        options=spawn.call_args.kwargs
        self.assertEqual(options['stdout'].name,str(self.root/'worker.log'))
        self.assertEqual(options['stderr'],subprocess.STDOUT)
        self.assertEqual(options['stdin'],subprocess.DEVNULL)

    def test_supervisor_does_not_spawn_during_worker_lock_owned_startup(self):
        import fcntl
        from okxquant_gateway import supervisor
        with (self.root/'.okxquant_gateway.lock').open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(supervisor,'PID_FILE',self.root/'worker.pid'),patch.object(supervisor,'current_pid',return_value=0),patch.object(supervisor,'_owned_process',None),patch.object(supervisor.subprocess,'Popen') as spawn:
                self.assertEqual(supervisor.ensure_worker(),0);spawn.assert_not_called()

    def test_both_job_pipes_are_drained_and_process_group_killed_on_timeout(self):
        process=Mock(pid=987654,returncode=0)
        process.communicate.side_effect=[subprocess.TimeoutExpired('fixture',1),('','')]
        with patch.object(scheduler_module.subprocess,'Popen',return_value=process) as spawn,patch.object(scheduler_module.os,'killpg') as kill:
            with self.assertRaises(subprocess.TimeoutExpired):scheduler_module._run_process(['offline-fixture'],timeout=1)
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        self.assertEqual(spawn.call_args.kwargs['stdout'],subprocess.PIPE)
        self.assertEqual(spawn.call_args.kwargs['stderr'],subprocess.PIPE)
        kill.assert_called_once_with(process.pid,signal.SIGKILL)
        self.assertEqual(process.communicate.call_count,2)

    def test_notify_transport_cannot_block_schedule_loop(self):
        from okxquant_gateway import worker
        stop=threading.Event();scheduler=Mock();calls=[]
        def tick():
            calls.append(1)
            if len(calls)==1:raise ValueError('one bad tick')
            stop.set();return ['trader']
        scheduler.tick.side_effect=tick
        with patch.object(worker,'log'),patch.object(worker,'NotificationChannelAdapter') as adapter:
            worker.schedule_loop(scheduler,stop)
            adapter.assert_not_called()
        self.assertEqual(len(calls),2)

    def test_legacy_systemd_scheduler_uses_same_flock_owner(self):
        old=(ROOT/'deploy/okxquant-scheduler.service').read_text();worker=(ROOT/'deploy/okxquant-gateway.service').read_text()
        for unit in (old,worker):
            self.assertIn('-m okxquant_gateway.worker',unit)
            self.assertIn('User=okxquant',unit)
            self.assertIn('NoNewPrivileges=true',unit)
        self.assertNotIn('okxquant_backend.scheduler',old)


class EvolutionOperationsCrossReview(unittest.TestCase):
    def setUp(self):
        # Only a disposable runner snapshot is importable here.
        sys.path.insert(0,str(ROOT/'scripts'))
        from scripts import self_improvement_engine, memory_registry, trade_lock
        self.engine=self_improvement_engine;self.memory=memory_registry
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.scope='okx:demo:cross-ops'
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        fields={'DATA_DIR':str(self.root),'WORKSPACE_DIR':str(self.root),'LOGS_DIR':str(self.root),
                'LOG_FILE':str(self.root/'review.log'),'AI_MEMORY_FILE':str(self.root/'ai_trading_memory.json'),
                'AI_MEMORY_MD_FILE':str(self.root/'AI_TRADING_MEMORY.md'),
                'REPORT_JSON_FILE':str(self.root/'self_improvement_report.json'),
                'LEDGER_JSON_FILE':str(self.root/'trading_ledger.json'),
                'EVOLUTION_LOCK_FILE':str(self.root/'.self_improvement.lock'),
                'EVOLUTION_LAST_PROMPT_FILE':str(self.root/'last_prompt.txt')}
        for key,value in fields.items():self.stack.enter_context(patch.object(self.engine,key,value))
        self.stack.enter_context(patch.object(self.engine,'log_msg'))
        self.stack.enter_context(patch.object(self.memory,'scope_of',side_effect=lambda scope=None:scope or self.scope))
        self.stack.enter_context(patch.object(trade_lock,'PATH',self.root/'.writer'))
        self.stack.enter_context(patch('qq_notifier.notify_evolution_report'))
        (self.root/'AI_TRADING_MEMORY.md').write_text('Approved historical observations only.')
        self.old_report={'timestamp':'2026-09-18 20:00:00','account_scope':self.scope,'insights':['old approved report']}
        (self.root/'self_improvement_report.json').write_text(json.dumps(self.old_report))

    def _run(self,reply=None,phase=None):
        reply=reply or {'change_status':'NO_CHANGE'}
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.engine,'load_closed_trades',return_value=[]))
            stack.enter_context(patch.object(self.engine,'call_llm_evolution_review',return_value=reply))
            if phase=='load_sources':stack.enter_context(patch.object(self.engine,'load_closed_trades',side_effect=OSError('fixture source unreadable')))
            if phase=='model_review':
                from okxquant_backend.llm_transport import LLMRequestError
                stack.enter_context(patch.object(self.engine,'call_llm_evolution_review',side_effect=LLMRequestError(503,2,'http_error')))
            if phase=='candidate_persistence':stack.enter_context(patch.object(self.memory,'stage_review',side_effect=sqlite3.OperationalError('database is locked')))
            if phase in {'review_draft','report_persistence'}:
                original=self.engine.atomic_write_json
                filename='self_improvement_review_draft.json' if phase=='review_draft' else 'self_improvement_report.json'
                def write(path,value):
                    if Path(path).name==filename:raise OSError('fixture full disk')
                    return original(path,value)
                stack.enter_context(patch.object(self.engine,'atomic_write_json',side_effect=write))
            return self.engine.run_self_evolution(force=True)

    def test_each_failure_phase_is_terminal_and_preserves_previous_success_and_memory(self):
        for phase in ('load_sources','model_review','review_draft','candidate_persistence','report_persistence'):
            with self.subTest(phase=phase),self.assertRaises(RuntimeError):self._run(phase=phase)
            status=json.loads((self.root/'self_improvement_status.json').read_text())
            self.assertEqual(status['status'],'failed');self.assertEqual(status['phase'],phase)
            self.assertEqual(json.loads((self.root/'self_improvement_report.json').read_text()),self.old_report)
            self.assertEqual((self.root/'AI_TRADING_MEMORY.md').read_text(),'Approved historical observations only.')
        self.assertFalse((self.root/'memory_registry.db').exists())

    def test_manual_engine_failure_becomes_failed_gateway_request_not_success(self):
        store=GatewayStore(self.root/'gateway.db');scheduler=scheduler_module.GatewayScheduler(store);self.addCleanup(scheduler.shutdown)
        request=store.request_job('self_improvement')
        spec=next(job for job in scheduler_module.JOBS if job.name=='self_improvement')
        def child(command,**_):
            self.assertIn('--force',command)
            try:self._run(phase='model_review')
            except RuntimeError:return subprocess.CompletedProcess(command,1,'','review failed')
            return subprocess.CompletedProcess(command,0,'','')
        with patch.object(scheduler_module,'_run_process',side_effect=child):scheduler._execute_manual(spec,request['request_id'])
        self.assertEqual(store.job_requests()[0]['status'],'failed')
        self.assertEqual(store.job_runs()[0]['return_code'],1)
        self.assertEqual(json.loads((self.root/'self_improvement_status.json').read_text())['phase'],'model_review')

    def test_review_candidate_staging_does_not_take_trade_writer_or_change_active_memory(self):
        from scripts import trade_lock
        before=self.memory.view(self.root,scope=self.scope)['prompt_hash']
        reply={'change_status':'ADD','memory_proposals':[{'action':'ADD','text':'Observe independent market structure before drawing conclusions from a small sample.'}]}
        with patch.object(trade_lock,'writer',side_effect=AssertionError('review must not reserve trading writer')) as writer:
            result=self._run(reply)
        writer.assert_not_called()
        self.assertEqual(result['review_status'],'success')
        self.assertEqual(result['pending_candidate_count'],1)
        self.assertEqual(self.memory.view(self.root,scope=self.scope)['prompt_hash'],before)
        self.assertEqual((self.root/'AI_TRADING_MEMORY.md').read_text(),'Approved historical observations only.')

    def test_publication_and_restore_cannot_run_while_trader_brain_or_review_lock_held(self):
        import fcntl
        for name in ('.ai_factor_trader.lock','.ai_brain_cycle.lock','.self_improvement.lock'):
            with self.subTest(lock=name),(self.root/name).open('a+') as handle:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(self.memory.MemoryConflict):
                    with self.memory.publication_gate(self.root,self.scope):self.fail('publication entered busy cycle')
            with self.memory.publication_gate(self.root,self.scope):pass

    def test_publication_rechecks_account_under_gate(self):
        with self.assertRaises(self.memory.MemoryConflict):
            with self.memory.publication_gate(self.root,'okx:live:different'):self.fail('wrong-account publication')

    def test_worker_crash_overrides_stale_engine_running_status(self):
        from scripts.evolution_status import public_status
        store=GatewayStore(self.root/'okxquant_gateway.db')
        store.begin_job('self_improvement')
        (self.root/'self_improvement_status.json').write_text(json.dumps({'status':'running','phase':'model_review','account_scope':self.scope,'last_attempt_at':'2026-09-18 21:00:00'}))
        store.recover_processing()
        status=public_status(self.root)
        self.assertEqual(status['status'],'failed')
        self.assertEqual(status['last_job']['return_code'],125)
        self.assertEqual(status['last_success_at'],self.old_report['timestamp'])

class LegacySchedulerOwnerTest(unittest.TestCase):
    def test_legacy_main_uses_gateway_single_owner(self):
        from okxquant_backend import scheduler as legacy
        with patch('okxquant_gateway.worker.run') as run,patch.object(legacy,'run_script') as old:
            legacy.main()
            run.assert_called_once_with();old.assert_not_called()
