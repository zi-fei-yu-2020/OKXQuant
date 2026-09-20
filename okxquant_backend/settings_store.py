"""Safe local .env configuration persistence for the OKXQuant admin plane."""
from __future__ import annotations
import os
import tempfile
from contextlib import contextmanager
from scripts.config_lock import configuration_write
from pathlib import Path
from typing import Mapping
from .config import ROOT, environment_file, refresh_settings

ENV_FILE = environment_file()
MANAGED_KEYS = {
    "OKX_BASE_URL",
    "OKXQUANT_OKX_ENV",
    "OKX_API_KEY",
    "OKX_SECRET_KEY",
    "OKX_PASSPHRASE",
    "OKX_LIVE_API_KEY", "OKX_LIVE_SECRET_KEY", "OKX_LIVE_PASSPHRASE",
    "OKX_DEMO_API_KEY", "OKX_DEMO_SECRET_KEY", "OKX_DEMO_PASSPHRASE",
    "OKX_IS_SIMULATED",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_REASONING_EFFORT",
    "OKXQUANT_NOTIFICATION_WEBHOOK",
    "OKXQUANT_NOTIFY_WEBHOOK_ENABLED",
    "OKXQUANT_NOTIFY_WECHAT_ENABLED",
    "OKXQUANT_WECHAT_WEBHOOK",
    "OKXQUANT_NOTIFY_TELEGRAM_ENABLED",
    "OKXQUANT_TELEGRAM_BOT_TOKEN",
    "OKXQUANT_TELEGRAM_CHAT_ID",
    "OKXQUANT_TELEGRAM_API_BASE",
    "OKXQUANT_NOTIFY_QQ_ENABLED",
    "OKXQUANT_QQ_APP_ID",
    "OKXQUANT_QQ_CLIENT_SECRET",
    "OKXQUANT_QQ_OPENID",
    "OKXQUANT_SETUP_TOKEN",
    "OKXQUANT_ADMIN_TOKEN",
    "OKXQUANT_MANUAL_CLOSE_ENABLED",
}


def mask(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}{'*' * 8}{value[-visible:]}"


def mask_url(url: str, visible_tail: int = 6) -> str:
    """Mask token/key inside webhook URLs like https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx."""
    if not url:
        return ""
    if "?" in url:
        base, query = url.split("?", 1)
        if len(query) <= visible_tail:
            return f"{base}?{'*' * 8}"
        return f"{base}?key={'*' * 8}{query[-visible_tail:]}"
    if len(url) <= 12:
        return "*" * len(url)
    return f"{url[:12]}{'*' * 8}{url[-visible_tail:]}"


@contextmanager
def _transaction():
    """Use the shared cross-process configuration lock, not a trading lock."""
    with configuration_write(ENV_FILE):
        yield


def _write_lines(lines: list[str]) -> None:
    fd, temp_path = tempfile.mkstemp(prefix=".okxquant-env-", dir=ENV_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, ENV_FILE)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _line_key(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return ""
    return stripped.split("=", 1)[0].strip()


def remove_env(keys: set[str] | list[str] | tuple[str, ...]) -> None:
    targets = set(keys)
    with _transaction():
        existing = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
        _write_lines([line for line in existing if _line_key(line) not in targets])
        for key in targets:
            os.environ.pop(key, None)


def update_env(values: Mapping[str, str | bool | None]) -> None:
    updates = {key: ("1" if value else "0") if isinstance(value, bool) else str(value)
               for key, value in values.items() if key in MANAGED_KEYS and value is not None}
    # Check all inputs before touching either file or process environment.
    if any(any(c in value for c in ("\r", "\n", "\x00")) for value in updates.values()):
        raise ValueError("Environment values must be single-line strings")
    with _transaction():
        existing = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
        # Remove every old occurrence: a later duplicate must not override a saved value.
        result = [line for line in existing if _line_key(line) not in updates]
        result.extend(f"{key}={value}" for key, value in updates.items())
        _write_lines(result)
        os.environ.update(updates)
        refresh_settings()
