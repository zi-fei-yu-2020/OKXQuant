#!/usr/bin/env python3
"""Causal entry-mechanism probes, NEVER production LLM signals or order authority.

Definitions are fixed before reading outcomes. Compare 15m/5m breakout triggers
on the same hourly frozen setups, and a separate confirmed-pullback hypothesis.
No model calls, exchange writes, automatic promotion, or parameter fitting.
"""
from __future__ import annotations
import argparse
from bisect import bisect_right
from dataclasses import asdict, replace
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.backtest_engine import BacktestEngine
from scripts.risk_policy import Policy

SPEC = {'version':'entry-probe-v1','ema_period':16,'atr_period':14,'channel_15m_bars':12,
        'setup_lifetime_minutes':60,'stop_atr':1.,'target_atr':3.,'pullback_touch_atr':.25,
        'order_lifetime_minutes':30,'training_days':20,'test_days':10,
        'variants':['breakout_15m','breakout_5m','pullback_5m'],
        'parameter_search':False,'production_llm_replay':False,'llm_score_gate':'not_applicable_to_mechanical_probe','auto_promote':False}


def resample(rows, minutes):
    width=minutes*60_000;expected=minutes//5;groups={};result=[]
    for row in rows:
        start=row['ts_ms']-300_000;bucket=start//width*width
        groups.setdefault(bucket,[]).append(row)
    for start,group in sorted(groups.items()):
        if len(group)!=expected or [r['ts_ms'] for r in group]!=list(range(start+300_000,start+width+1,300_000)):continue
        result.append({'ts_ms':start+width,'open':group[0]['open'],'high':max(r['high'] for r in group),
                       'low':min(r['low'] for r in group),'close':group[-1]['close'],'volume':sum(r['volume'] for r in group)})
    return result


def features(rows):
    ema=None;previous_close=None;trs=[];out=[]
    alpha=2/(SPEC['ema_period']+1)
    for i,row in enumerate(rows):
        previous_ema=ema
        ema=row['close'] if ema is None else alpha*row['close']+(1-alpha)*ema
        tr=row['high']-row['low'] if previous_close is None else max(row['high']-row['low'],abs(row['high']-previous_close),abs(row['low']-previous_close))
        trs.append(tr);previous_close=row['close']
        out.append({**row,'ema':ema,'previous_ema':previous_ema,'ready':i>=max(SPEC['ema_period'],SPEC['atr_period']),
                    'atr':sum(trs[-SPEC['atr_period']:])/min(len(trs),SPEC['atr_period'])})
    return out


def round_price(value,tick,up=False):
    tick=Decimal(str(tick));value=Decimal(str(value))
    return float((value/tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)*tick)


def generate(rows,metadata,variant):
    if variant not in SPEC['variants']:raise ValueError('Unknown prespecified variant')
    hourly=features(resample(rows,60));macro=features(resample(rows,240));quarter=resample(rows,15)
    ht=[r['ts_ms'] for r in hourly];mt=[r['ts_ms'] for r in macro];qt=[r['ts_ms'] for r in quarter]
    signals=[];setups=[];setup=None;previous_5=None;consumed=set();counts={'setups':0,'triggers':0,'invalidated_bias':0,'expired':0}
    def bias(h,m):
        if h['close']>h['ema'] and m['close']>m['ema'] and h['ema']>h['previous_ema'] and m['ema']>m['previous_ema']:return 1
        if h['close']<h['ema'] and m['close']<m['ema'] and h['ema']<h['previous_ema'] and m['ema']<m['previous_ema']:return -1
        return 0
    for row in rows:
        at=row['ts_ms'];hi=bisect_right(ht,at)-1;mi=bisect_right(mt,at)-1;qi=bisect_right(qt,at)-1
        if hi<0 or mi<0 or qi<SPEC['channel_15m_bars']-1:previous_5=row;continue
        h,m=hourly[hi],macro[mi]
        if not h['ready'] or not m['ready']:previous_5=row;continue
        direction=bias(h,m)
        if setup and at>setup['expires_at']:
            counts['expired']+=1;setup=None
        if setup and direction!=setup['direction']:
            counts['invalidated_bias']+=1;setup=None
        if setup and at>setup['created_at'] and setup['id'] not in consumed:
            d=setup['direction'];level=setup['level'];hit=False
            if variant=='breakout_15m':
                if at%900_000==0 and qi>0:
                    hit=quarter[qi-1]['close']<=level<row['close'] if d==1 else quarter[qi-1]['close']>=level>row['close']
            elif variant=='breakout_5m' and previous_5:
                hit=previous_5['close']<=level<row['close'] if d==1 else previous_5['close']>=level>row['close']
            elif variant=='pullback_5m':
                touch=SPEC['pullback_touch_atr']*setup['atr']
                hit=(row['low']<=level+touch and row['close']>level and row['close']>row['open']) if d==1 else (row['high']>=level-touch and row['close']<level and row['close']<row['open'])
            if hit:
                tick=metadata['tickSz'];entry=round_price(row['close'],tick,up=d==1)
                stop=round_price(setup['stop'],tick,up=d==-1);target=round_price(setup['target'],tick,up=d==-1)
                consumed.add(setup['id']);counts['triggers']+=1
                signals.append({'timestamp':row['timestamp'],'action':'BUY_LONG' if d==1 else 'SELL_SHORT',
                    'score_semantics':'No LLM score; only the prespecified mechanical rules are being tested',
                    'entry_price':entry,'stop_loss_price':stop,'take_profit_price':target,'atr':setup['atr'],
                    'decision_id':setup['id'],'setup_created_at_ms':setup['created_at'],'features_as_of_ms':at,
                    'generated_at_ms':at,'trigger_delay_ms':at-setup['created_at'],'model_cost_usdt':0,
                    'provenance':'deterministic_mechanism_probe_not_production_authority'})
        # At the hourly boundary first process the old setup with the newly closed bar,
        # then freeze a new setup for subsequent bars. Never trigger on its creation bar.
        if at%3_600_000==0 and direction and h['atr']>0:
            window=quarter[qi-SPEC['channel_15m_bars']+1:qi+1]
            level=h['ema'] if variant=='pullback_5m' else max(r['high'] for r in window) if direction==1 else min(r['low'] for r in window)
            setup={'id':f"{row['symbol']}:{at}:{direction}:{'pullback' if variant=='pullback_5m' else 'breakout'}",
                   'created_at':at,'expires_at':at+SPEC['setup_lifetime_minutes']*60_000,'direction':direction,'level':level,
                   'atr':h['atr'],'stop':level-direction*SPEC['stop_atr']*h['atr'],'target':level+direction*SPEC['target_atr']*h['atr']}
            setups.append(dict(setup));counts['setups']+=1
        previous_5=row
    return {'signals':signals,'setups':setups,'counts':counts}


def prepare_series(dataset):
    result={}
    for inst,rows in dataset['series'].items():
        # Capture attaches event t to the bar ending t. Replay charges funding at
        # the next bar opening t, before any new fills at that boundary.
        result[inst]=[]
        for i,row in enumerate(rows):
            value=dict(row)
            if dataset['funding_complete']:value['funding_rate']=rows[i-1].get('funding_rate',0.) if i else 0.
            result[inst].append(value)
    return result


def compact(summary):
    value=asdict(summary)
    for key in ('equity_curve','trades','recent_trades','open_positions','pending_orders'):value.pop(key,None)
    value['open_position_count']=len(summary.open_positions);value['pending_order_count']=len(summary.pending_orders)
    return value


def evaluate(dataset,*,capital=5000.,leverage=3.,policy=None,max_positions=None,pool_profile=False):
    from scripts.research_data import digest
    expected_hash=digest({k:dataset[k] for k in ('series','metadata','start_ms','end_ms')})
    if expected_hash!=dataset.get('capture_hash'):raise ValueError('Research capture hash mismatch')
    for rows in dataset['series'].values():
        if [r['ts_ms'] for r in rows]!=list(range(dataset['start_ms']+300000,dataset['end_ms']+1,300000)) or any(r.get('confirm') is not True for r in rows):raise ValueError('Incomplete/unconfirmed research window')
    policy=policy or Policy();series=prepare_series(dataset);start=dataset['evaluation_start_ms'];end=dataset['end_ms']
    if end-start!=30*86400000:raise ValueError('This preregistered study requires exactly 30 evaluation days')
    split=start+SPEC['training_days']*86400000
    from scripts.capital_pool import Config
    pool=Config() if pool_profile else None
    if pool:max_positions=pool.max_active_instruments
    code_hash=hashlib.sha256(''.join((ROOT/'scripts'/name).read_text(encoding='utf8') for name in ('entry_research.py','backtest_engine.py','risk_policy.py','capital_pool.py')).encode()).hexdigest()
    report={'implementation_hash':code_hash,'python_version':sys.version.split()[0],'spec':SPEC,'capture_hash':dataset['capture_hash'],'capital':capital,'leverage':leverage,'risk_policy':asdict(policy),
            'max_positions':max_positions,'pool_profile':pool_profile,'windows':{'train':[start,split],'test':[split,end]},'variants':{},
            'limitations':dataset.get('limitations',[])+['Only 10 held-out days; not sufficient to establish strategy advantage',
                'Each evaluation window starts flat; positions and pending orders are not carried between train/test',
                'No historical LLM decisions exist for most of this window; these are mechanical probes, not a live-strategy backtest',
                'Funding events charged at bar open before new fills; end boundary excluded',
                'Funding notional uses traded bar open, not archived exchange settlement mark price',
                'Alphabetical instrument arbitration is fixed across variants; no optimized allocation',
                'Not a replay of production custom interceptors, stop cooldowns, time exits or LLM position management',
                'No liquidation model; leverage comparison is not approval for live use'],
            'auto_promote':False,'executed_orders':0}
    generated={}
    for variant in SPEC['variants']:
        gen={inst:generate(rows,dataset['metadata'][inst],variant) for inst,rows in series.items()};generated[variant]=gen
        out={'generation':{k:v['counts'] for k,v in gen.items()}}
        for name,a,b in [('train',start,split),('test',split,end)]:
            window={inst:[r for r in rows if a<r['ts_ms']<=b] for inst,rows in series.items()}
            signals={inst:[s for s in gen[inst]['signals'] if a<s['features_as_of_ms']<=b] for inst in window}
            engine=BacktestEngine(initial_capital=capital,bar='5m',policy=policy,risk_per_trade_pct=policy.per_trade_equity_pct,min_confidence_gate=0,
                taker_fee=policy.taker_fee,slippage=policy.slippage,order_ttl_bars=6,metadata=dataset['metadata'],leverage=leverage,max_positions=max_positions,
                capital_ceiling=capital if pool else None,asset_margin_fraction=pool.single_asset_margin_fraction if pool else None,total_margin_fraction=pool.total_margin_fraction if pool else None)
            result=engine.run_portfolio(window,signals)
            out[name]=compact(result)
        report['variants'][variant]=out
    early=[]
    for inst in series:
        a={s['decision_id']:s for s in generated['breakout_15m'][inst]['signals'] if split<s['features_as_of_ms']<=end}
        b={s['decision_id']:s for s in generated['breakout_5m'][inst]['signals'] if split<s['features_as_of_ms']<=end}
        for key in a.keys()&b.keys():early.append((a[key]['features_as_of_ms']-b[key]['features_as_of_ms'])/60000)
    report['matched_breakout_timing']={'matched_setups':len(early),'mean_minutes_earlier_for_5m':sum(early)/len(early) if early else None,
                                        'not_a_profit_metric':True}
    report['promotion_gate']={'approved':False,'negative_holdout_variants':[k for k,v in report['variants'].items() if v['test']['total_return_pct']<=0],
        'reason':'Insufficient held-out duration and no independent forward validation; negative held-out variants cannot be promoted'}
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True);p.add_argument('--output',required=True)
    p.add_argument('--capital',type=float,default=5000);p.add_argument('--leverage',type=float,default=3)
    args=p.parse_args();dataset=json.loads(Path(args.input).read_text(encoding='utf8'))
    report=evaluate(dataset,capital=args.capital,leverage=args.leverage)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    print(json.dumps({'output':str(path),'promotion_gate':report['promotion_gate'],'executed_orders':0},ensure_ascii=False))

if __name__=='__main__':main()
