"""Single source of truth for OKX live/demo selection across CLI and REST paths."""
from __future__ import annotations
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ENVIRONMENTS = {"demo", "live"}


def _load_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    configured = os.getenv("OKXQUANT_ENV_FILE", "").strip()
    path = Path(configured).expanduser() if configured else ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            key, value = line.split("=", 1); values[key.strip()] = value.strip().strip('"').strip("'")
    try:
        from okxquant_gateway.secrets import load_secrets
        values.update(load_secrets())
    except Exception:
        pass
    # Dynamic project configuration and encrypted secrets override stale inherited process values.
    return {**os.environ, **values}


@dataclass(frozen=True)
class OKXEnvironment:
    mode: str
    api_key: str
    secret_key: str
    passphrase: str
    base_url: str = "https://www.okx.com"
    source: str = "environment"
    connection_id: str = ""
    binding_version: int = 0
    account_scope: str = ""

    @property
    def simulated(self) -> bool: return self.mode == "demo"
    @property
    def configured(self) -> bool: return bool(self.api_key and self.secret_key and self.passphrase)
    @property
    def fingerprint(self) -> str:
        seed = f"{self.mode}:{self.api_key}".encode()
        return hashlib.sha256(seed).hexdigest()[:12] if self.api_key else f"{self.mode}-oauth"
    @property
    def identity(self) -> str:
        if self.account_scope: return self.account_scope
        if self.connection_id: return f"okx:{self.mode}:{self.fingerprint}:{self.connection_id}:{self.binding_version}"
        return f"okx:{self.mode}:{self.fingerprint}"

    def cli_env(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        if self.source == "account-center-unbound": raise RuntimeError("Trading connection is unbound; no credential fallback")
        env = dict(base or os.environ)
        if self.configured:
            env.update({"OKX_API_KEY": self.api_key, "OKX_SECRET_KEY": self.secret_key, "OKX_PASSPHRASE": self.passphrase})
        env["OKX_DEMO"] = "1" if self.simulated else "0"
        env["OKXQUANT_OKX_ENV"] = self.mode
        return env

    def cli_prefix(self) -> str:
        if self.source == "account-center-unbound": raise RuntimeError("Trading connection is unbound; no credential fallback")
        return f"okx --{self.mode}"


def legacy_environment(values: Mapping[str, str] | None = None, *, mode=None) -> OKXEnvironment:
    env = dict(values or _load_dotenv())
    legacy_simulated = str(env.get("OKX_IS_SIMULATED", "1")).lower() in {"1", "true", "yes"}
    mode = mode or str(env.get("OKXQUANT_OKX_ENV") or ("demo" if legacy_simulated else "live")).lower()
    if mode not in ALLOWED_ENVIRONMENTS: mode = "demo"
    prefix = "OKX_DEMO" if mode == "demo" else "OKX_LIVE"
    api_key = str(env.get(f"{prefix}_API_KEY") or env.get("OKX_API_KEY") or "")
    secret_key = str(env.get(f"{prefix}_SECRET_KEY") or env.get("OKX_SECRET_KEY") or "")
    passphrase = str(env.get(f"{prefix}_PASSPHRASE") or env.get("OKX_PASSPHRASE") or "")
    base_url = str(env.get("OKX_BASE_URL") or "https://www.okx.com").rstrip("/")
    if base_url != "https://www.okx.com": raise ValueError("OKX REST Base URL 只允许 https://www.okx.com")
    return OKXEnvironment(mode, api_key, secret_key, passphrase, base_url, "separate-credentials" if env.get(f"{prefix}_API_KEY") else "legacy-or-oauth")


def selected_environment(values: Mapping[str, str] | None = None) -> OKXEnvironment:
    if values is None:
        from okxquant_backend.account_connections import resolve_environment
        managed = resolve_environment()
        if managed is not None: return managed
    return legacy_environment(values)


def cli_command(arguments: str, values: Mapping[str, str] | None = None) -> str:
    return f"{selected_environment(values).cli_prefix()} {arguments.strip()}"


_FROZEN_ENVIRONMENT: OKXEnvironment | None = None


def freeze_environment(values: Mapping[str, str] | None = None) -> OKXEnvironment:
    """Freeze LIVE/DEMO and credentials for one trading cycle."""
    global _FROZEN_ENVIRONMENT
    _FROZEN_ENVIRONMENT = selected_environment(values)
    return _FROZEN_ENVIRONMENT


def unfreeze_environment() -> None:
    global _FROZEN_ENVIRONMENT
    _FROZEN_ENVIRONMENT = None


def replace_cli_prefix(command: str, values: Mapping[str, str] | None = None) -> str:
    """Bind the process to the frozen/current credential group and replace a legacy CLI prefix."""
    selected = _FROZEN_ENVIRONMENT or selected_environment(values)
    if selected.configured:
        os.environ.update({"OKX_API_KEY": selected.api_key, "OKX_SECRET_KEY": selected.secret_key, "OKX_PASSPHRASE": selected.passphrase})
    os.environ["OKX_DEMO"] = "1" if selected.simulated else "0"
    os.environ["OKXQUANT_OKX_ENV"] = selected.mode
    stripped = command.strip()
    for prefix in ("okx --demo ", "okx --live ", "okx "):
        if stripped.startswith(prefix):
            return f"{selected.cli_prefix()} {stripped[len(prefix):]}"
    return f"{selected.cli_prefix()} {stripped}"
