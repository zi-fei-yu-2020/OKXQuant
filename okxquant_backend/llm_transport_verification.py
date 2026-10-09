"""Explicit, bounded and deduplicated capability probes; never trading requests.

Jobs have bounded, cross-process leases in a private shared journal. HTTP POST
returns immediately; restart never replays a model request. Expired leases become
unknown/failed rather than authorizing another generation automatically.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
from scripts.config_lock import configuration_write
import time
import uuid
from . import llm_transport_policy as policy_store
from .llm_transport import LLMRequestError, public_failure, safe_transport_diagnostics

JOBS_PATH=policy_store.ROOT/"data"/"llm_transport_verification_jobs.json"
_EXECUTOR=ThreadPoolExecutor(max_workers=2,thread_name_prefix='llm-verify')
MAX_RUNNING=2
TTL_SECONDS=3600


def _public(job):
    return {k:copy.deepcopy(v) for k,v in job.items() if k not in {'fingerprint','context'}}


def _jobs():
    try:
        if JOBS_PATH.stat().st_size>4194304:raise ValueError('oversized')
        data=json.loads(JOBS_PATH.read_text(encoding='utf8'))
        if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('jobs'),dict):raise ValueError('invalid')
        return data['jobs']
    except FileNotFoundError:return {}
    except (OSError,ValueError,TypeError):raise RuntimeError('Verification journal unavailable') from None


def _save(jobs):
    import tempfile
    JOBS_PATH.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.llm-verification-',dir=JOBS_PATH.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as f:json.dump({'version':1,'jobs':jobs},f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.chmod(name,0o600);os.replace(name,JOBS_PATH)
    finally:
        if os.path.exists(name):os.unlink(name)


def _expire(jobs):
    now=time.time()
    for job in jobs.values():
        if job.get('status') in {'queued','running'} and now>job.get('expires_at',0):
            job.update(status='completed',ok=False,finished_at=now,error={'category':'verification_expired','message':'Verification worker ended or deadline expired; outcome unknown, no automatic replay.'})
    for key in list(jobs):
        if jobs[key].get('status')=='completed' and now-jobs[key].get('created_at',0)>TTL_SECONDS:jobs.pop(key)


def get_job(job_id):
    jobs=_jobs();_expire(jobs)
    job=jobs.get(job_id)
    return _public(job) if job else None


def start(provider_id,model_id,mode):
    if mode not in {'stream','json'}:raise ValueError('Verify mode must be stream or json')
    context=policy_store.selected_context(provider_id,model_id)
    if not context.get('base_url'):raise ValueError('Save provider URL before verification')
    if context['api_format'] not in policy_store.PROTOCOLS:raise ValueError('Protocol has no transport adapter')
    policy=policy_store.policy_for(provider_id);policy['mode']=mode
    fingerprint=policy_store.connection_fingerprint(context)
    with configuration_write(JOBS_PATH):
        jobs=_jobs();_expire(jobs)
        for job in jobs.values():
            if job['fingerprint']==fingerprint and job['mode']==mode and job['status'] in {'queued','running'}:return _public(job)
        if sum(j['status'] in {'queued','running'} for j in jobs.values())>=MAX_RUNNING:raise RuntimeError('Verification capacity busy; wait for existing probes')
        if len(jobs)>=256:
            completed=sorted((k for k in jobs if jobs[k]['status']=='completed'),key=lambda k:jobs[k]['created_at'])
            for key in completed[:len(jobs)-255]:jobs.pop(key)
        job_id=uuid.uuid4().hex;now=time.time()
        job={'job_id':job_id,'provider_id':provider_id,'model_id':model_id,'mode':mode,'status':'queued','created_at':now,'expires_at':now+min(180.0,policy['total_timeout_seconds'])+30,'fingerprint':fingerprint}
        jobs[job_id]=job;_save(jobs)
    try:_EXECUTOR.submit(_run,job_id,context,policy)
    except Exception:
        with configuration_write(JOBS_PATH):
            jobs=_jobs()
            if job_id in jobs:jobs[job_id].update(status='completed',ok=False,error={'category':'verification_start_failed','message':'Verification was not started.'})
            _save(jobs)
        raise
    return _public(job)


def _run(job_id,context,policy):
    with configuration_write(JOBS_PATH):
        jobs=_jobs();_expire(jobs)
        if job_id not in jobs or jobs[job_id]['status']!='queued':return
        jobs[job_id]['status']='running';_save(jobs)
    diagnostics={};failure=None;ok=False
    try:
        from .llm_manager import execute_llm_request
        content,reasoning,usage,latency=execute_llm_request(
            messages=[{'role':'user','content':'Connection capability test. Return only the JSON object {"ok":true}; no other fields or explanation.'}],
            provider_id=context['provider_id'],model=context['model'],base_url=context['base_url'],api_key=context['api_key'],api_format=context['api_format'],
            reasoning_effort=context['reasoning_effort'],reasoning_type=context['reasoning_type'],temperature=0.2,response_format={'type':'json_object'},
            max_tokens=4096,timeout=min(180.0,policy['total_timeout_seconds']),max_attempts=1,require_complete=True,
            transport_policy=policy,transport_diagnostics=diagnostics)
        parsed=json.loads(content)
        if not isinstance(parsed,dict) or set(parsed)!={'ok'} or parsed['ok'] is not True:raise LLMRequestError(200,1,'invalid_response')
        ok=True
    except LLMRequestError as exc:
        failure=public_failure(exc)
    except (ValueError,TypeError):failure=public_failure(LLMRequestError(200,1,'invalid_response'))
    except Exception:failure=public_failure(LLMRequestError(0,1,'invalid_response'))
    safe=safe_transport_diagnostics(diagnostics)
    result={'ok':ok,'diagnostics':safe}
    if failure:result['error']=failure
    # Fence publication under the same cross-process job lock that admits newer
    # probes. A late worker cannot stamp a receipt after its lease was superseded.
    with configuration_write(JOBS_PATH):
        jobs=_jobs();_expire(jobs)
        current=jobs.get(job_id)
        if not current or current.get('status')!='running' or current.get('fingerprint')!=policy_store.connection_fingerprint(context):
            _save(jobs);return
        try:
            policy_store.record_verification(context,policy['mode'],ok,safe,(failure or {}).get('category',''))
            result['state']=policy_store.status(policy_store.selected_context(context['provider_id'],context['model']))
        except (ValueError,LLMRequestError):
            result={'ok':False,'diagnostics':safe,'error':{'category':'connection_changed','message':'Connection changed during verification; old result was not applied.'}}
            try:result['state']=policy_store.status(policy_store.selected_context(context['provider_id'],context['model']))
            except (ValueError,LLMRequestError):pass
        current.update(result,status='completed',finished_at=time.time())
        _save(jobs)
