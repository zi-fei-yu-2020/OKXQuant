"""Explicit memory review/publication API. All live mutations require a superadmin."""
from typing import Literal
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from scripts import memory_registry as memory

class ScopeRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scope: str

class InitializeRequest(ScopeRequest):
    confirmation: str

class CandidateRequest(ScopeRequest):
    action: Literal['ADD','REVISE','DEACTIVATE','CLEAR_LEGACY']='ADD'
    text: str=Field(default='',max_length=3000)
    target_rule_id: str | None=None
    rationale: str=Field(default='',max_length=3000)
    request_id: str=Field(min_length=8,max_length=100)

class ReviewRequest(ScopeRequest):
    expected_revision: int=Field(ge=0)
    note: str=Field(min_length=4,max_length=3000)

class PublishRequest(ReviewRequest):
    evidence_hash: str | None = None
    supporting_trade_ids: list[str]=Field(default_factory=list,max_length=100)
    confirmation: str

class RollbackRequest(ReviewRequest):
    confirmation: str


def install(app, require_superadmin, audit_record, data_dir):
    router=APIRouter(prefix='/api/v1/admin/memory')
    def execute(action,actor,payload,fn,**kwargs):
        try:
            if payload.scope!=memory.scope_of():raise memory.MemoryConflict('当前交易账户已变化，请刷新后重新审核')
            result=fn(data_dir=data_dir(),scope=payload.scope,actor=actor['username'],**kwargs)
            audit_record('memory.'+action,'success',{'actor':actor['username'],'scope':payload.scope})
            return {'ok':True,**result}
        except (memory.MemoryConflict,TimeoutError) as exc:raise HTTPException(409,str(exc)) from None
        except memory.MemoryError as exc:raise HTTPException(400,str(exc)) from None

    @router.post('/initialize')
    def initialize(payload:InitializeRequest,x_okxquant_session: str|None=Header(default=None,alias='X-OKXQuant-Session')):
        actor=require_superadmin(x_okxquant_session)
        return execute('initialize',actor,payload,memory.initialize,confirmation=payload.confirmation)

    @router.post('/candidates')
    def create(payload:CandidateRequest,x_okxquant_session: str|None=Header(default=None,alias='X-OKXQuant-Session')):
        actor=require_superadmin(x_okxquant_session)
        return execute('candidate.create',actor,payload,memory.propose,proposal=payload.model_dump(exclude={'scope','request_id'}),request_id=payload.request_id)

    @router.post('/candidates/{identity}/publish')
    def publish(identity:str,payload:PublishRequest,x_okxquant_session: str|None=Header(default=None,alias='X-OKXQuant-Session')):
        actor=require_superadmin(x_okxquant_session)
        return execute('publish',actor,payload,memory.publish,identity=identity,revision=payload.expected_revision,note=payload.note,trade_ids=payload.supporting_trade_ids,confirmation=payload.confirmation,evidence_hash=payload.evidence_hash)

    @router.post('/candidates/{identity}/reject')
    def reject(identity:str,payload:ReviewRequest,x_okxquant_session: str|None=Header(default=None,alias='X-OKXQuant-Session')):
        actor=require_superadmin(x_okxquant_session)
        return execute('reject',actor,payload,memory.reject,identity=identity,revision=payload.expected_revision,note=payload.note)

    @router.post('/versions/{version}/restore')
    def restore(version:int,payload:RollbackRequest,x_okxquant_session: str|None=Header(default=None,alias='X-OKXQuant-Session')):
        actor=require_superadmin(x_okxquant_session)
        return execute('rollback',actor,payload,memory.rollback,target_version=version,revision=payload.expected_revision,note=payload.note,confirmation=payload.confirmation)

    @router.get('/versions/{version}')
    def version_detail(version:int,x_okxquant_session: str|None=Header(default=None,alias='X-OKXQuant-Session')):
        require_superadmin(x_okxquant_session)
        try:return memory.version_detail(version,data_dir=data_dir())
        except memory.MemoryError as exc:raise HTTPException(404,str(exc)) from None

    app.include_router(router)
