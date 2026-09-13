"""Unified multi-format LLM management, connection testing, and runtime dispatch.
Supports:
1. openai_chat: OpenAI Standard /chat/completions (OpenAI, Gemini OpenAI endpoint, DeepSeek, etc.)
2. openai_responses: OpenAI Structured /responses API (Responses API format)
3. claude_messages: Anthropic Claude /messages API (Claude 3.7 / 3.5 native)
"""
from __future__ import annotations
import copy
import json
import os
import re
import tempfile
import time
import urllib.request
import urllib.error
from .llm_transport import LLMRequestError, request_json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LLM_CONFIG_FILE = DATA_DIR / "llm_models.json"
LEGACY_PROVIDERS_FILE = DATA_DIR / "llm_providers.json"

SUPPORTED_API_FORMATS = [
    {"id": "openai_chat", "name": "OpenAI Chat (/chat/completions)", "desc": "标准 ChatML 对话格式，兼容 OpenAI/Gemini/DeepSeek/主流中继"},
    {"id": "openai_responses", "name": "OpenAI Responses (/responses)", "desc": "OpenAI 专属 Responses API 结构化接口"},
    {"id": "claude_messages", "name": "Claude Messages (/messages)", "desc": "Anthropic Claude 原生 Messages API，支持原生长思维链"},
]

STANDARD_REASONING_EFFORTS = ["max", "xhigh", "high", "medium", "low", "minimal", "none", "auto"]


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}{'*' * 8}{value[-visible:]}"


def _detect_reasoning_type(model_id: str) -> str:
    m = model_id.lower()
    if "deepseek-reasoner" in m or "deepseek-r1" in m or "-r1" in m:
        return "deepseek_reasoner"
    if (
        m.startswith(("o1", "o3", "o4"))
        or "/o1" in m or "/o3" in m or "/o4" in m
        or "gemini" in m
        or "claude-3-7" in m
        or "claude-3.7" in m
        or "qwq" in m
    ):
        return "standard_effort"
    if "chat" in m or "gpt-4o" in m or "gpt-3" in m or "qwen" in m or "llama" in m:
        return "none"
    return "auto"


def _detect_capabilities(model_id: str) -> List[str]:
    m = model_id.lower()
    caps = ["chat"]
    if any(k in m for k in ["vision", "image", "flash", "gpt-4o", "gpt-5", "gpt-6", "gemini", "claude", "grok", "muse", "vl", "omni", "multimodal"]):
        caps.append("vision")
    if not ("-r1-distill" in m or "-thinking" in m):
        caps.append("tools")
    if any(k in m for k in ["reasoner", "r1", "o1", "o3", "o4", "gpt-5", "gpt-6", "high", "thinking", "qwq", "deepseek-r1"]):
        caps.append("reasoning")
    return caps


def _detect_api_format(url: str, model_id: str) -> str:
    u = url.lower()
    m = model_id.lower()
    if "anthropic.com" in u or "claude" in u or "claude" in m and "messages" in u:
        return "claude_messages"
    if "responses" in u:
        return "openai_responses"
    return "openai_chat"


DEFAULT_PROVIDERS = [
    {
        "id": "openai",
        "name": "OpenAI",
        "type": "OpenAI",
        "group": "基础供应",
        "enabled": True,
        "multi_key_enabled": False,
        "response_api_enabled": False,
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "api_format": "openai_chat",
        "api_path": "/chat/completions",
        "description": "OpenAI 兼容协议端点，支持中继网关与官方直连",
        "models": [
            {
                "id": "gemini-3.7-flash-high",
                "name": "gemini-3.7-flash-high",
                "capabilities": ["chat", "vision", "tools", "reasoning"],
                "reasoning_type": "standard_effort",
                "reasoning_effort": "high",
                "context_length": 1048576,
                "description": "Gemini 3.7 Flash 深度推演版，极速响应与强逻辑决策",
            },
            {
                "id": "gemini-3.1-flash-image",
                "name": "gemini-3.1-flash-image",
                "capabilities": ["chat", "vision"],
                "reasoning_type": "none",
                "reasoning_effort": "none",
                "context_length": 131072,
                "description": "Gemini 多模态盘口图表视觉感知模型",
            },
        ],
    },
    {
        "id": "claude",
        "name": "Claude",
        "type": "Anthropic",
        "group": "基础供应",
        "enabled": False,
        "multi_key_enabled": False,
        "response_api_enabled": False,
        "base_url": "https://api.anthropic.com/v1",
        "api_key": "",
        "api_format": "claude_messages",
        "api_path": "/messages",
        "description": "Anthropic 官方原生 Messages API 直连",
        "models": [],
    },
    {
        "id": "gemini",
        "name": "Gemini",
        "type": "Gemini",
        "group": "基础供应",
        "enabled": False,
        "multi_key_enabled": False,
        "response_api_enabled": False,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key": "",
        "api_format": "openai_chat",
        "api_path": "/chat/completions",
        "description": "Google AI Studio 官方原生/OpenAI 兼容端点",
        "models": [],
    },
]



def init_llm_config() -> Dict[str, Any]:
    """Load or initialize clean, user-centric model configuration with multi-provider support."""
    from .config import settings

    data: Dict[str, Any] = {}
    if LLM_CONFIG_FILE.exists():
        try:
            with open(LLM_CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except Exception:
            data = {}

    # Extract current settings from .env / settings
    cur_url = getattr(settings, "llm_base_url", "") or os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1"
    cur_key = getattr(settings, "llm_api_key", "") or os.getenv("LLM_API_KEY") or ""
    cur_model = getattr(settings, "llm_model", "") or os.getenv("LLM_MODEL") or "gemini-3.8-flash-high"
    cur_effort = getattr(settings, "llm_reasoning_effort", "") or os.getenv("LLM_REASONING_EFFORT") or "high"

    existing_providers = data.get("providers", [])
    merged_providers: List[Dict[str, Any]] = []

    for dp in DEFAULT_PROVIDERS:
        pid = dp["id"]
        found = next((p for p in existing_providers if p.get("id") == pid), None)
        if found:
            p_obj = dict(dp)
            p_obj.update(found)
            # Never overwrite models with global defaults if provider was already configured
            if "models" in found:
                p_obj["models"] = list(found.get("models", []))
            if pid == "openai":
                if not p_obj.get("api_key") and cur_key:
                    p_obj["api_key"] = cur_key
                if not p_obj.get("base_url"):
                    p_obj["base_url"] = cur_url
            merged_providers.append(p_obj)
        else:
            p_obj = copy.deepcopy(dp)
            if pid == "openai":
                if cur_key:
                    p_obj["api_key"] = cur_key
                if cur_url:
                    p_obj["base_url"] = cur_url
                p_obj["enabled"] = True
            merged_providers.append(p_obj)

    # Any custom provider added by user (ignore legacy hardcoded providers from older versions)
    legacy_ids = {
        "siliconflow", "openrouter", "kelivoin", "tensdaq", "deepseek",
        "alhubmix", "suixiang", "dashscope", "zhipu", "grok", "volcengine"
    }
    for ep in existing_providers:
        epid = ep.get("id")
        if epid in legacy_ids:
            continue
        if not any(dp["id"] == epid for dp in DEFAULT_PROVIDERS):
            merged_providers.append(ep)

    active_m_id = data.get("active_model_id") or cur_model or "gemini-3.8-flash-high"
    active_effort = data.get("active_reasoning_effort") or cur_effort or "high"

    models_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for p in merged_providers:
        p_id = p.get("id", "")
        p_name = p.get("name", p_id)
        p_base = p.get("base_url", "")
        p_key = p.get("api_key", "")
        p_fmt = p.get("api_format", "openai_chat")
        for m in p.get("models", []):
            mid = m.get("id", "")
            if not mid:
                continue
            models_map[(p_id, mid)] = {
                "id": mid,
                "name": m.get("name", mid),
                "provider_id": p_id,
                "provider_name": p_name,
                "base_url": m.get("base_url") or p_base,
                "api_key": m.get("api_key") or p_key,
                "api_format": m.get("api_format") or p_fmt,
                "reasoning_type": m.get("reasoning_type", _detect_reasoning_type(mid)),
                "reasoning_effort": m.get("reasoning_effort") or m.get("default_effort", "high"),
                "capabilities": m.get("capabilities", _detect_capabilities(mid)),
                "context_length": m.get("context_length"),
                "description": m.get("description", ""),
            }

    # Provider-local definitions are authoritative; the flat array is only a
    # compatibility view. Never let its cached credentials override a rotation.
    for m in data.get("models", []):
        mid = m.get("id")
        identity = (m.get("provider_id", "custom"), mid)
        if mid and identity not in models_map:
            models_map[identity] = m

    flat_models = list(models_map.values())
    active_pid = data.get("active_provider_id", "")
    candidates = [m for m in flat_models if m["id"] == active_m_id]
    if not active_pid and not candidates and active_m_id == cur_model and cur_key:
        # An operator-provided LLM_MODEL may not be in the built-in examples.
        # It already drives the runtime fallback; represent the SAME connection
        # explicitly so the UI and council model picker agree with execution.
        owner = next((p for p in merged_providers
                      if str(p.get("base_url", "")).rstrip("/") == str(cur_url).rstrip("/")
                      and p.get("api_key") == cur_key and p.get("enabled") is not False), None)
        if owner is None:
            identifier = "environment"
            while any(p.get("id") == identifier for p in merged_providers):
                identifier += "_local"
            owner = {"id": identifier, "name": "Environment configuration",
                     "base_url": cur_url, "api_key": cur_key, "enabled": True,
                     "api_format": _detect_api_format(cur_url, cur_model), "models": []}
            merged_providers.append(owner)
        model_spec = {"id": cur_model, "name": cur_model, "reasoning_effort": cur_effort,
                      "reasoning_type": _detect_reasoning_type(cur_model),
                      "capabilities": _detect_capabilities(cur_model)}
        owner.setdefault("models", []).append(model_spec)
        view = {**model_spec, "provider_id": owner["id"], "provider_name": owner.get("name", owner["id"]),
                "base_url": cur_url, "api_key": cur_key,
                "api_format": owner.get("api_format", "openai_chat"),
                "context_length": None, "description": "Operator environment model"}
        flat_models.append(view)
        candidates = [view]
    if not active_pid and candidates:
        active_pid = candidates[0].get("provider_id", "custom")

    config = {
        "version": "3.2",
        "active_provider_id": active_pid,
        "active_model_id": active_m_id,
        "active_reasoning_effort": active_effort,
        "providers": merged_providers,
        "models": flat_models,
    }
    _atomic_write_json(LLM_CONFIG_FILE, config)
    return config


# Backwards compatibility alias for app.py
LLM_PROVIDERS_FILE = LLM_CONFIG_FILE
init_llm_providers = init_llm_config


def load_llm_config(mask_keys: bool = True) -> Dict[str, Any]:
    """Return clean model configurations and configured providers matching modern client architecture."""
    config = init_llm_config()
    providers_list = config.get("providers", [])
    active_mid = config.get("active_model_id", "gemini-3.8-flash-high")
    active_effort = config.get("active_reasoning_effort", "high")

    res: Dict[str, Any] = {
        "version": config.get("version", "3.1"),
        "active_model_id": active_mid,
        "active_reasoning_effort": active_effort,
        "standard_reasoning_efforts": STANDARD_REASONING_EFFORTS,
        "supported_api_formats": SUPPORTED_API_FORMATS,
        "providers": [],
        "models": [],
        "active_provider_id": config.get("active_provider_id", ""),
    }

    for p in providers_list:
        pid = p.get("id", "")
        # ONLY return models that are explicitly registered under this specific provider
        models_in_p = list(p.get("models", []))
        formatted_p_models = []
        for m in models_in_p:
            m_id = m.get("id", "")
            formatted_p_models.append({
                "id": m_id,
                "name": m.get("name") or m_id,
                "capabilities": m.get("capabilities") or _detect_capabilities(m_id),
                "reasoning_type": m.get("reasoning_type") or _detect_reasoning_type(m_id),
                "reasoning_effort": m.get("reasoning_effort") or "high",
                "context_length": m.get("context_length"),
                "description": m.get("description", ""),
                "is_active": m_id == active_mid and pid == config.get("active_provider_id"),
            })

        p_copy = {
            "id": pid,
            "name": p.get("name", pid),
            "type": p.get("type", p.get("name", pid)),
            "group": p.get("group", "其他"),
            "enabled": bool(p.get("enabled", False)),
            "multi_key_enabled": bool(p.get("multi_key_enabled", False)),
            "response_api_enabled": bool(p.get("response_api_enabled", False)),
            "base_url": p.get("base_url", ""),
            "api_format": p.get("api_format", "openai_chat"),
            "api_path": p.get("api_path", "/chat/completions"),
            "description": p.get("description", ""),
            "has_key": bool(p.get("api_key")),
            "models_count": len(models_in_p),
            "models": formatted_p_models,
        }
        if mask_keys:
            p_copy["api_key_masked"] = mask_secret(p.get("api_key", ""))
        else:
            p_copy["api_key"] = p.get("api_key", "")
        res["providers"].append(p_copy)

    # Flattened models for backward compatibility
    for m in config.get("models", []):
        m_pid = m.get("provider_id", "openai")
        p_entry = next((p for p in providers_list if p.get("id") == m_pid), None)
        m_key = m.get("api_key", "")
        has_key = bool(m_key or (p_entry and p_entry.get("api_key")))

        m_copy = {
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "provider_id": m_pid,
            "provider_name": m.get("provider_name") or (p_entry.get("name") if p_entry else "自定义"),
            "base_url": m.get("base_url", "") or (p_entry.get("base_url", "") if p_entry else ""),
            "api_format": m.get("api_format", "openai_chat"),
            "reasoning_type": m.get("reasoning_type", "auto"),
            "reasoning_effort": m.get("reasoning_effort", "high"),
            "capabilities": m.get("capabilities") or _detect_capabilities(m["id"]),
            "context_length": m.get("context_length"),
            "description": m.get("description", ""),
            "has_key": has_key,
            "is_active": m["id"] == active_mid and m_pid == config.get("active_provider_id"),
        }
        if mask_keys:
            m_copy["api_key_masked"] = mask_secret(m_key) if m_key else (mask_secret(p_entry.get("api_key", "")) if p_entry and p_entry.get("api_key") else "")
        else:
            m_copy["api_key"] = m_key
        res["models"].append(m_copy)

    return res



def select_model(config: Dict[str, Any], model_id: str, provider_id: str = "") -> Optional[Dict[str, Any]]:
    """Resolve an identity without silently borrowing another provider's key."""
    candidates = [m for m in config.get("models", []) if m["id"] == model_id]
    if provider_id:
        candidates = [m for m in candidates if m.get("provider_id", "custom") == provider_id]
    if len(candidates) > 1:
        raise ValueError("多个供应商包含同名模型，请明确选择供应商")
    return candidates[0] if candidates else None


def get_active_llm_runtime() -> Dict[str, Any]:
    """Retrieve active LLM credentials and configuration for runtime execution."""
    from .config import settings
    config = init_llm_config()
    active_mid = config.get("active_model_id", "")
    active_effort = config.get("active_reasoning_effort", "high")

    target_model = select_model(config, active_mid, config.get("active_provider_id", ""))

    base_url = target_model.get("base_url") if target_model else getattr(settings, "llm_base_url", "")
    api_key = target_model.get("api_key") if target_model else getattr(settings, "llm_api_key", "")
    provider_id = target_model.get("provider_id", "") if target_model else ""
    provider_name = target_model.get("provider_name", "") if target_model else "默认"

    if target_model:
        t_base = target_model.get("base_url", "").rstrip("/")
        prov = next(
            (
                p for p in config.get("providers", [])
                if p.get("id") == provider_id or (not provider_id and t_base and p.get("base_url", "").rstrip("/") == t_base)
            ),
            None,
        )
        if prov:
            if not api_key:
                api_key = prov.get("api_key", "")
            if not base_url:
                base_url = prov.get("base_url", "")
            if not provider_name or provider_name == "自定义":
                provider_name = prov.get("name", provider_name)
            if not provider_id:
                provider_id = prov.get("id", "openai")

    base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
    # A configured model without a key must not borrow the active environment's
    # credential and send it to another provider's endpoint.
    api_key = (api_key or "") if target_model else (api_key or os.getenv("LLM_API_KEY", ""))

    model_name = active_mid or getattr(settings, "llm_model", "gemini-3.8-flash-high")
    api_format = target_model.get("api_format") if target_model else _detect_api_format(base_url, model_name)
    reasoning_type = target_model.get("reasoning_type", "auto") if target_model else _detect_reasoning_type(model_name)

    return {
        "model": model_name,
        "name": target_model.get("name", model_name) if target_model else model_name,
        "provider_name": provider_name or "默认",
        "provider_id": provider_id or "openai",
        "base_url": base_url,
        "api_key": api_key,
        "api_format": api_format,
        "reasoning_effort": active_effort,
        "reasoning_type": reasoning_type,
    }


def activate_provider_model(provider_id: str, model_id: str, reasoning_effort: Optional[str] = None) -> Dict[str, Any]:
    """One-click switch to activate a model. Updates config, .env, and encrypted store."""
    from .settings_store import update_env, remove_env
    from .config import refresh_settings
    try:
        from okxquant_gateway.secrets import save_secrets
    except ImportError:
        save_secrets = None

    config = init_llm_config()
    target_model = select_model(config, model_id, provider_id)
    if not target_model:
        raise ValueError("所选供应商下未找到模型，请先添加模型后再激活")

    effort = reasoning_effort or target_model.get("reasoning_effort") or "high"
    if effort not in STANDARD_REASONING_EFFORTS:
        effort = "auto"

    # Persist the active selection only after credentials have been saved.
    # A missing encryption dependency must not leave a failed activation active.
    # Sync to .env and secrets
    base_url = target_model.get("base_url", "")
    api_key = target_model.get("api_key", "")
    m_pid = target_model.get("provider_id")
    if m_pid:
        prov = next((p for p in config.get("providers", []) if p.get("id") == m_pid), None)
        if prov:
            if not api_key:
                api_key = prov.get("api_key", "")
            if not base_url:
                base_url = prov.get("base_url", "")

    base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")

    env_values = {
        "LLM_BASE_URL": base_url,
        "LLM_MODEL": model_id,
        "LLM_REASONING_EFFORT": effort,
    }
    if api_key:
        if not save_secrets:
            raise RuntimeError("加密密钥存储不可用，未将密钥写入明文配置")
        save_secrets({"LLM_API_KEY": api_key})
        remove_env({"LLM_API_KEY"})

    update_env(env_values)
    refresh_settings()
    config["active_provider_id"] = target_model.get("provider_id", "custom")
    config["active_model_id"] = model_id
    config["active_reasoning_effort"] = effort
    _atomic_write_json(LLM_CONFIG_FILE, config)

    return {
        "success": True,
        "active_model_id": model_id,
        "active_model_name": target_model.get("name"),
        "active_reasoning_effort": effort,
        "base_url": base_url,
        "api_format": target_model.get("api_format", "openai_chat"),
        "provider_name": target_model.get("provider_name", "自定义"),
        "active_provider_id": target_model.get("provider_id", "openai"),
        "active_provider_name": target_model.get("provider_name", "自定义"),
    }


def upsert_model(provider_id: str, model_data: Dict[str, Any]) -> Dict[str, Any]:
    """Add or update a custom model definition."""
    requested_provider = model_data.get("provider_id")
    if requested_provider and provider_id not in ("", "custom", requested_provider):
        raise ValueError("请求路径与模型的供应商不一致")
    provider_id = requested_provider or provider_id
    mid = str(model_data.get("id", "")).strip()
    name = str(model_data.get("name", "")).strip() or mid
    base_url = str(model_data.get("base_url", "")).strip().rstrip("/")
    api_key = str(model_data.get("api_key", "")).strip()
    api_format = str(model_data.get("api_format", "openai_chat")).strip()
    provider_name = str(model_data.get("provider_name", "")).strip()
    reasoning_type = str(model_data.get("reasoning_type", "auto")).strip()
    default_effort = str(model_data.get("default_effort") or model_data.get("reasoning_effort", "high")).strip()
    desc = str(model_data.get("description", "")).strip()
    caps = model_data.get("capabilities") or _detect_capabilities(mid)
    ctx_len = model_data.get("context_length")

    if not mid:
        raise ValueError("模型 ID 不能为空")

    config = init_llm_config()

    prov = None
    if provider_id and provider_id != "custom":
        prov = next((p for p in config.get("providers", []) if p["id"] == provider_id), None)
    if not prov and provider_name:
        prov = next((p for p in config.get("providers", []) if p.get("name") == provider_name), None)

    if prov:
        if not base_url:
            base_url = prov.get("base_url", "")
        if not api_key and prov.get("api_key"):
            api_key = prov.get("api_key", "")
        if not provider_name:
            provider_name = prov.get("name", "自定义")
        if not provider_id:
            provider_id = prov.get("id", "openai")

    if not base_url or not base_url.startswith(("http://", "https://")):
        active = get_active_llm_runtime()
        base_url = active.get("base_url", "https://api.openai.com/v1")

    valid_formats = [f["id"] for f in SUPPORTED_API_FORMATS]
    if api_format not in valid_formats:
        api_format = _detect_api_format(base_url, mid)

    models = config.setdefault("models", [])
    existing = next((m for m in models if m["id"] == mid and m.get("provider_id", "custom") == provider_id), None)

    if existing:
        existing["name"] = name
        existing["provider_id"] = provider_id or existing.get("provider_id", "openai")
        existing["provider_name"] = provider_name or existing.get("provider_name", "自定义")
        existing["base_url"] = base_url
        if api_key:
            existing["api_key"] = api_key
        existing["api_format"] = api_format
        existing["reasoning_type"] = reasoning_type
        existing["reasoning_effort"] = default_effort
        existing["capabilities"] = caps
        existing["context_length"] = ctx_len
        existing["description"] = desc
    else:
        models.append({
            "id": mid,
            "name": name,
            "provider_id": provider_id or "openai",
            "provider_name": provider_name or "自定义",
            "base_url": base_url,
            "api_key": api_key,
            "api_format": api_format,
            "reasoning_type": reasoning_type,
            "reasoning_effort": default_effort,
            "capabilities": caps,
            "context_length": ctx_len,
            "description": desc,
        })

    # Also update provider's local models array
    if prov:
        prov_models = prov.setdefault("models", [])
        p_existing = next((m for m in prov_models if m.get("id") == mid), None)
        if p_existing:
            p_existing["name"] = name
            p_existing["capabilities"] = caps
            p_existing["reasoning_type"] = reasoning_type
            p_existing["reasoning_effort"] = default_effort
            p_existing["context_length"] = ctx_len
            p_existing["description"] = desc
        else:
            prov_models.append({
                "id": mid,
                "name": name,
                "capabilities": caps,
                "reasoning_type": reasoning_type,
                "reasoning_effort": default_effort,
                "context_length": ctx_len,
                "description": desc,
            })

    if prov:
        local_model = next(m for m in prov["models"] if m["id"] == mid)
        for field in ("base_url", "api_key", "api_format"):
            supplied = model_data.get(field)
            if supplied and supplied != prov.get(field):
                local_model[field] = supplied
            elif field != "api_key" or supplied:
                local_model.pop(field, None)

    _atomic_write_json(LLM_CONFIG_FILE, config)
    return {
        "model_id": mid,
        "name": name,
        "base_url": base_url,
        "api_format": api_format,
        "provider_id": provider_id or "openai",
    }


def delete_model(provider_id: str, model_id: str) -> bool:
    """Delete a custom model."""
    config = init_llm_config()
    target = select_model(config, model_id, provider_id)
    if not target:
        return False
    target_pid = target.get("provider_id", "custom")
    if config.get("active_model_id") == model_id and config.get("active_provider_id") == target_pid:
        raise ValueError("不能删除当前正在使用的模型；请先切换到其他模型后再删除。")

    models = config.get("models", [])
    filtered = [m for m in models if not (m["id"] == model_id and m.get("provider_id", "custom") == target_pid)]
    for prov in config.get("providers", []):
        if prov.get("id") == target_pid:
            prov["models"] = [m for m in prov.get("models", []) if m.get("id") != model_id]

    if len(filtered) == len(models):
        return False

    config["models"] = filtered
    _atomic_write_json(LLM_CONFIG_FILE, config)
    return True


def upsert_provider(provider_data: Dict[str, Any]) -> Dict[str, Any]:
    """Add or update an LLM provider definition."""
    pid = str(provider_data.get("id", "")).strip().lower()
    name = str(provider_data.get("name", "")).strip() or pid
    p_type = str(provider_data.get("type", "")).strip() or name
    p_group = str(provider_data.get("group", "")).strip() or "其他"
    enabled = bool(provider_data.get("enabled", False)) if "enabled" in provider_data else None
    multi_key_enabled = bool(provider_data.get("multi_key_enabled", False))
    response_api_enabled = bool(provider_data.get("response_api_enabled", False))
    base_url = str(provider_data.get("base_url", "")).strip().rstrip("/")
    api_key = str(provider_data.get("api_key", "")).strip()
    api_format = str(provider_data.get("api_format", "openai_chat")).strip()
    api_path = str(provider_data.get("api_path", "/chat/completions")).strip()
    desc = str(provider_data.get("description", "")).strip()

    if not api_format:
        api_format = "claude_messages" if "claude" in pid or "anthropic" in base_url.lower() else "openai_chat"

    # Automatically synchronize api_path with selected api_format if default was provided
    if api_path in ["/chat/completions", "/messages", "/responses", ""]:
        if api_format == "claude_messages":
            api_path = "/messages"
        elif api_format == "openai_responses":
            api_path = "/responses"
        else:
            api_path = "/chat/completions"

    response_api_enabled = (api_format == "openai_responses")

    if not pid:
        pid = re.sub(r"[^a-zA-Z0-9_\-]", "", name.lower()) or f"prov-{int(time.time())}"

    if not base_url or not base_url.startswith(("http://", "https://")):
        raise ValueError("供应商 Base URL 必须以 http:// 或 https:// 开头")

    config = init_llm_config()
    providers = config.setdefault("providers", [])
    existing = next((p for p in providers if p["id"] == pid), None)
    if existing:
        existing["name"] = name
        existing["type"] = p_type
        existing["group"] = p_group
        if enabled is not None:
            existing["enabled"] = enabled
        existing["multi_key_enabled"] = multi_key_enabled
        existing["response_api_enabled"] = response_api_enabled
        existing["base_url"] = base_url
        if api_key:
            existing["api_key"] = api_key
        existing["api_format"] = api_format
        existing["api_path"] = api_path
        existing["description"] = desc
    else:
        providers.append({
            "id": pid,
            "name": name,
            "type": p_type,
            "group": p_group,
            "enabled": enabled if enabled is not None else False,
            "multi_key_enabled": multi_key_enabled,
            "response_api_enabled": response_api_enabled,
            "base_url": base_url,
            "api_key": api_key,
            "api_format": api_format,
            "api_path": api_path,
            "description": desc,
            "models": [],
        })
    _atomic_write_json(LLM_CONFIG_FILE, config)
    return {"id": pid, "name": name, "base_url": base_url}


def toggle_provider(provider_id: str, enabled: Optional[bool] = None) -> Dict[str, Any]:
    """Toggle a provider's enabled/disabled state."""
    config = init_llm_config()
    providers = config.get("providers", [])
    p = next((x for x in providers if x["id"] == provider_id), None)
    if not p:
        raise ValueError(f"供应商 {provider_id} 未找到")
    if enabled is None:
        p["enabled"] = not p.get("enabled", False)
    else:
        p["enabled"] = bool(enabled)
    _atomic_write_json(LLM_CONFIG_FILE, config)
    return {"id": provider_id, "enabled": p["enabled"]}


def clear_provider_models(provider_id: str) -> bool:
    """Clear all models under a specific provider."""
    config = init_llm_config()
    if config.get("active_provider_id") == provider_id:
        raise ValueError("不能清空或删除当前使用的供应商，请先切换模型")
    providers = config.get("providers", [])
    p = next((x for x in providers if x["id"] == provider_id), None)
    if not p:
        return False
    p["models"] = []
    config["models"] = [m for m in config.get("models", []) if m.get("provider_id") != provider_id]
    _atomic_write_json(LLM_CONFIG_FILE, config)
    return True


def delete_provider(provider_id: str) -> bool:
    """Delete a provider definition."""
    config = init_llm_config()
    if config.get("active_provider_id") == provider_id:
        raise ValueError("不能清空或删除当前使用的供应商，请先切换模型")
    providers = config.get("providers", [])
    filtered = [p for p in providers if p["id"] != provider_id]
    if len(filtered) == len(providers):
        return False
    config["providers"] = filtered
    config["models"] = [m for m in config.get("models", []) if m.get("provider_id") != provider_id]
    _atomic_write_json(LLM_CONFIG_FILE, config)
    return True


def fetch_remote_models(
    base_url: str = "",
    api_key: str = "",
    provider_id: Optional[str] = None,
    timeout: float = 12.0,
) -> Dict[str, Any]:
    """Fetch live models list from an OpenAI / OpenRouter / Anthropic compatible endpoint."""
    cleaned_url = str(base_url or "").strip().rstrip("/")
    config = init_llm_config()

    if not cleaned_url and provider_id:
        prov = next((p for p in config.get("providers", []) if p["id"] == provider_id), None)
        if prov:
            cleaned_url = prov.get("base_url", "").strip().rstrip("/")
            if not api_key:
                api_key = prov.get("api_key", "")

    if not cleaned_url:
        active = get_active_llm_runtime()
        cleaned_url = active.get("base_url", "").strip().rstrip("/")
        if not api_key:
            api_key = active.get("api_key", "")

    if not cleaned_url or not cleaned_url.startswith(("http://", "https://")):
        return {
            "ok": False,
            "error": "Base URL 格式无效，必须以 http:// 或 https:// 开头",
            "recommendation": "请填写有效的供应商 Base URL",
        }

    if not api_key and provider_id:
        prov = next((p for p in config.get("providers", []) if p["id"] == provider_id), None)
        if prov and prov.get("api_key"):
            api_key = prov.get("api_key")

    if not api_key:
        prov = next((p for p in config.get("providers", []) if p.get("base_url", "").rstrip("/") == cleaned_url and p.get("api_key")), None)
        if prov:
            api_key = prov.get("api_key", "")

    endpoints = []
    if "anthropic.com" in cleaned_url:
        ep = f"{cleaned_url}/models" if not cleaned_url.endswith("/v1") else f"{cleaned_url}/models"
        hdrs = {"x-api-key": api_key, "anthropic-version": "2023-06-01"} if api_key else {}
        endpoints.append((ep, hdrs))
    elif cleaned_url.endswith("/v1"):
        endpoints.append((f"{cleaned_url}/models", {"Authorization": f"Bearer {api_key}"} if api_key else {}))
        endpoints.append((f"{cleaned_url[:-3]}/models", {"Authorization": f"Bearer {api_key}"} if api_key else {}))
    elif cleaned_url.endswith("/models"):
        endpoints.append((cleaned_url, {"Authorization": f"Bearer {api_key}"} if api_key else {}))
    else:
        endpoints.append((f"{cleaned_url}/v1/models", {"Authorization": f"Bearer {api_key}"} if api_key else {}))
        endpoints.append((f"{cleaned_url}/models", {"Authorization": f"Bearer {api_key}"} if api_key else {}))

    last_err = ""
    for ep, hdrs in endpoints:
        hdrs["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OKXQuant-Quantum-Trader/6.6"
        req = urllib.request.Request(ep, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                raw_list = data.get("data") if isinstance(data, dict) and "data" in data else data.get("models", data if isinstance(data, list) else [])
                if not isinstance(raw_list, list):
                    continue

                parsed_models = []
                for item in raw_list:
                    if isinstance(item, str):
                        m_id = item
                        m_name = item
                        ctx = None
                        desc = ""
                    elif isinstance(item, dict):
                        m_id = str(item.get("id", "")).strip()
                        if not m_id:
                            continue
                        m_name = str(item.get("name") or item.get("display_name") or m_id).strip()
                        ctx = item.get("context_length") or item.get("max_tokens")
                        desc = str(item.get("description") or "").strip()
                    else:
                        continue

                    detected_format = _detect_api_format(cleaned_url, m_id)
                    detected_rtype = _detect_reasoning_type(m_id)
                    detected_caps = _detect_capabilities(m_id)
                    default_effort = "high" if detected_rtype != "none" else "auto"

                    parsed_models.append({
                        "id": m_id,
                        "name": m_name,
                        "capabilities": detected_caps,
                        "context_length": ctx,
                        "description": desc,
                        "api_format": detected_format,
                        "reasoning_type": detected_rtype,
                        "default_effort": default_effort,
                    })

                def _model_sort_key(m: Dict[str, Any]) -> Tuple[int, str]:
                    mid = m["id"].lower()
                    if any(k in mid for k in ["gemini-3", "claude-3-7", "claude-3.7", "o3", "o4", "gpt-5", "deepseek-r1", "deepseek-v4", "qwen-max", "qwq"]):
                        return (0, mid)
                    if any(k in mid for k in ["gemini-2", "claude-3-5", "claude-3.5", "o1", "gpt-4o", "qwen-2.5", "doubao"]):
                        return (1, mid)
                    return (2, mid)

                parsed_models.sort(key=_model_sort_key)

                return {
                    "ok": True,
                    "endpoint_used": ep,
                    "total": len(parsed_models),
                    "models": parsed_models,
                }
        except urllib.error.HTTPError as exc:
            last_err = f"HTTP {exc.code}"
            if exc.code == 401:
                return {
                    "ok": False,
                    "error": "供应商身份验证失败 (HTTP 401 Unauthorized)",
                    "recommendation": "请先在此供应商填入正确的 API Key 后再拉取模型",
                }
        except Exception as exc:
            last_err = str(exc)

    return {
        "ok": False,
        "error": f"拉取失败: {last_err or '未响应模型列表'}",
        "recommendation": "请检查 Base URL 是否正确，或供应商是否支持 /models 端点查询",
    }



def build_request_spec(
    model: str,
    messages: List[Dict[str, str]],
    base_url: str,
    api_key: str = "",
    api_format: str = "openai_chat",
    reasoning_effort: str = "high",
    temperature: Optional[float] = 0.2,
    response_format: Optional[Dict[str, Any]] = None,
    reasoning_type: str = "auto",
    max_tokens: int = 4096,
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """Build endpoint URL, headers, and request payload according to the specific API protocol format."""
    cleaned_url = base_url.rstrip("/")
    m_lower = model.lower()
    rtype = reasoning_type if reasoning_type != "auto" else _detect_reasoning_type(model)
    effort = (reasoning_effort or "auto").strip().lower()

    # Protocol 1: Anthropic Claude Messages API
    if api_format == "claude_messages":
        if not cleaned_url.endswith("/messages"):
            endpoint = f"{cleaned_url}/messages" if cleaned_url.endswith("/v1") else f"{cleaned_url}/v1/messages"
        else:
            endpoint = cleaned_url

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "OKXQuant-Quantum-Trader/5.4 (Claude-Messages)",
            "anthropic-version": "2023-06-01",
        }
        if api_key:
            headers["x-api-key"] = api_key

        # Separate system message
        system_chunks = [m["content"] for m in messages if m.get("role") == "system"]
        chat_messages = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") != "system"]

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": chat_messages,
        }
        if system_chunks:
            payload["system"] = "\n\n".join(system_chunks)

        if effort in ("max", "xhigh", "high", "medium", "low"):
            budget_map = {
                "max": 64000,
                "xhigh": 32000,
                "high": 16000,
                "medium": 8000,
                "low": 2048,
            }
            budget = budget_map[effort]
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload["max_tokens"] = budget + max_tokens
        elif effort == "none":
            payload["thinking"] = {"type": "disabled"}
            if temperature is not None:
                payload["temperature"] = temperature
        else:
            if temperature is not None:
                payload["temperature"] = temperature

        return endpoint, headers, payload

    # Protocol 2: OpenAI Responses API (/responses)
    elif api_format == "openai_responses":
        if not cleaned_url.endswith("/responses"):
            endpoint = f"{cleaned_url}/responses" if cleaned_url.endswith("/v1") else f"{cleaned_url}/v1/responses"
        else:
            endpoint = cleaned_url

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "OKXQuant-Quantum-Trader/5.4 (OpenAI-Responses)",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: Dict[str, Any] = {
            "model": model,
            "input": messages,
        }
        if response_format and response_format.get("type") == "json_object":
            payload["text"] = {"format": {"type": "json_object"}}
        if effort in ("max", "xhigh", "high", "medium", "low", "minimal"):
            payload["reasoning"] = {"effort": effort}

        return endpoint, headers, payload

    # Protocol 3: OpenAI Chat Completions (/chat/completions, Default)
    else:
        if not cleaned_url.endswith("/chat/completions"):
            endpoint = f"{cleaned_url}/chat/completions" if cleaned_url.endswith("/v1") else f"{cleaned_url}/v1/chat/completions"
        else:
            endpoint = cleaned_url

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "OKXQuant-Quantum-Trader/5.4 (OpenAI-Chat)",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }

        # Temperature handling for reasoning models vs normal models
        is_reasoning_model = (
            rtype in ("deepseek_reasoner", "standard_effort")
            or m_lower.startswith(("o1", "o3", "o4"))
            or "reasoner" in m_lower
            or "-r1" in m_lower
        )
        if not is_reasoning_model:
            if temperature is not None:
                payload["temperature"] = temperature
        else:
            if "gemini" in m_lower and temperature is not None:
                payload["temperature"] = temperature

        # Standard reasoning effort parameter (supports max, xhigh, high, medium, low, minimal, none)
        if rtype == "standard_effort" or (rtype == "auto" and ("gemini" in m_lower or m_lower.startswith(("o1", "o3", "o4", "gpt-5", "gpt-6")) or "gpt-5" in m_lower or "gpt-6" in m_lower)):
            if effort in ("max", "xhigh", "high", "medium", "low", "minimal"):
                payload["reasoning_effort"] = effort
            elif effort == "none" and ("gemini" in m_lower or "gpt" in m_lower):
                payload["reasoning_effort"] = "none"

        if response_format and rtype != "deepseek_reasoner":
            payload["response_format"] = response_format

        return endpoint, headers, payload


def build_chat_payload(
    model: str,
    messages: List[Dict[str, str]],
    reasoning_effort: str = "high",
    temperature: Optional[float] = 0.2,
    response_format: Optional[Dict[str, Any]] = None,
    reasoning_type: str = "auto",
) -> Dict[str, Any]:
    """Compatibility wrapper for standard chat payload generation."""
    _, _, payload = build_request_spec(
        model=model,
        messages=messages,
        base_url="https://api.openai.com/v1",
        api_format="openai_chat",
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        response_format=response_format,
        reasoning_type=reasoning_type,
    )
    return payload


def execute_llm_request(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    api_format: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    temperature: Optional[float] = 0.2,
    response_format: Optional[Dict[str, Any]] = None,
    timeout: float = 50.0,
    max_attempts: Optional[int] = None,
    require_complete: bool = False,
) -> Tuple[str, str, Dict[str, Any], int]:
    """Unified executor for LLM calls across all 3 protocols.
    Returns: (content, reasoning_content, usage_dict, latency_ms)
    """
    runtime = get_active_llm_runtime()
    target_model = model or runtime.get("model") or "gemini-3.8-flash-high"
    target_url = base_url or runtime.get("base_url") or "https://api.openai.com/v1"
    target_key = api_key if api_key is not None else runtime.get("api_key", "")
    target_format = api_format or runtime.get("api_format") or _detect_api_format(target_url, target_model)
    target_effort = reasoning_effort or runtime.get("reasoning_effort") or "high"
    target_rtype = runtime.get("reasoning_type", "auto")

    endpoint, headers, payload = build_request_spec(
        model=target_model,
        messages=messages,
        base_url=target_url,
        api_key=target_key,
        api_format=target_format,
        reasoning_effort=target_effort,
        temperature=temperature,
        response_format=response_format,
        reasoning_type=target_rtype,
    )

    res_json, _, latency_ms, attempts = request_json(endpoint, headers, payload, timeout, **({"max_attempts":max_attempts} if max_attempts is not None else {}))

    # Structured trading output must never consume a provider-declared partial answer.
    if require_complete:
        from scripts.model_json import verify_completion
        verify_completion(res_json, target_format)
    content = ""
    reasoning_content = ""
    usage = res_json.get("usage") or {}

    # Protocol 1: Claude Messages Response
    if target_format == "claude_messages":
        text_chunks = [c.get("text", "") for c in res_json.get("content", []) if c.get("type") == "text"]
        thinking_chunks = [c.get("thinking", "") for c in res_json.get("content", []) if c.get("type") == "thinking"]
        content = "".join(text_chunks).strip()
        reasoning_content = "\n".join(thinking_chunks).strip()
        if not usage:
            usage = {
                "total_tokens": res_json.get("usage", {}).get("input_tokens", 0) + res_json.get("usage", {}).get("output_tokens", 0)
            }

    # Protocol 2: OpenAI Responses Response
    elif target_format == "openai_responses":
        content = str(res_json.get("output_text") or "").strip()
        if not content:
            for item in res_json.get("output", []):
                if item.get("type") == "message":
                    for part in item.get("content", []):
                        if part.get("type") == "output_text" or "text" in part:
                            content += str(part.get("text", ""))
                elif item.get("type") == "reasoning":
                    reasoning_content += str(item.get("content") or item.get("summary") or "")
        content = content.strip()
        reasoning_content = reasoning_content.strip()

    # Protocol 3: OpenAI Chat Completions Response
    else:
        msg = (res_json.get("choices") or [{}])[0].get("message") or {}
        from scripts.model_json import text_content
        content = text_content(msg.get("content"))
        reasoning_content = str(msg.get("reasoning_content") or "").strip()

    if not content:
        if require_complete:
            from scripts.trading_prompt import ContractError
            raise ContractError("模型响应内容为空，未提供决策JSON")
        raise LLMRequestError(200, attempts, "empty_model_output")
    return content, reasoning_content, usage, latency_ms


def test_llm_connection(
    base_url: str,
    api_key: str,
    model: str,
    api_format: str = "openai_chat",
    reasoning_effort: str = "auto",
    reasoning_type: str = "auto",
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Execute a real diagnostic ping across any of the 3 API formats."""
    cleaned_url = str(base_url or "").strip().rstrip("/")
    if not cleaned_url.startswith(("http://", "https://")):
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": 0,
            "model": model,
            "error": "Base URL 格式无效，必须以 http:// 或 https:// 开头",
            "recommendation": "请检查并填写正确的服务 Base URL，例如 https://api.openai.com/v1",
        }

    test_messages = [
        {"role": "user", "content": "Ping test for connection. Please respond with exactly the single word: PONG"}
    ]

    endpoint, headers, payload = build_request_spec(
        model=model,
        messages=test_messages,
        base_url=cleaned_url,
        api_key=api_key,
        api_format=api_format,
        reasoning_effort=reasoning_effort,
        temperature=0.1,
        reasoning_type=reasoning_type,
    )

    t0 = time.perf_counter()
    try:
        res_json, status_code, latency_ms, attempts = request_json(endpoint, headers, payload, timeout)

        content = ""
        reasoning_content = ""
        usage = res_json.get("usage") or {}

        if api_format == "claude_messages":
            content = "".join(c.get("text", "") for c in res_json.get("content", []) if c.get("type") == "text")
            reasoning_content = "\n".join(c.get("thinking", "") for c in res_json.get("content", []) if c.get("type") == "thinking")
        elif api_format == "openai_responses":
            content = str(res_json.get("output_text") or "")
            for item in res_json.get("output", []):
                if item.get("type") == "reasoning":
                    reasoning_content += str(item.get("content") or item.get("summary") or "")
        else:
            msg = (res_json.get("choices") or [{}])[0].get("message") or {}
            content = str(msg.get("content") or "")
            reasoning_content = str(msg.get("reasoning_content") or "")

        content = content.strip()
        if not content:
            raise LLMRequestError(status_code, attempts, "empty_model_output")
        reasoning_tokens = (
            usage.get("completion_tokens_details", {}).get("reasoning_tokens")
            or usage.get("output_tokens_details", {}).get("reasoning_tokens")
            or usage.get("reasoning_tokens")
            or (len(reasoning_content.split()) if reasoning_content else None)
        )

        format_label = next((f["name"] for f in SUPPORTED_API_FORMATS if f["id"] == api_format), api_format)

        return {
            "ok": True,
            "attempts": attempts,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "model": model,
            "api_format": api_format,
            "api_format_name": format_label,
            "endpoint": endpoint,
            "response_preview": content[:120] if content else "(响应成功，返回空正文)",
            "reasoning_detected": bool(reasoning_content),
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": usage.get("total_tokens") or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)),
            "payload_sent": {k: v for k, v in payload.items() if k not in ("messages", "input", "system")},
            "compatibility_note": f"协议 {api_format} 连接与解析成功" + (" · 已捕获链式推演输出" if reasoning_content else ""),
        }

    except LLMRequestError as exc:
        return {
            "ok": False, "status_code": exc.status_code,
            "attempts": exc.attempts, "error_category": exc.category,
            "provider_error_code": exc.provider_code, "request_id": exc.request_id,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "model": model, "api_format": api_format, "endpoint": endpoint,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False, "status_code": 0,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "model": model, "api_format": api_format, "endpoint": endpoint,
            "error": "Model response validation failed: " + type(exc).__name__,
        }
