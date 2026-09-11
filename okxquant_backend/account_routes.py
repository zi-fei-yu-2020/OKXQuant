"""Superadministrator account-center API. Responses never expose credentials/tokens."""
from typing import Literal
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, SecretStr
from okxquant_backend import account_connections as center, connection_transport


class CreateConnection(BaseModel):
    label: str
    auth_type: Literal['api_key','oauth']
    mode: Literal['demo','live']
    site: Literal['global','eea','us','tr']
    api_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    passphrase: SecretStr | None = None


class ModeRequest(BaseModel):
    mode: Literal['demo','live']


class ManualCloseRequest(BaseModel):
    enabled: bool
    confirmation: str


class ConfirmRequest(BaseModel):
    confirmation: str
    connection_id: str = ''


def install(app, require_superadmin, audit_record):
    router=APIRouter(prefix='/api/v1/admin/accounts')
    def execute(action, function, actor, *args):
        try:
            result=function(*args)
            audit_record('accounts.'+action,'success',{'actor':actor['username']})
            return result
        except (center.AccountChangeError, connection_transport.ConnectionError, TimeoutError, ValueError) as exc:
            audit_record('accounts.'+action,'failed',{'actor':actor['username'],'error':type(exc).__name__})
            raise HTTPException(status_code=409,detail=str(exc)) from None

    @router.get('')
    def status(x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        actor=require_superadmin(x_okxquant_session)
        return execute('status',center.public_status,actor)

    @router.post('/connections')
    def create(payload: CreateConnection,x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        actor=require_superadmin(x_okxquant_session)
        credentials={k:getattr(payload,k).get_secret_value() if getattr(payload,k) else '' for k in ('api_key','secret_key','passphrase')}
        identity=execute('create',center.create,actor,payload.label,payload.auth_type,payload.mode,payload.site,credentials)
        return {'connection_id':identity,'state':center.public_status()}

    @router.post('/import-legacy')
    def import_legacy(payload: ModeRequest,x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        actor=require_superadmin(x_okxquant_session)
        identity=execute('import',center.import_legacy,actor,payload.mode)
        return {'connection_id':identity,'state':center.public_status()}

    @router.post('/connections/{identity}/oauth/start')
    def oauth_start(identity: str,x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        return execute('oauth.start',center.oauth_start,require_superadmin(x_okxquant_session),identity)

    @router.get('/connections/{identity}/oauth/status')
    def oauth_status(identity: str,x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        actor=require_superadmin(x_okxquant_session)
        def read():
            c=center._connection(center.load(),identity)
            if c['auth_type']!='oauth': raise center.AccountChangeError('该连接不是OAuth')
            raw=connection_transport.auth_command(identity,'status')
            return {k:raw.get(k) for k in ('status','site','scopes')}
        return execute('oauth.status',read,actor)

    @router.post('/connections/{identity}/probe')
    def probe(identity: str,payload: ModeRequest,x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        return execute('probe',center.probe,require_superadmin(x_okxquant_session),identity,payload.mode)

    @router.put('/bindings/{purpose}')
    def bind(purpose: str,payload: ConfirmRequest,x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        return execute('bind',center.bind,require_superadmin(x_okxquant_session),purpose,payload.connection_id,payload.confirmation)

    @router.delete('/bindings/{purpose}')
    def unbind(purpose: str,payload: ConfirmRequest,x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        return execute('unbind',center.unbind,require_superadmin(x_okxquant_session),purpose,payload.confirmation)

    @router.post('/activate/{mode}')
    def activate(mode: str,payload: ConfirmRequest,x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        return execute('activate',center.activate,require_superadmin(x_okxquant_session),mode,payload.confirmation)

    @router.delete('/connections/{identity}')
    def delete(identity: str,payload: ConfirmRequest,x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        return execute('delete',center.delete,require_superadmin(x_okxquant_session),identity,payload.confirmation)

    @router.put('/manual-close')
    def manual_close(payload: ManualCloseRequest,x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        return execute('manual_close',center.set_manual_close,require_superadmin(x_okxquant_session),payload.enabled,payload.confirmation)

    @router.post('/news/refresh')
    def refresh_news(x_okxquant_session: str | None = Header(default=None,alias='X-OKXQuant-Session')):
        from scripts.news_sentiment_harvester import fetch_and_analyze_news_sentiment
        return execute('news.refresh',fetch_and_analyze_news_sentiment,require_superadmin(x_okxquant_session))

    app.include_router(router)
