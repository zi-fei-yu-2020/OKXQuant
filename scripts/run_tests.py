#!/usr/bin/env python3
"""Run offline tests in a disposable source snapshot (Linux/WSL/macOS).

Never copy runtime data, secrets, user configuration, or the Git directory.
Use this entry point instead of unittest discovery in a live checkout.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("okxquant_backend", "okxquant_gateway", "scripts", "dashboard", "plugins", "tests", "okxquant_frontend")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules", "dist", ".ui-artifacts", ".env", ".env.*")


def copy_sources(destination: Path) -> None:
    for name in SOURCE_DIRS:
        shutil.copytree(ROOT / name, destination / name, ignore=IGNORE)
    for name in ("README.md", "STANDALONE.md", "requirements.txt", "env.example"):
        shutil.copy2(ROOT / name, destination / name)
    for name in ("data", "logs", "backups", "home"):
        (destination / name).mkdir()
    (destination / ".okxquant-test-sandbox").touch()


def test_environment(destination: Path) -> dict[str, str]:
    # An allowlist prevents inherited OKX, LLM, webhook and backup credentials
    # (including arbitrary custom credential variable names) from leaking in.
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "LD_LIBRARY_PATH", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update({
        "HOME": str(destination / "home"),
        "USERPROFILE": str(destination / "home"),
        "PYTHONPATH": str(destination),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "OKXQUANT_TESTING": "1",
        "OKXQUANT_TEST_ROOT": str(destination),
        "OKXQUANT_OKX_ENV": "demo",
    })
    return env


def run_isolated(pattern: str, verbosity: int) -> int:
    if (os.environ.get("OKXQUANT_TEST_ROOT") != str(ROOT)
            or not (ROOT / ".okxquant-test-sandbox").is_file()
            or os.environ.get("OKXQUANT_TESTING") != "1"):
        raise RuntimeError("Internal test mode requires a disposable sandbox; run without --isolated.")
    sys.path.insert(0, str(ROOT))
    violations: list[str] = []
    active_test = "test discovery"

    class OfflineTestResult(unittest.TextTestResult):
        def startTest(self, test):
            nonlocal active_test
            active_test = test.id()
            super().startTest(test)

    def deny(kind: str):
        def blocked(*args, **kwargs):
            # Do not log arguments: a failing test could pass a credential.
            violations.append(f"{kind}: {active_test}")
            raise AssertionError(f"Offline tests forbid {kind}; mock the external boundary explicitly.")
        return blocked

    with ExitStack() as stack:
        for target, kind in (
            ("socket.socket.connect", "network connections"),
            ("socket.socket.connect_ex", "network connections"),
            ("socket.socket.sendto", "network datagrams"),
            ("socket.getaddrinfo", "DNS resolution"),
            ("subprocess.Popen", "subprocess execution"),
            ("os.system", "shell execution"),
        ):
            stack.enter_context(patch(target, side_effect=deny(kind)))
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
        result = unittest.TextTestRunner(verbosity=verbosity, resultclass=OfflineTestResult).run(suite)
    if violations:
        print(f"\nBlocked {len(violations)} unmocked external operation(s):", file=sys.stderr)
        for violation in sorted(set(violations)):
            print(f"  {violation}", file=sys.stderr)
    return 0 if result.wasSuccessful() and result.testsRun > 0 and not violations else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py", help="unittest discovery filename pattern")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--isolated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform == "win32":
        parser.error("The suite needs Unix fcntl and POSIX file permissions. Run this script with Python inside WSL or Linux; do not install a fake fcntl module.")
    if args.isolated:
        return run_isolated(args.pattern, 2 if args.verbose else 1)
    with tempfile.TemporaryDirectory(prefix="okxquant-tests-") as temporary:
        destination = Path(temporary).resolve()
        copy_sources(destination)
        print("Running offline tests in a disposable source snapshot; live runtime data is excluded.", flush=True)
        command = [sys.executable, str(destination / "scripts" / "run_tests.py"), "--isolated", "--pattern", args.pattern]
        if args.verbose:
            command.append("--verbose")
        return subprocess.run(command, cwd=destination, env=test_environment(destination)).returncode


if __name__ == "__main__":
    raise SystemExit(main())
