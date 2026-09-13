"""Pure per-cycle reporting. Environment notices are not trade execution actions."""

def clean(value, limit=100):
    return ' '.join(str(value or '').split())[:limit]


def summarize(cache, notices=None, *, unavailable_reason='', circuit_breaker=False):
    rows=[]
    for inst,row in (cache or {}).items():
        decision=row.get('decision') or {}
        action=decision.get('action','WAIT')
        status=decision.get('decision_status') or ('entry_candidate' if action in ('BUY_LONG','SELL_SHORT') else 'incomplete')
        if not decision.get('contract_valid') and status!='execution_rejected':status='incomplete'
        audit=decision.get('wait_audit') or {}
        rows.append({'instId':inst,'name':row.get('name',inst.split('-')[0]),'action':action,'status':status,
                     'reason':clean(decision.get('validation_reason') or decision.get('summary_reason'),240),
                     'long_blocker':clean((audit.get('long') or {}).get('reason'),160),
                     'short_blocker':clean((audit.get('short') or {}).get('reason'),160),
                     'previous_check':decision.get('previous_wait_review',{}),'wait_repair':decision.get('wait_repair'),'entry_plans':decision.get('entry_plans'),
                     'candidate_id':decision.get('candidate_id'),'candidate_reviews':decision.get('candidate_reviews',[])})
    counts={key:sum(r['status']==key for r in rows) for key in ('audited_wait','incomplete','entry_candidate','execution_rejected')}
    counts['program_plans']=sum(len((r.get('entry_plans') or {}).get('plans',[])) for r in rows)
    return {'status':'circuit_breaker' if circuit_breaker else 'unavailable' if not rows else 'incomplete' if counts['incomplete'] else 'reviewed',
            'counts':counts,'evaluated_count':len(rows),'items':rows,'environment_notices':notices or [],
            'unavailable_reason':clean(unavailable_reason,240)}


def format_summary(summary):
    """One-line counts; full reasons remain in summary.items and the audit views."""
    if not summary['items']:
        text='决策不可用：'+clean(summary.get('unavailable_reason') or ('熔断暂停' if summary['status']=='circuit_breaker' else '未取得模型输出'),45)
    else:
        n=summary['counts']
        text=f"审查{summary['evaluated_count']} | 候选{n['entry_candidate']} | WAIT{n['audited_wait']}"
        if n.get('program_plans'):text+=f" | 程序草案{n['program_plans']}"
        for status,label in (('incomplete','不完整'),('execution_rejected','风控拒绝')):
            names=[r['name'] for r in summary['items'] if r['status']==status]
            if names:text+=f" | {label}{len(names)}({','.join(names[:6])})"
    diagnostics=summary.get('wait_diagnostics')
    if diagnostics:
        streaks=diagnostics.get('streaks',{})
        if (streaks.get('no_program_plans') or 0)>=8:text+=f" | 连续{streaks['no_program_plans']}轮无草案"
        if (streaks.get('audit_incomplete') or 0)>0:text+=f" | 审计异常连续{streaks['audit_incomplete']}轮"
        elif (streaks.get('model_all_wait') or 0)>=8 and not streaks.get('no_program_plans'):text+=f" | 模型连续{streaks['model_all_wait']}轮WAIT"
    elif summary.get('wait_alert'):text+=f" | 最终WAIT连续{summary.get('no_entry_candidate_streak',0)}轮（含校验失败）"
    notices=summary.get('environment_notices') or []
    if notices:
        names=[clean(n,100).split('：',1)[0].split(':',1)[0].strip('[] ') for n in notices]
        text+=' | 观察:'+','.join(names[:6])
    return text


def format_actions(actions, *, maximum=3):
    """Compact headlines, not replacements for the durable full execution record."""
    import re
    if not actions:return '无开平仓'
    def headline(action):
        text=clean(action,2000)
        code=re.search(r'(?:HTTP\s+|Code:\s*)([45][0-9]{2,4})',text)
        text=re.sub(r'\s*\(order=[^)]*\)','',text)
        text=re.split(r':\s+|原因[:：]',text,maxsplit=1)[0]
        text=clean(text,85)
        if code and code[1] not in text:text+=' ['+code[1]+']'
        return text
    # Surface failures before ordinary maintenance when the summary must be bounded.
    ranked=sorted(enumerate(actions),key=lambda pair:(0 if any(w in str(pair[1]) for w in ('失败','未确认','无法','未获')) else 1,pair[0]))
    selected=sorted(ranked[:maximum])
    result='；'.join(headline(value) for _,value in selected)
    if len(actions)>maximum:result+=f'；另{len(actions)-maximum}项见执行记录'
    return result


def execution_history(scope, limit=12):
    """Read-only full cycle details for the authenticated administrator view."""
    import json
    import sqlite3
    from scripts import strategy_evidence
    path=strategy_evidence.DB_PATH
    if not path.exists():return []
    try:
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            rows=db.execute("SELECT payload FROM events WHERE scope=? AND kind='execution_cycle' ORDER BY at DESC LIMIT ?",(scope,max(1,min(30,int(limit))))).fetchall()
        return [json.loads(row[0]) for row in rows]
    except (OSError,sqlite3.Error,ValueError):return []
