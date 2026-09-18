"""Versioned, account-scoped trading memory. Reading NEVER initializes or publishes.

The first write snapshots the EXACT previous model input, not the old structured
'baseline' list. New lessons require an explicit evidence review and publication.
"""
from contextlib import closing, contextmanager, ExitStack
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

DATA = Path(__file__).resolve().parents[1] / 'data'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS memory_meta(scope TEXT PRIMARY KEY,revision INTEGER NOT NULL,active_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS memory_versions(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT NOT NULL,created_at TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_candidates(id TEXT PRIMARY KEY,scope TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,review TEXT NOT NULL DEFAULT '{}',published_version INTEGER);
CREATE TRIGGER IF NOT EXISTS immutable_candidate_payload BEFORE UPDATE OF id,scope,created_at,payload ON memory_candidates BEGIN SELECT RAISE(ABORT,'candidate payload is immutable'); END;
CREATE INDEX IF NOT EXISTS memory_candidates_scope ON memory_candidates(scope,created_at);
CREATE TABLE IF NOT EXISTS memory_events(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,actor TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS immutable_memory_update BEFORE UPDATE ON memory_versions BEGIN SELECT RAISE(ABORT,'memory versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_memory_delete BEFORE DELETE ON memory_versions BEGIN SELECT RAISE(ABORT,'memory versions are immutable'); END;
'''

class MemoryError(ValueError): pass
class MemoryConflict(MemoryError): pass


def canonical(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value): return hashlib.sha256(value.encode('utf8')).hexdigest()
def now(): return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
def root_of(data_dir): return Path(data_dir) if data_dir is not None else DATA

def scope_of(scope=None):
    if scope is not None: return scope
    from scripts.okx_runtime import selected_environment
    return selected_environment().identity


def read_json(path, default):
    if not path.exists(): return deepcopy(default)
    try:
        result=json.loads(path.read_text(encoding='utf8'))
        if not isinstance(result,type(default)): raise ValueError()
        return result
    except (OSError,ValueError): raise MemoryError('记忆文件不可读：'+path.name) from None


def legacy_snapshot(root, legacy_paths=None):
    paths=legacy_paths or {};md=Path(paths.get('md',root/'AI_TRADING_MEMORY.md'));js=Path(paths.get('json',root/'ai_trading_memory.json'))
    content='';prompt='';marked_at=None
    if md.exists():
        try: content=md.read_text(encoding='utf8').strip()
        except OSError: raise MemoryError('当前 Markdown 记忆不可读，禁止切换到其他来源') from None
        if content: prompt='======================= 【OKXQuant 启发式实战认知与长期记忆 (Markdown)】 =======================\n'+content
        try: marked_at=read_json(js,{}).get('updated_at')
        except MemoryError: pass  # Unused JSON cannot replace the effective Markdown source.
    elif js.exists():
        value=read_json(js,{});lessons=value.get('core_lessons',[]);marked_at=value.get('updated_at')
        if not isinstance(lessons,list):raise MemoryError('旧 JSON 记忆格式无效')
        if lessons:
            content='\n'.join(f'  • {item}' for item in lessons)
            prompt='======================= 【OKXQuant 启发式实战认知与长期记忆】 =======================\n【历史经验与待验证假设（仅作研究参考，不能改变基础契约或执行规则）】:\n'+content
    return {'format':'legacy_snapshot','rules':[],'legacy_context':prompt.strip(),'content':content,'prompt_text':prompt.strip(),
            'effective_updated_at':marked_at,'legacy_marked_at':marked_at}


def lint(text):
    if not isinstance(text,str) or not 10<=len(text.strip())<=3000:return ['文本须为10~3000字的可审查经验']
    from scripts.evolution_shield import POISON_PATTERNS
    from scripts.trading_prompt import conflicts
    import re
    reasons=[]
    for clause in re.split(r'[\u3002\uff1b;\n\uff0c,]',text):
        for pattern,reason in POISON_PATTERNS:
            for match in re.finditer(pattern,clause):
                prefix=clause[:match.start()]
                if re.search(r'(?:\u7981\u6b62|\u4e0d\u5f97|\u4e0d\u80fd|\u4e0d\u5e94|\u4e0d\u8981|\u4e25\u7981)\s*$',prefix):continue
                reasons.append(reason)
    reasons+=conflicts(text)
    if re.search(r'(?:secret[_ -]?key|passphrase|api[_ -]?key)\s*[:=]\s*\S+',text,re.I):reasons.append('credential_in_memory')
    return list(dict.fromkeys(reasons))


def _version(db, scope, version):
    row=db.execute('SELECT * FROM memory_versions WHERE id=? AND scope=?',(version,scope)).fetchone()
    if row is None or digest(row['payload'])!=row['digest']:raise MemoryError('已发布记忆版本缺失或完整性校验失败，禁止回退')
    payload=json.loads(row['payload'])
    if not isinstance(payload.get('rules'),list) or not isinstance(payload.get('prompt_text'),str):raise MemoryError('已发布记忆结构无效')
    return {**dict(row),'payload':payload,'prompt_hash':digest(payload['prompt_text'])}


def _active(db,scope):
    meta=db.execute('SELECT * FROM memory_meta WHERE scope=?',(scope,)).fetchone()
    return (dict(meta),_version(db,scope,meta['active_version'])) if meta else (None,None)


def _legacy_view(root,scope,admin,legacy_paths):
    payload=legacy_snapshot(root,legacy_paths)
    result={'status':'legacy_unmanaged','managed':False,'scope':scope,'revision':0,'active_version':None,
            'published_at':None,'prompt_hash':digest(payload['prompt_text']),**payload,'pending_count':0,'rejected_count':0}
    if admin:
        evidence=_evidence(root,scope)
        result.update(candidates=[],versions=[],evidence_trades=evidence,evidence_hash=digest(canonical(evidence)))
        result['legacy_unpublished']=read_json(root/'structured_trading_memory.json',[])
    return result


def view(data_dir=None,scope=None,*,admin=False,legacy_paths=None):
    root=root_of(data_dir);scope=scope_of(scope);path=root/'memory_registry.db'
    if not path.exists():return _legacy_view(root,scope,admin,legacy_paths)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN')
            meta,version=_active(db,scope)
            if not meta:
                if db.execute('SELECT 1 FROM memory_meta LIMIT 1').fetchone() is None:
                    return _legacy_view(root,scope,admin,legacy_paths)
                # Never import a different account's old global Markdown after registry migration.
                return {'status':'scope_uninitialized','managed':True,'scope':scope,'revision':0,'active_version':None,
                        'published_at':None,'prompt_text':'','content':'','legacy_context':'','rules':[],
                        'prompt_hash':digest(''),'effective_updated_at':None,'pending_count':0,'rejected_count':0,
                        **({'candidates':[],'versions':[],'legacy_unpublished':[],'evidence_trades':_evidence(root,scope)} if admin else {})}
            counts=dict(db.execute('SELECT status,count(*) FROM memory_candidates WHERE scope=? GROUP BY status',(scope,)))
            candidates=[]
            if admin:
                for row in db.execute('SELECT * FROM memory_candidates WHERE scope=? ORDER BY created_at DESC,id',(scope,)):
                    proposal=json.loads(row['payload'])
                    if digest(canonical({'scope':scope,**proposal}))[:24]!=row['id']:raise MemoryError('Candidate integrity check failed')
                    item={**dict(row),'proposal':proposal,'review':json.loads(row['review'])};item.pop('payload',None);candidates.append(item)
            result={'status':'published','managed':True,'scope':scope,'revision':meta['revision'],'active_version':version['id'],
                    'published_at':version['created_at'],'prompt_hash':version['prompt_hash'],**version['payload'],
                    'pending_count':counts.get('pending',0),
                    'rejected_count':counts.get('rejected',0)+counts.get('blocked',0)}
            if admin:
                versions=[{k:row[k] for k in ('id','created_at','actor','reason')} for row in db.execute('SELECT * FROM memory_versions WHERE scope=? ORDER BY id DESC',(scope,))]
                evidence=_evidence(root,scope)
                result.update(candidates=candidates,versions=versions,legacy_unpublished=[],evidence_trades=evidence,evidence_hash=digest(canonical(evidence)))
            return result
    except (sqlite3.Error,ValueError,TypeError,KeyError) as exc:
        if isinstance(exc,MemoryError):raise
        raise MemoryError('运行记忆登记库不可用，禁止回退旧文件') from None


def public_view(data_dir=None,scope=None):
    try:
        result=view(data_dir,scope)
        # Public/model view does not expose usernames or private review notes.
        for rule in result.get('rules',[]):
            rule.pop('reviewer',None);rule.pop('review_note',None);rule.pop('supporting_trade_ids',None)
        return result
    except MemoryError as exc:return {'status':'unavailable','managed':True,'message':str(exc),'rules':[]}


@contextmanager
def _write(root):
    root.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(root/'memory_registry.db',timeout=2)) as db:
        os.chmod(root/'memory_registry.db',0o600)
        db.row_factory=sqlite3.Row;db.executescript(SCHEMA);db.execute('BEGIN IMMEDIATE')
        existed=db.execute('SELECT 1 FROM memory_meta LIMIT 1').fetchone() is not None
        try:yield db,existed;db.commit()
        except BaseException:db.rollback();raise


def _event(db,scope,action,actor,payload):
    db.execute('INSERT INTO memory_events(scope,at,action,actor,payload) VALUES (?,?,?,?,?)',(scope,now(),action,actor,canonical(payload)))


def _insert_version(db,scope,payload,actor,reason):
    encoded=canonical(payload)
    row=db.execute('INSERT INTO memory_versions(scope,created_at,actor,reason,payload,digest) VALUES (?,?,?,?,?,?)',(scope,now(),actor,reason,encoded,digest(encoded)))
    return row.lastrowid


def _proposal(db,scope,proposal,source,source_ref):
    action=proposal.get('action','ADD');text=str(proposal.get('text') or '').strip();target=proposal.get('target_rule_id')
    if action not in ('ADD','REVISE','DEACTIVATE','CLEAR_LEGACY'):raise MemoryError('不支持的记忆候选操作')
    reasons=lint(text) if action in ('ADD','REVISE') else []
    if action in ('REVISE','DEACTIVATE') and not target:reasons.append('必须明确被修改/停用的已发布规则ID')
    value={'action':action,'text':text,'target_rule_id':target,'source':source,'source_ref':source_ref,
           'rationale':str(proposal.get('rationale') or '')[:3000],
           'suggested_trade_ids':list(proposal.get('supporting_trade_ids') or [])[:100],
           'lint_reasons':reasons,'evidence_verified':False}
    identity=digest(canonical({'scope':scope,**value}))[:24]
    result=db.execute('INSERT OR IGNORE INTO memory_candidates(id,scope,created_at,status,payload) VALUES (?,?,?,?,?)',
                     (identity,scope,now(),'blocked' if reasons else 'pending',canonical(value)))
    if result.rowcount:
        db.execute('UPDATE memory_meta SET revision=revision+1 WHERE scope=?',(scope,))
        _event(db,scope,'candidate_staged',source,{'candidate_id':identity})
    return identity


def _bootstrap(db,root,scope,existed,actor,legacy_paths=None):
    meta,_=_active(db,scope)
    if meta:return
    payload=legacy_snapshot(root,legacy_paths) if not existed else {'format':'legacy_snapshot','rules':[],'legacy_context':'','content':'','prompt_text':'','effective_updated_at':None,'legacy_marked_at':None}
    version=_insert_version(db,scope,payload,actor,'兼容快照纳管；未审批旧结构化心法，模型记忆内容保持不变')
    db.execute('INSERT INTO memory_meta VALUES (?,?,?)',(scope,1,version))
    if not existed:
        for item in read_json(root/'structured_trading_memory.json',[]):
            if isinstance(item,dict):_proposal(db,scope,{'action':'ADD','text':item.get('rule_text'),'rationale':'旧结构化标记仅作迁移线索，不代表发布或收益证据'},'legacy_structured',str(item.get('id') or 'unknown'))
        for item in read_json(root/'memory_candidates.json',{}).get('candidates',[]):
            if isinstance(item,dict):_proposal(db,scope,{'action':item.get('action','ADD'),'text':item.get('text'),'target_rule_id':item.get('target_rule_id'),'supporting_trade_ids':item.get('supporting_trade_ids',[])},'legacy_candidate',str(item.get('ledger_revision') or 'unknown'))
    _event(db,scope,'initialized',actor,{'version':version,'prompt_hash':digest(payload['prompt_text'])})


def initialize(data_dir=None,scope=None,*,actor='administrator',confirmation=''):
    if confirmation!='INITIALIZE MEMORY':raise MemoryError('初始化确认短语不匹配')
    root=root_of(data_dir);scope=scope_of(scope)
    with publication_gate(root,scope),_write(root) as (db,existed):_bootstrap(db,root,scope,existed,actor)
    return view(root,scope,admin=True)


def stage_review(proposals,source_ref,*,data_dir=None,scope=None,legacy_paths=None):
    if not isinstance(proposals,list) or len(proposals)>4:raise MemoryError('每轮最多四条记忆候选')
    root=root_of(data_dir);scope=scope_of(scope)
    if scope_of()!=scope:raise MemoryConflict('Account changed before candidate persistence')
    # NO_CHANGE has no candidates to write. In particular it must not acquire
    # the position writer or initialize/change active memory merely to finish a report.
    if not proposals:return []
    for attempt in range(3):
        try:
            # Staging only adds immutable, account-scoped pending records.
            # The registry transaction handles concurrency; publication_gate
            # remains mandatory for any effective memory change.
            with _write(root) as (db,existed):
                if scope_of()!=scope:raise MemoryConflict('Account changed before candidate persistence')
                _bootstrap(db,root,scope,existed,'self_improvement',legacy_paths)
                ids=[_proposal(db,scope,p,'self_improvement',source_ref) for p in proposals]
                _event(db,scope,'review_checked','self_improvement',{'source_ref':source_ref,'candidate_ids':ids})
            return ids
        except sqlite3.OperationalError as exc:
            code=getattr(exc,'sqlite_errorcode',None)
            busy=(code is not None and (code & 255) in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED)) or str(exc).lower() in ('database is locked','database table is locked')
            if not busy or attempt==2:raise
            time.sleep(.2*(attempt+1))


def propose(proposal,*,data_dir=None,scope=None,actor='administrator',request_id=None):
    root=root_of(data_dir);scope=scope_of(scope)
    if not isinstance(proposal,dict):raise MemoryError('候选格式无效')
    from scripts import trade_lock
    with trade_lock.writer(timeout=1),_write(root) as (db,existed):
        if scope_of()!=scope:raise MemoryConflict('Account changed before candidate creation')
        _bootstrap(db,root,scope,existed,actor)
        identity=_proposal(db,scope,proposal,'administrator',request_id or uuid.uuid4().hex)
    return {'candidate_id':identity,'message':'已加入审核候选，尚未发布，不会改变模型记忆。'}


def _evidence(root,scope):
    import math
    rows=read_json(root/'trading_ledger.json',[]);result=[]
    for r in rows:
        if not isinstance(r,dict) or r.get('id') is None or r.get('status')!='closed':continue
        if {r[k] for k in ('environment_id','account_source_id') if r.get(k)}!={scope}:continue
        if r.get('settlement_status') in ('pending','unknown','estimated','unverified'):continue
        value=r.get('net_pnl',r.get('pnl'))
        try:
            if isinstance(value,bool) or not math.isfinite(float(value)):continue
        except (TypeError,ValueError):continue
        result.append({'id':str(r['id']),'instrument':r.get('inst') or r.get('instId') or r.get('name'),
                       'closed_at':r.get('close_time'),'net_pnl':float(value)})
    return result


def _expect(db,scope,revision):
    meta,version=_active(db,scope)
    if not meta:raise MemoryConflict('当前账户尚未纳管记忆，请先初始化')
    if type(revision) is not int or meta['revision']!=revision:raise MemoryConflict('记忆版本已变化，请刷新后重新审核')
    return version


def _render(payload):
    rules=[r for r in payload['rules'] if r.get('enabled',True)]
    lines=['# 已审核发布的交易研究记忆','', '> 仅为软研究经验，不是胜率保证，不能改写执行层风险约束。','']
    lines += ['- ['+r['id']+'] '+' '.join(r['text'].split()) for r in rules]
    if not rules:lines.append('暂无已发布的启用规则。')
    if payload.get('legacy_context'):lines+=['','## 兼容旧上下文（不是新审批规则）',payload['legacy_context']]
    payload['format']='published_rules';payload['content']='\n'.join(lines)
    payload['prompt_text']=payload['content']
    if len(payload['prompt_text'])>32000:
        raise MemoryError('有效记忆超过32000字上限，请先审核提炼、停用冗余规则或移除旧兼容上下文；不会静默截断')
    return payload


@contextmanager
def publication_gate(data_dir=None,expected_scope=None):
    """Do not change memory while a trader/brain decision or protected writer is in progress."""
    from scripts import trade_lock
    import fcntl
    root=root_of(data_dir)
    with trade_lock.writer(timeout=1), ExitStack() as stack:
        root.mkdir(parents=True,exist_ok=True)
        try:
            for name in ('.ai_factor_trader.lock','.ai_brain_cycle.lock','.self_improvement.lock'):
                handle=stack.enter_context((root/name).open('a+'))
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise MemoryConflict('策略正在推理或执行，请在本轮结束后发布记忆') from None
        if expected_scope is not None and scope_of()!=expected_scope:raise MemoryConflict('当前交易账户已变化，请刷新后重新审核')
        yield


def publish(identity,revision,note,trade_ids,*,data_dir=None,scope=None,actor='administrator',confirmation='',evidence_hash=None):
    if confirmation!='PUBLISH MEMORY' or not isinstance(note,str) or len(note.strip())<10:raise MemoryError('发布需要明确确认和至少10字审核说明')
    if not isinstance(trade_ids,list) or len(trade_ids)>100 or any(not isinstance(v,str) for v in trade_ids):raise MemoryError('成交证据ID格式无效')
    root=root_of(data_dir);scope=scope_of(scope)
    with publication_gate(root,scope),_write(root) as (db,existed):
        version=_expect(db,scope,revision)
        row=db.execute('SELECT * FROM memory_candidates WHERE id=? AND scope=?',(identity,scope)).fetchone()
        if row is None:raise MemoryError('候选不存在或属于其他账户')
        if row['status']!='pending':raise MemoryConflict('候选不是待审核状态，不能重复发布')
        proposal=json.loads(row['payload'])
        if digest(canonical({'scope':scope,**proposal}))[:24]!=identity:raise MemoryError('Candidate integrity check failed')
        action=proposal['action'];payload=deepcopy(version['payload'])
        rules=payload['rules'];target=next((r for r in rules if r['id']==proposal.get('target_rule_id')),None)
        verified=set(trade_ids);evidence_rows=_evidence(root,scope);available={r['id'] for r in evidence_rows}
        if not verified<=available:raise MemoryError('证据必须是当前账户可核验的已平仓记录')
        if action in ('ADD','REVISE'):
            if evidence_hash!=digest(canonical(evidence_rows)):raise MemoryConflict('Closed-trade evidence changed; refresh before publication')
            if lint(proposal['text']):raise MemoryError('候选触及记忆安全约束，不能发布')
            if len(verified)<2:raise MemoryError('新经验至少需要两条不同的已平仓证据；样本数量本身不代表收益已验证')
            if any(r.get('enabled',True) and r['text']==proposal['text'] for r in rules):raise MemoryConflict('同样的启用规则已存在，不创建虚假新版本')
            if action=='REVISE' and (not target or not target.get('enabled',True)):raise MemoryConflict('待修订规则不存在或已停用')
            if target:target['enabled']=False
            rules.append({'id':'rule_'+identity,'text':proposal['text'],'enabled':True,'source':proposal['source'],'supporting_trade_ids':sorted(verified),'reviewer':actor,'review_note':note.strip(),'reviewed_at':now()})
        elif action=='DEACTIVATE':
            if not target or not target.get('enabled',True):raise MemoryConflict('待停用规则不存在或已停用')
            target['enabled']=False
        elif action=='CLEAR_LEGACY':
            if not payload.get('legacy_context'):raise MemoryConflict('没有可移除的兼容上下文')
            payload['legacy_context']=''
        payload=_render(payload);payload['effective_updated_at']=now()
        created=_insert_version(db,scope,payload,actor,note.strip())
        db.execute('UPDATE memory_meta SET active_version=?,revision=revision+1 WHERE scope=?',(created,scope))
        review={'reviewer':actor,'note':note.strip(),'verified_trade_ids':sorted(verified),'at':now(),'base_version':version['id'],'evidence_snapshot':[r for r in evidence_rows if r['id'] in verified]}
        db.execute("UPDATE memory_candidates SET status='published',review=?,published_version=? WHERE id=?",(canonical(review),created,identity))
        _event(db,scope,'published',actor,{'candidate_id':identity,'version':created,'base_version':version['id'],'evidence_snapshot':[r for r in evidence_rows if r['id'] in verified]})
    return {'version':created,'message':'已发布新记忆版本；未触发任何交易。'}


def reject(identity,revision,note,*,data_dir=None,scope=None,actor='administrator'):
    if not isinstance(note,str) or len(note.strip())<4:raise MemoryError('请提供拒绝原因')
    root=root_of(data_dir);scope=scope_of(scope)
    from scripts import trade_lock
    with trade_lock.writer(timeout=1),_write(root) as (db,existed):
        if scope_of()!=scope:raise MemoryConflict('Account changed before candidate rejection')
        _expect(db,scope,revision)
        row=db.execute('SELECT status FROM memory_candidates WHERE id=? AND scope=?',(identity,scope)).fetchone()
        if not row or row['status'] not in ('pending','blocked'):raise MemoryConflict('候选不存在或已处理')
        db.execute("UPDATE memory_candidates SET status='rejected',review=? WHERE id=?",(canonical({'reviewer':actor,'note':note.strip(),'at':now()}),identity))
        db.execute('UPDATE memory_meta SET revision=revision+1 WHERE scope=?',(scope,))
        _event(db,scope,'rejected',actor,{'candidate_id':identity})
    return {'message':'候选已拒绝，运行记忆未改变。'}


def rollback(target_version,revision,note,*,data_dir=None,scope=None,actor='administrator',confirmation=''):
    if confirmation!='ROLLBACK MEMORY' or not isinstance(note,str) or len(note.strip())<10:raise MemoryError('回滚需要明确确认和至少10字说明')
    root=root_of(data_dir);scope=scope_of(scope)
    with publication_gate(root,scope),_write(root) as (db,existed):
        current=_expect(db,scope,revision);target=_version(db,scope,target_version)
        if current['prompt_hash']==target['prompt_hash']:return {'version':current['id'],'no_change':True,'message':'有效内容相同，没有创建新版本。'}
        payload=deepcopy(target['payload']);payload['effective_updated_at']=now()
        created=_insert_version(db,scope,payload,actor,'回滚至版本 '+str(target_version)+'：'+note.strip())
        db.execute('UPDATE memory_meta SET active_version=?,revision=revision+1 WHERE scope=?',(created,scope))
        _event(db,scope,'rolled_back',actor,{'version':created,'source_version':target_version,'previous_version':current['id']})
    return {'version':created,'message':'已发布所选历史内容的新版本，未恢复任何旧业务数据。'}


def version_detail(version,data_dir=None,scope=None):
    root=root_of(data_dir);scope=scope_of(scope);path=root/'memory_registry.db'
    if not path.exists():raise MemoryError('No published memory versions')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            value=_version(db,scope,version)
            return {'id':value['id'],'created_at':value['created_at'],'actor':value['actor'],'reason':value['reason'],
                    'prompt_hash':value['prompt_hash'],'content':value['payload']['content'],'rules':value['payload']['rules']}
    except sqlite3.Error:raise MemoryError('Memory version unavailable') from None
