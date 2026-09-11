#!/usr/bin/env python3
"""Run one or more configured OKXQuant custom backup jobs."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path: sys.path.insert(0, str(ROOT / "scripts"))

from okxquant_backend.backup_store import get_job, list_jobs
from backup_runtime import run_backup_job


def notify(result: dict) -> None:
    try:
        from qq_notifier import send_qq_message
        icon = "✅" if result["status"] == "success" else "⚠️" if result["status"] == "partial" else "❌"
        lines = [f"{icon} 【OKXQuant 自定义灾备】{result['job_name']}", f"状态：{result['status']}", f"时间：{result['started_at']} - {result['finished_at']}"]
        if result.get("sha256"): lines.append(f"SHA256：{result['sha256'][:16]}...")
        lines.append(f"目标：{len(result.get('targets', []))}，SQLite：{len(result.get('sqlite', []))}")
        if result.get("errors"): lines.append("错误：" + "；".join(result["errors"])[:600])
        send_qq_message("\n".join(lines))
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--job-id", default=""); parser.add_argument("--all-enabled", action="store_true")
    args = parser.parse_args()
    jobs = [get_job(args.job_id)] if args.job_id else [x for x in list_jobs() if x.get("enabled")]
    if not jobs: print(json.dumps({"status": "skipped", "reason": "no enabled backup jobs"}, ensure_ascii=False)); return 0
    results = []
    for job in jobs:
        result = run_backup_job(job); results.append(result)
        if (result["status"] == "success" and job.get("notify_on_success")) or (result["status"] != "success" and job.get("notify_on_failure")): notify(result)
        print(json.dumps(result, ensure_ascii=False))
    return 0 if all(x["status"] == "success" for x in results) else 2


if __name__ == "__main__": raise SystemExit(main())
