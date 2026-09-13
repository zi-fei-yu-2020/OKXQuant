"""Auditable WAIT decisions. No exchange calls, model calls, or trading writes.

A verified audit is not proof of economic edge or its absence. Invalid audits never
become entry orders. Only the live brain commits cross-cycle state; previews read.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

VERSION = 'wait-evidence-v1'
DATA = Path(__file__).resolve().parents[1] / 'data'
ALERT_ROUNDS = 8
REVIEW_MAX_AGE = 3600
CODES = ('no_setup', 'confirmation_pending', 'data_missing', 'position_constraint', 'macro_constraint', 'net_rr_below_minimum')
OPS = ('gt', 'gte', 'lt', 'lte', 'eq', 'ne', 'available')

INSTRUCTIONS = """【WAIT 同等审计与跨轮复查：wait-evidence-v1】
WAIT 不等于跳过研究。每个 WAIT 必须提供 wait_audit，分别审查 long 和 short，不设交易配额。
每个方向必须包含 code、reason、evidence 和 reconsider。code 只能是 no_setup（尚未建立候选）、confirmation_pending（仍缺确认）、data_missing、position_constraint、macro_constraint 或 net_rr_below_minimum。输入 wait_constraints 按标的和方向给出 allowed_codes，不得使用该方向不允许的类别。
evidence 与开仓引用格式相同：[{"ref":"/structure_1h","value":"facts原值","interpretation":"该观测为何阻碍这个方向"}]，至少1条。data_missing 可没有 evidence，但 missing_refs 必须列出实际缺失的已知字段，不能把已有数据说成缺失。
reconsider={"conditions":[{"ref":"/calculus/timeframes/15M/velocity","op":"gt","value":0}],"reason":"动量转正后重新研究"}。conditions 为1~3项且全部满足才触发，op 为 gt/gte/lt/lte/eq/ne/available。数值或字符串阈值类型必须匹配 facts；available 只用于 missing_refs 且 value=true。新条件不能已经全部满足，否则不得以此继续等待。触发只授权重新研究，不授权下单。
position_constraint 只适用于真实非零反向持仓或同向浮亏，必须引用 /position/ 字段；本轮未提供活动仓位时禁止使用。4H看多不等于持有多单，4H看空不等于持有空单。
macro_constraint 专门表示现有4H方向规则：4H_MACRO_BULL限制做空、4H_MACRO_BEAR限制做多，必须引用本标的 /macro_4h 原值。4H_MACRO_RANGE不能使用此类别；不得把宏观方向、行情震荡或一般风险写成position_constraint。
未触发形态用no_setup或confirmation_pending；只有提供可计算价格方案才使用net_rr_below_minimum。已有程序检查中的geometry可作为待核验计算输入，不允许为了凑字段自造价格。
声称“净盈亏比不足”必须使用 net_rr_below_minimum，并给出 geometry={entry_price,stop_loss_price,take_profit_price,evidence}；程序按同一费用与滑点政策重算。该计算只否定这个具体方案，不能证明全部价格位置都无机会。尚无可计算方案用 no_setup，不得冒充已算过盈亏比。
普通限价单允许提出当前已有证据支持的回踩买入/反弹卖出候选；触价不会等待额外指标确认。依赖未来止跌、动量转向或突破确认的假设仍必须等待。
输入 previous_wait_reviews 给出上一轮条件的程序核验结果。如 required=true 或修改了上轮任何重审条件，继续 WAIT 必须提供 wait_audit.previous_review={review_id,reason,evidence}，引用至少1项 changed_refs 内的新观测解释为何重新审查后仍不成立；不得默默移动原门槛。previous_review 中 review_id 必须精确匹配。
审计缺失或错误将标记 decision_incomplete，不作为正常等待；但绝不因此转成开仓。独立持仓保护始终运行。
"""

def position_constraint_allowed(catalog, direction):
    side = catalog.get('/position/side', {}).get('value')
    size = catalog.get('/position/pos', catalog.get('/position/size', {})).get('value')
    pnl = catalog.get('/position/upl', {}).get('value')
    return (side in ('long','short') and isinstance(size,(int,float)) and not isinstance(size,bool)
            and math.isfinite(size) and abs(size)>0
            and (side != direction or isinstance(pnl,(int,float)) and not isinstance(pnl,bool) and math.isfinite(pnl) and pnl<0))


def macro_constraint_allowed(catalog, direction):
    macro = catalog.get('/macro_4h', {}).get('value')
    if not isinstance(macro,str):return False
    bull = bool(re.search(r'\b4H_MACRO_BULL\b',macro))
    bear = bool(re.search(r'\b4H_MACRO_BEAR\b',macro))
    return bull != bear and (bull and direction=='short' or bear and direction=='long')


def constraints(catalog, prior=None):
    prior=prior or {}
    result={'position_side':catalog.get('/position/side',{}).get('value'),
            'position_size':catalog.get('/position/pos',catalog.get('/position/size',{})).get('value'),
            'position_note':'没有可核验的非零仓位时，不得以持仓约束等待',
            'previous_review_required':bool(prior.get('required')),
            'previous_review_id':prior.get('review_id'), 'changed_refs':prior.get('changed_refs',[]),
            'previous_conditions':prior.get('previous_conditions',{}),
            'review_note':'required为true或修改了原重审条件时，必须提供精确review_id及changed_refs证据'}
    for direction in ('long','short'):
        allowed=[code for code in CODES if code not in ('position_constraint','macro_constraint')]
        if position_constraint_allowed(catalog,direction):allowed.append('position_constraint')
        if macro_constraint_allowed(catalog,direction):allowed.append('macro_constraint')
        result[direction]={'allowed_codes':allowed,'macro_direction_blocked':macro_constraint_allowed(catalog,direction)}
    return result


def schema():
    # Evidence schema is deliberately independent to avoid recursive output_schema calls.
    evidence = {'type':'array','maxItems':12,'items':{'type':'object','required':['ref','value','interpretation'],
        'properties':{'ref':{'type':'string'},'value':{'type':['number','string']},'interpretation':{'type':'string'}}}}
    condition = {'type':'object','required':['ref','op','value'],'properties':{
        'ref':{'type':'string'},'op':{'enum':list(OPS)},'value':{'type':['number','string','boolean']}}}
    side = {'type':'object','required':['code','reason','evidence','reconsider'],'properties':{
        'code':{'enum':list(CODES)},'reason':{'type':'string'},'evidence':evidence,
        'missing_refs':{'type':'array','items':{'type':'string'}},
        'geometry':{'type':'object','required':['entry_price','stop_loss_price','take_profit_price','evidence'],
            'properties':{'entry_price':{'type':'number'},'stop_loss_price':{'type':'number'},'take_profit_price':{'type':'number'},'evidence':evidence}},
        'reconsider':{'type':'object','required':['conditions','reason'],'properties':{
            'conditions':{'type':'array','minItems':1,'maxItems':3,'items':condition},'reason':{'type':'string'}}}}}
    return {'type':'object','required':['version','long','short'],'properties':{
        'version':{'const':VERSION},'long':side,'short':side,
        'previous_review':{'type':'object','required':['review_id','reason','evidence'],
            'properties':{'review_id':{'type':'string'},'reason':{'type':'string'},'evidence':evidence}}}}


def known_ref(ref):
    from scripts.trading_prompt import _SCALARS
    if not isinstance(ref,str): return False
    if ref in {'/'+k for k in _SCALARS}: return True
    return bool(re.fullmatch(r'/calculus/timeframes/(15M|1H|4H)/(velocity|acceleration|jerk|impulse|regime|(?:definite_integrals/(energy_integral|deviation_area_integral|volume_action_integral))|(?:probability_theory/(continuation_prob_pct|breakdown_prob_pct|var_95_pct|cvar_95_pct|skewness|kurtosis)))',ref)
        or re.fullmatch(r'/range/(15M|1H|4H)/(high|low)',ref)
        or re.fullmatch(r'/entry_candles/15M/(last|previous)/(open|high|low|close)',ref))


def evaluate(conditions, catalog):
    """Pure ALL predicate; missing operands are unknown, never a false confirmation."""
    outcomes=[]
    for item in conditions:
        ref=item['ref'];op=item['op'];target=item['value']
        if op=='available': outcomes.append(ref in catalog);continue
        if ref not in catalog: outcomes.append(None);continue
        value=catalog[ref]['value']
        if type(value) is not type(target) and not (isinstance(value,(int,float)) and not isinstance(value,bool) and isinstance(target,(int,float)) and not isinstance(target,bool)):
            outcomes.append(None);continue
        try: outcomes.append({'gt':lambda:value>target,'gte':lambda:value>=target,'lt':lambda:value<target,
                              'lte':lambda:value<=target,'eq':lambda:value==target,'ne':lambda:value!=target}[op]())
        except (KeyError,TypeError): outcomes.append(None)
    return 'unknown' if any(v is None for v in outcomes) else 'met' if outcomes and all(outcomes) else 'not_met'


def validate(raw, catalog, *, prior=None, policy=None):
    from scripts.trading_prompt import ContractError, check_refs, text, numeric
    from scripts.risk_policy import Policy
    policy=policy or vars(Policy())
    current_direction = None
    def require(ok, message):
        if not ok: raise ContractError((('做多' if current_direction=='long' else '做空')+'：' if current_direction else '')+message)
    require(isinstance(raw,dict) and raw.get('version')==VERSION,'WAIT审计版本缺失')
    audited={k:copy.deepcopy(raw[k]) for k in ('version','long','short','previous_review') if k in raw}
    for direction in ('long','short'):
        current_direction=direction
        item=audited.get(direction)
        require(isinstance(item,dict),'WAIT缺少多空双向审查')
        item.pop('net_rr_check',None)  # Computed results cannot be supplied by the model.
        code=item.get('code');reason=item.get('reason');evidence=item.get('evidence')
        require(code in CODES and text(reason),'WAIT阻碍类别或理由缺失')
        missing=item.get('missing_refs') or []
        if code=='data_missing':
            require(isinstance(missing,list) and 1<=len(missing)<=6 and all(known_ref(r) and r not in catalog for r in missing),'WAIT声称缺失的数据实际存在或字段未知')
            if evidence: check_refs(evidence,catalog)
            else: require(evidence==[],'WAIT缺失审查需要显式空证据列表')
        else: check_refs(evidence,catalog)
        if code=='position_constraint':
            require(position_constraint_allowed(catalog,direction),'WAIT持仓约束与实际仓位不符：必须有非零反向仓位或同向浮亏；宏观限制请使用macro_constraint')
            require(any(e['ref'].startswith('/position/') for e in evidence),'WAIT持仓约束缺少持仓引用')
        if code=='macro_constraint':
            require(macro_constraint_allowed(catalog,direction),'宏观限制与该方向不符；区间或顺宏观方向不能使用macro_constraint')
            require(any(e['ref']=='/macro_4h' for e in evidence),'宏观限制必须引用本标的 /macro_4h 事实')
        rr_claim=re.search(r'(?:盈亏比|风险收益比|净\s*R\s*[:/]?\s*R).{0,16}(?:不足|低于|不达|无法达到|不满足|不够|小于|<)',str(reason),re.I)
        require(not rr_claim or code=='net_rr_below_minimum','声称盈亏比不足必须给出可计算方案')
        if code=='net_rr_below_minimum':
            geometry=item.get('geometry');require(isinstance(geometry,dict),'WAIT缺少盈亏比计算方案')
            entry,stop,target=[numeric(geometry.get(k)) for k in ('entry_price','stop_loss_price','take_profit_price')]
            require(0<stop<entry<target if direction=='long' else 0<target<entry<stop,'WAIT计算方案价格几何无效')
            check_refs(geometry.get('evidence'),catalog)
            # Realistic cost: limit entry pays maker fee, OCO stop pays taker fee once, slippage on stop.
            cost=entry*policy['maker_fee']+max(stop,target)*policy['taker_fee']+entry*policy['slippage']
            net_rr=(abs(target-entry)-cost)/(abs(entry-stop)+cost)
            require(math.isfinite(net_rr) and net_rr<policy['minimum_net_rr'],'WAIT声称盈亏比不足但计算已满足门槛')
            item['net_rr_check']={'net_rr':net_rr,'minimum':policy['minimum_net_rr'],'cost_per_unit':cost,'scope':'this_geometry_only'}
        reconsider=item.get('reconsider');require(isinstance(reconsider,dict) and text(reconsider.get('reason')),'WAIT缺少可观测重审理由')
        conditions=reconsider.get('conditions');require(isinstance(conditions,list) and 1<=len(conditions)<=3,'WAIT重审条件数量无效')
        for cond in conditions:
            require(isinstance(cond,dict) and cond.get('op') in OPS and isinstance(cond.get('ref'),str),'WAIT重审条件无效')
            ref=cond['ref'];op=cond['op'];value=cond.get('value')
            if op=='available':require(ref in missing and value is True,'available仅可引用本轮真实缺失字段')
            else:
                require(ref in catalog,'WAIT重审引用缺失')
                actual=catalog[ref]['value']
                if isinstance(actual,(int,float)) and not isinstance(actual,bool):
                    numeric(value)
                    if ref in ('/price','/bidPx','/askPx') or ref.startswith(('/range/','/entry_candles/')):require(float(value)>0,'价格重审阈值必须为正')
                    if ref.endswith(('/velocity','/acceleration','/jerk')):require(-3<=float(value)<=3,'动力学阈值超出指标有效范围')
                    if ref in ('/rsi_1h','/rsi_15m') or ref.endswith('_prob_pct'):require(0<=float(value)<=100,'百分比阈值超出有效范围')
                else: require(op in ('eq','ne') and isinstance(value,str) and 0<len(value)<=160,'WAIT类别条件类型无效')
        require(evaluate(conditions,catalog)=='not_met','WAIT的新重审条件已满足或不可计算')
    current_direction=None
    prior=prior or {}
    require(not prior.get('context_error'),'历史WAIT审计状态损坏，不能假装正常等待')
    shifted=bool(prior.get('previous_conditions')) and any(audited[s]['reconsider']['conditions']!=prior['previous_conditions'][s]['conditions'] for s in ('long','short'))
    if prior.get('required') or shifted or audited.get('previous_review') is not None:
        require(isinstance(prior.get('review_id'),str) and bool(prior['review_id']),'当前没有可核验前轮记录，不能提交previous_review')
        review=audited.get('previous_review')
        require(isinstance(review,dict) and review.get('review_id')==prior.get('review_id') and text(review.get('reason')),'前轮条件已触发或过期，缺少继续等待的复查说明')
        check_refs(review.get('evidence'),catalog)
        require(any(e['ref'] in prior.get('changed_refs',[]) for e in review['evidence']),'继续等待必须引用相对前轮变化的新证据')
    return audited


def _path(scope):
    return DATA/('wait_audit_'+hashlib.sha256(scope.encode()).hexdigest()[:16]+'.json')


def _load(scope):
    path=_path(scope)
    if not path.exists(): return {'scope':scope,'version':VERSION,'items':{},'streak':0}
    raw=json.loads(path.read_text(encoding='utf8'))
    if not isinstance(raw,dict) or raw.get('scope')!=scope or not isinstance(raw.get('items'),dict):raise ValueError('Invalid WAIT audit state')
    if raw.get('version')!=VERSION or not isinstance(raw.get('streak',0),int):raise ValueError('Invalid WAIT audit version')
    for item in raw['items'].values():
        if not isinstance(item,dict):raise ValueError('Invalid WAIT audit item')
        verified=item.get('last_verified')
        if verified:
            if not isinstance(verified,dict) or not isinstance(verified.get('values'),dict) or not isinstance(verified.get('review_id'),str):raise ValueError('Invalid WAIT review')
            if not isinstance(verified.get('at'),(int,float)) or not math.isfinite(verified['at']):raise ValueError('Invalid WAIT time')
            for direction in ('long','short'):
                try:conditions=verified['audit'][direction]['reconsider']['conditions']
                except (KeyError,TypeError):raise ValueError('Invalid WAIT saved conditions') from None
                if not isinstance(conditions,list) or not 1<=len(conditions)<=3 or any(not isinstance(c,dict) or c.get('op') not in OPS or not isinstance(c.get('ref'),str) or not isinstance(c.get('value'),(int,float,str,bool)) for c in conditions):raise ValueError('Invalid WAIT saved conditions')
    return raw


def _atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.wait-audit-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as f:
            json.dump(value,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def prepare(scope, packages, positions=None, *, now=None):
    from scripts.trading_prompt import facts_for
    now=time.time() if now is None else now
    positions={p['instId']:p for p in positions or [] if p.get('instId')}
    try: state=_load(scope)
    except (OSError,ValueError,TypeError):return {p['instId']:{'context_error':True} for p in packages}
    result={}
    for package in packages:
        inst=package['instId'];old=state['items'].get(inst,{});verified=old.get('last_verified')
        if not verified:continue
        catalog=facts_for(package,positions.get(inst));audit=verified['audit']
        checks={side:evaluate(audit[side]['reconsider']['conditions'],catalog) for side in ('long','short')}
        values={k:v['value'] for k,v in catalog.items()}
        changed=[k for k,v in values.items() if verified.get('values',{}).get(k)!=v]
        result[inst]={'review_id':verified['review_id'],'age_seconds':max(0,int(now-verified['at'])),
                      'trigger_checks':checks,'previous_conditions':{s:audit[s]['reconsider'] for s in ('long','short')},
                      'required':any(v in ('met','unknown') for v in checks.values()) or now-verified.get('anchor_at',verified['at'])>=REVIEW_MAX_AGE,
                      'changed_refs':changed}
    return result


def commit(scope, cache, packages, positions=None, *, frame_id, now=None):
    from scripts.trading_prompt import facts_for
    now=time.time() if now is None else now
    state=_load(scope)  # Fail visibly rather than resetting an unreadable audit history.
    if state.get('frame_id')==frame_id:return public_status(scope)
    positions={p['instId']:p for p in positions or [] if p.get('instId')};features={p['instId']:p for p in packages}
    all_wait=bool(cache) and all(r.get('decision',{}).get('action')=='WAIT' for r in cache.values())
    state['streak']=state.get('streak',0)+1 if all_wait else 0
    state['since']=(state.get('since') or now) if all_wait else None
    from scripts.wait_counters import advance
    state['diagnostics']=advance(state.get('diagnostics'),cache,now)
    current={}
    for inst,row in cache.items():
        decision=row['decision'];old=state['items'].get(inst,{})
        audit=decision.get('wait_audit')
        if decision.get('action')=='WAIT' and decision.get('contract_valid') and audit:
            previous=old.get('last_verified',{})
            same=bool(previous) and all(previous['audit'][s]['reconsider']['conditions']==audit[s]['reconsider']['conditions'] for s in ('long','short'))
            anchor=previous.get('anchor_at',previous.get('at',now)) if same and not audit.get('previous_review') else now
            old['last_verified']={'review_id':row.get('decision_id') or hashlib.sha256((str(frame_id)+inst).encode()).hexdigest()[:20],
                                  'at':now,'anchor_at':anchor,'audit':audit,'values':{k:v['value'] for k,v in facts_for(features[inst],positions.get(inst)).items()}}
        elif decision.get('action')!='WAIT':old.pop('last_verified',None)
        old.update(status=decision.get('decision_status','incomplete'),reason=decision.get('summary_reason',''),
                   error=decision.get('validation_reason'),at=now,current_audit=audit,
                   previous_check=decision.get('previous_wait_review',{}),wait_repair=decision.get('wait_repair'))
        current[inst]=old
    state.update(items=current,frame_id=frame_id,updated_at=now)
    _atomic(_path(scope),state)
    return public_status(scope)


def public_status(scope):
    try:state=_load(scope)
    except (OSError,ValueError,TypeError):return {'status':'error','message':'WAIT审计状态不可读','items':[]}
    rows=[]
    for inst,item in state['items'].items():
        rows.append({'instId':inst,'status':item.get('status'),'reason':item.get('reason'),
                     'error':item.get('error'),'audit':item.get('current_audit'),'previous_check':item.get('previous_check',{}),
                     'updated_at':item.get('at'),'wait_repair':item.get('wait_repair')})
    incomplete=sum(r['status']=='incomplete' for r in rows)
    streak=state.get('streak',0)
    return {'status':'incomplete' if incomplete else 'ready' if rows else 'empty','version':VERSION,
            'updated_at':state.get('updated_at'),'no_entry_candidate_streak':streak,'legacy_final_wait_streak':streak,
            'diagnostics':copy.deepcopy(state.get('diagnostics')),'incomplete_count':incomplete,
            'alert':streak>=ALERT_ROUNDS,'alert_after_rounds':ALERT_ROUNDS,'since':state.get('since'),
            'message':'最终WAIT累计包含校验失败；请分别查看无程序草案、模型WAIT与审计异常统计，不触发强制交易' if streak>=ALERT_ROUNDS else '', 'items':rows}
