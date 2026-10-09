"""SQLite-backed durable event and per-channel delivery queue."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import Any

from okxquant_gateway.events import GatewayEvent

BJ_TZ = timezone(timedelta(hours=8))
MANUAL_JOBS = frozenset({"self_improvement"})
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  priority INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  channel TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL,
  last_error TEXT NOT NULL DEFAULT '',
  delivered_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(event_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_deliveries_due ON deliveries(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_name TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL DEFAULT '',
  return_code INTEGER,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_job_runs_name ON job_runs(job_name, id DESC);
CREATE TABLE IF NOT EXISTS manual_job_requests (
  request_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_name TEXT NOT NULL CHECK(job_name = 'self_improvement'),
  actor TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','success','failed')),
  created_at TEXT NOT NULL,
  finished_at TEXT NOT NULL DEFAULT '',
  run_id INTEGER REFERENCES job_runs(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_job_active
  ON manual_job_requests(job_name) WHERE status IN ('pending','running');
CREATE TABLE IF NOT EXISTS runtime_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  caller TEXT NOT NULL,
  model TEXT NOT NULL,
  reasoning_effort TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  duration_ms INTEGER NOT NULL,
  input_chars INTEGER NOT NULL,
  output_chars INTEGER NOT NULL,
  prompt_fingerprint TEXT NOT NULL,
  prompt_transport TEXT NOT NULL DEFAULT 'python-direct',
  input_tokens INTEGER,
  output_tokens INTEGER,
  total_tokens INTEGER,
  error_type TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_model_calls_caller ON model_calls(caller, id DESC);
CREATE TABLE IF NOT EXISTS model_call_transport (
  model_call_id INTEGER PRIMARY KEY REFERENCES model_calls(id),
  diagnostics_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_recovery_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_scope TEXT NOT NULL,
  slot_start TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','success','failed','cancelled')),
  scheduled_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 1),
  failure_fingerprint TEXT NOT NULL,
  original_model_call_id INTEGER REFERENCES model_calls(id),
  run_id INTEGER REFERENCES job_runs(id),
  detail TEXT NOT NULL DEFAULT '',
  UNIQUE(account_scope, slot_start)
);
CREATE INDEX IF NOT EXISTS idx_ai_recovery_due ON ai_recovery_jobs(status, scheduled_at);
"""


class GatewayStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
        self._secure_files()

    def _secure_files(self) -> None:
        for candidate in (self.path, Path(str(self.path)+"-wal"), Path(str(self.path)+"-shm")):
            try:
                if candidate.exists(): candidate.chmod(0o600)
            except OSError: pass

    @contextmanager
    def connect(self, *, timeout=10):
        connection = sqlite3.connect(self.path, timeout=timeout)
        self._secure_files()
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def publish(self, event: GatewayEvent, channels: list[str]) -> str:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event.event_id, event.event_type, event.title, event.message, json.dumps(event.payload, ensure_ascii=False), event.priority, event.created_at),
            )
            for channel in channels:
                connection.execute(
                    "INSERT OR IGNORE INTO deliveries(event_id, channel, next_attempt_at, created_at) VALUES (?, ?, ?, ?)",
                    (event.event_id, channel, event.created_at, event.created_at),
                )
        return event.event_id

    def claim_due(self, limit: int = 20) -> list[dict[str, Any]]:
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        due_sql = """SELECT d.id, d.channel, d.attempts, e.* FROM deliveries d
                     JOIN events e ON e.event_id=d.event_id
                     WHERE d.status IN ('pending','retry') AND d.next_attempt_at<=?
                     ORDER BY e.priority DESC, d.id ASC LIMIT ?"""
        # The worker polls once per second. Do an unlocked WAL read first so an
        # idle poll does not acquire SQLite's writer lock and contend with event
        # publishers/runtime-state writers. Recheck under BEGIN IMMEDIATE only
        # when work actually appears; concurrent claimers remain idempotent.
        with self.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM deliveries WHERE status IN ('pending','retry') AND next_attempt_at<=? LIMIT 1",
                (now,),
            ).fetchone() is None:
                return []
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(due_sql, (now, limit)).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                marks = ",".join("?" for _ in ids)
                connection.execute(f"UPDATE deliveries SET status='processing' WHERE id IN ({marks})", ids)
            return [dict(row) for row in rows]

    def complete(self, delivery_id: int, status: str = "delivered", detail: str = "") -> None:
        if status not in {"delivered", "accepted"}:
            raise ValueError("delivery completion status must be delivered or accepted")
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as connection:
            connection.execute(
                "UPDATE deliveries SET status=?, delivered_at=?, last_error=? WHERE id=?",
                (status, now, detail[:1000] if status == "accepted" else "", delivery_id),
            )

    def fail(self, delivery_id: int, attempts: int, error: str, max_attempts: int = 6) -> None:
        new_attempts = attempts + 1
        status = "dead" if new_attempts >= max_attempts else "retry"
        delay = min(3600, 30 * (2 ** min(new_attempts - 1, 7)))
        next_at = (datetime.now(BJ_TZ) + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as connection:
            connection.execute(
                "UPDATE deliveries SET status=?, attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
                (status, new_attempts, next_at, error[:1000], delivery_id),
            )

    def recover_processing(self) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE deliveries SET status='retry' WHERE status='processing'")
            connection.execute(
                "UPDATE job_runs SET status='failed', return_code=125, finished_at=?, "
                "detail='gateway restarted; previous execution outcome unknown' WHERE status='running'",
                (datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),),
            )
            connection.execute(
                "UPDATE manual_job_requests SET status='failed',finished_at=? WHERE status='running'",
                (datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),),
            )
            connection.execute(
                "UPDATE ai_recovery_jobs SET status='failed',finished_at=?, "
                "detail='gateway restarted; recovery outcome unknown' WHERE status='running'",
                (datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),),
            )

    def request_job(self, job_name: str, *, actor: str = "") -> dict[str, Any]:
        """Persist one review-only request; pending/running clicks are idempotent.

        Authentication/confirmation belongs to the API. This second allowlist
        prevents internal callers from manually dispatching trading jobs.
        """
        if job_name not in MANUAL_JOBS:
            raise ValueError("Manual execution is only allowed for self_improvement review")
        actor = str(actor).replace("\r", " ").replace("\n", " ")[:160]
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM manual_job_requests WHERE job_name=? AND status IN ('pending','running')",
                (job_name,),
            ).fetchone()
            deduplicated = row is not None
            if row is None:
                cursor = connection.execute(
                    "INSERT INTO manual_job_requests(job_name,actor,created_at) VALUES (?,?,?)",
                    (job_name, actor, now),
                )
                row = connection.execute("SELECT * FROM manual_job_requests WHERE request_id=?", (cursor.lastrowid,)).fetchone()
        return {**dict(row), "deduplicated": deduplicated}

    def job_requests(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM manual_job_requests ORDER BY request_id DESC LIMIT ?",
                                      (max(1, min(limit, 200)),)).fetchall()
        return [dict(row) for row in rows]

    def pending_job_requests(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM manual_job_requests WHERE status='pending' ORDER BY request_id").fetchall()
        return [dict(row) for row in rows]

    def claim_job_request(self, request_id: int) -> dict[str, Any] | None:
        """Claim and create its job run in one transaction, at actual execution."""
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM manual_job_requests WHERE request_id=? AND status='pending'",
                                     (request_id,)).fetchone()
            if row is None or row["job_name"] not in MANUAL_JOBS:
                return None
            cursor = connection.execute("INSERT INTO job_runs(job_name,status,started_at) VALUES (?,'running',?)",
                                        (row["job_name"], now))
            run_id = int(cursor.lastrowid)
            connection.execute("UPDATE manual_job_requests SET status='running',run_id=? WHERE request_id=?",
                               (run_id, request_id))
        return {**dict(row), "status": "running", "run_id": run_id}

    def replay_dead(self, delivery_id: int) -> bool:
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE deliveries SET status='pending', attempts=0, next_attempt_at=?, last_error='', delivered_at='' WHERE id=? AND status='dead'",
                (now, delivery_id),
            )
            return cursor.rowcount == 1

    def begin_job(self, job_name: str) -> int:
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as connection:
            cursor = connection.execute("INSERT INTO job_runs(job_name,status,started_at) VALUES (?,'running',?)", (job_name, now))
            return int(cursor.lastrowid)

    def finish_job(self, run_id: int, return_code: int, detail: str, *, timeout=10) -> None:
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        status = "success" if return_code == 0 else "failed"
        with self.connect(timeout=timeout) as connection:
            connection.execute(
                "UPDATE job_runs SET status=?, finished_at=?, return_code=?, detail=? WHERE id=?",
                (status, now, return_code, detail[-2000:], run_id),
            )
            connection.execute(
                "UPDATE manual_job_requests SET status=?,finished_at=? WHERE run_id=? AND status='running'",
                (status, now, run_id),
            )

    def job_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        return [dict(row) for row in rows]

    def enqueue_ai_recovery(self, *, account_scope: str, slot_start: str, scheduled_at: str,
                            expires_at: str, failure_fingerprint: str,
                            original_model_call_id: int | None) -> dict[str, Any]:
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO ai_recovery_jobs(
                     account_scope,slot_start,scheduled_at,expires_at,created_at,
                     failure_fingerprint,original_model_call_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (account_scope, slot_start, scheduled_at, expires_at, now,
                 failure_fingerprint, original_model_call_id),
            )
            row = connection.execute(
                "SELECT * FROM ai_recovery_jobs WHERE account_scope=? AND slot_start=?",
                (account_scope, slot_start),
            ).fetchone()
        return dict(row)

    def ai_recovery(self, recovery_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM ai_recovery_jobs WHERE id=?", (recovery_id,)).fetchone()
        return dict(row) if row else None

    def ai_recoveries(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_recovery_jobs ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def due_ai_recoveries(self, now: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_recovery_jobs WHERE status='pending' AND scheduled_at<=? ORDER BY id",
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    def cancel_ai_recovery(self, recovery_id: int, detail: str) -> None:
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as connection:
            connection.execute(
                "UPDATE ai_recovery_jobs SET status='cancelled',finished_at=?,detail=? "
                "WHERE id=? AND status='pending'",
                (now, detail[:1000], recovery_id),
            )

    def claim_ai_recovery(self, recovery_id: int) -> dict[str, Any] | None:
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM ai_recovery_jobs WHERE id=? AND status='pending' AND attempts=0",
                (recovery_id,),
            ).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                "INSERT INTO job_runs(job_name,status,started_at) VALUES ('trader_recovery','running',?)",
                (now,),
            )
            run_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE ai_recovery_jobs SET status='running',started_at=?,attempts=1,run_id=? WHERE id=?",
                (now, run_id, recovery_id),
            )
            claimed = connection.execute("SELECT * FROM ai_recovery_jobs WHERE id=?", (recovery_id,)).fetchone()
        return dict(claimed)

    def finish_ai_recovery(self, recovery_id: int, return_code: int, detail: str) -> None:
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        status = "success" if return_code == 0 else "failed"
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT run_id FROM ai_recovery_jobs WHERE id=? AND status='running'",
                (recovery_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                "UPDATE ai_recovery_jobs SET status=?,finished_at=?,detail=? WHERE id=?",
                (status, now, detail[:1000], recovery_id),
            )
            connection.execute(
                "UPDATE job_runs SET status=?,finished_at=?,return_code=?,detail=? WHERE id=?",
                (status, now, return_code, detail[-2000:], row["run_id"]),
            )

    def successful_model_call_in_slot(self, slot_start: str, slot_end: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM model_calls
                   WHERE caller IN ('trading_brain','trading_brain_recovery')
                     AND status='success' AND started_at>=? AND started_at<? LIMIT 1""",
                (slot_start, slot_end),
            ).fetchone()
        return row is not None

    def successful_recovery_call(self, recovery_id: int) -> bool:
        raw_call_id = self.get_state(f"ai_recovery.model_success.{recovery_id}")
        if not raw_call_id.isdigit():
            return False
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM model_calls WHERE id=? AND caller='trading_brain_recovery' "
                "AND status='success' LIMIT 1",
                (int(raw_call_id),),
            ).fetchone()
        return row is not None

    def record_model_call(self, record: dict[str, Any]) -> int:
        columns = (
            "caller", "model", "reasoning_effort", "status", "started_at", "duration_ms",
            "input_chars", "output_chars", "prompt_fingerprint", "prompt_transport",
            "input_tokens", "output_tokens", "total_tokens", "error_type",
        )
        with self.connect() as connection:
            cursor = connection.execute(
                f"INSERT INTO model_calls({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(record.get(column) for column in columns),
            )
            call_id = int(cursor.lastrowid)
            from okxquant_backend.llm_transport import safe_transport_diagnostics
            diagnostics = safe_transport_diagnostics(record.get('transport'))
            if diagnostics:
                connection.execute('INSERT INTO model_call_transport(model_call_id,diagnostics_json) VALUES (?,?)',
                    (call_id,json.dumps(diagnostics,ensure_ascii=False,allow_nan=False)))
            return call_id

    def model_calls(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT m.*,t.diagnostics_json FROM model_calls m LEFT JOIN model_call_transport t ON t.model_call_id=m.id ORDER BY m.id DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        result=[]
        from okxquant_backend.llm_transport import safe_transport_diagnostics
        for row in rows:
            value=dict(row);raw=value.pop('diagnostics_json',None)
            if raw:
                try:value['transport']=safe_transport_diagnostics(json.loads(raw))
                except (ValueError,TypeError):value['transport']={}
            result.append(value)
        return result

    def model_stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) total_calls,
                   SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) successful_calls,
                   COALESCE(ROUND(AVG(duration_ms)),0) avg_duration_ms,
                   COALESCE(SUM(total_tokens),0) total_tokens
                   FROM model_calls"""
            ).fetchone()
        return {key: int(row[key] or 0) for key in ("total_calls", "successful_calls", "avg_duration_ms", "total_tokens")}

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute("INSERT INTO runtime_state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_state(self, key: str, default: str = "") -> str:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM runtime_state WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def get_states(self, keys: list[str] | tuple[str, ...]) -> dict[str, str]:
        """Fetch scheduler state keys in one SQLite session instead of N connections."""
        normalized=tuple(dict.fromkeys(str(key) for key in keys if str(key)))
        if not normalized:return {}
        placeholders=','.join('?' for _ in normalized)
        with self.connect() as connection:
            rows=connection.execute(
                f"SELECT key,value FROM runtime_state WHERE key IN ({placeholders})",normalized
            ).fetchall()
        return {str(row['key']):str(row['value']) for row in rows}

    def event_health(self) -> dict[str, int]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT
                   SUM(CASE WHEN priority>=90 THEN 1 ELSE 0 END) critical_total,
                   SUM(CASE WHEN priority>=90 AND accepted=0 AND delivery_count>0 THEN 1 ELSE 0 END) critical_unmet,
                   SUM(CASE WHEN priority>=90 AND accepted=0 AND delivery_count>0 AND dead=delivery_count THEN 1 ELSE 0 END) critical_failed,
                   SUM(CASE WHEN priority>=90 AND delivery_count=0 THEN 1 ELSE 0 END) critical_unroutable
                   FROM (
                     SELECT e.event_id,e.priority,COUNT(d.id) delivery_count,
                       SUM(CASE WHEN d.status IN ('delivered','accepted') THEN 1 ELSE 0 END) accepted,
                       SUM(CASE WHEN d.status='dead' THEN 1 ELSE 0 END) dead
                     FROM events e LEFT JOIN deliveries d ON d.event_id=e.event_id GROUP BY e.event_id
                   )"""
            ).fetchone()
        return {key: int(row[key] or 0) for key in ("critical_total", "critical_unmet", "critical_failed", "critical_unroutable")}

    def stats(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) count FROM deliveries GROUP BY status").fetchall()
        result = {"pending": 0, "processing": 0, "retry": 0, "accepted": 0, "delivered": 0, "dead": 0}
        result.update({row["status"]: row["count"] for row in rows})
        return result

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT d.id, d.channel, d.status, d.attempts, d.last_error, d.delivered_at,
                          e.event_id, e.event_type, e.title, e.message, e.priority, e.created_at
                   FROM deliveries d JOIN events e ON e.event_id=d.event_id
                   ORDER BY d.id DESC LIMIT ?""",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]
