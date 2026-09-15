"""Standalone control plane: read-only monitoring plus process health."""
from __future__ import annotations
import copy
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from fastapi import Body, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pydantic import BaseModel, Field
from okxquant_backend.config import refresh_settings, settings
from okxquant_backend.okx_client import OKXClient
from okxquant_backend.okx_trade_service import account_snapshot as okx_account_snapshot, fast_close_confirmed
from okxquant_backend.okx_setup import diagnose_okx_runtime, install_okx_cli, check_node_npm, start_oauth_device_login, oauth_status
from okxquant_backend.account_baseline import load_account_baseline, update_initial_capital
from okxquant_backend.backup_secrets import credential_status as backup_credential_status, save_credentials as save_backup_credentials
from okxquant_backend.prompt_views import EVOLUTION_USER_TEMPLATE, TRADING_USER_TEMPLATE, rendered_snapshots
from okxquant_backend.settings_store import mask, remove_env, update_env
from okxquant_backend.notifications import _env as notification_env, diagnose_channel, test_channel
from okxquant_backend.audit import recent as recent_audit, record as audit_record
from okxquant_backend.admin_auth import AdminAuthStore
from okxquant_backend.backup_store import (
    create_job as create_backup_job, delete_job as delete_backup_job, export_job as export_backup_job,
    get_job as get_backup_job, import_job as import_backup_job, list_jobs as list_backup_jobs,
    load_backup_methods, save_backup_methods, update_job as update_backup_job, validate_backup_job,
)
from okxquant_backend.schedule_store import load_schedule, save_schedule
from okxquant_gateway.agents import agent_statuses
from okxquant_gateway.publisher import DB_PATH as GATEWAY_DB_PATH
from okxquant_gateway.plugins import plugin_statuses
from okxquant_gateway import __version__ as GATEWAY_VERSION
from okxquant_gateway.scheduler import scheduler_snapshot
from okxquant_gateway.secrets import delete_secrets, save_secrets, status as secret_store_status
from okxquant_gateway.store import GatewayStore
from okxquant_gateway.supervisor import start_supervisor as start_gateway_supervisor, stop_supervisor as stop_gateway_supervisor
from scripts.instrument_pool import from_okx_instrument, load_instruments, save_instruments
from scripts.instrument_support import pool_support, opening_status
from okxquant_backend.llm_manager import (
    load_llm_config,
    get_active_llm_runtime,
    activate_provider_model,
    upsert_provider,
    delete_provider,
    toggle_provider,
    clear_provider_models,
    upsert_model,
    delete_model,
    test_llm_connection,
    fetch_remote_models,
    init_llm_providers,
    _atomic_write_json,
    LLM_PROVIDERS_FILE,
)
from scripts.prompt_library import (
    PRESETS, TEMPLATE_KEYS, active_profile, activate_profile, all_profiles, apply_module_layout,
    create_profile, delete_profile, export_profile, get_profile, import_profile,
    load_library, pipeline_view, profile_history, resolve_profile, rollback_profile, save_library, update_profile, validate_profile,
)

PROMPT_OVERRIDE_FILE = DATA_DIR / "system_prompt_override.txt"
BACKUP_LOG_FILE = ROOT / "logs" / "okxquant_backup_manual.log"
STARTED_AT = time.time()
REQUEST_SESSION: ContextVar[str] = ContextVar("okxquant_admin_session", default="")

@asynccontextmanager
async def lifespan(_: FastAPI):
    refresh_settings()
    admin_auth.initialize_from_legacy(settings.admin_token or settings.setup_token)
    start_gateway_supervisor()
    try:
        from dashboard.app import start_dashboard_background_worker
        start_dashboard_background_worker()
    except Exception:
        pass
    yield
    try:
        from dashboard.app import stop_dashboard_background_worker
        stop_dashboard_background_worker()
    except Exception:
        pass
    stop_gateway_supervisor()


from fastapi.middleware.gzip import GZipMiddleware
app = FastAPI(title="OKXQuant Standalone Backend", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", redoc_url="/api/redoc")
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def admin_session_context(request: Request, call_next):
    token = REQUEST_SESSION.set(request.headers.get("X-OKXQuant-Session", ""))
    try:
        response = await call_next(request)
        path = request.url.path
        # Guarantee admin routes and sensitive account endpoints never get cached by Cloudflare or public CDNs
        if path.startswith("/api/v1/admin") or path.startswith("/admin") or path.startswith("/api/v1/account"):
            response.headers["Cache-Control"] = "private, no-cache, no-store, must-revalidate"
        return response
    finally:
        REQUEST_SESSION.reset(token)


okx = OKXClient()
admin_auth = AdminAuthStore()
VUE_DIST = ROOT / "okxquant_frontend" / "dist"


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class AdminCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=128)
    role: str = Field(default="admin", pattern=r"^(superadmin|admin)$")


class AdminPasswordRequest(BaseModel):
    current_password: str = Field(default="", max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class AdminEnabledRequest(BaseModel):
    enabled: bool


class AdminUnlockRequest(BaseModel):
    confirmation: str = Field(min_length=12, max_length=100)


class OkxCliInstallRequest(BaseModel):
    confirmation: str = Field(min_length=8, max_length=80)


class OkxOAuthStartRequest(BaseModel):
    site: str = Field(pattern=r"^(global|eea|us|tr)$")


class AdminConfigUpdate(BaseModel):
    okx_environment: str | None = Field(default=None, pattern=r"^(demo|live)$")
    okx_live_api_key: str | None = None
    okx_live_secret_key: str | None = None
    okx_live_passphrase: str | None = None
    okx_demo_api_key: str | None = None
    okx_demo_secret_key: str | None = None
    okx_demo_passphrase: str | None = None
    okx_api_key: str | None = None
    okx_secret_key: str | None = None
    okx_passphrase: str | None = None
    okx_simulated: bool | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_reasoning_effort: str | None = Field(default=None, pattern=r"^(low|medium|high|minimal|none|auto)$")
    notification_webhook: str | None = None
    manual_close_enabled: bool | None = None


class LLMActivateRequest(BaseModel):
    model_id: str
    provider_id: str | None = None
    reasoning_effort: str | None = Field(default=None, pattern=r"^(low|medium|high|minimal|none|auto)$")


class LLMTestRequest(BaseModel):
    model: str
    provider_id: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_format: str = "openai_chat"
    reasoning_effort: str = Field(default="auto", pattern=r"^(low|medium|high|minimal|none|auto)$")
    reasoning_type: str = "auto"


class LLMProviderUpsertRequest(BaseModel):
    id: str | None = None
    name: str
    type: str | None = None
    group: str | None = "其他"
    enabled: bool | None = False
    multi_key_enabled: bool | None = False
    response_api_enabled: bool | None = False
    base_url: str
    api_key: str | None = None
    api_format: str | None = "openai_chat"
    api_path: str | None = "/chat/completions"
    description: str | None = ""
    models: list[dict[str, Any]] | None = None


class LLMProviderToggleRequest(BaseModel):
    enabled: bool | None = None


class LLMModelUpsertRequest(BaseModel):
    id: str
    name: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_format: str = "openai_chat"
    reasoning_type: str = "auto"
    default_effort: str = "high"
    reasoning_effort: str | None = None
    capabilities: list[str] | None = None
    context_length: int | None = None
    description: str | None = ""


class LLMFetchModelsRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    provider_id: str | None = None


class CouncilConfigUpdateRequest(BaseModel):
    enabled: bool
    consensus_mode: str = Field(default="strict")
    timeout_seconds: float = Field(default=60.0, ge=10.0, le=300.0)
    roles: dict[str, Any]


class CouncilApplySuiteRequest(BaseModel):
    suite_id: str


class CouncilResetRoleRequest(BaseModel):
    role_id: str


class CouncilTestRequest(BaseModel):
    mock_market_prompt: str | None = None


class InitialCapitalUpdate(BaseModel):
    initial_capital: float = Field(gt=0, le=1_000_000_000)
    confirmation: str = Field(min_length=1, max_length=80)


class GatewayReplayRequest(BaseModel):
    confirmation: str


class InstrumentAddRequest(BaseModel):
    inst_id: str = Field(pattern=r"^[A-Z0-9]{2,15}-USDT-SWAP$")


class InstrumentDeleteRequest(BaseModel):
    confirmation: str


class ManualCloseRequest(BaseModel):
    close_token: str = Field(min_length=20, max_length=200)
    admin_password: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=8, max_length=200)


class UpdateRequest(BaseModel):
    confirmation: str


class PromptOverrideRequest(BaseModel):
    content: str = Field(max_length=12000)


class PromptLibraryUpdate(BaseModel):
    active_style: str = Field(pattern=r"^(stable|aggressive|custom)$")
    trading_system: str = Field(default="", max_length=12000)
    trading_user: str = Field(default="", max_length=12000)
    evolution_system: str = Field(default="", max_length=12000)
    evolution_user: str = Field(default="", max_length=12000)


class PromptProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=240)
    source_id: str = Field(default="stable", max_length=80)


class PromptProfileUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=240)
    enabled: bool = True
    editor_mode: str = Field(default="modules", pattern=r"^(simple|advanced|modules)$")
    simple_policy: dict[str, Any] = Field(default_factory=dict)
    pipelines: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    trading_system: str = Field(default="", max_length=12000)
    trading_user: str = Field(default="", max_length=12000)
    evolution_system: str = Field(default="", max_length=12000)
    evolution_user: str = Field(default="", max_length=12000)
    note: str = Field(default="后台更新", max_length=240)


class PromptImportRequest(BaseModel):
    payload: dict[str, Any]
    name_override: str = Field(default="", max_length=60)


class PromptRollbackRequest(BaseModel):
    revision_id: str = Field(min_length=1, max_length=80)


class InterceptorToggleRequest(BaseModel):
    enabled: bool

class InterceptorCodeRequest(BaseModel):
    code: str

class InterceptorCreateRequest(BaseModel):
    filename: str = Field(min_length=3, max_length=100)
    code: str

class InterceptorReorderRequest(BaseModel):
    pipeline_order: list[str]

class InterceptorTestRequest(BaseModel):
    scenario: dict[str, Any] | None = None

class BackupJobCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source_id: str = Field(default="nightly-default", max_length=80)


class BackupJobUpdateRequest(BaseModel):
    job: dict[str, Any]


class SimpleBackupUpdateRequest(BaseModel):
    enabled: bool = True
    schedule_time: str = Field(default="02:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    destination: str = Field(pattern=r"^(local|s3|oss|webdav|baidu_oauth)$")
    retention: int = Field(default=3, ge=1, le=365)
    endpoint: str = Field(default="", max_length=300)
    bucket: str = Field(default="", max_length=120)
    credentials: dict[str, str] = Field(default_factory=dict)


class BackupCredentialUpdateRequest(BaseModel):
    credential_ref: str = Field(min_length=1, max_length=100)
    credentials: dict[str, str]


class BackupJobRunRequest(BaseModel):
    confirmation: str


class BackupJobImportRequest(BaseModel):
    payload: dict[str, Any]
    name_override: str = Field(default="", max_length=80)


class BackupVerifyRequest(BaseModel):
    archive_path: str = Field(min_length=1, max_length=300)
    expected_sha256: str = Field(default="", max_length=64)
    key_env: str = Field(default="", max_length=64)


class ChannelToggleRequest(BaseModel):
    enabled: bool
    webhook_url: str | None = None
    wechat_webhook: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_api_base: str | None = None
    qq_app_id: str | None = None
    qq_client_secret: str | None = None
    qq_openid: str | None = None


class BackupMethodsUpdate(BaseModel):
    baidu_enabled: bool
    local_enabled: bool
    local_retention: int = Field(ge=1, le=30)
    sqlite_enabled: bool
    sqlite_retention: int = Field(ge=1, le=90)


class NotificationConfigUpdate(BaseModel):
    webhook_enabled: bool = False
    webhook_url: str = ""
    wechat_enabled: bool = False
    wechat_webhook: str = ""
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str = ""
    telegram_api_base: str | None = None
    qq_enabled: bool = False
    qq_app_id: str = ""
    qq_client_secret: str | None = None
    qq_openid: str = ""


class QQOpenIDCaptureStartRequest(BaseModel):
    app_id: str | None = None
    client_secret: str | None = None
    timeout: int = 60


class NotificationTestRequest(BaseModel):
    channel: str = Field(pattern=r"^(webhook|wechat|telegram|qq)$")
    confirmation: str = ""


class NotificationScheduleUpdate(BaseModel):
    briefing_times: list[str] = Field(min_length=1, max_length=6)


class BackupRequest(BaseModel):
    confirmation: str


class BackupRestoreRequest(BaseModel):
    archive_name: str
    confirmation: str


class MemoryItemRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class MemoryUpdateAllRequest(BaseModel):
    items: list[str]


def require_admin_token(token: str) -> None:
    expected = settings.admin_token or settings.setup_token
    if not expected:
        raise HTTPException(status_code=503, detail="后台尚未设置 OKXQUANT_SETUP_TOKEN 或 OKXQUANT_ADMIN_TOKEN")
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="管理员令牌无效")


def current_admin(x_okxquant_session: str | None = None, x_okxquant_admin_token: str | None = None) -> dict[str, Any]:
    user = admin_auth.validate_session(x_okxquant_session or "")
    if user:
        return user
    if x_okxquant_admin_token and not admin_auth.has_users():
        require_admin_token(x_okxquant_admin_token)
        return {"id": 0, "username": "legacy-token", "role": "legacy", "enabled": 1}
    raise HTTPException(status_code=401, detail="管理员会话已失效，请重新登录")


def require_admin_header(x_okxquant_admin_token: str | None = None, x_okxquant_session: str | None = None) -> dict[str, Any]:
    # All browser/admin routes resolve the session from the same context.  The
    # legacy token remains only for headless bootstrap compatibility.
    return current_admin(x_okxquant_session or REQUEST_SESSION.get(), x_okxquant_admin_token)


def require_superadmin(x_okxquant_session: str | None = None) -> dict[str, Any]:
    user = admin_auth.validate_session(x_okxquant_session or REQUEST_SESSION.get())
    if not user:
        raise HTTPException(status_code=401, detail="管理员会话已失效，请重新登录")
    if user["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="仅超级管理员可以执行此操作")
    return user


def read_json(filename: str, default: Any) -> Any:
    path = DATA_DIR / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def script_state(script_name: str) -> dict[str, Any]:
    path = SCRIPTS_DIR / script_name
    return {"name": script_name, "exists": path.exists(), "path": str(path)}


def file_health(filename: str, expected_interval: int) -> dict[str, Any]:
    path = DATA_DIR / filename
    if not path.exists():
        return {"name": filename, "exists": False, "age_seconds": None, "fresh": False}
    age = max(0, int(time.time() - path.stat().st_mtime))
    result = {"name": filename, "exists": True, "age_seconds": age, "fresh": age <= expected_interval * 2, "bytes": path.stat().st_size}
    if filename == "news_sentiment.json":
        try:
            news = json.loads(path.read_text(encoding="utf-8"))
            state = news.get("connection_status", "unavailable")
            at = news.get("last_success_at")
            ready = (state == "fresh" and isinstance(at, (int, float))
                     and not isinstance(at, bool) and 0 <= time.time() - at <= expected_interval * 2)
            result.update(fresh=bool(result["fresh"] and ready), data_status=state)
        except (OSError, ValueError, TypeError, AttributeError):
            result.update(fresh=False, data_status="invalid")
    return result


def log_tail(filename: str, lines: int = 30) -> str:
    path = ROOT / "logs" / filename
    if not path.exists():
        return "暂无日志"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(lines, 200)):])


def decision_summary() -> list[dict[str, Any]]:
    raw = read_json("ai_brain_decisions.json", {})
    result = []
    for inst_id, item in raw.items():
        decision = item.get("decision", {}) if isinstance(item, dict) else {}
        result.append({
            "instId": inst_id,
            "action": decision.get("action", "WAIT"),
            "confidence": decision.get("confidence", 0),
            "summary": decision.get("summary_reason", ""),
            "updated_at": item.get("time_str", "") if isinstance(item, dict) else "",
        })
    return result


def get_admin_configuration() -> dict[str, str]:
    baseline = load_account_baseline()
    from okxquant_backend.council_manager import load_council_config
    council = load_council_config()
    try:
        llm_rt = get_active_llm_runtime()
        model_name = llm_rt.get("model") or settings.llm_model or "默认主脑"
        effort = (llm_rt.get("reasoning_effort") or settings.llm_reasoning_effort or "HIGH").upper()
    except Exception:
        model_name = settings.llm_model or "默认主脑"
        effort = (settings.llm_reasoning_effort or "HIGH").upper()

    has_notify = bool(settings.notification_webhook or getattr(settings, "qq_bot_app_id", None) or getattr(settings, "tg_bot_token", None) or getattr(settings, "wechat_webhook", None))

    from okxquant_backend.account_connections import load as load_connections
    connection_state=load_connections()
    def configured_mode(mode):
        if connection_state["managed"]:
            connection=connection_state["connections"].get(connection_state["bindings"].get(mode))
            return bool(connection and connection.get("auth_type")=="api_key" and all(connection.get("credentials",{}).get(k) for k in ("api_key","secret_key","passphrase")))
        from scripts.okx_runtime import legacy_environment
        legacy=legacy_environment(mode=mode)
        return legacy.configured and (mode==legacy_environment().mode or legacy.source=='separate-credentials')
    return {
        "OKX 当前环境": "模拟盘 DEMO" if settings.okx_simulated else "实盘 LIVE",
        "OKX 实盘凭证": "已完整配置" if configured_mode("live") else "未配置",
        "OKX 模拟盘凭证": "已配置" if configured_mode("demo") else "未配置",
        "LLM 决策主脑": model_name,
        "LLM 思考强度": effort,
        "模型委员会": "已启用" if council.get("enabled") else "未启用",
        "物理拦截管线": "以拦截器配置与执行记录为准",
        "云端 OCO 覆盖": "以当前持仓保护单校验结果为准",
        "初始本金基准": f"{baseline['initial_capital']:,.2f} USDT" if baseline["baseline_configured"] else "尚未确认；累计收益暂不展示",
        "管理员系统": "账号密码 + 服务端会话" if admin_auth.has_users() else "尚未初始化",
        "通知告警通道": "已配置多通道" if has_notify else "未配置",
        "应急平仓机制": "一次性 Token 复核已就绪" if settings.manual_close_enabled else "已禁用",
    }


def runtime_overview() -> dict[str, Any]:
    from okxquant_gateway.scheduler import runtime_controls
    from okxquant_backend.account_connections import runtime_credentials, trading_connection_summary
    health_files = [
        file_health("ai_brain_decisions.json", 15 * 60),
        file_health("factor_library_snapshot.json", 60),
        file_health("news_sentiment.json", 10 * 60),
        file_health("trading_ledger.json", 15 * 60),
    ]
    all_fresh = all(h.get("fresh", False) for h in health_files)
    health_payload = {
        "overall": "LIVE" if all_fresh else "STALE",
        "files": health_files,
    }
    positions_payload = read_json("position_trackers.json", {})
    return {
        "service": {"version": "0.1.0", "pid": os.getpid(), "uptime_seconds": int(time.time() - STARTED_AT)},
        "runtime_controls": runtime_controls(),
        "credentials": runtime_credentials(bool(settings.llm_api_key)),
        "trading_connection": trading_connection_summary(),
        "configuration": get_admin_configuration(),
        "data_health": health_payload,
        "decisions": decision_summary(),
        "trackers": len(positions_payload) if isinstance(positions_payload, dict) else 0,
        "logs": {
            "trader": log_tail("ai_factor_trader.log", 18),
            "backend": log_tail("okxquant_backend.log", 18),
            "scheduler": log_tail("okxquant_scheduler.log", 18),
        },
        "audit": recent_audit(20),
    }


def git(command: list[str]) -> str:
    try:
        result = subprocess.run(["git", *command], cwd=ROOT, text=True, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git command timed out after {exc.timeout}s") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout.strip()


def update_status(*, fetch_remote: bool = True) -> dict[str, Any]:
    if os.getenv("OKXQUANT_DEPLOYMENT_MODE", "").strip().lower() == "docker":
        return {
            "deployment": "docker",
            "managed_externally": True,
            "branch": os.getenv("OKXQUANT_BUILD_BRANCH", "image"),
            "local": os.getenv("OKXQUANT_BUILD_COMMIT", "image"),
            "remote": "",
            "behind": 0,
            "ahead": 0,
            "dirty": False,
            "update_note": "Docker deployment is managed by the host: docker compose build --pull && docker compose up -d.",
        }
    try:
        local = git(["rev-parse", "--short", "HEAD"])
        branch = git(["branch", "--show-current"])
        dirty = bool(git(["status", "--porcelain"]))
        remote = ""
        behind = ahead = 0
        try:
            if fetch_remote:
                git(["fetch", "--quiet", "origin", branch])
            remote = git(["rev-parse", "--short", f"origin/{branch}"])
            ahead, behind = [int(item) for item in git(["rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"]).split()]
        except RuntimeError:
            pass
        return {"branch": branch, "local": local, "remote": remote, "behind": behind, "ahead": ahead, "dirty": dirty}
    except RuntimeError as exc:
        return {"error": str(exc)}


@app.get("/robots.txt", include_in_schema=False)
def top_robots_txt() -> Response:
    rf = ROOT / "okxquant_frontend" / "dist" / "robots.txt"
    if rf.is_file():
        return FileResponse(str(rf), media_type="text/plain", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    pf = ROOT / "okxquant_frontend" / "public" / "robots.txt"
    if pf.is_file():
        return FileResponse(str(pf), media_type="text/plain", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    return PlainTextResponse("User-agent: *\nAllow: /\nAllow: /trading\nAllow: /factors\nAllow: /news\nAllow: /lab\nAllow: /history\nAllow: /docs\nAllow: /images/\nDisallow: /admin/\nDisallow: /api/\nSitemap: https://trade.112102.xyz/sitemap.xml\n")


@app.get("/sitemap.xml", include_in_schema=False)
def top_sitemap_xml() -> Response:
    sf = ROOT / "okxquant_frontend" / "dist" / "sitemap.xml"
    if sf.is_file():
        return FileResponse(str(sf), media_type="application/xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    pf = ROOT / "okxquant_frontend" / "public" / "sitemap.xml"
    if pf.is_file():
        return FileResponse(str(pf), media_type="application/xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    return Response(content="""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://trade.112102.xyz/</loc><priority>1.0</priority></url><url><loc>https://trade.112102.xyz/factors</loc><priority>0.9</priority></url><url><loc>https://trade.112102.xyz/news</loc><priority>0.8</priority></url><url><loc>https://trade.112102.xyz/lab</loc><priority>0.8</priority></url><url><loc>https://trade.112102.xyz/history</loc><priority>0.8</priority></url><url><loc>https://trade.112102.xyz/docs</loc><priority>0.9</priority></url></urlset>""", media_type="application/xml")


@app.get("/trading", include_in_schema=False)
@app.get("/factors", include_in_schema=False)
@app.get("/news", include_in_schema=False)
@app.get("/lab", include_in_schema=False)
@app.get("/history", include_in_schema=False)
@app.get("/docs", include_in_schema=False)
@app.get("/docs/{subpath:path}", include_in_schema=False)
def public_tab_spa_page(subpath: str = "") -> FileResponse:
    vue_index = ROOT / "okxquant_frontend" / "dist" / "index.html"
    if vue_index.is_file():
        return FileResponse(
            str(vue_index),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate, max-age=0"},
        )
    return FileResponse(
        str(ROOT / "dashboard" / "templates" / "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate, max-age=0"},
    )


@app.get("/admin", include_in_schema=False)
@app.get("/admin/{subpath:path}", include_in_schema=False)
def admin_page(subpath: str = "") -> FileResponse:
    # Vue SPA single entry point
    vue_index = ROOT / "okxquant_frontend" / "dist" / "index.html"
    if vue_index.is_file():
        return FileResponse(str(vue_index), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    fallback_index = ROOT / "okxquant_frontend" / "index.html"
    return FileResponse(str(fallback_index), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/api/v1/admin/auth/status")
def admin_auth_status() -> dict[str, Any]:
    return {"initialized": admin_auth.has_users(), "mode": "account-password", "session_hours": 12}


@app.post("/api/v1/admin/login", include_in_schema=False)
@app.post("/api/v1/admin/auth/login")
def admin_login(payload: AdminLoginRequest) -> dict[str, Any]:
    try:
        result = admin_auth.login(payload.username, payload.password)
    except PermissionError as exc:
        audit_record("admin.login", "failed", {"username": payload.username})
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    audit_record("admin.login", "success", {"username": result["user"]["username"]})
    return result


@app.post("/api/v1/admin/logout", include_in_schema=False)
@app.post("/api/v1/admin/auth/logout")
def admin_logout(x_okxquant_session: str | None = Header(default=None)) -> dict[str, Any]:
    user = admin_auth.validate_session(x_okxquant_session or "")
    admin_auth.logout(x_okxquant_session or "")
    if user:
        audit_record("admin.logout", "success", {"username": user["username"]})
    return {"logged_out": True}


@app.get("/api/v1/admin/auth/me")
def admin_me(x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    user = current_admin(x_okxquant_session, None)
    if user.get("role") == "legacy":
        raise HTTPException(status_code=401, detail="请使用管理员账号密码登录")
    return {"user": user}


@app.get("/api/v1/admin/users")
def admin_users(x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    return {"users": admin_auth.list_users(), "current_user_id": actor["id"]}


@app.post("/api/v1/admin/users")
def create_admin_user(payload: AdminCreateRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try:
        user = admin_auth.create_user(payload.username, payload.password, payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("admin.user.create", "success", {"actor": actor["username"], "username": user["username"], "role": user["role"]})
    return {"created": user}


@app.put("/api/v1/admin/users/{user_id}/enabled")
def update_admin_enabled(user_id: int, payload: AdminEnabledRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try:
        admin_auth.set_enabled(user_id, payload.enabled, actor["id"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_record("admin.user.enabled", "success", {"actor": actor["username"], "user_id": user_id, "enabled": payload.enabled})
    return {"user": admin_auth.get_user(user_id)}


@app.post("/api/v1/admin/users/{user_id}/unlock")
def unlock_admin_user(user_id: int, payload: AdminUnlockRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    expected = f"UNLOCK ADMIN {user_id}"
    if payload.confirmation.strip().upper() != expected:
        raise HTTPException(status_code=400, detail=f"确认短语必须精确为：{expected}")
    try:
        admin_auth.unlock_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit_record("admin.user.unlock", "success", {"actor": actor["username"], "user_id": user_id})
    return {"user": admin_auth.get_user(user_id)}


@app.put("/api/v1/admin/users/{user_id}/password")
def update_admin_password(user_id: int, payload: AdminPasswordRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = current_admin(x_okxquant_session, None)
    if actor.get("role") == "legacy":
        raise HTTPException(status_code=401, detail="请使用管理员账号密码登录")
    if actor["id"] != user_id and actor["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="只能修改自己的密码")
    if actor["id"] == user_id and not admin_auth.verify_password(actor["id"], payload.current_password):
        raise HTTPException(status_code=403, detail="当前密码不正确")
    try:
        admin_auth.change_password(user_id, payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("admin.password.update", "success", {"actor": actor["username"], "user_id": user_id})
    return {"changed": True, "reauthenticate": True}


@app.get("/api/v1/admin/overview")
def admin_overview(x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    return runtime_overview()


@app.get("/api/v1/admin/audit")
def admin_audit(x_okxquant_admin_token: str | None = Header(default=None), limit: int = 50) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    return {"records": recent_audit(limit)}


ADMIN_LOG_SOURCES = {"trader": "ai_factor_trader.log", "backend": "okxquant_backend.log", "scheduler": "okxquant_scheduler.log"}


@app.get("/api/v1/admin/runtime")
def admin_runtime(x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    payload = runtime_overview()
    from scripts.wait_audit import public_status as wait_status
    from scripts.okx_runtime import selected_environment
    payload["wait_audit"] = wait_status(selected_environment().identity)
    from scripts.entry_opportunities import public_status as opportunity_status
    payload["entry_opportunities"] = opportunity_status(selected_environment().identity)
    from scripts.decision_reporting import execution_history
    payload["recent_execution_cycles"] = execution_history(selected_environment().identity)
    from scripts.scenario_shadow import public_status as shadow_status
    payload["scenario_shadow"] = shadow_status(selected_environment().identity)
    from scripts.capital_pool import status as capital_status
    payload["capital_pool"] = capital_status(selected_environment())
    payload["decision_cycle"] = read_json("trading_state.json", {}).get("decision_cycle", {})
    raw_decisions = read_json("ai_brain_decisions.json", {})
    if isinstance(raw_decisions, dict):
        full_list = []
        for k, v in raw_decisions.items():
            if isinstance(v, dict):
                full_list.append({
                    "instId": v.get("instId", k),
                    "action": v.get("decision", {}).get("action", v.get("action", "WAIT")),
                    "confidence": (v.get("decision", {}).get("confidence", v.get("confidence", 0.0)) or 0.0) / (100.0 if (v.get("decision", {}).get("confidence", 0) or 0) > 1 else 1.0),
                    "timestamp": v.get("time_str", str(v.get("timestamp", ""))),
                    "reason": v.get("decision", {}).get("summary_reason") or v.get("thought_process", {}).get("market_structure") or v.get("reason", ""),
                })
        payload["full_decisions"] = full_list
    elif isinstance(raw_decisions, list):
        payload["full_decisions"] = raw_decisions
    else:
        payload["full_decisions"] = []
    payload["llm_runtime"] = get_active_llm_runtime()
    return payload


@app.get("/api/v1/admin/logs")
def admin_logs(source: str = "trader", lines: int = 100, x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    filename = ADMIN_LOG_SOURCES.get(source)
    if not filename:
        raise HTTPException(status_code=400, detail=f"日志来源仅支持：{', '.join(ADMIN_LOG_SOURCES)}")
    return {"source": source, "file": filename, "content": log_tail(filename, lines)}


@app.get("/api/v1/admin/gateway")
def gateway_status(x_okxquant_admin_token: str | None = Header(default=None), limit: int = 50) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    store = GatewayStore(GATEWAY_DB_PATH)
    pid_file = DATA_DIR / "okxquant_gateway.pid"
    pid = int(pid_file.read_text().strip()) if pid_file.exists() and pid_file.read_text().strip().isdigit() else 0
    running = False
    if pid:
        try:
            os.kill(pid, 0)
            running = True
        except OSError:
            pass
    worker_supported = sys.platform != "win32"
    return {"version": GATEWAY_VERSION, "running": running, "pid": pid or None,
            "worker_supported": worker_supported,
            "runtime_note": "" if worker_supported else "当前服务运行在 Windows：支持管理与只读连接测试；自动调度与交易 Worker 需要在 WSL/Linux 启动。",
            "stats": store.stats(), "event_health": store.event_health(), "deliveries": store.recent(limit), "scheduler": scheduler_snapshot(store)}


@app.post("/api/v1/admin/gateway/deliveries/{delivery_id}/replay")
def replay_gateway_delivery(delivery_id: int, payload: GatewayReplayRequest, x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    if payload.confirmation.strip().upper() != f"REPLAY {delivery_id}":
        raise HTTPException(status_code=400, detail=f"确认短语必须精确为：REPLAY {delivery_id}")
    store = GatewayStore(GATEWAY_DB_PATH)
    if not store.replay_dead(delivery_id):
        raise HTTPException(status_code=409, detail="仅允许重放当前处于 dead 状态的投递")
    audit_record("gateway.delivery.replay", "accepted", {"delivery_id": delivery_id})
    return {"accepted": True, "delivery_id": delivery_id, "status": "pending"}


@app.get("/api/v1/admin/agents")
def admin_agents(x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    store = GatewayStore(GATEWAY_DB_PATH)
    return {
        "agents": agent_statuses(store.job_runs(100)),
        "model_stats": store.model_stats(),
        "model_calls": store.model_calls(50),
        "prompt_policy": "交易主脑和自进化均由 Python 直接构造并传输提示词；Gateway 只记录无内容遥测。",
        "secret_store": secret_store_status(),
    }


@app.get("/api/v1/admin/plugins")
def admin_plugins(x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    return {"plugins": plugin_statuses(), "installation_policy": "builtin-only", "reason": "实盘控制面不允许远程上传或执行任意插件代码"}


@app.get("/api/v1/admin/config")
def admin_config(x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    return {
        "authentication_mode": "account-password",
        "configuration": get_admin_configuration(),
        "editable": {
            "okx_environment": settings.okx_environment,
            "okx_live_configured": settings.okx_live_configured,
            "okx_demo_configured": settings.okx_demo_configured,
            "okx_simulated": settings.okx_simulated,
            "llm_base_url": settings.llm_base_url,
            "llm_model": settings.llm_model,
            "llm_reasoning_effort": settings.llm_reasoning_effort,
            "notification_webhook": settings.notification_webhook,
            "manual_close_enabled": settings.manual_close_enabled,
            "initial_capital": load_account_baseline()["initial_capital"],
            "baseline_configured": load_account_baseline()["baseline_configured"],
            "initial_capital_reset_time": load_account_baseline()["reset_time"],
        },
    }


@app.put("/api/v1/admin/account-baseline")
def admin_update_account_baseline(payload: InitialCapitalUpdate, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    if payload.confirmation.strip().upper() != "UPDATE CAPITAL":
        raise HTTPException(status_code=400, detail="确认短语必须精确为：UPDATE CAPITAL")
    try:
        result = update_initial_capital(payload.initial_capital)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("account.baseline.update", "success", {
        "actor": actor["username"],
        "previous_initial_capital": result["previous_initial_capital"],
        "initial_capital": result["initial_capital"],
        "reset_time_preserved": result["reset_time"],
    })
    return {
        "updated": True,
        **result,
        "effect": "主页累计盈亏、累计 ROI 与权益基准线将按新本金重算；历史起算时间保持不变。",
    }


_OKX_RUNTIME_CACHE: dict[str, Any] = {"at": 0.0, "mode": "", "payload": None}


@app.get("/api/v1/admin/okx/runtime")
def admin_okx_runtime(x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session"), refresh: int = 0) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_session=x_okxquant_session)
    configured = settings.okx_demo_configured if settings.okx_environment == "demo" else settings.okx_live_configured
    now = time.time()
    identity = hashlib.sha256("\0".join((settings.okx_environment, settings.okx_api_key, settings.okx_secret_key, settings.okx_passphrase)).encode()).hexdigest()
    # Each CLI probe spawns 4-6 subprocesses (~1.5s). Serve a 15s cache so page
    # navigation feels instant; the UI's "重新诊断" passes refresh=1 to bypass.
    if not refresh and _OKX_RUNTIME_CACHE["payload"] and _OKX_RUNTIME_CACHE.get("identity") == identity and now - _OKX_RUNTIME_CACHE["at"] < 15:
        return dict(_OKX_RUNTIME_CACHE["payload"])
    payload = diagnose_okx_runtime(settings.okx_environment, configured)
    _OKX_RUNTIME_CACHE.update({"at": time.time(), "mode": settings.okx_environment, "identity": identity, "payload": copy.deepcopy(payload)})
    return payload


@app.post("/api/v1/admin/okx/oauth/start")
def admin_okx_oauth_start(payload: OkxOAuthStartRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    from okxquant_backend.account_connections import load as load_connections
    if load_connections()['connections']:
        raise HTTPException(status_code=409, detail='已有独立连接，请在账户中心授权；禁止覆盖全局OAuth登录态')
    try:
        result = start_oauth_device_login(payload.site)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("okx.oauth.start", "success", {"actor": actor["username"], "site": payload.site, "status": result.get("status")})
    return result


@app.get("/api/v1/admin/okx/oauth/status")
def admin_okx_oauth_status(x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_session=x_okxquant_session)
    return oauth_status()


@app.get("/api/v1/admin/okx/cli-check")
def admin_okx_cli_check(x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    """Check Node.js/npm/OKX CLI prerequisites without side effects."""
    require_admin_header(x_okxquant_session=x_okxquant_session)
    return check_node_npm()


@app.post("/api/v1/admin/okx/install-cli")
def admin_okx_install_cli(payload: OkxCliInstallRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    """One-click install or upgrade OKX CLI via npm. Requires superadmin and explicit confirmation."""
    actor = require_superadmin(x_okxquant_session)
    if payload.confirmation.strip().upper() != "INSTALL OKX CLI":
        raise HTTPException(status_code=400, detail="确认短语必须精确为：INSTALL OKX CLI")
    result = install_okx_cli()
    audit_record("okx.cli.install", "success" if result.get("ok") else "failed", {"actor": actor["username"], "detail": result.get("detail", "")[:300]})
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("detail") or "OKX CLI 安装失败")
    return result


@app.put("/api/v1/admin/config")
def update_admin_config(payload: AdminConfigUpdate, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    refresh_settings()
    data = payload.model_dump(exclude_none=True)
    sensitive = any(key.startswith("okx_") or key == "manual_close_enabled" for key in data)
    if sensitive: require_superadmin(x_okxquant_session)
    else: require_admin_header(x_okxquant_admin_token)
    if "llm_base_url" in data and data["llm_base_url"] and not data["llm_base_url"].startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="LLM Base URL 必须以 http:// 或 https:// 开头")
    if "notification_webhook" in data and data["notification_webhook"] and not data["notification_webhook"].startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="Webhook 必须以 http:// 或 https:// 开头")
    from okxquant_backend.account_connections import load as load_connections
    if any(k.startswith('okx_') for k in data) and load_connections()['managed']:
        raise HTTPException(status_code=409, detail='账户中心已接管连接，请在账户中心检查、绑定或更换账号，不能通过旧配置绕过安全检查')
    selected_mode = data.get("okx_environment") or ("demo" if data.get("okx_simulated") else "live" if "okx_simulated" in data else None)
    if selected_mode and selected_mode != settings.okx_environment:
        import fcntl
        lock_path = DATA_DIR / ".ai_factor_trader.lock"; lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            try: fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise HTTPException(status_code=409, detail="交易周期正在执行，OKX 环境已冻结；请等待本周期结束后再切换")
            finally:
                try: fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                except OSError: pass
    secret_values = {
        "OKX_LIVE_API_KEY": data.get("okx_live_api_key"), "OKX_LIVE_SECRET_KEY": data.get("okx_live_secret_key"), "OKX_LIVE_PASSPHRASE": data.get("okx_live_passphrase"),
        "OKX_DEMO_API_KEY": data.get("okx_demo_api_key"), "OKX_DEMO_SECRET_KEY": data.get("okx_demo_secret_key"), "OKX_DEMO_PASSPHRASE": data.get("okx_demo_passphrase"),
        "OKX_API_KEY": data.get("okx_api_key"), "OKX_SECRET_KEY": data.get("okx_secret_key"), "OKX_PASSPHRASE": data.get("okx_passphrase"),
        "LLM_API_KEY": data.get("llm_api_key"),
    }
    save_secrets({key: value for key, value in secret_values.items() if value})
    env_values = {
        "OKXQUANT_OKX_ENV": selected_mode,
        "OKX_IS_SIMULATED": "1" if selected_mode == "demo" else "0" if selected_mode else None,
        "LLM_BASE_URL": data.get("llm_base_url"),
        "LLM_MODEL": data.get("llm_model"),
        "LLM_REASONING_EFFORT": data.get("llm_reasoning_effort"),
        "OKXQUANT_NOTIFICATION_WEBHOOK": data.get("notification_webhook"),
        "OKXQUANT_MANUAL_CLOSE_ENABLED": "1" if data.get("manual_close_enabled") else "0" if "manual_close_enabled" in data else None,
    }
    update_env(env_values)
    refresh_settings()
    if any(k.startswith("llm_") for k in data):
        try:
            cfg = init_llm_providers()
            active_p = next((p for p in cfg.get("providers", []) if p["id"] == cfg.get("active_provider_id")), None)
            if active_p:
                if "llm_base_url" in data and data["llm_base_url"]:
                    active_p["base_url"] = data["llm_base_url"].rstrip("/")
                if "llm_api_key" in data and data["llm_api_key"]:
                    active_p["api_key"] = data["llm_api_key"]
                if "llm_model" in data and data["llm_model"]:
                    cfg["active_model_id"] = data["llm_model"]
                if "llm_reasoning_effort" in data and data["llm_reasoning_effort"]:
                    cfg["active_reasoning_effort"] = data["llm_reasoning_effort"]
                _atomic_write_json(LLM_PROVIDERS_FILE, cfg)
        except Exception:
            pass
    audit_record("config.update", "success", {"fields": sorted(data.keys())})
    return {
        "updated": True,
        "instrument_support": pool_support(load_instruments(), settings.okx_environment) if selected_mode else None,
        "restart_note": "Long-running strategy processes read updated .env on their next execution cycle.",
        "manual_close_enabled": settings.manual_close_enabled,
    }


@app.get("/api/v1/admin/llm/providers")
@app.get("/api/v1/admin/llm/models")
def admin_get_llm_models(x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_session=x_okxquant_session)
    return load_llm_config(mask_keys=True)


@app.post("/api/v1/admin/llm/activate")
def admin_activate_llm_model(payload: LLMActivateRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try:
        result = activate_provider_model(payload.provider_id or "", payload.model_id, payload.reasoning_effort)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("llm.model.activate", "success", {
        "actor": actor["username"],
        "model_id": payload.model_id,
        "reasoning_effort": result.get("active_reasoning_effort"),
    })
    return result


@app.post("/api/v1/admin/llm/test")
def admin_test_llm(payload: LLMTestRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_session=x_okxquant_session)
    base_url = payload.base_url
    api_key = payload.api_key
    api_format = payload.api_format or "openai_chat"

    raw_config = load_llm_config(mask_keys=False)
    from okxquant_backend.llm_manager import select_model
    try:
        m_entry = select_model(raw_config, payload.model, payload.provider_id or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.provider_id and not m_entry:
        raise HTTPException(status_code=400, detail="所选供应商下未找到模型")
    if m_entry:
        if not base_url:
            base_url = m_entry.get("base_url")
        if not api_key:
            api_key = m_entry.get("api_key")
        if not payload.api_format or payload.api_format == "openai_chat":
            api_format = m_entry.get("api_format", "openai_chat")

    if not base_url:
        active_runtime = get_active_llm_runtime()
        base_url = active_runtime.get("base_url")
        if not api_key:
            api_key = active_runtime.get("api_key")
        if not payload.api_format:
            api_format = active_runtime.get("api_format", "openai_chat")

    result = test_llm_connection(
        base_url=base_url or "",
        api_key=api_key or "",
        model=payload.model,
        api_format=api_format,
        reasoning_effort=payload.reasoning_effort,
        reasoning_type=payload.reasoning_type,
        timeout=25.0,
    )
    audit_record("llm.connection.test", "success" if result.get("ok") else "failed", {
        "model": payload.model,
        "api_format": api_format,
        "latency_ms": result.get("latency_ms"),
        "status_code": result.get("status_code"),
        "reasoning_detected": result.get("reasoning_detected"),
        "endpoint": result.get("endpoint"),
    })
    return result


@app.post("/api/v1/admin/llm/models")
@app.post("/api/v1/admin/llm/providers/{provider_id}/models")
def admin_upsert_llm_model(payload: LLMModelUpsertRequest, provider_id: str = "custom", x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try:
        res = upsert_model(provider_id if provider_id != "custom" else (payload.provider_id or "custom"), payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("llm.model.upsert", "success", {"actor": actor["username"], "model_id": payload.id, "api_format": res.get("api_format")})
    return res


@app.delete("/api/v1/admin/llm/models/{model_id}")
@app.delete("/api/v1/admin/llm/providers/{provider_id}/models/{model_id}")
def admin_delete_llm_model(model_id: str, provider_id: str = "custom", x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try:
        deleted = delete_model(provider_id if provider_id != "custom" else "", model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到该模型")
    audit_record("llm.model.delete", "success", {"actor": actor["username"], "model_id": model_id})
    return {"deleted": True, "model_id": model_id}


@app.post("/api/v1/admin/llm/providers")
def admin_upsert_llm_provider(payload: LLMProviderUpsertRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try:
        res = upsert_provider(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("llm.provider.upsert", "success", {"actor": actor["username"], "provider_id": res.get("id")})
    return res


@app.post("/api/v1/admin/llm/fetch-models")
def admin_fetch_remote_models(payload: LLMFetchModelsRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_session=x_okxquant_session)
    res = fetch_remote_models(
        base_url=payload.base_url or "",
        api_key=payload.api_key or "",
        provider_id=payload.provider_id,
    )
    audit_record("llm.remote.fetch", "success" if res.get("ok") else "failed", {
        "provider_id": payload.provider_id,
        "base_url": payload.base_url,
        "total": res.get("total", 0),
    })
    return res


@app.post("/api/v1/admin/llm/providers/{provider_id}/toggle")
def admin_toggle_llm_provider(provider_id: str, payload: LLMProviderToggleRequest | None = None, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try:
        enabled_val = payload.enabled if payload else None
        res = toggle_provider(provider_id, enabled=enabled_val)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("llm.provider.toggle", "success", {"actor": actor["username"], "provider_id": provider_id, "enabled": res["enabled"]})
    return res


@app.delete("/api/v1/admin/llm/providers/{provider_id}/models")
def admin_clear_llm_provider_models(provider_id: str, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try:
        cleared = clear_provider_models(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not cleared:
        raise HTTPException(status_code=404, detail="未找到该供应商")
    audit_record("llm.provider.clear_models", "success", {"actor": actor["username"], "provider_id": provider_id})
    return {"cleared": True, "provider_id": provider_id}


@app.delete("/api/v1/admin/llm/providers/{provider_id}")
def admin_delete_llm_provider(provider_id: str, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try:
        deleted = delete_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到该模型供应商")
    audit_record("llm.provider.delete", "success", {"actor": actor["username"], "provider_id": provider_id})
    return {"deleted": True, "provider_id": provider_id}


# ============================================================================
# MULTI-AGENT COUNCIL (多模型协作决策系统) ENDPOINTS
# ============================================================================

@app.get("/api/v1/admin/council/config")
def admin_get_council_config(x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_session=x_okxquant_session)
    from okxquant_backend.council_manager import load_council_config, get_available_presets, get_preset_suites
    cfg = load_council_config()
    cfg["available_presets"] = get_available_presets()
    cfg["available_suites"] = get_preset_suites()
    return cfg


@app.put("/api/v1/admin/council/config")
def admin_update_council_config(payload: CouncilConfigUpdateRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    from okxquant_backend.council_manager import save_council_config
    try:
        saved = save_council_config({
            "enabled": payload.enabled,
            "consensus_mode": payload.consensus_mode,
            "timeout_seconds": payload.timeout_seconds,
            "roles": payload.roles,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("council.config.update", "success", {
        "actor": actor["username"],
        "enabled": payload.enabled,
        "consensus_mode": payload.consensus_mode,
        "timeout_seconds": payload.timeout_seconds,
    })
    return {"status": "ok", "config": saved}


@app.post("/api/v1/admin/council/apply-suite")
def admin_apply_council_suite(payload: CouncilApplySuiteRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    from okxquant_backend.council_manager import apply_preset_suite
    try:
        saved = apply_preset_suite(payload.suite_id)
        audit_record("council.suite.apply", "success", {"actor": actor["username"], "suite_id": payload.suite_id})
        return {"status": "ok", "suite_id": payload.suite_id, "config": saved}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/admin/council/reset-role")
def admin_reset_council_role(payload: CouncilResetRoleRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    from okxquant_backend.council_manager import reset_role_template
    saved = reset_role_template(payload.role_id)
    audit_record("council.role.reset", "success", {"actor": actor["username"], "role_id": payload.role_id})
    return {"status": "ok", "role_id": payload.role_id, "config": saved}


@app.post("/api/v1/admin/council/test")
def admin_test_council_debate(payload: CouncilTestRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_session=x_okxquant_session)
    from okxquant_backend.council_manager import execute_council_debate, load_council_config
    from scripts.instrument_pool import load_instruments
    c_cfg = load_council_config()

    from scripts import trading_prompt
    from scripts.ai_brain_trader import get_user_prompt_override
    from scripts.risk_policy import load_policy
    from dataclasses import asdict
    test_packages = [{'instId':item['instId'], 'name':item.get('name',item['instId']), 'data_quality':'invalid'} for item in load_instruments()]
    # Free-form test text is not an authenticated account/market snapshot. Never invent balances/prices.
    bundle = trading_prompt.compose(active_profile(), {
        'test_context':payload.mock_market_prompt or '未提供已核验行情；仅测试连接与输出契约，应输出 WAIT。',
        'data_source':'unverified_test_text','account_balance':'UNKNOWN','account_positions':'UNKNOWN',
        'pending_orders':'UNKNOWN','execution_allowed':False,
    }, test_packages, override=get_user_prompt_override(), risk_contract=asdict(load_policy()))
    if not bundle.allow_open:
        return {'status':'error','error':'提示词偏好冲突，请先修正配置','composition':bundle.manifest,'executable':False}
    test_market=bundle.user
    test_sys=bundle.system

    try:
        brain_output, transcript = execute_council_debate(
            market_prompt=test_market,
            original_system_prompt=test_sys,
            timeout=float(c_cfg.get("timeout_seconds", 60.0)),
        )
        checked = trading_prompt.validate_response(brain_output, test_packages, allow_open=False)
        return {
            "status": "ok", "executable": False,
            "brain_output": checked,
            "transcript": transcript,
            "composition": bundle.manifest,
            "data_source": "unverified_test_text",
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
        }


# =========================================================================
# Physical Risk Interceptor Plugins API (Python Strategy/Risk Plugins)
# =========================================================================

@app.get("/api/v1/admin/interceptors")
def admin_list_interceptors(x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_session=x_okxquant_session)
    from okxquant_backend.interceptor_manager import list_plugins
    return {"plugins": list_plugins()}


@app.get("/api/v1/admin/interceptors/{filename}")
def admin_get_interceptor(filename: str, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_session=x_okxquant_session)
    from okxquant_backend.interceptor_manager import get_plugin_detail
    try:
        return get_plugin_detail(filename)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.put("/api/v1/admin/interceptors/{filename}/toggle")
def admin_toggle_interceptor(filename: str, payload: InterceptorToggleRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    from okxquant_backend.interceptor_manager import toggle_plugin
    res = toggle_plugin(filename, payload.enabled)
    audit_record("interceptor.toggle", "success", {"actor": actor["username"], "filename": filename, "enabled": payload.enabled})
    return res


@app.put("/api/v1/admin/interceptors/{filename}/code")
def admin_save_interceptor_code(filename: str, payload: InterceptorCodeRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    from okxquant_backend.interceptor_manager import save_plugin_code
    try:
        res = save_plugin_code(filename, payload.code)
        audit_record("interceptor.code.update", "success", {"actor": actor["username"], "filename": filename})
        return res
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@app.post("/api/v1/admin/interceptors")
def admin_create_interceptor(payload: InterceptorCreateRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    from okxquant_backend.interceptor_manager import create_plugin
    try:
        res = create_plugin(payload.filename, payload.code)
        audit_record("interceptor.create", "success", {"actor": actor["username"], "filename": payload.filename})
        return res
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@app.delete("/api/v1/admin/interceptors/{filename}")
def admin_delete_interceptor(filename: str, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    from okxquant_backend.interceptor_manager import delete_plugin
    try:
        delete_plugin(filename)
        audit_record("interceptor.delete", "success", {"actor": actor["username"], "filename": filename})
        return {"deleted": True, "filename": filename}
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


@app.post("/api/v1/admin/interceptors/reorder")
def admin_reorder_interceptors(payload: InterceptorReorderRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    from okxquant_backend.interceptor_manager import reorder_plugins
    res = reorder_plugins(payload.pipeline_order)
    audit_record("interceptor.reorder", "success", {"actor": actor["username"]})
    return {"plugins": res}


@app.post("/api/v1/admin/interceptors/test")
def admin_test_interceptors(payload: InterceptorTestRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_session=x_okxquant_session)
    from okxquant_backend.interceptor_manager import run_sandbox_test
    return run_sandbox_test(payload.scenario)

    audit_record("llm.model.delete", "success", {"actor": actor["username"], "provider_id": provider_id, "model_id": model_id})
    return {"deleted": True, "provider_id": provider_id, "model_id": model_id}


@app.get("/api/v1/admin/strategy/status")
def admin_strategy_status(x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    from okxquant_backend.strategy_status import strategy_status
    try:
        return strategy_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="策略风险状态暂不可核验") from exc


@app.get("/api/v1/admin/okx/account-snapshot")
def admin_okx_account_snapshot(x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    try: return okx_account_snapshot()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"获取 OKX 当前订单失败：{exc}") from exc


@app.post("/api/v1/admin/positions/close")
def manual_close_position(payload: ManualCloseRequest) -> dict[str, Any]:
    actor = require_superadmin(REQUEST_SESSION.get())
    refresh_settings()
    if not settings.manual_close_enabled:
        raise HTTPException(status_code=403, detail="后台手动平仓功能未启用")
    import fcntl
    lock_path = DATA_DIR / ".ai_factor_trader.lock"; lock_path.parent.mkdir(parents=True, exist_ok=True)
    if actor.get("role") == "legacy" or not admin_auth.verify_password(int(actor["id"]), payload.admin_password):
        raise HTTPException(status_code=403, detail="管理员密码验证失败")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try: fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise HTTPException(status_code=409, detail="交易主循环正在执行，暂不允许后台快速平仓；请等待本周期结束")
        try:
            result = fast_close_confirmed(payload.close_token, payload.confirmation)
            audit_record("position.close", "confirmed_closed", {"instId": result.get("instId"), "side": result.get("posSide"), "environment": result.get("environment"), "size": result.get("closed_size")})
            return result
        except ValueError as exc:
            audit_record("position.close", "rejected", {"error": str(exc)[:300]})
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            audit_record("position.close", "verification_failed", {"error": str(exc)[:300]})
            raise HTTPException(status_code=502, detail=f"OKX 快速平仓未完成确认：{exc}") from exc
        finally: fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@app.get("/api/v1/admin/instruments")
def admin_instruments(x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    trackers = read_json("position_trackers.json", {})
    active = set(trackers.keys()) if isinstance(trackers, dict) else set()
    pool = load_instruments()
    support = pool_support(pool, settings.okx_environment)
    return {
        "environment": settings.okx_environment,
        "support_summary": support,
        "instruments": [{**item, "protected": item["instId"] == "BTC-USDT-SWAP", "has_tracker": item["instId"] in active or item["name"] in active, "environment_support": support["items"][item["instId"]]} for item in pool],
        "limits": {"minimum": 1, "maximum": 6, "btc_required": True},
    }


@app.get("/api/v1/admin/instruments/support")
def admin_instrument_support(environment: str | None = None, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    refresh_settings()
    mode = environment or settings.okx_environment
    if mode not in {"demo", "live"}:
        raise HTTPException(status_code=400, detail="交易环境必须为 demo 或 live")
    return pool_support(load_instruments(), mode, refresh=True)


@app.post("/api/v1/admin/instruments")
def add_admin_instrument(payload: InstrumentAddRequest, x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    inst_id = payload.inst_id.upper()
    current = load_instruments()
    if any(item["instId"] == inst_id for item in current):
        raise HTTPException(status_code=409, detail="该币种已在交易池中")
    availability = opening_status(inst_id, settings.okx_environment)
    if not availability["can_open"]:
        raise HTTPException(status_code=503 if availability["status"] == "unknown" else 400, detail=availability["message"])
    if len(current) >= 6:
        raise HTTPException(status_code=409, detail="交易池最多允许 6 个币种；请先删除一个无持仓币种")
    try:
        matches = okx.instruments("SWAP", inst_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OKX 合约校验失败：{exc}") from exc
    raw = matches[0] if matches else {}
    if raw.get("instId") != inst_id or raw.get("settleCcy") != "USDT" or raw.get("state") != "live":
        raise HTTPException(status_code=400, detail="仅允许添加 OKX 在线可交易的 USDT 永续合约")
    item = from_okx_instrument(raw)
    save_instruments([*current, item])
    audit_record("instrument.add", "success", {"instId": inst_id})
    return {"added": item, "count": len(current) + 1, "effective": "immediate", "message": f"{item['name']} 已成功加入交易池并实时同步全网大屏与因果雷达"}


@app.delete("/api/v1/admin/instruments/{inst_id}")
def delete_admin_instrument(inst_id: str, payload: InstrumentDeleteRequest, x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    inst_id = inst_id.upper()
    if payload.confirmation.strip().upper() != f"REMOVE {inst_id}":
        raise HTTPException(status_code=400, detail=f"确认短语必须精确为：REMOVE {inst_id}")
    if inst_id == "BTC-USDT-SWAP":
        raise HTTPException(status_code=403, detail="BTC 是全局黑天鹅哨兵基准，不允许从交易池删除")
    current = load_instruments()
    if len(current) <= 1:
        raise HTTPException(status_code=409, detail="交易池至少保留 1 个币种")
    if not any(item["instId"] == inst_id for item in current):
        raise HTTPException(status_code=404, detail="该币种不在交易池中")
    trackers = read_json("position_trackers.json", {})
    coin = inst_id.split("-", 1)[0]
    if isinstance(trackers, dict) and (inst_id in trackers or coin in trackers):
        raise HTTPException(status_code=409, detail="该币种存在持仓追踪记录，为防止失去风控接管，禁止删除")
    updated = [item for item in current if item["instId"] != inst_id]
    save_instruments(updated)
    audit_record("instrument.remove", "success", {"instId": inst_id})
    return {"removed": inst_id, "count": len(updated), "effective": "immediate", "message": f"{inst_id} 已从交易池移除并实时同步全网大屏与因果雷达"}


@app.get("/api/v1/admin/about")
def admin_about(x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    import platform
    version = update_status(fetch_remote=False)
    return {
        "product": {"name": "OKXQuant", "version": "0.1.0", "control_plane": "OKXQuant Gateway Runtime", "gateway_version": GATEWAY_VERSION},
        "runtime": {"python": platform.python_version(), "platform": platform.platform(), "backend_pid": os.getpid(), "gateway": gateway_status(x_okxquant_admin_token)},
        "components": [
            {"name": "FastAPI Control Plane", "version": "0.1.0"},
            {"name": "Gateway Event Runtime", "version": GATEWAY_VERSION},
            {"name": "SQLite", "version": __import__("sqlite3").sqlite_version},
        ],
        "repository": {"url": "https://github.com/zi-fei-yu-2020/OKXQuant", "branch": version.get("branch", "unknown"), "commit": version.get("local", "unknown")},
        "update": version,
        "security": {"authentication": "PBKDF2-SHA256 + server-side sessions", "session_hours": 12, "plugin_policy": "builtin-only", "prompt_transport": "python-direct"},
    }


@app.get("/api/v1/admin/update-status")
def admin_update_status(x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    return update_status()


@app.post("/api/v1/admin/update")
def update_application(payload: UpdateRequest, x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    if payload.confirmation.strip().upper() != "UPDATE OKXQUANT":
        raise HTTPException(status_code=400, detail="确认短语必须精确为：UPDATE OKXQuant")
    if os.getenv("OKXQUANT_DEPLOYMENT_MODE", "").strip().lower() == "docker":
        raise HTTPException(
            status_code=409,
            detail="Docker deployment updates are managed by the host; run docker compose build --pull && docker compose up -d.",
        )
    status_before = update_status()
    if status_before.get("error"):
        raise HTTPException(status_code=502, detail=status_before["error"])
    if status_before["dirty"]:
        raise HTTPException(status_code=409, detail="工作区存在未提交修改；为防止覆盖本地改动，后台拒绝更新")
    if not status_before["remote"]:
        raise HTTPException(status_code=502, detail="无法读取远程仓库状态")
    try:
        output = git(["pull", "--ff-only", "origin", status_before["branch"]])
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"更新失败：{exc}") from exc
    status_after = update_status()
    audit_record("application.update", "success", {"before": status_before.get("local"), "after": status_after.get("local")})
    return {
        "updated": status_before["local"] != status_after.get("local"),
        "before": status_before,
        "after": status_after,
        "git_output": output,
        "restart_required": True,
        "restart_note": "请重启 okxquant-backend 与 okxquant-scheduler 服务，让新代码接管后台与调度。",
    }


@app.get("/api/v1/admin/prompt-library")
def prompt_library(x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    from scripts.ai_brain_trader import SYSTEM_PROMPT, get_user_prompt_override
    from scripts.self_improvement_engine import EVOLUTION_SYSTEM_PROMPT
    from scripts import trading_prompt
    from scripts.risk_policy import load_policy
    from dataclasses import asdict
    library = load_library()
    profile = active_profile()
    preview = trading_prompt.compose(profile, {k:'{{'+k+'}}' for k in ('decision_timestamp','account_balance','account_positions','pending_orders','news_intelligence','trading_memory','market_matrix')}, [], override=get_user_prompt_override(), risk_contract=asdict(load_policy()))
    return {
        "active_style": library["active_style"],
        "active_profile_id": library["active_profile_id"],
        "profiles": [{**item, "pipeline_views": {
            "trading_system": pipeline_view(SYSTEM_PROMPT, item, "trading_system"),
            "trading_user": pipeline_view(TRADING_USER_TEMPLATE, item, "trading_user"),
            "evolution_system": pipeline_view(EVOLUTION_SYSTEM_PROMPT, item, "evolution_system"),
            "evolution_user": pipeline_view(EVOLUTION_USER_TEMPLATE, item, "evolution_user"),
        }} for item in all_profiles()],
        "base_templates": {
            "trading_system": SYSTEM_PROMPT,
            "trading_user": TRADING_USER_TEMPLATE,
            "evolution_system": EVOLUTION_SYSTEM_PROMPT,
            "evolution_user": EVOLUTION_USER_TEMPLATE,
        },
        "pipelines": {
            "trading_system": pipeline_view(SYSTEM_PROMPT, profile, "trading_system"),
            "trading_user": pipeline_view(TRADING_USER_TEMPLATE, profile, "trading_user"),
            "evolution_system": pipeline_view(EVOLUTION_SYSTEM_PROMPT, profile, "evolution_system"),
            "evolution_user": pipeline_view(EVOLUTION_USER_TEMPLATE, profile, "evolution_user"),
        },
        "effective_templates": {
            "trading_system": preview.system,
            "trading_user": preview.user,
            "evolution_system": apply_module_layout(EVOLUTION_SYSTEM_PROMPT, profile, "evolution_system", "自进化 System"),
            "evolution_user": apply_module_layout(EVOLUTION_USER_TEMPLATE, profile, "evolution_user", "自进化 User"),
        },
        "snapshots": rendered_snapshots(),
        "composition": preview.manifest,
        "template_variables": __import__("scripts.prompt_library", fromlist=["TEMPLATE_VARIABLES_METADATA"]).TEMPLATE_VARIABLES_METADATA,
        "transport": "python-direct",
    }


@app.put("/api/v1/admin/prompt-library")
def update_prompt_library(payload: PromptLibraryUpdate, x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    library = load_library()
    library["active_style"] = payload.active_style
    library["custom"] = {
        "id": "custom", "name": "自定义", "description": "管理员自定义风格附加层。", "editable": True,
        "trading_system": payload.trading_system.strip(), "trading_user": payload.trading_user.strip(),
        "evolution_system": payload.evolution_system.strip(), "evolution_user": payload.evolution_user.strip(),
    }
    save_library(library)
    audit_record("prompt.library.update", "success", {"active_style": payload.active_style, "custom_characters": sum(len(getattr(payload, key)) for key in ("trading_system", "trading_user", "evolution_system", "evolution_user"))})
    return {"saved": True, "active_style": payload.active_style, "restart_note": "下一次 Python 交易主脑与自进化进程自动读取选中风格。"}


@app.get("/api/v1/admin/prompt-profiles")
def prompt_profiles(x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    library = load_library()
    prompts_mod = __import__("scripts.prompt_library", fromlist=["ALLOWED_VARIABLES", "TEMPLATE_VARIABLES_METADATA"])
    return {
        "active_profile_id": library["active_profile_id"],
        "profiles": all_profiles(),
        "allowed_variables": sorted(list(prompts_mod.ALLOWED_VARIABLES)),
        "template_variables": prompts_mod.TEMPLATE_VARIABLES_METADATA,
    }


@app.post("/api/v1/admin/prompt-profiles")
def create_prompt_profile_api(payload: PromptProfileCreateRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try: profile = create_profile(payload.name, payload.description, payload.source_id)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("prompt.profile.create", "success", {"actor": actor["username"], "profile_id": profile["id"]})
    return {"profile": profile}


@app.put("/api/v1/admin/prompt-profiles/{profile_id}")
def update_prompt_profile_api(profile_id: str, payload: PromptProfileUpdateRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try: profile = update_profile(profile_id, payload.model_dump(exclude={"note"}), payload.note)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("prompt.profile.update", "success", {"actor": actor["username"], "profile_id": profile_id})
    return {"profile": profile, "validation": validate_profile(profile)}


@app.post("/api/v1/admin/prompt-profiles/{profile_id}/activate")
def activate_prompt_profile_api(profile_id: str, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try: profile = activate_profile(profile_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_record("prompt.profile.activate", "success", {"actor": actor["username"], "profile_id": profile_id})
    from scripts.execution_profiles import runtime as execution_runtime
    return {"active_profile_id": profile_id, "profile": profile, "execution_settings": execution_runtime(),
            "effective_for": "next_fresh_decision", "existing_positions_resized": False}


@app.delete("/api/v1/admin/prompt-profiles/{profile_id}")
def delete_prompt_profile_api(profile_id: str, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try: delete_profile(profile_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_record("prompt.profile.delete", "success", {"actor": actor["username"], "profile_id": profile_id})
    return {"deleted": True}


@app.post("/api/v1/admin/prompt-profiles/validate")
def validate_prompt_profile_api(payload: PromptProfileUpdateRequest, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    return validate_profile(payload.model_dump())


@app.get("/api/v1/admin/prompt-profiles/{profile_id}/history")
def prompt_profile_history_api(profile_id: str, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    return {"history": profile_history(profile_id)}


@app.post("/api/v1/admin/prompt-profiles/{profile_id}/rollback")
def rollback_prompt_profile_api(profile_id: str, payload: PromptRollbackRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try: profile = rollback_profile(profile_id, payload.revision_id)
    except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit_record("prompt.profile.rollback", "success", {"actor": actor["username"], "profile_id": profile_id, "revision_id": payload.revision_id})
    return {"profile": profile}


@app.get("/api/v1/admin/prompt-profiles/{profile_id}/export")
def export_prompt_profile_api(profile_id: str, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    try: return export_profile(profile_id)
    except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/admin/prompt-profiles/import")
def import_prompt_profile_api(payload: PromptImportRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try: profile = import_profile(payload.payload, payload.name_override)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("prompt.profile.import", "success", {"actor": actor["username"], "profile_id": profile["id"]})
    return {"profile": profile}


@app.get("/api/v1/admin/prompts")
def prompt_override(x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    from scripts.ai_brain_trader import SYSTEM_PROMPT
    from scripts import trading_prompt
    from scripts.risk_policy import load_policy
    from dataclasses import asdict
    content = PROMPT_OVERRIDE_FILE.read_text(encoding="utf-8") if PROMPT_OVERRIDE_FILE.exists() else ""
    preview = trading_prompt.compose(active_profile(), {'preview':'动态数据运行时注入'}, [], override=content, risk_contract=asdict(load_policy()))
    return {
        "content": content,
        "enabled": bool(content.strip()),
        "base_prompt": SYSTEM_PROMPT,
        "effective_prompt": "【SYSTEM】\n" + preview.system + "\n\n【USER】\n" + preview.user,
        "effective_messages": [{'role':'system','content':preview.system},{'role':'user','content':preview.user}],
        "composition": preview.manifest,
        "path": str(PROMPT_OVERRIDE_FILE),
    }


@app.put("/api/v1/admin/prompts")
def update_prompt_override(payload: PromptOverrideRequest, x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    content = payload.content.strip()
    from scripts.trading_prompt import preference_layers
    _, issues, allowed = preference_layers({}, override=content)
    if not allowed:
        raise HTTPException(status_code=400, detail="交易偏好与基础契约冲突或长度超限：" + ", ".join(item['code'] for item in issues))
    if content:
        temp = PROMPT_OVERRIDE_FILE.with_suffix(".tmp")
        temp.write_text(content + "\n", encoding="utf-8")
        os.replace(temp, PROMPT_OVERRIDE_FILE)
    elif PROMPT_OVERRIDE_FILE.exists():
        PROMPT_OVERRIDE_FILE.unlink()
    audit_record("prompt.update", "success", {"enabled": bool(content), "characters": len(content)})
    return {"saved": True, "enabled": bool(content), "restart_note": "下一次 AI 推演循环将自动叠加此提示词覆盖层。"}


@app.get("/api/v1/admin/notifications")
def notification_config(x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session"), x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    env = notification_env()
    from okxquant_backend.settings_store import mask_url
    return {
        "webhook": {"enabled": env.get("OKXQUANT_NOTIFY_WEBHOOK_ENABLED", "0") == "1", "url": mask_url(env.get("OKXQUANT_NOTIFICATION_WEBHOOK", ""))},
        "wechat": {"enabled": env.get("OKXQUANT_NOTIFY_WECHAT_ENABLED", "0") == "1", "webhook": mask_url(env.get("OKXQUANT_WECHAT_WEBHOOK", ""))},
        "telegram": {"enabled": env.get("OKXQUANT_NOTIFY_TELEGRAM_ENABLED", "0") == "1", "bot_token": mask(env.get("OKXQUANT_TELEGRAM_BOT_TOKEN", "")), "chat_id": env.get("OKXQUANT_TELEGRAM_CHAT_ID", ""), "api_base": env.get("OKXQUANT_TELEGRAM_API_BASE", "")},
        "qq": {"enabled": env.get("OKXQUANT_NOTIFY_QQ_ENABLED", "0") == "1", "app_id": env.get("OKXQUANT_QQ_APP_ID", ""), "client_secret": mask(env.get("OKXQUANT_QQ_CLIENT_SECRET", "")), "openid": env.get("OKXQUANT_QQ_OPENID", "")},
    }


@app.put("/api/v1/admin/channels/{channel}/toggle")
def toggle_channel(channel: str, payload: ChannelToggleRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session"), x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    keys = {
        "qq": "OKXQUANT_NOTIFY_QQ_ENABLED",
        "telegram": "OKXQUANT_NOTIFY_TELEGRAM_ENABLED",
        "wechat": "OKXQUANT_NOTIFY_WECHAT_ENABLED",
        "webhook": "OKXQUANT_NOTIFY_WEBHOOK_ENABLED",
    }
    channel_names = {
        "qq": "QQ 官方 Bot",
        "telegram": "Telegram Bot",
        "wechat": "企业微信",
        "webhook": "通用 Webhook",
    }
    if channel not in keys:
        raise HTTPException(status_code=404, detail="未知频道")

    # If the user provided inputs while toggling, save them immediately
    if channel == "wechat" and payload.wechat_webhook is not None:
        val = payload.wechat_webhook.strip()
        if val:
            save_secrets({"OKXQUANT_WECHAT_WEBHOOK": val})
            remove_env({"OKXQUANT_WECHAT_WEBHOOK"})
    elif channel == "webhook" and payload.webhook_url is not None:
        val = payload.webhook_url.strip()
        if val:
            save_secrets({"OKXQUANT_NOTIFICATION_WEBHOOK": val})
            remove_env({"OKXQUANT_NOTIFICATION_WEBHOOK"})
    elif channel == "telegram":
        if payload.telegram_bot_token is not None and payload.telegram_bot_token.strip():
            save_secrets({"OKXQUANT_TELEGRAM_BOT_TOKEN": payload.telegram_bot_token.strip()})
            remove_env({"OKXQUANT_TELEGRAM_BOT_TOKEN"})
        tg_env = {}
        if payload.telegram_chat_id is not None:
            tg_env["OKXQUANT_TELEGRAM_CHAT_ID"] = payload.telegram_chat_id.strip()
        if payload.telegram_api_base is not None:
            tg_env["OKXQUANT_TELEGRAM_API_BASE"] = payload.telegram_api_base.strip()
        if tg_env:
            update_env(tg_env)
    elif channel == "qq":
        if payload.qq_client_secret is not None and payload.qq_client_secret.strip():
            save_secrets({"OKXQUANT_QQ_CLIENT_SECRET": payload.qq_client_secret.strip()})
            remove_env({"OKXQUANT_QQ_CLIENT_SECRET"})
        qq_env = {}
        if payload.qq_app_id is not None:
            qq_env["OKXQUANT_QQ_APP_ID"] = payload.qq_app_id.strip()
        if payload.qq_openid is not None:
            qq_env["OKXQUANT_QQ_OPENID"] = payload.qq_openid.strip()
        if qq_env:
            update_env(qq_env)

    name = channel_names.get(channel, channel)
    if payload.enabled:
        env = notification_env()
        readiness = {
            "qq": bool(env.get("OKXQUANT_QQ_APP_ID") and env.get("OKXQUANT_QQ_CLIENT_SECRET") and env.get("OKXQUANT_QQ_OPENID")),
            "telegram": bool(env.get("OKXQUANT_TELEGRAM_BOT_TOKEN") and env.get("OKXQUANT_TELEGRAM_CHAT_ID")),
            "wechat": bool(env.get("OKXQUANT_WECHAT_WEBHOOK")),
            "webhook": bool(env.get("OKXQUANT_NOTIFICATION_WEBHOOK")),
        }
        if not readiness[channel]:
            if channel == "qq":
                if not env.get("OKXQUANT_QQ_OPENID"):
                    raise HTTPException(status_code=400, detail="QQ 缺少目标用户 OpenID，请先点击「⚡ 自动获取 OpenID」向 Bot 发送消息完成绑定")
                raise HTTPException(status_code=400, detail="QQ App ID 或 Client Secret 尚未配置完整")
            elif channel == "wechat":
                raise HTTPException(status_code=400, detail="企业微信尚未配置 Webhook URL，请先填入有效 Webhook 地址再开启")
            elif channel == "webhook":
                raise HTTPException(status_code=400, detail="通用 Webhook 尚未配置 URL，请先填入有效 Webhook 地址再开启")
            elif channel == "telegram":
                raise HTTPException(status_code=400, detail="Telegram 缺少 Bot Token 或 Chat ID，请填写完整后再开启")
            raise HTTPException(status_code=400, detail=f"{name} 凭证或目标未配置完整，请先填写有效配置再开启")

    update_env({keys[channel]: "1" if payload.enabled else "0"})
    audit_record("channel.toggle", "success", {"channel": channel, "enabled": payload.enabled})
    return {"channel": channel, "enabled": payload.enabled, "message": f"{name} 通道已成功{'开启' if payload.enabled else '关闭'}"}


@app.put("/api/v1/admin/notifications")
def update_notification_config(payload: NotificationConfigUpdate, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    refresh_settings()
    require_superadmin(x_okxquant_session)

    # Save credentials unconditionally to preserve user configurations
    secret_values = {}
    if payload.webhook_url:
        secret_values["OKXQUANT_NOTIFICATION_WEBHOOK"] = payload.webhook_url.strip()
    if payload.wechat_webhook:
        secret_values["OKXQUANT_WECHAT_WEBHOOK"] = payload.wechat_webhook.strip()
    if payload.telegram_bot_token:
        secret_values["OKXQUANT_TELEGRAM_BOT_TOKEN"] = payload.telegram_bot_token.strip()
    if payload.qq_client_secret:
        secret_values["OKXQUANT_QQ_CLIENT_SECRET"] = payload.qq_client_secret.strip()

    if secret_values:
        save_secrets(secret_values)
        remove_env(set(secret_values))

    env_update = {
        "OKXQUANT_TELEGRAM_CHAT_ID": payload.telegram_chat_id.strip(),
        "OKXQUANT_QQ_APP_ID": payload.qq_app_id.strip(),
        "OKXQUANT_QQ_OPENID": payload.qq_openid.strip(),
    }
    if payload.telegram_api_base is not None:
        env_update["OKXQUANT_TELEGRAM_API_BASE"] = payload.telegram_api_base.strip()

    # Determine readiness
    current_env = notification_env()
    readiness = {
        "qq": bool(payload.qq_app_id and (payload.qq_client_secret or current_env.get("OKXQUANT_QQ_CLIENT_SECRET")) and payload.qq_openid),
        "telegram": bool((payload.telegram_bot_token or current_env.get("OKXQUANT_TELEGRAM_BOT_TOKEN")) and payload.telegram_chat_id),
        "wechat": bool(payload.wechat_webhook or current_env.get("OKXQUANT_WECHAT_WEBHOOK")),
        "webhook": bool(payload.webhook_url or current_env.get("OKXQUANT_NOTIFICATION_WEBHOOK")),
    }

    warnings = []
    # If user checked enabled for an incomplete channel, auto-turn off that specific channel with a helpful warning instead of crashing entire save with 409
    eff_qq = payload.qq_enabled
    if payload.qq_enabled and not readiness["qq"]:
        eff_qq = False
        warnings.append("QQ 频道因缺少 OpenID 暂未开启（请点击「⚡ 自动获取 OpenID」绑定）")

    eff_tg = payload.telegram_enabled
    if payload.telegram_enabled and not readiness["telegram"]:
        eff_tg = False
        warnings.append("Telegram 频道因缺少 Token 或 Chat ID 暂未开启")

    eff_wx = payload.wechat_enabled
    if payload.wechat_enabled and not readiness["wechat"]:
        eff_wx = False
        warnings.append("企业微信频道因缺少 Webhook 暂未开启")

    eff_wh = payload.webhook_enabled
    if payload.webhook_enabled and not readiness["webhook"]:
        eff_wh = False
        warnings.append("通用 Webhook 因缺少 URL 暂未开启")

    env_update.update({
        "OKXQUANT_NOTIFY_WEBHOOK_ENABLED": "1" if eff_wh else "0",
        "OKXQUANT_NOTIFY_WECHAT_ENABLED": "1" if eff_wx else "0",
        "OKXQUANT_NOTIFY_TELEGRAM_ENABLED": "1" if eff_tg else "0",
        "OKXQUANT_NOTIFY_QQ_ENABLED": "1" if eff_qq else "0",
    })

    update_env(env_update)
    refresh_settings()

    audit_record("notifications.update", "success", {
        "webhook": eff_wh,
        "wechat": eff_wx,
        "telegram": eff_tg,
        "qq": eff_qq,
        "warnings": warnings,
    })
    msg = "全部通知配置已成功保存"
    if warnings:
        msg += f"（提示：{'；'.join(warnings)}）"
    return {"saved": True, "message": msg, "warnings": warnings}


@app.post("/api/v1/admin/notifications/diagnose")
def diagnose_notification(payload: NotificationTestRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session"), x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings(); require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    result = diagnose_channel(payload.channel)
    audit_record("notifications.diagnose", "completed", {"channel": payload.channel, "status": result.get("status")})
    return {"channel": payload.channel, "result": result, "sent": False}


@app.post("/api/v1/admin/notifications/test")
def send_notification_test(payload: NotificationTestRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    refresh_settings(); require_superadmin(x_okxquant_session)
    if payload.confirmation.strip().upper() != f"SEND TEST {payload.channel.upper()}":
        raise HTTPException(status_code=400, detail=f"确认短语必须为：SEND TEST {payload.channel.upper()}")
    result = test_channel(payload.channel)
    audit_record("notifications.test", "completed", {"channel": payload.channel, "result": result})
    return {"channel": payload.channel, "result": result, "sent": True, "meaning": "远端接口已受理不等于用户客户端已读"}


@app.post("/api/v1/admin/notifications/qq/capture-openid/start")
def qq_capture_openid_start(payload: QQOpenIDCaptureStartRequest | None = None, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    refresh_settings(); require_superadmin(x_okxquant_session)
    from okxquant_backend.qq_bind import start_openid_capture
    app_id = payload.app_id if payload else None
    secret = payload.client_secret if payload else None
    timeout = payload.timeout if payload else 60
    try:
        res = start_openid_capture(app_id=app_id, client_secret=secret, timeout=timeout)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"启动 QQ OpenID 监听网关失败：{exc}") from exc
    audit_record("qq.capture_openid.start", "success", {"capture_id": res.get("capture_id"), "app_id": res.get("app_id")})
    return res


@app.get("/api/v1/admin/notifications/qq/capture-openid/{capture_id}")
def qq_capture_openid_poll(capture_id: str, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    refresh_settings(); require_superadmin(x_okxquant_session)
    from okxquant_backend.qq_bind import poll_openid_capture
    res = poll_openid_capture(capture_id)
    if res.get("status") == "captured":
        audit_record("qq.capture_openid.complete", "success", {"capture_id": capture_id, "openid": res.get("openid")})
    return res


@app.post("/api/v1/admin/notifications/qq/bind/start")
def qq_bind_start(x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    refresh_settings(); require_superadmin(x_okxquant_session)
    from okxquant_backend.qq_bind import create_bind_task
    try:
        task = create_bind_task()
    except Exception as exc:
        audit_record("qq.bind.start", "failed", {"error": str(exc)[:200]})
        raise HTTPException(status_code=502, detail=f"QQ 绑定任务创建失败：{exc}")
    qr_data_uri = ""
    try:
        import segno
        qr_data_uri = segno.make(task["connect_url"], error="M").png_data_uri(scale=6, border=2)
    except Exception:
        pass
    audit_record("qq.bind.start", "success", {"task_id": task["task_id"]})
    return {"task_id": task["task_id"], "qr_data_uri": qr_data_uri, "connect_url": task["connect_url"], "expires_in": task["expires_in"]}


@app.get("/api/v1/admin/notifications/qq/bind/{task_id}")
def qq_bind_poll(task_id: str, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    refresh_settings(); require_superadmin(x_okxquant_session)
    from okxquant_backend.qq_bind import poll_bind_task
    try:
        result = poll_bind_task(task_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    if result["status"] == "bound" or result["status"] == "awaiting_message":
        audit_record("qq.bind.complete", "success", {"app_id": result["app_id"], "status": result["status"], "openid_present": bool(result.get("openid"))})
    return result



@app.get("/api/v1/admin/notifications/schedule")
def notification_schedule(x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    schedule = load_schedule()
    return {
        **schedule,
        "event_notifications": "开仓、平仓与风险事件实时推送，不受每日简报时间限制",
        "restart_note": "保存后调度器将在 60 秒内读取新时间，无需重启。",
    }


@app.put("/api/v1/admin/notifications/schedule")
def update_notification_schedule(payload: NotificationScheduleUpdate, x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    normalized: list[str] = []
    for value in payload.briefing_times:
        value = value.strip()
        try:
            parsed = time.strptime(value, "%H:%M")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"无效时间：{value}；必须使用 HH:MM 24 小时格式") from exc
        canonical = f"{parsed.tm_hour:02d}:{parsed.tm_min:02d}"
        if canonical not in normalized:
            normalized.append(canonical)
    normalized.sort()
    schedule = load_schedule()
    schedule["briefing_times"] = normalized
    save_schedule(schedule)
    audit_record("notifications.schedule", "success", {"briefing_times": normalized, "timezone": "Asia/Shanghai"})
    return {**schedule, "saved": True, "restart_note": "调度器将在 60 秒内读取新时间。"}


@app.get("/api/v1/admin/backups/simple")
def simple_backup_config(x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    jobs = list_backup_jobs()
    job = next((x for x in jobs if x.get("id") == "nightly-default"), jobs[0] if jobs else None)
    if not job:
        raise HTTPException(status_code=404, detail="主灾备任务不存在")
    target = next((x for x in job.get("targets", []) if x.get("enabled")), None)
    if not target:
        target = next((x for x in job.get("targets", []) if x.get("type") == "local"), None)
    target = dict(target or {})
    target_type = str(target.get("type") or "local")
    auth_mode = str(target.get("auth_mode") or "")
    legacy_bypy = target_type == "baidu" and auth_mode != "oauth"
    destination = "baidu_oauth" if target_type == "baidu" and not legacy_bypy else target_type if target_type in {"local", "s3", "oss", "webdav"} else "local"
    missing_fields = []
    configured = target_type == "local"
    if target_type != "local":
        ref = str(target.get("credential_ref") or f"backup:{target.get('id', '')}")
        credential_state = backup_credential_status(ref)
        fields = set(credential_state.get("fields") or [])
        required = {
            "s3": {"access_key_id", "secret_access_key"},
            "oss": {"access_key_id", "secret_access_key"},
            "webdav": {"username", "password"},
            "aliyundrive": {"username", "password"},
            "quark": {"username", "password"},
            "baidu": {"app_key", "app_secret", "refresh_token"},
        }.get(target_type, set())
        missing_fields = sorted(required - fields)
        target_fields = {"s3": ("endpoint", "bucket"), "oss": ("endpoint", "bucket"),
                         "webdav": ("endpoint",), "aliyundrive": ("endpoint",), "quark": ("endpoint",)}.get(target_type, ())
        missing_fields.extend(name for name in target_fields if not str(target.get(name) or "").strip())
        configured = bool(required) and not missing_fields and not legacy_bypy
        if target_type in {"aliyundrive", "quark"} and auth_mode != "webdav":
            configured = False
        # Return presence metadata only; never return credential values.
        target["credential_status"] = {"configured": configured, "fields": sorted(fields), "count": len(fields)}
    else:
        target.pop("credential_status", None)
    validation = validate_backup_job(job)
    latest = None
    manifests_dir = ROOT / "backups" / "manifests"
    for path in sorted(manifests_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30] if manifests_dir.exists() else []:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(item, dict) and item.get("job_id") == job["id"]:
                latest = item
                if not isinstance(item.get("status"), str) or item["status"] not in {"success", "failed", "partial", "running", "skipped"}:
                    latest["status"] = "unknown"
                break
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "job_id": job["id"], "target": target, "enabled": job["enabled"],
        "schedule_time": job["schedule_times"][0], "destination": destination,
        "retention": int(target.get("retention") or 3), "legacy_bypy": legacy_bypy,
        "migration_note": "当前为旧版 ByPy 配置，请选择新的保存位置后保存完成迁移" if legacy_bypy else "",
        "configured": configured, "missing_fields": missing_fields,
        "directory_write_verified": False, "connection_verified": False,
        "configuration_note": "本地目标无需凭据；已配置不代表目录可写已验证" if target_type == "local" else "字段齐全不代表远端连接或写入已验证",
        "validation": validation, "latest": latest, "advanced_preserved": True,
    }

@app.put("/api/v1/admin/backups/simple")
def update_simple_backup(payload: SimpleBackupUpdateRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor=require_superadmin(x_okxquant_session); jobs=list_backup_jobs(); job=next((x for x in jobs if x.get("id")=="nightly-default"), jobs[0] if jobs else None)
    if not job: raise HTTPException(status_code=404,detail="主灾备任务不存在")
    wanted_type="baidu" if payload.destination=="baidu_oauth" else payload.destination
    existing=next((x for x in job.get("targets",[]) if x.get("type")==wanted_type and (wanted_type!="baidu" or x.get("auth_mode")=="oauth")),None)
    if not existing:
        target_id=f"{wanted_type}-{__import__('uuid').uuid4().hex[:10]}"; existing={"id":target_id,"type":wanted_type,"label":{"local":"本地归档","s3":"S3存储","oss":"阿里云OSS","webdav":"WebDAV/OpenList","baidu":"百度网盘"}[wanted_type],"credential_ref":f"backup:{target_id}","enabled":False,"remote_path":"OKXQUANT_Backups","path":"backups/local","retention":3,"retries":3,"auth_mode":"oauth" if wanted_type=="baidu" else "native"}; job.setdefault("targets",[]).append(existing)
    for target in job.get("targets",[]): target["enabled"] = target is existing
    existing["retention"] = payload.retention if wanted_type=="local" else 0
    if wanted_type in {"s3","oss","webdav"}: existing["endpoint"] = payload.endpoint.strip()
    if wanted_type in {"s3","oss"}: existing["bucket"] = payload.bucket.strip()
    if wanted_type=="baidu": existing["auth_mode"]="oauth"
    if payload.credentials: save_backup_credentials(existing["credential_ref"], payload.credentials)
    job["enabled"]=payload.enabled; job["schedule_times"]=[payload.schedule_time]
    try: saved=update_backup_job(job["id"],job)
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    audit_record("backup.simple.update","success",{"actor":actor["username"],"destination":payload.destination,"enabled":payload.enabled})
    return {"saved":True,"job":saved}


@app.post("/api/v1/admin/backups/simple/test")
def test_simple_backup(payload: SimpleBackupUpdateRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_superadmin(x_okxquant_session)
    if payload.destination == "local":
        directory=(ROOT/"backups"/"local").resolve(); directory.mkdir(parents=True,exist_ok=True)
        if not directory.is_relative_to((ROOT/"backups").resolve()): raise HTTPException(status_code=400,detail="本地灾备目录无效")
        return {"status":"ready","sent":False,"detail":"本地目录可写；未生成或上传归档"}
    wanted_type="baidu" if payload.destination=="baidu_oauth" else payload.destination
    target={"type":wanted_type,"endpoint":payload.endpoint.strip(),"bucket":payload.bucket.strip(),"auth_mode":"oauth" if wanted_type=="baidu" else "native"}
    required={"s3":{"access_key_id","secret_access_key"},"oss":{"access_key_id","secret_access_key"},"webdav":set(),"baidu":{"app_key","app_secret","refresh_token"}}[wanted_type]
    missing=sorted(key for key in required if not payload.credentials.get(key))
    if missing: raise HTTPException(status_code=400,detail=f"连接信息不完整：{', '.join(missing)}")
    if wanted_type in {"s3","oss","webdav"}:
        try: target["endpoint"]=__import__("okxquant_backend.net_security",fromlist=["validate_outbound_url"]).validate_outbound_url(target["endpoint"])
        except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    if wanted_type in {"s3","oss"} and not target["bucket"]: raise HTTPException(status_code=400,detail="Bucket 不能为空")
    # Intentionally no upload and no OAuth token exchange: this endpoint validates
    # configuration and safe reachability only, never creates remote objects.
    return {"status":"ready","sent":False,"detail":"配置格式与目标地址校验通过；未上传任何文件","destination":payload.destination}


@app.get("/api/v1/admin/backup-target-types")
def backup_target_types(x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    return {"target_types": [
        {"type":"local","label":"本地归档","auth":"none","description":"项目 backups/ 内滚动保留"},
        {"type":"baidu","label":"百度网盘","auth":"oauth","description":"仅支持官方 OAuth，新配置不再提供 ByPy"},
        {"type":"s3","label":"S3 兼容存储","auth":"access-key","description":"AWS S3、R2、MinIO、COS 等 S3 兼容端点"},
        {"type":"oss","label":"阿里云 OSS","auth":"access-key","description":"官方 oss2 SDK"},
        {"type":"webdav","label":"WebDAV / NAS / OpenList","auth":"basic","description":"标准 WebDAV PUT/MKCOL"},
        {"type":"aliyundrive","label":"阿里云盘","auth":"webdav-or-oauth","description":"推荐开放平台或 OpenList WebDAV 桥接"},
        {"type":"quark","label":"夸克网盘（实验性）","auth":"webdav-or-experimental-oauth","description":"官方开放平台仍在内测，推荐 OpenList WebDAV 桥接"},
    ]}


@app.put("/api/v1/admin/backup-credentials")
def update_backup_credentials(payload: BackupCredentialUpdateRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try: status = save_backup_credentials(payload.credential_ref, payload.credentials)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("backup.credentials.update", "success", {"actor": actor["username"], "credential_ref": payload.credential_ref, "fields": status["fields"]})
    return {"saved": True, "credential_ref": payload.credential_ref, "status": status}


@app.get("/api/v1/admin/backup-jobs")
def backup_jobs_api(x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    manifests_dir = ROOT / "backups" / "manifests"
    manifests = []
    for path in sorted(manifests_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50] if manifests_dir.exists() else []:
        try:
            item = json.loads(path.read_text(encoding="utf-8")); item["manifest_file"] = path.name; manifests.append(item)
        except (OSError, json.JSONDecodeError): pass
    jobs = list_backup_jobs()
    for job in jobs:
        for target in job.get("targets", []): target["credential_status"] = backup_credential_status(str(target.get("credential_ref") or ""))
    return {"jobs": jobs, "validations": {job["id"]: validate_backup_job(job) for job in jobs}, "recent_manifests": manifests, "timezone": "Asia/Shanghai", "limits": {"maximum_jobs": 12}}


@app.post("/api/v1/admin/backup-jobs")
def create_backup_job_api(payload: BackupJobCreateRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try: job = create_backup_job(payload.name, payload.source_id)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("backup.job.create", "success", {"actor": actor["username"], "job_id": job["id"]})
    return {"job": job}


@app.put("/api/v1/admin/backup-jobs/{job_id}")
def update_backup_job_api(job_id: str, payload: BackupJobUpdateRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try: job = update_backup_job(job_id, payload.job)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("backup.job.update", "success", {"actor": actor["username"], "job_id": job_id, "enabled": job["enabled"]})
    return {"job": job, "validation": validate_backup_job(job)}


@app.delete("/api/v1/admin/backup-jobs/{job_id}")
def delete_backup_job_api(job_id: str, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try: delete_backup_job(job_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_record("backup.job.delete", "success", {"actor": actor["username"], "job_id": job_id})
    return {"deleted": True}


@app.post("/api/v1/admin/backup-jobs/validate")
def validate_backup_job_api(payload: BackupJobUpdateRequest, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    return validate_backup_job(payload.job)


@app.post("/api/v1/admin/backup-jobs/{job_id}/run")
def run_backup_job_api(job_id: str, payload: BackupJobRunRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    if payload.confirmation.strip().upper() != f"BACKUP {job_id}".upper():
        raise HTTPException(status_code=400, detail=f"确认短语必须精确为：BACKUP {job_id}")
    try: get_backup_job(job_id)
    except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    script = SCRIPTS_DIR / "nightly_backup_and_clean.py"
    result = subprocess.run([sys.executable, str(script), "--job-id", job_id], cwd=ROOT, text=True, capture_output=True, timeout=1800)
    BACKUP_LOG_FILE.parent.mkdir(exist_ok=True); BACKUP_LOG_FILE.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    audit_record("backup.job.run", "success" if result.returncode == 0 else "failed", {"actor": actor["username"], "job_id": job_id, "returncode": result.returncode})
    if result.returncode: raise HTTPException(status_code=502, detail=f"灾备任务失败：{result.stderr[-800:] or result.stdout[-800:]}")
    return {"completed": True, "output": result.stdout[-4000:]}


@app.get("/api/v1/admin/backup-jobs/{job_id}/export")
def export_backup_job_api(job_id: str, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    try: return export_backup_job(job_id)
    except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/admin/backup-jobs/import")
def import_backup_job_api(payload: BackupJobImportRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    try: job = import_backup_job(payload.payload, payload.name_override)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("backup.job.import", "success", {"actor": actor["username"], "job_id": job["id"]})
    return {"job": job}


@app.post("/api/v1/admin/backup-jobs/verify")
def verify_backup_archive_api(payload: BackupVerifyRequest, x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_okxquant_session)
    candidate = (ROOT / payload.archive_path).resolve()
    if not candidate.is_relative_to((ROOT / "backups").resolve()): raise HTTPException(status_code=400, detail="只能验证项目 backups/ 目录内的归档")
    from scripts.backup_runtime import verify_archive
    try: result = verify_archive(candidate, payload.expected_sha256, payload.key_env)
    except Exception as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_record("backup.archive.verify", "success", {"actor": actor["username"], "archive": str(candidate.relative_to(ROOT)), "members": result["members"]})
    return result


@app.put("/api/v1/admin/backups/methods")
def update_backup_methods(payload: BackupMethodsUpdate, x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    methods = {
        "baidu": {"enabled": payload.baidu_enabled, "retention": 0},
        "local": {"enabled": payload.local_enabled, "retention": payload.local_retention},
        "sqlite": {"enabled": payload.sqlite_enabled, "retention": payload.sqlite_retention},
    }
    if not any(item["enabled"] for item in methods.values()):
        raise HTTPException(status_code=400, detail="至少启用一种灾备方式")
    save_backup_methods(methods)
    audit_record("backup.methods.update", "success", {key: value["enabled"] for key, value in methods.items()})
    return {"saved": True, "methods": load_backup_methods()}


@app.get("/api/v1/admin/backups")
def backup_status(x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    backups_dir = ROOT / "backups"
    archive_paths = list(backups_dir.glob("*.tar.gz")) + list((backups_dir / "local").glob("*.tar.gz")) if backups_dir.exists() else []
    local_archives = [{"name": str(item.relative_to(backups_dir)), "bytes": item.stat().st_size, "mtime": int(item.stat().st_mtime)} for item in archive_paths]
    sqlite_snapshots = [{"name": item.name, "bytes": item.stat().st_size, "mtime": int(item.stat().st_mtime)} for item in (backups_dir / "sqlite").glob("*.db")] if (backups_dir / "sqlite").exists() else []
    return {
        "schedule": "每天北京时间 02:00，由 Gateway Scheduler 执行全部已启用灾备方式",
        "script": str(SCRIPTS_DIR / "nightly_backup_and_clean.py"),
        "methods": load_backup_methods(),
        "jobs": list_backup_jobs(),
        "local_archives": sorted(local_archives, key=lambda item: item["mtime"], reverse=True),
        "sqlite_snapshots": sorted(sqlite_snapshots, key=lambda item: item["mtime"], reverse=True),
        "last_log": BACKUP_LOG_FILE.read_text(encoding="utf-8")[-4000:] if BACKUP_LOG_FILE.exists() else "尚无后台手动灾备日志",
    }


@app.post("/api/v1/admin/backups/run")
def run_backup(payload: BackupRequest, x_okxquant_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token)
    if payload.confirmation.strip().upper() != "BACKUP OKXQUANT":
        raise HTTPException(status_code=400, detail="确认短语必须精确为：BACKUP OKXQuant")
    script = SCRIPTS_DIR / "nightly_backup_and_clean.py"
    result = subprocess.run([sys.executable, str(script)], cwd=ROOT, text=True, capture_output=True, timeout=600)
    BACKUP_LOG_FILE.parent.mkdir(exist_ok=True)
    BACKUP_LOG_FILE.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.returncode:
        audit_record("backup.run", "failed", {"returncode": result.returncode})
        raise HTTPException(status_code=502, detail=f"灾备任务失败：{result.stderr[-800:] or result.stdout[-800:]}")
    audit_record("backup.run", "success", {})
    return {"completed": True, "output": result.stdout[-2500:]}


@app.get("/api/v1/admin/backups/download/{filename:path}")
def download_backup_archive(filename: str, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> FileResponse:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    clean_name = Path(filename).name
    backups_dir = ROOT / "backups"
    candidate = backups_dir / clean_name
    if not candidate.exists():
        candidate = backups_dir / "local" / clean_name
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="备份文件不存在或已清理")
    audit_record("backup.download", "success", {"filename": clean_name})
    return FileResponse(
        path=str(candidate),
        media_type="application/gzip",
        filename=clean_name,
        headers={"Content-Disposition": f'attachment; filename="{clean_name}"'}
    )


@app.post("/api/v1/admin/backups/upload")
async def upload_backup_archive(file: UploadFile = File(...), x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    refresh_settings()
    require_superadmin(x_okxquant_session)
    if not file.filename or not (file.filename.endswith(".tar.gz") or file.filename.endswith(".tgz")):
        raise HTTPException(status_code=400, detail="仅支持上传 .tar.gz 或 .tgz 格式备份包")
    clean_name = Path(file.filename).name
    target_dir = ROOT / "backups" / "local"
    target_dir.mkdir(parents=True, exist_ok=True)
    dest_path = target_dir / clean_name
    content = await file.read()
    dest_path.write_bytes(content)
    audit_record("backup.upload", "success", {"filename": clean_name, "bytes": len(content)})
    return {"uploaded": True, "filename": clean_name, "bytes": len(content), "path": str(dest_path.relative_to(ROOT))}


@app.post("/api/v1/admin/backups/restore")
def restore_backup_archive(payload: BackupRestoreRequest, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    refresh_settings()
    require_superadmin(x_okxquant_session)
    if payload.confirmation.strip().upper() != "RESTORE OKXQUANT":
        raise HTTPException(status_code=400, detail="确认短语必须精确为：RESTORE OKXQuant")
    clean_name = payload.archive_name
    if (not clean_name or Path(clean_name).name != clean_name or "\\" in clean_name
            or ":" in clean_name or not clean_name.endswith((".tar.gz", ".tgz"))):
        raise HTTPException(status_code=400, detail="无效归档文件名；仅支持本地 .tar.gz / .tgz")
    candidate = ROOT / "backups" / clean_name
    if not candidate.exists() and not candidate.is_symlink():
        candidate = ROOT / "backups" / "local" / clean_name
    if not candidate.exists() and not candidate.is_symlink():
        raise HTTPException(status_code=404, detail="指定的备份归档文件不存在")

    from contextlib import ExitStack
    from scripts.backup_restore import UnsafeArchive, restore_archive
    # Never stop processes or touch cloud protection orders here. Hold all runtime
    # locks throughout validation AND publication so a scheduler cannot start a cycle.
    conflict = "恢复被拒绝：请先由管理员停止本实例 gateway、factor trader、brain trader 及其自动拉起/调度器，再重试。系统不会自动停止交易或修改云端保护订单。"
    if sys.platform != "linux":
        raise HTTPException(status_code=409, detail=conflict + " 安全停机检查仅支持 Linux/WSL。")
    supervisor = sys.modules.get("okxquant_gateway.supervisor")
    supervisor_thread = getattr(supervisor, "_thread", None)
    if supervisor_thread is not None and supervisor_thread.is_alive():
        raise HTTPException(status_code=409, detail=conflict + " gateway 自动拉起线程仍在运行。")
    import fcntl
    import stat
    with ExitStack() as stack:
        try:
            # Use descriptor-relative no-follow opens; never follow a data/lock link.
            root_fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, root_fd)
            data_fd = os.open("data", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            stack.callback(os.close, data_fd)
            for lock_name in (".okxquant_gateway.lock", ".ai_factor_trader.lock", ".ai_brain_cycle.lock", ".okxquant_scheduler.lock"):
                fd = os.open(lock_name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=data_fd)
                stack.callback(os.close, fd)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise OSError("unsafe runtime lock")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Trader locks are per-cycle, so also reject sleeping same-instance
            # processes. No signals, subprocesses, gateway imports or API calls.
            process_names = {"ai_factor_trader.py", "ai_brain_trader.py", "okxquant_gateway.worker",
                             "okxquant_gateway/worker.py", "okxquant_scheduler.py", "okxquant_backend.scheduler",
                             "okxquant_backend/scheduler.py", "scripts.ai_factor_trader", "scripts.ai_brain_trader"}
            for process in Path("/proc").iterdir():
                if not process.name.isdigit() or int(process.name) == os.getpid():
                    continue
                try:
                    args = process.joinpath("cmdline").read_bytes().split(b"\0")
                    words = [arg.decode("utf-8", errors="replace") for arg in args if arg]
                    if not any(word in process_names or Path(word).name in process_names
                               or word.endswith(("/okxquant_gateway/worker.py", "/okxquant_backend/scheduler.py")) for word in words):
                        continue
                    cwd = process.joinpath("cwd").resolve(strict=True)
                    absolute_script = any(Path(word).is_absolute() and Path(word).is_relative_to(ROOT) for word in words)
                    if cwd == ROOT or ROOT in cwd.parents or absolute_script:
                        raise HTTPException(status_code=409, detail=conflict)
                except (FileNotFoundError, ProcessLookupError):
                    continue
        except OSError as exc:
            # Permission/inspection failures are not evidence that trading stopped.
            raise HTTPException(status_code=409, detail=conflict + " 无法取得独占锁或确认进程停止。") from exc
        try:
            result = restore_archive(candidate, ROOT)
        except UnsafeArchive as exc:
            raise HTTPException(status_code=400, detail=f"恢复安全检查失败：{exc}") from exc
    audit_record("backup.restore", "success", {"filename": clean_name, "files_count": result["restored_count"], "skipped_count": result["skipped_count"]})
    return {"restored": True, "filename": clean_name, **result}


# -------------------------------------------------------------
# AI Trading Heuristic Memory APIs (Self-Improvement Memory CRUD)
# -------------------------------------------------------------
MEMORY_FILE = DATA_DIR / "AI_TRADING_MEMORY.md"  # legacy snapshot input only

def _parse_memory_items() -> list[str]:
    from scripts.memory_registry import view
    return [r['text'] for r in view(DATA_DIR)['rules'] if r.get('enabled',True)]


def _save_memory_items(items: list[str]) -> None:
    raise ValueError('请通过记忆候选审核发布，禁止直接覆盖运行 Markdown')


@app.get("/api/v1/admin/memory")
def get_admin_memory(x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    from scripts import memory_registry
    from scripts.evolution_status import public_status as evolution_status
    try:
        state=memory_registry.view(DATA_DIR,admin=True)
    except memory_registry.MemoryError as exc:
        raise HTTPException(503,str(exc)) from None
    items=[r['text'] for r in state['rules'] if r.get('enabled',True)]
    from okxquant_backend.schedule_store import load_schedule
    return {'items':items,'count':len(items),'raw':state['content'],
            'structured_lessons':[{**r,'rule_text':r['text']} for r in state['rules']],
            'publication':state,'evolution_review':evolution_status(DATA_DIR),
            'self_improvement_schedule':load_schedule().get('self_improvement_time')}


@app.post("/api/v1/admin/memory")
def add_admin_memory_item(payload: MemoryItemRequest, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    actor=require_superadmin(x_okxquant_session)
    from scripts import memory_registry
    try:
        result=memory_registry.propose({'action':'ADD','text':payload.text},data_dir=DATA_DIR,actor=actor['username'])
        audit_record('memory.candidate.create','success',{'actor':actor['username']})
        return {'saved':False,'pending':True,**result}
    except memory_registry.MemoryError as exc:raise HTTPException(400,str(exc)) from None


def _retired_memory_write(session):
    require_superadmin(session)
    raise HTTPException(410,'旧记忆直写/按索引删除/黄金基准回滚已停用，请在记忆候选中审核发布或选择真实历史版本回滚。')


@app.post("/api/v1/admin/memory/toggle/{lesson_id}")
def toggle_admin_memory_lesson(lesson_id: str, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")):
    _retired_memory_write(x_okxquant_session)


@app.post("/api/v1/admin/memory/rollback")
def rollback_admin_memory_lessons(x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")):
    _retired_memory_write(x_okxquant_session)


@app.delete("/api/v1/admin/memory/{index}")
def delete_admin_memory_item(index: int, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")):
    _retired_memory_write(x_okxquant_session)


@app.put("/api/v1/admin/memory")
def update_admin_memory_all(payload: MemoryUpdateAllRequest, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")):
    _retired_memory_write(x_okxquant_session)


@app.get("/health", include_in_schema=False)
@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    return {
        "service": "okxquant-standalone-backend",
        "version": "0.1.0",
        "status": "ok",
        "timestamp": int(time.time()),
        "credentials": {
            "okx_configured": bool(settings.okx_api_key and settings.okx_secret_key and settings.okx_passphrase),
            "llm_configured": bool(settings.llm_api_key),
            "simulated_trading": settings.okx_simulated,
        },
    }


@app.get("/api/v1/status")
def status() -> dict[str, Any]:
    return {
        "version": "0.1.0",
        "mode": "read_only_control_plane",
        "scripts": [
            script_state("ai_factor_trader.py"),
            script_state("ai_brain_trader.py"),
            script_state("daemon_web_sync.py"),
            script_state("self_improvement_engine.py"),
            script_state("nightly_backup_and_clean.py"),
        ],
        "last_decisions": read_json("ai_brain_decisions.json", {}),
        "position_trackers": read_json("position_trackers.json", {}),
    }


@app.get("/api/v1/cache/{resource}")
def cache(resource: str, x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> JSONResponse:
    allowed = {
        "decisions": "ai_brain_decisions.json",
        "factors": "factor_library_snapshot.json",
        "ledger": "trading_ledger.json",
        "sentiment": "news_sentiment.json",
        "self-improvement": "self_improvement_report.json",
        "horizon-stats": "horizon_stats.json",
    }
    filename = allowed.get(resource)
    if not filename:
        raise HTTPException(status_code=404, detail="unknown cache resource")
    # Private ledger contains historical financial profit/loss records; require admin auth
    if resource == "ledger":
        require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    return JSONResponse(read_json(filename, {} if resource != "ledger" else []))


from okxquant_backend.chart_market import router as chart_market_router
app.include_router(chart_market_router)


@app.get("/api/v1/market/{inst_id}")
def market(inst_id: str) -> dict[str, Any]:
    if not inst_id.endswith("-SWAP"):
        raise HTTPException(status_code=400, detail="only SWAP instrument ids are accepted")
    try:
        ticker = okx.ticker(inst_id)
        return {"instId": inst_id, "ticker": ticker[0] if ticker else {}, "source": "OKX REST"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OKX market request failed: {exc}") from exc


@app.get("/api/v1/account/positions")
def positions(x_okxquant_admin_token: str | None = Header(default=None), x_okxquant_session: str | None = Header(default=None, alias="X-OKXQuant-Session")) -> dict[str, Any]:
    require_admin_header(x_okxquant_admin_token, x_okxquant_session)
    if not settings.okx_api_key:
        raise HTTPException(status_code=503, detail="OKX credentials are not configured in .env")
    try:
        return {"positions": okx.positions(), "source": "OKX REST"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OKX account request failed: {exc}") from exc


from okxquant_backend.account_routes import install as install_account_routes
install_account_routes(app, require_superadmin, audit_record)
from okxquant_backend.memory_routes import install as install_memory_routes
install_memory_routes(app, require_superadmin, audit_record, lambda: DATA_DIR)

# Preserve the existing public dashboard and its relative-path API contract at /.
# Admin and /api/v1 routes above are evaluated before this catch-all mount.
from dashboard.app import app as dashboard_app
app.mount("/", dashboard_app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
