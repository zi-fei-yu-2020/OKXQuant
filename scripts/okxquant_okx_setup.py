#!/usr/bin/env python3
"""Interactive, secret-free OKX dependency preflight for standalone OKXQuant."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from okxquant_backend.okx_setup import diagnose_okx_runtime  # noqa: E402
from scripts.okx_runtime import selected_environment  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Check OKXQuant OKX CLI, credentials/OAuth, and private read readiness")
    parser.add_argument("--json", action="store_true", help="output machine-readable JSON")
    args = parser.parse_args()
    selected = selected_environment()
    status = diagnose_okx_runtime(selected.mode, selected.configured)
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print("OKXQuant OKX 独立运行预检")
        print(f"环境: {status['selected_mode'].upper()}")
        print(f"CLI: {status['cli']['version'] or '未安装'} {status['cli']['path']}")
        print(f"认证来源: {status['credential_source']}")
        print(f"OAuth: {status['oauth']['status']} {status['oauth']['site']}")
        print(f"私有只读探针: {status['read_probe']['detail']}")
        print(f"结论: {'READY' if status['ready'] else 'NOT READY'}")
        if status["issues"]:
            print("\n待处理：")
            for item in status["issues"]:
                print(f"- {item}")
        if status["steps"]:
            print("\n下一步：")
            for item in status["steps"]:
                print(f"- {item}")
        print(f"\n服务用户 HOME: {os.environ.get('HOME', '')}")
    return 0 if status["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
