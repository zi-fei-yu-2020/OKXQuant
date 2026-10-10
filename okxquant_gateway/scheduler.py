"""Gateway-owned scheduler running existing jobs in isolated subprocesses."""
from __future__ import annotations
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import threading
import logging
import os
import signal
import sys
import time
from typing import Any

from okxquant_backend.schedule_store import load_schedule
from okxquant_backend.backup_store import list_jobs as list_backup_jobs
from okxquant_gateway.store import GatewayStore, MANUAL_JOBS

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BJ_TZ = timezone(timedelta(hours=8))
_LAST_RAW_UNSET = object()


@dataclass(frozen=True)
class JobSpec:
    name: str
    script: str
    interval_seconds: int | None = None
    timeout_seconds: int = 600
    schedule_key: str = ""
    default_times: tuple[str, ...] = ()
    phase_seconds: int | None = None


JOBS = (
    JobSpec("position_guard", "position_guard.py", 60, 240),
    JobSpec("evidence_sync", "evidence_sync.py", 300, 60),
    # Keep a five-second hot path after local submissions/fills, but let the
    # job's lightweight due check back off while there is nothing to sample.
    JobSpec("execution_quality", "execution_quality.py", 5, 20),
    JobSpec("ledger_sync", "ledger_monitor.py", 60, 50),
    JobSpec("trader", "ai_factor_trader.py", 15 * 60, 840),
    # The independent minute entry engine is retired. Protection and reconciliation
    # retain their own cadence; only the AI trader may schedule new entries.
    # Stagger non-critical research jobs so Python imports and indicator work
    # do not all hit the CPU at the trader/guard boundary.
    JobSpec("factor_library", "factor_library.py", 60, 55, phase_seconds=25),
    JobSpec("market_observations", "market_observations.py", 60, 55, phase_seconds=45),
    # Read-only macro collector; never an entry engine or model call.
    JobSpec("macro_data", "macro_market.py", 300, 120, phase_seconds=120),
    JobSpec("news", "news_sentiment_harvester.py", 10 * 60, 300),
    JobSpec("daily_briefing", "daily_summary_and_backup.py", None, 600, "briefing_times", ("08:00", "20:00")),
    JobSpec("self_improvement", "self_improvement_engine.py", None, 480, "self_improvement_time", ("02:00", "08:00", "14:00", "20:00")),
)


def _run_process(command: list[str], *, timeout: int, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """A timeout owns the entire job process group, not just its Python parent."""
    process = subprocess.Popen(
        command, cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **(env_overrides or {})},
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            process.communicate()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise
        # Only Popen failures may be retried as never-started jobs. An I/O
        # failure after spawn has an unknown outcome and must not be retried.
        raise RuntimeError(f"Job communication failed: {type(exc).__name__}") from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def backup_job_specs() -> tuple[JobSpec, ...]:
    specs: list[JobSpec] = []
    try:
        jobs = list_backup_jobs()
    except Exception as exc:
        logging.getLogger(__name__).error("Backup schedule unavailable: %s", type(exc).__name__)
        return ()
    for job in jobs:
        if not job.get("enabled"):
            continue
        name = "nightly_backup" if job.get("id") == "nightly-default" else f"backup:{job['id']}"
        specs.append(JobSpec(name, "nightly_backup_and_clean.py", None, 1800, f"backup_job:{job['id']}", tuple(job.get("schedule_times", ["02:00"]))))
    return tuple(specs)


def runtime_controls() -> dict[str, bool]:
    """Read-only operator controls; enabled is not proof of exchange connectivity."""
    from scripts.okx_runtime import _load_dotenv
    values = _load_dotenv()
    return {"gateway_autostart": values.get("OKXQUANT_GATEWAY_AUTOSTART", "1") == "1",
            "automatic_trader": values.get("OKXQUANT_AUTOTRADE_ENABLED", "1") == "1"}


def current_jobs() -> tuple[JobSpec, ...]:
    # Operator pause stops scheduled inference/new entries, never position protection.
    # Read the writable configuration each tick so a pause does not need a restart.
    from scripts.okx_runtime import _load_dotenv
    automatic = _load_dotenv().get("OKXQUANT_AUTOTRADE_ENABLED", "1") == "1"
    jobs = tuple(job for job in JOBS if automatic or job.name != "trader")
    from scripts.runtime_features import status as runtime_status
    features = runtime_status()["features"]
    disabled_jobs = set()
    if not features["factor_snapshots"]:
        disabled_jobs.add("factor_library")
    if not features["market_observations"]:
        disabled_jobs.add("market_observations")
    if not features["automatic_review"]:
        disabled_jobs.add("self_improvement")
    # Manual self-improvement requests are dispatched separately below and
    # remain available even when its automatic schedule is disabled.
    jobs = tuple(job for job in jobs if job.name not in disabled_jobs)
    return (*jobs, *backup_job_specs())


def scheduler_snapshot(store: GatewayStore) -> dict[str, Any]:
    schedule = load_schedule()
    now = datetime.now(BJ_TZ)
    jobs = []
    for spec in current_jobs():
        raw = store.get_state(f"job.last.{spec.name}")
        try:
            last = datetime.fromisoformat(raw) if raw else None
            if last and last.tzinfo is None:
                last = last.replace(tzinfo=BJ_TZ)
        except ValueError:
            last = None
        value = schedule.get(spec.schedule_key) if spec.schedule_key else None
        times = tuple(str(item) for item in value) if isinstance(value, list) else ((str(value),) if isinstance(value, str) else spec.default_times)
        jobs.append({
            "name": spec.name,
            "script": spec.script,
            "last_scheduled_at": last.isoformat() if last else "",
            "schedule": f"每 {spec.interval_seconds // 60} 分钟" if spec.interval_seconds else "、".join(times),
            "timezone": "Asia/Shanghai",
            "overdue": bool(spec.interval_seconds and last and (now - last).total_seconds() > spec.interval_seconds * 2),
        })
    return {"jobs": jobs, "recent_runs": store.job_runs(30), "manual_requests": store.job_requests(30),
            "ai_recoveries": store.ai_recoveries(30)}


class GatewayScheduler:
    def __init__(self, store: GatewayStore, max_workers: int = 3):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="okxquant-job")
        self.guard_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="okxquant-position-guard")
        self.ledger_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="okxquant-ledger-sync")
        # Long maintenance jobs must not delay the unchanged entry time window.
        self.backup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="okxquant-backup")
        self.trader_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="okxquant-trader")
        self.running: dict[str, Future[None]] = {}
        self.pending_completions = {}
        self.completion_lock = threading.Lock()
        self.retry_after: dict[str, float] = {}

    @staticmethod
    def _parse_last_at(raw: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(raw) if raw else None
            return parsed.replace(tzinfo=BJ_TZ) if parsed and parsed.tzinfo is None else parsed
        except ValueError:
            return None

    def _last_at(self, name: str) -> datetime | None:
        return self._parse_last_at(self.store.get_state(f"job.last.{name}"))

    def initialize_migration_baseline(self, now: datetime | None = None) -> None:
        now = now or datetime.now(BJ_TZ)
        for spec in current_jobs():
            if not self.store.get_state(f"job.last.{spec.name}"):
                self.store.set_state(f"job.last.{spec.name}", now.isoformat())

    def _scheduled_times(self, spec: JobSpec, schedule: dict[str, Any]) -> tuple[str, ...]:
        if spec.schedule_key.startswith("backup_job:"):
            return spec.default_times
        value = schedule.get(spec.schedule_key)
        if isinstance(value, list):
            return tuple(str(item) for item in value)
        if isinstance(value, str):
            return (value,)
        return spec.default_times

    def due(self, spec: JobSpec, now: datetime, schedule: dict[str, Any], *, last_raw=_LAST_RAW_UNSET) -> bool:
        # Scheduler ticks provide a bulk-fetched value; direct callers retain the
        # original behavior and read the single key on demand.
        last = self._last_at(spec.name) if last_raw is _LAST_RAW_UNSET else self._parse_last_at(str(last_raw or ""))
        if spec.name == "ledger_sync":
            from scripts.ledger_monitor import should_run
            return should_run(last.timestamp() if last else 0, now.timestamp())
        if spec.name == "execution_quality":
            from scripts.execution_quality import should_run
            return should_run(last.timestamp() if last else 0, now.timestamp())
        if spec.interval_seconds:
            if spec.name == "trader":
                slot = int(now.timestamp()) // spec.interval_seconds
                last_slot = int(last.timestamp()) // spec.interval_seconds if last else -1
                return slot > last_slot and int(now.timestamp()) % spec.interval_seconds < 10
            if spec.phase_seconds is not None:
                shifted=int(now.timestamp())-spec.phase_seconds
                slot=shifted//spec.interval_seconds
                last_slot=(int(last.timestamp())-spec.phase_seconds)//spec.interval_seconds if last else -1
                return slot>last_slot and shifted%spec.interval_seconds<10
            return not last or (now - last).total_seconds() >= spec.interval_seconds
        if spec.name == "self_improvement":
            # A manual review can overlap a scheduled slot. Catch up that slot
            # once afterwards without letting manual work advance the clock.
            slots = []
            for value in self._scheduled_times(spec, schedule):
                try:
                    hour, minute = map(int, value.split(":"))
                    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                except (ValueError, TypeError):
                    continue
                if slot <= now:
                    slots.append(slot)
            return bool(slots) and (last is None or last < max(slots))
        minute = now.strftime("%H:%M")
        if minute not in self._scheduled_times(spec, schedule):
            return False
        return not last or last.date() != now.date() or last.strftime("%H:%M") != minute

    def _finish_job(self, run_id, code, detail):
        # Retry metadata persistence, never re-run completed model/trading work.
        with self.completion_lock:
            self.pending_completions[run_id] = (code, detail)
        try:
            self.store.finish_job(run_id, code, detail, timeout=0)
        except Exception:
            return False
        with self.completion_lock:
            self.pending_completions.pop(run_id, None)
        return True

    def _flush_completions(self):
        with self.completion_lock:
            pending = list(self.pending_completions.items())
        for run_id, (code, detail) in pending:
            self._finish_job(run_id, code, detail)

    def _execute(self, spec: JobSpec, scheduled_at: datetime | None = None) -> None:
        # A queued job must not run with an obsolete/disabled configuration.
        active = next((job for job in current_jobs() if job.name == spec.name), None)
        if active != spec:
            return
        now = datetime.now(BJ_TZ)
        eligibility_time = now if spec.name == "trader" else scheduled_at or now
        if not self.due(spec, eligibility_time, load_schedule()):
            return
        previous = self.store.get_state(f"job.last.{spec.name}")
        run_id = self.store.begin_job(spec.name)
        self.store.set_state(f"job.last.{spec.name}", now.isoformat())
        self._run_job(spec, run_id, previous=previous)

    def _self_improvement_window_open(self, now: datetime, spec: JobSpec) -> bool:
        """Run costly review inference only between protected trader slots."""
        trader = next((job for job in current_jobs() if job.name == "trader"), None)
        if trader is None:
            return True
        trader_future = self.running.get("trader")
        if trader_future is not None and not trader_future.done():
            return False
        interval = int(trader.interval_seconds or 0)
        if interval <= 0:
            return True
        seconds_until_trader = interval - (int(now.timestamp()) % interval)
        # The subprocess group is killed at the job timeout. Keep an additional
        # minute so cleanup/persistence cannot spill into the next trading slot.
        return seconds_until_trader > spec.timeout_seconds + 60

    def _execute_manual(self, spec: JobSpec, request_id: int) -> None:
        if spec.name not in MANUAL_JOBS or spec not in JOBS:
            raise ValueError("Manual job is not allowlisted")
        request = self.store.claim_job_request(request_id)
        if request is not None:
            self._run_job(spec, int(request["run_id"]), manual=True)

    def _execute_recovery(self, recovery: dict[str, Any]) -> None:
        recovery_id = int(recovery["id"])
        claimed = self.store.claim_ai_recovery(recovery_id)
        if claimed is None:
            return
        trader = next((job for job in current_jobs() if job.name == "trader"), None)
        if trader is None:
            self.store.finish_ai_recovery(recovery_id, 1, "automatic trader disabled before recovery launch")
            return
        result = None
        try:
            command = [sys.executable, str(SCRIPTS / trader.script)]
            result = _run_process(
                command, timeout=trader.timeout_seconds,
                env_overrides={
                    "OKXQUANT_AI_RECOVERY": "1",
                    "OKXQUANT_RECOVERY_ID": str(recovery_id),
                    "OKXQUANT_RECOVERY_ACCOUNT_SCOPE": str(claimed["account_scope"]),
                    "OKXQUANT_RECOVERY_SLOT_START": str(claimed["slot_start"]),
                },
            )
            output = result.stderr if result.returncode else result.stdout
            detail = output[-2000:]
            model_succeeded = self.store.successful_recovery_call(recovery_id)
            if result.returncode == 0 and model_succeeded:
                self.store.finish_ai_recovery(recovery_id, 0, detail or "503 recovery produced a validated model response")
            else:
                reason = detail or "recovery ended without a successful model decision"
                self.store.finish_ai_recovery(recovery_id, result.returncode or 1, reason)
        except subprocess.TimeoutExpired as exc:
            self.store.finish_ai_recovery(recovery_id, 124, f"timeout after {trader.timeout_seconds}s: {exc}")
        except Exception as exc:
            self.store.finish_ai_recovery(recovery_id, 1, f"{type(exc).__name__}")

    def _launch_due_recovery(self, now: datetime, launched: list[str]) -> None:
        trader_busy = "trader" in self.running
        trader_enabled = any(job.name == "trader" for job in current_jobs())
        now_text = now.strftime("%Y-%m-%d %H:%M:%S")
        for recovery in self.store.due_ai_recoveries(now_text):
            recovery_id = int(recovery["id"])
            try:
                slot = datetime.strptime(str(recovery["slot_start"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ_TZ)
                expires = datetime.strptime(str(recovery["expires_at"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ_TZ)
            except ValueError:
                self.store.cancel_ai_recovery(recovery_id, "invalid persisted recovery time")
                continue
            slot_end = slot + timedelta(seconds=15 * 60)
            if not trader_enabled:
                self.store.cancel_ai_recovery(recovery_id, "automatic trader disabled")
                continue
            if now >= expires or not (slot <= now < slot_end):
                self.store.cancel_ai_recovery(recovery_id, "recovery safety window expired")
                continue
            if self.store.successful_model_call_in_slot(
                slot.strftime("%Y-%m-%d %H:%M:%S"), slot_end.strftime("%Y-%m-%d %H:%M:%S")
            ):
                self.store.cancel_ai_recovery(recovery_id, "slot already has a successful model decision")
                continue
            if trader_busy:
                continue
            self.running["trader"] = self.trader_executor.submit(self._execute_recovery, recovery)
            launched.append("trader_recovery")
            return

    def _run_job(self, spec: JobSpec, run_id: int, *, manual: bool = False, previous: str = "") -> None:
        result = None
        try:
            command = [sys.executable, str(SCRIPTS / spec.script)]
            if manual:
                command.append("--force")
            if spec.schedule_key.startswith("backup_job:"):
                command.extend(["--job-id", spec.schedule_key.split(":", 1)[1]])
            result = _run_process(command, timeout=spec.timeout_seconds)
            detail = (result.stderr if result.returncode else result.stdout)[-2000:]
            self._finish_job(run_id, result.returncode, detail)
        except subprocess.TimeoutExpired as exc:
            self._finish_job(run_id, 124, f"timeout after {spec.timeout_seconds}s: {exc}")
        except OSError as exc:
            if result is not None:
                # Completion persistence failed after the process returned:
                # never roll back its schedule and inadvertently execute twice.
                raise
            # Spawn failures have no side effects: permit a bounded retry rather
            # than losing a whole daily slot. Never retry a started trading job.
            if not manual:
                self.store.set_state(f"job.last.{spec.name}", previous)
                self.retry_after[spec.name] = time.monotonic() + 30
            self._finish_job(run_id, 1, f"launch failed: {type(exc).__name__}")
        except Exception as exc:
            self._finish_job(run_id, 1, f"{type(exc).__name__}")

    def tick(self, now: datetime | None = None) -> list[str]:
        self._flush_completions()
        now = now or datetime.now(BJ_TZ)
        for name, future in list(self.running.items()):
            if future.done():
                try:
                    future.result()
                except Exception as exc:
                    self.store.set_state(f"job.error.{name}", type(exc).__name__)
                    self.retry_after[name] = time.monotonic() + 30
                self.running.pop(name, None)
        schedule = load_schedule()
        jobs = current_jobs()
        last_states = self.store.get_states([f"job.last.{spec.name}" for spec in jobs])
        launched: list[str] = []
        for spec in jobs:
            try:
                if spec.name in self.running or time.monotonic() < self.retry_after.get(spec.name, 0) or not self.due(
                        spec, now, schedule, last_raw=last_states.get(f"job.last.{spec.name}", "")):
                    continue
                if spec.name == "self_improvement" and not self._self_improvement_window_open(now, spec):
                    continue
                executor = {"position_guard": self.guard_executor, "ledger_sync": self.ledger_executor,
                            "trader": self.trader_executor}.get(spec.name, self.executor)
                if spec.schedule_key.startswith("backup_job:"):
                    executor = self.backup_executor
                self.running[spec.name] = executor.submit(self._execute, spec, now)
                launched.append(spec.name)
            except Exception as exc:
                # One broken job must not starve the jobs later in the registry.
                self.store.set_state(f"job.error.{spec.name}", type(exc).__name__)
                self.retry_after[spec.name] = time.monotonic() + 30
        # A natural 15-minute trader slot always has precedence over delayed
        # recovery. Recovery uses the same single-worker lane and running key.
        self._launch_due_recovery(now, launched)
        # Scheduled work keeps precedence; manual review uses the same running
        # map/executor, so it cannot overlap the scheduled self-improvement job.
        for request in self.store.pending_job_requests():
            name = str(request["job_name"])
            if name not in MANUAL_JOBS or name in self.running:
                continue
            spec = next(job for job in JOBS if job.name == name)
            if name == "self_improvement" and not self._self_improvement_window_open(now, spec):
                continue
            self.running[name] = self.executor.submit(self._execute_manual, spec, int(request["request_id"]))
            launched.append(name)
        return launched

    def status(self) -> dict[str, Any]:
        schedule = load_schedule()
        result = []
        now = datetime.now(BJ_TZ)
        for spec in current_jobs():
            last = self._last_at(spec.name)
            result.append({
                "name": spec.name,
                "script": spec.script,
                "running": spec.name in self.running and not self.running[spec.name].done(),
                "last_scheduled_at": last.isoformat() if last else "",
                "schedule": f"每 {spec.interval_seconds // 60} 分钟" if spec.interval_seconds else "、".join(self._scheduled_times(spec, schedule)),
                "timezone": "Asia/Shanghai",
                "overdue": bool(spec.interval_seconds and last and (now - last).total_seconds() > spec.interval_seconds * 2),
            })
        return {"jobs": result, "recent_runs": self.store.job_runs(30),
                "manual_requests": self.store.job_requests(30),
                "ai_recoveries": self.store.ai_recoveries(30)}

    def shutdown(self) -> None:
        executors = (self.guard_executor, self.ledger_executor, self.trader_executor,
                     self.backup_executor, self.executor)
        # Cancel every queue before waiting on any long-running job. Keep the
        # worker flock until every active child has returned.
        for executor in executors:
            executor.shutdown(wait=False, cancel_futures=True)
        for executor in executors:
            executor.shutdown(wait=True, cancel_futures=True)
