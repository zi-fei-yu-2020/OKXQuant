"""Superadmin-only official-source settings, status and bounded read-only refresh."""
import os
import subprocess
import sys
from fastapi import APIRouter, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from okxquant_backend import macro_store
from scripts import macro_market


class MacroUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    official_enabled: bool
    expected_revision: int = Field(ge=0)


class SafeSettingsRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                # Retired clients may still submit a credential. Never echo input.
                raise HTTPException(422, '仅接受官方数据开关和配置版本，请刷新控制台') from None
        return handler


def install(app, require_superadmin, audit_record):
    router = APIRouter(prefix='/api/v1/admin/macro-data', route_class=SafeSettingsRoute)

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
        values = payload.model_dump()
        return execute('update', actor, lambda: macro_store.update(**values))

    @router.post('/refresh')
    def refresh(x_okxquant_session: str | None = Header(default=None, alias='X-OKXQuant-Session')):
        actor = require_superadmin(x_okxquant_session)
        # Existing next-attempt times apply even to manual refreshes.
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
