"""Standalone process scheduler for OKXQuant maintenance jobs.

It owns scheduling but deliberately invokes existing scripts as isolated processes,
which preserves each script's file lock and fail-closed behavior.
"""
from __future__ import annotations
import fcntl
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from okxquant_backend.schedule_store import load_schedule
except ModuleNotFoundError:
    from schedule_store import load_schedule

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "data"
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

logging.basicConfig(
    filename=LOGS / "okxquant_scheduler.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

JOBS = {
    "trader": ("ai_factor_trader.py", 15 * 60),
    "factor_library": ("factor_library.py", 60),
    "news": ("news_sentiment_harvester.py", 10 * 60),
    "daily_briefing": ("daily_summary_and_backup.py", None),
    "self_improvement": ("self_improvement_engine.py", None),
    "nightly_backup": ("nightly_backup_and_clean.py", None),
}


def run_script(name: str) -> None:
    script = SCRIPTS / JOBS[name][0]
    try:
        result = subprocess.run([sys.executable, str(script)], cwd=ROOT, text=True, capture_output=True, timeout=600)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logging.error("job=%s unavailable error=%s", name, type(exc).__name__)
        return
    if result.returncode:
        logging.error("job=%s rc=%s stderr=%s", name, result.returncode, result.stderr[-1000:])
    else:
        logging.info("job=%s completed stdout=%s", name, result.stdout[-500:])


def due_daily(now: datetime, schedule_time: str, last_run: datetime | None) -> bool:
    try:
        hour, minute = [int(part) for part in schedule_time.split(":", 1)]
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (AttributeError, TypeError, ValueError):
        return False
    # Sequential jobs can overrun the exact minute. Catch up once, never every poll.
    return now >= scheduled and (last_run is None or last_run < scheduled)


def main() -> None:
    """Legacy CLI delegates to the single Gateway owner, never a second clock."""
    if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
    from okxquant_gateway.worker import run
    run()


if __name__ == "__main__":
    main()
