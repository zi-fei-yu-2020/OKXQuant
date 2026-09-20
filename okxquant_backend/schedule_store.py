"""Persistent Beijing-time schedules shared by the admin plane and scheduler."""
from __future__ import annotations
import json
import copy
import re
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE_FILE = ROOT / "data" / "notification_schedule.json"
DEFAULT_SCHEDULE = {
    "timezone": "Asia/Shanghai",
    "briefing_times": ["08:00", "20:00"],
    "self_improvement_time": "20:00",
    "backup_time": "02:00",
}


def load_schedule() -> dict[str, Any]:
    if not SCHEDULE_FILE.exists():
        return copy.deepcopy(DEFAULT_SCHEDULE)
    try:
        payload = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return copy.deepcopy(DEFAULT_SCHEDULE)
        result = copy.deepcopy(DEFAULT_SCHEDULE)
        times = payload.get("briefing_times")
        valid = lambda value: isinstance(value, str) and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is not None
        if isinstance(times, list) and 1 <= len(times) <= 6 and all(valid(t) for t in times):
            result["briefing_times"] = sorted(set(times))
        for key in ("self_improvement_time", "backup_time"):
            if valid(payload.get(key)):
                result[key] = payload[key]
        return result
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(DEFAULT_SCHEDULE)


def save_schedule(schedule: dict[str, Any]) -> None:
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".notification-schedule-", suffix=".tmp", dir=SCHEDULE_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(schedule, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, SCHEDULE_FILE)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
