"""Superadmin-only market-data credentials, status and rate-limited read-only probe."""
from typing import Literal
import os
import subprocess
import sys
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, SecretStr, Field
from okxquant_backend import macro_store
from scripts import macro_market


class MacroUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    official_enabled: bool
    fmp_enabled: bool
    calendar_timezone: Literal['', 'UTC', 'America/New_York'] = ''
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    expected_revision: int = Field(ge=0)


def install(app, require_superadmin, audit_record):
    router = APIRouter(prefix='/api/v1/admin/macro-data')

    def execute(action, actor, operation):
        try:
            result = operation()
            if action != 'status':
                audit_record('macro-data.'+action, 'success', {'actor': actor['username']})
            return result
        except TimeoutError:
            raise HTTPException(409, '宏观数据正在采集或配置中，请稍后重试') from None
        except ValueError as exc:
            # Store errors contain only fixed safe messages, never submitted keys.
            raise HTTPException(400, str(exc)) from None
        except Exception as exc:
            audit_record('macro-data.'+action, 'failed', {'actor': actor['username'], 'error': type(exc).__name__})
            raise HTTPException(503, '宏观数据服务暂不可用，未修改交易权限') from None

    @router.get('')
    def status(x_okxquant_session: str | None = Header(default=None, alias='X-OKXQuant-Session')):
        actor = require_superadmin(x_okxquant_session)
        return execute('status', actor, macro_store.status)

    @router.put('')
    def update(payload: MacroUpdate, x_okxquant_session: str | None = Header(default=None, alias='X-OKXQuant-Session')):
        actor = require_superadmin(x_okxquant_session)
        values = payload.model_dump(exclude={'api_key'})
        values['api_key'] = payload.api_key.get_secret_value() if payload.api_key is not None else None
        return execute('update', actor, lambda: macro_store.update(**values))

    @router.post('/refresh')
    def refresh(x_okxquant_session: str | None = Header(default=None, alias='X-OKXQuant-Session')):
        actor = require_superadmin(x_okxquant_session)
        # Existing next-attempt times apply even to manual probes: no quota bypass.
        def run():
            # A separate bounded process owns network sockets. Even a slow-drip
            # response cannot occupy an API worker beyond this hard deadline.
            completed = subprocess.run([sys.executable, str(macro_market.ROOT / 'scripts' / 'macro_market.py')],
                cwd=macro_market.ROOT, capture_output=True, timeout=110, check=False,
                env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
            if completed.returncode != 0:
                raise RuntimeError('macro_collector_failed')
            return macro_store.status()
        return execute('refresh', actor, run)

    app.include_router(router)
