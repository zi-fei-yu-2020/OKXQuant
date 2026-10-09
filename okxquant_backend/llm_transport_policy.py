"""Provider-scoped transport policy and model/connection-bound verification receipts.

No network, no automatic probes, no credentials/prompts in the persisted document.
Unknown capability preserves JSON compatibility; auto never guesses stream support.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from scripts.config_lock import configuration_write
from .llm_transport import LLMRequestError, safe_transport_diagnostics

ROOT=Path(__file__).resolve().parents[1]
CONFIG_PATH=ROOT/'data'/'llm_transport_settings.json'
PROTOCOLS=frozenset({'openai_chat','openai_responses','claude_messages'})
DEFAULT_POLICY={'mode':'auto','connect_timeout_seconds':10.0,'first_byte_timeout_seconds':150.0,
                'idle_timeout_seconds':90.0,'total_timeout_seconds':180.0,'max_response_bytes':8388608}
LIMITS={'connect_timeout_seconds':(1,60),'first_byte_timeout_seconds':(1,600),
        'idle_timeout_seconds':(1,300),'total_timeout_seconds':(10,600),'max_response_bytes':(65536,16777216)}


def validate_policy(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT_POLICY):raise ValueError('Unknown transport policy fields')
    result={**DEFAULT_POLICY,**value}
    if result['mode'] not in {'auto','stream','json'}:raise ValueError('mode must be auto, stream or json')
    for key,(low,high) in LIMITS.items():
        v=result[key]
        if type(v) not in (int,float) or not math.isfinite(v) or not low<=v<=high:raise ValueError('Invalid '+key)
        if key=='max_response_bytes' and int(v)!=v:raise ValueError('max_response_bytes must be integer')
        result[key]=int(v) if key=='max_response_bytes' else float(v)
    if result['connect_timeout_seconds']>result['first_byte_timeout_seconds']:raise ValueError('Connect timeout must not exceed first-byte timeout')
    if result['first_byte_timeout_seconds']>result['total_timeout_seconds']:raise ValueError('First-byte timeout must not exceed total timeout')
    if result['idle_timeout_seconds']>result['total_timeout_seconds']:raise ValueError('Idle timeout must not exceed total timeout')
    return result


def _read():
    try:
        if CONFIG_PATH.stat().st_size>4194304:raise ValueError('oversized')
        doc=json.loads(CONFIG_PATH.read_text(encoding='utf8'))
        if not isinstance(doc,dict) or doc.get('version')!=1 or not isinstance(doc.get('providers'),dict) or not isinstance(doc.get('capabilities'),dict):raise ValueError('invalid')
        return doc
    except FileNotFoundError:return {'version':1,'providers':{},'capabilities':{}}
    except (OSError,ValueError,TypeError):raise LLMRequestError(0,0,'configuration_error') from None


def _write(doc):
    CONFIG_PATH.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.llm-transport-',dir=CONFIG_PATH.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as f:json.dump(doc,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.chmod(name,0o600);os.replace(name,CONFIG_PATH)
    finally:
        if os.path.exists(name):os.unlink(name)


def policy_for(provider_id):
    doc=_read()
    try:return validate_policy(doc['providers'].get(str(provider_id),{}))
    except (ValueError,TypeError):raise LLMRequestError(0,0,'configuration_error') from None


def save_policy(provider_id,policy):
    if not isinstance(provider_id,str) or not provider_id or len(provider_id)>160:raise ValueError('Invalid provider id')
    clean=validate_policy(policy)
    with configuration_write(CONFIG_PATH):
        doc=_read();doc['providers'][provider_id]=clean;_write(doc)
    return clean


def connection_fingerprint(context):
    # Include credentials only in a one-way digest; never persist plaintext keys.
    identity={k:str(context.get(k) or '') for k in ('provider_id','model','base_url','api_format','reasoning_effort','reasoning_type','api_key')}
    identity['base_url']=identity['base_url'].rstrip('/')
    identity['contract']='llm-transport-v1-json-probe'
    return hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def selected_context(provider_id,model_id):
    from .llm_manager import load_llm_config, select_model
    config=load_llm_config(mask_keys=False)
    provider=next((p for p in config.get('providers',[]) if p.get('id')==provider_id),None)
    if not provider:raise ValueError('Provider not found')
    model=select_model(config,model_id,provider_id)
    if not model:raise ValueError('Model not found in provider')
    active=config.get('active_provider_id')==provider_id and config.get('active_model_id')==model_id
    return {'provider_id':provider_id,'model':model_id,'base_url':model.get('base_url') or provider.get('base_url') or '',
            'api_key':model.get('api_key') or provider.get('api_key') or '',
            'api_format':model.get('api_format') or provider.get('api_format') or 'openai_chat',
            'reasoning_type':model.get('reasoning_type') or 'auto',
            'reasoning_effort':(config.get('active_reasoning_effort') if active else model.get('reasoning_effort')) or 'high'}


def resolve_context(model,base_url,api_key,protocol,effort,active_runtime):
    actual={'provider_id':'','model':model,'base_url':base_url,'api_key':api_key,'api_format':protocol,'reasoning_effort':effort,'reasoning_type':'auto'}
    def same(key):
        left=str(actual.get(key) or '');right=str(active_runtime.get(key) or '')
        return left.rstrip('/')==right.rstrip('/') if key=='base_url' else left==right
    if all(same(k) for k in ('model','base_url','api_key','api_format')):
        actual.update(provider_id=active_runtime.get('provider_id',''),reasoning_type=active_runtime.get('reasoning_type','auto'))
        return actual
    from .llm_manager import load_llm_config
    config=load_llm_config(mask_keys=False)
    matches=[m for m in config.get('models',[]) if m.get('id')==model and str(m.get('base_url') or '').rstrip('/')==str(base_url).rstrip('/') and (m.get('api_key') or '')==(api_key or '') and (m.get('api_format') or 'openai_chat')==protocol]
    if len(matches)==1:actual.update(provider_id=matches[0].get('provider_id',''),reasoning_type=matches[0].get('reasoning_type','auto'))
    return actual


def capability_state(context):
    entries=_read()['capabilities'].get(connection_fingerprint(context),{})
    out={}
    for mode in ('stream','json'):
        value=entries.get(mode,{}) if isinstance(entries,dict) else {}
        state=value.get('status') if isinstance(value,dict) else None
        if state not in {'verified','unsupported','failed'}:out[mode]={'status':'unknown'};continue
        out[mode]={'status':state,'diagnostics':safe_transport_diagnostics(value.get('diagnostics'))}
        checked=value.get('checked_at')
        if isinstance(checked,str):
            try:out[mode]['checked_at']=datetime.fromisoformat(checked).isoformat()
            except ValueError:pass
        category=value.get('error_category')
        from .llm_transport import ERROR_LABELS
        if category in ERROR_LABELS:out[mode]['error_category']=category
        if value.get('last_probe_status') == 'failed':out[mode]['last_probe_status']='failed'
    return out


def status(context):
    policy=policy_for(context.get('provider_id',''))
    caps=capability_state(context)
    effective=policy['mode']
    warnings=[]
    if effective=='auto':
        effective='stream' if caps['stream']['status']=='verified' else 'json'
        if caps['stream']['status']!='verified':warnings.append('自动模式尚无当前连接的流式验证记录，暂用非流式兼容；长响应仍可能遇到代理超时。')
    if effective=='stream' and caps['stream']['status']!='verified':warnings.append('流式尚未验证；失败不会自动重发为非流式请求。')
    if context.get('api_format') not in PROTOCOLS:warnings.append('此协议尚无流式适配器，不能假装兼容。')
    warnings.append('短探针只验证本次协议与小型 JSON，不代表任意上下文、输出预算或长请求均可用。心跳须由服务端发送，不能代替完整结果；总时限仍受调用任务预算约束。')
    return {'provider_id':context.get('provider_id',''),'model_id':context.get('model',''),'protocol':context.get('api_format'),
            'policy':policy,'effective_mode':effective,'capabilities':caps,'warnings':warnings}


def resolved_policy(context):
    value=status(context);return {**value['policy'],'mode':value['effective_mode']}


def record_verification(context,mode,ok,diagnostics,error_category=''):
    if mode not in {'stream','json'}:raise ValueError('Invalid verification mode')
    current=selected_context(context['provider_id'],context['model'])
    if connection_fingerprint(current)!=connection_fingerprint(context):raise ValueError('Connection changed during verification')
    state='verified' if ok else 'unsupported' if error_category=='stream_unsupported' else 'failed'
    record={'status':state,'checked_at':datetime.now(timezone.utc).isoformat(), 'diagnostics':safe_transport_diagnostics(diagnostics)}
    if error_category:record['error_category']=error_category
    with configuration_write(CONFIG_PATH):
        # Recheck under the file transaction; an old receipt cannot authorize a new connection.
        if connection_fingerprint(selected_context(context['provider_id'],context['model']))!=connection_fingerprint(context):raise ValueError('Connection changed during verification')
        doc=_read();fingerprint=connection_fingerprint(context)
        entries=doc['capabilities'].setdefault(fingerprint,{})
        previous=entries.get(mode,{})
        # A temporary network/probe failure is not evidence the protocol stopped
        # being supported. Keep prior verified capability, report failed recheck.
        if state=='failed' and isinstance(previous,dict) and previous.get('status')=='verified':
            previous=dict(previous);previous['last_probe_status']='failed';entries[mode]=previous
        else:entries[mode]=record
        while len(doc['capabilities'])>512:doc['capabilities'].pop(next(iter(doc['capabilities'])))
        _write(doc)
    return record
