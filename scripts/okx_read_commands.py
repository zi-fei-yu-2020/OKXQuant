from __future__ import annotations
"""Explicit adapter for a tiny set of read-only private OKX CLI commands.

Anything outside the parser stays on its caller's existing CLI path. This
module never accepts or transports a write command.
"""
import json
import shlex
import subprocess
from typing import Any


_READ_COMMANDS = {
    ("account", "balance"): "balance",
    ("account", "positions"): "positions",
    ("swap", "orders"): "orders",
}


def _resource(command: str, mode: str) -> str | None:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    # The only accepted grammar is: okx --{demo|live} <read command> --json.
    # Permit the legacy trailing stderr redirect used by ai_brain_trader.
    tokens = [token for token in tokens if token not in {"2>/dev/null", "1>/dev/null"}]
    if len(tokens) < 4 or tokens[0] != "okx":
        return None
    if tokens[1] != f"--{mode}":
        # A recognizable command aimed at another environment is not eligible.
        if tokens[1] in {"--demo", "--live"}:
            return "__environment_mismatch__"
        return None
    key = tuple(tokens[2:4])
    resource = _READ_COMMANDS.get(key)
    if resource is None:
        return None
    allowed = {"--json"}
    # Static reads are mapped only for the exact account-wide allowlisted calls.
    if any(token not in allowed for token in tokens[4:]) or "--json" not in tokens[4:]:
        return None
    return resource


def read_result(command: str, environment: Any) -> subprocess.CompletedProcess | None:
    """Return a CompletedProcess-shaped result, or None for existing CLI.

    Native-read failures are nonzero and empty-output, never an empty account;
    they are not retried through the CLI or another account.
    """
    if not hasattr(environment, "mode") or not hasattr(environment, "configured"):
        return None
    resource = _resource(command, environment.mode)
    if resource is None:
        return None
    if resource == "__environment_mismatch__":
        return subprocess.CompletedProcess(command, -1, "", "OKX read command environment mismatch")
    if not environment.configured:
        return None  # OAuth-only CLI behavior remains unchanged.
    try:
        from okxquant_backend.okx_read_service import read_private_resource
        # Signature: (resource, environment, inst_id=""). Explicit keyword
        # documents that this exact frozen account supplies the signed GET.
        rows = read_private_resource(resource, environment=environment)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Malformed OKX read response")
        return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")
    except Exception as exc:
        return subprocess.CompletedProcess(command, -1, "", f"Native OKX read unavailable: {type(exc).__name__}")
