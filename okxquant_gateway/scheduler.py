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


@dataclass(frozen=True)
class JobSpec:
    name: str
    script: str
    interval_seconds: int | None = None
    timeout_seconds: int = 600
    schedule_key: str = ""
    default_times: tuple[str, ...] = ()


JOBS = (
    JobSpec("position_guard", "position_guard.py", 60, 240),
    JobSpec("evidence_sync", "evidence_sync.py", 300, 60),
    JobSpec("ledger_sync", "ledger_monitor.py", 60, 50),
    JobSpec("trader", "ai_factor_trader.py", 15 * 60, 840),
    JobSpec("demo_scalp", "demo_scalp.py", 60, 120),
    JobSpec("factor_library", "factor_library.py", 60, 55),
    JobSpec("news", "news_sentiment_harvester.py", 10 * 60, 300),
    JobSpec("daily_briefing", "daily_summary_and_backup.py", None, 600, "briefing_times", ("08:00", "20:00")),
    JobSpec("self_improvement", "self_improvement_engine.py", None, 1200, "self_improvement_time", ("02:00", "08:00", "14:00", "20:00")),
)


def _run_process(command: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    """A timeout owns the entire job process group, not just its Python parent."""
    process = subprocess.Popen(
        command, cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
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
    jobs = tuple(job for job in JOBS if automatic or job.name not in {"trader", "demo_scalp"})
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
    return {"jobs": jobs, "recent_runs": store.job_runs(30), "manual_requests": store.job_requests(30)}


class GatewayScheduler:
    def __init__(self, store: GatewayStore, max_workers: int = 3):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="okxquant-job")
        self.guard_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="okxquant-position-guard")
        self.ledger_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="okxquant-ledger-sync")
        # Long maintenance jobs must not delay the unchanged entry time window.
        self.backup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="okxquant-backup")
        self.trader_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="okxquant-trader")
        self.scalp_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="okxquant-demo-scalp")
        self.running: dict[str, Future[None]] = {}
        self.pending_completions = {}
        self.completion_lock = threading.Lock()
        self.retry_after: dict[str, float] = {}

    def _last_at(self, name: str) -> datetime | None:
        raw = self.store.get_state(f"job.last.{name}")
        try:
            parsed = datetime.fromisoformat(raw) if raw else None
            return parsed.replace(tzinfo=BJ_TZ) if parsed and parsed.tzinfo is None else parsed
        except ValueError:
            return None

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

    def due(self, spec: JobSpec, now: datetime, schedule: dict[str, Any]) -> bool:
        last = self._last_at(spec.name)
        if spec.name == "ledger_sync":
            from scripts.ledger_monitor import should_run
            return should_run(last.timestamp() if last else 0, now.timestamp())
        if spec.interval_seconds:
            if spec.name == "trader":
                slot = int(now.timestamp()) // spec.interval_seconds
                last_slot = int(last.timestamp()) // spec.interval_seconds if last else -1
                return slot > last_slot and int(now.timestamp()) % spec.interval_seconds < 10
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

    def _execute_manual(self, spec: JobSpec, request_id: int) -> None:
        if spec.name not in MANUAL_JOBS or spec not in JOBS:
            raise ValueError("Manual job is not allowlisted")
        request = self.store.claim_job_request(request_id)
        if request is not None:
            self._run_job(spec, int(request["run_id"]), manual=True)

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
        launched: list[str] = []
        for spec in current_jobs():
            try:
                if spec.name in self.running or time.monotonic() < self.retry_after.get(spec.name, 0) or not self.due(spec, now, schedule):
                    continue
                executor = {"position_guard": self.guard_executor, "ledger_sync": self.ledger_executor,
                            "trader": self.trader_executor, "demo_scalp": self.scalp_executor}.get(spec.name, self.executor)
                if spec.schedule_key.startswith("backup_job:"):
                    executor = self.backup_executor
                self.running[spec.name] = executor.submit(self._execute, spec, now)
                launched.append(spec.name)
            except Exception as exc:
                # One broken job must not starve the jobs later in the registry.
                self.store.set_state(f"job.error.{spec.name}", type(exc).__name__)
                self.retry_after[spec.name] = time.monotonic() + 30
        # Scheduled work keeps precedence; manual review uses the same running
        # map/executor, so it cannot overlap the scheduled self-improvement job.
        for request in self.store.pending_job_requests():
            name = str(request["job_name"])
            if name not in MANUAL_JOBS or name in self.running:
                continue
            spec = next(job for job in JOBS if job.name == name)
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
        return {"jobs": result, "recent_runs": self.store.job_runs(30), "manual_requests": self.store.job_requests(30)}

    def shutdown(self) -> None:
        executors = (self.guard_executor, self.ledger_executor, self.trader_executor,
                     self.scalp_executor, self.backup_executor, self.executor)
        # Cancel every queue before waiting on any long-running job. Keep the
        # worker flock until every active child has returned.
        for executor in executors:
            executor.shutdown(wait=False, cancel_futures=True)
        for executor in executors:
            executor.shutdown(wait=True, cancel_futures=True)
