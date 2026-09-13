"""Read-only macro projection. Dashboard refresh time is not model generation time."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import math
import time
BJ=timezone(timedelta(hours=8))

def epoch(value):
    try:
        number=float(value)
        if not math.isfinite(number):return 0
        return number/1000 if number>1e12 else number
    except (TypeError,ValueError):
        try:return datetime.strptime(str(value)[:19],'%Y-%m-%d %H:%M:%S').replace(tzinfo=BJ).timestamp()
        except (ValueError,TypeError):return 0

def project(decisions,history,*,validation=None,validation_at=0,now=None):
    now=time.time() if now is None else now
    candidates=[]
    for row in (decisions.values() if isinstance(decisions,dict) else []):
        if not isinstance(row,dict):continue
        if not isinstance(row.get('macro_assessment'),str):continue
        text=row['macro_assessment'].strip();at=epoch(row.get('timestamp') or row.get('time_str'))
        if text and 0<at<=now+60:candidates.append((at,1,text,'decision_cache'))
    for row in history if isinstance(history,list) else []:
        if not isinstance(row,dict):continue
        if not isinstance(row.get('macro_assessment'),str):continue
        text=row['macro_assessment'].strip();at=epoch(row.get('time'))
        if text and 0<at<=now+60:candidates.append((at,0,text,'history'))
    chosen=max(candidates,default=None)
    at=chosen[0] if chosen else 0
    result={'text':chosen[2] if chosen else '', 'analyzed_at':datetime.fromtimestamp(at,BJ).strftime('%Y-%m-%d %H:%M:%S') if at else '',
            'source':chosen[3] if chosen else 'none','age_seconds':max(0,int(now-at)) if at else None,
            'status':'ready' if chosen else 'empty','message':''}
    if chosen and now-at>1800:result.update(status='stale',message='上次分析已过期，不能作为当前交易依据')
    status=(validation or {}).get('status')
    if epoch(validation_at)>at and status in {'pending','blocked','unavailable','composition_rejected','rejected'}:
        result['status']='running' if status=='pending' else 'blocked' if status=='blocked' else 'failed'
        result['message']=('新一轮分析中' if status=='pending' else str((validation or {}).get('reason') or '本轮未产生可用模型结果'))+('；展示上次分析' if chosen else '')
    if status=='incomplete' and epoch(validation_at)>=at:
        result.update(status='incomplete',message='本轮决策审计不完整，不能将 WAIT 当作已验证的正常等待')
    if not chosen and not result['message']:result['message']='尚未取得可用模型分析；请查看任务运行状态'
    return result

def fields(data_dir,decisions=None,history=None,now=None,state=None):
    root=Path(data_dir)
    def read(name,default):
        try:return json.loads((root/name).read_text(encoding='utf-8'))
        except (OSError,ValueError):return default
    status_path=root/'trading_output_validation.json'
    status=read(status_path.name,{})
    status_at=status_path.stat().st_mtime if status_path.exists() else 0
    if not status:
        state=read('trading_state.json',{}) if state is None else state
        actions=state.get('executed_actions',[]) if isinstance(state,dict) else []
        failed=next((a for a in actions if isinstance(a,str) and ('AI' in a) and any(word in a for word in ('\u672a\u5c31\u7eea','\u63a8\u7406\u5931\u8d25'))),None)
        if failed:status={'status':'rejected','reason':failed};status_at=epoch(state.get('timestamp'))
    result=project(read('ai_brain_decisions.json',{}) if decisions is None else decisions,
        read('ai_brain_history.json',[]) if history is None else history,validation=status,
        validation_at=status_at,now=now)
    return {'macro_assessment':result['text'],'macro_analysis':result}
