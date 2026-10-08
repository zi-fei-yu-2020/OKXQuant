from __future__ import annotations
"""Small, fail-safe switches for optional research/runtime workloads.

Protection, ledger, evidence, news and execution paths are intentionally not
represented here and therefore cannot be disabled through this module.
"""
import json
import os
from pathlib import Path
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data" / "runtime_features.json"
VERSION = 1
FEATURES = (
    "factor_snapshots",
    "market_observations",
    "entry_research",
    "scalp_research",
    "automatic_review",
)
PROFILES = {"standard", "light"}
LIGHT_DISABLED = frozenset(FEATURES)


def _profile_from_environment() -> str | None:
    value = os.environ.get("OKXQUANT_RUNTIME_PROFILE")
    if value is None or not value.strip():
        return None
    profile = value.strip().lower()
    if profile not in PROFILES:
        return None
    return profile


def _read_config() -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != VERSION:
            return {}, "config_error"
        profile = payload.get("profile", "standard")
        overrides = payload.get("features", {})
        if profile not in PROFILES or not isinstance(overrides, dict):
            return {}, "config_error"
        if any(name not in FEATURES or type(value) is not bool for name, value in overrides.items()):
            return {}, "config_error"
        return {"profile": profile, "features": overrides}, "config"
    except FileNotFoundError:
        return {}, "default"
    except (OSError, ValueError, TypeError):
        return {}, "config_error"


def status() -> dict[str, Any]:
    config, source = _read_config()
    if source == "config_error":
        # Corrupt/unreadable configuration may disable optional work, never
        # silently turn it back on. Safety, ledger, evidence and news are not
        # represented by this optional-feature allowlist.
        return {
            "profile": "light",
            "features": {name: False for name in FEATURES},
            "source": "config_error",
            "version": VERSION,
            "error": "runtime_features_config_invalid_or_unreadable",
        }

    if source == "config":
        # A valid persisted operator choice is authoritative; environment is
        # only a bootstrap default when no config has been saved yet.
        profile = config["profile"]
    else:
        profile = _profile_from_environment() or "standard"
        if os.environ.get("OKXQUANT_RUNTIME_PROFILE", "").strip().lower() in PROFILES:
            source = "environment"

    enabled = {name: not (profile == "light" and name in LIGHT_DISABLED) for name in FEATURES}
    enabled.update(config.get("features", {}))
    return {"profile": profile, "features": enabled, "source": source, "version": VERSION}


def is_enabled(feature: str) -> bool:
    if feature not in FEATURES:
        raise KeyError(f"Unknown runtime feature: {feature}")
    return bool(status()["features"][feature])


def save_config(payload: Any) -> dict[str, Any]:
    """Validate and atomically save profile plus boolean feature overrides.

    ``overrides`` is accepted as a frontend-facing alias for ``features``.
    If both are supplied they must agree exactly, avoiding ambiguous writes.
    """
    if not isinstance(payload, dict):
        raise ValueError("Runtime feature configuration must be an object")
    unknown = set(payload) - {"profile", "features", "overrides"}
    if unknown:
        raise ValueError("Unknown runtime feature configuration fields")
    if "features" in payload and "overrides" in payload and payload["features"] != payload["overrides"]:
        raise ValueError("features and overrides conflict")
    profile = payload.get("profile", status()["profile"])
    features = payload.get("features", payload.get("overrides", {}))
    if profile not in PROFILES:
        raise ValueError("profile must be 'standard' or 'light'")
    if not isinstance(features, dict):
        raise ValueError("features/overrides must be an object")
    if set(features) - set(FEATURES):
        raise ValueError("Unknown runtime feature")
    if any(type(value) is not bool for value in features.values()):
        raise ValueError("Runtime feature overrides must be booleans")

    document = {"version": VERSION, "profile": profile, "features": features}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    from scripts.config_lock import configuration_write
    with configuration_write(CONFIG_PATH):
        fd, temporary = tempfile.mkstemp(prefix=".runtime-features-", suffix=".tmp", dir=CONFIG_PATH.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, CONFIG_PATH)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return status()

