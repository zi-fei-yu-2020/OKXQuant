#!/usr/bin/env python3
"""Offline paired, chronological ablation evaluation. Never enables a trading variant."""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import random
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.backtest_engine import BacktestEngine
from scripts.risk_policy import Policy, number, linear_metadata
from scripts.signal_data import bar_seconds

FEATURES={'llm','calculus','microstructure','memory'}

def paired_block_interval(a,b,*,seed=20260906,iterations=1000,block=24,alpha=.05):
    if len(a)!=len(b) or len(a)<2*block: return None
    differences=[number(x)-number(y) for x,y in zip(a,b)]
    rng=random.Random(seed); means=[];n=len(differences)
    for _ in range(iterations):
        sample=[]
        while len(sample)<n:
            start=rng.randrange(n)
            sample.extend(differences[(start+j)%n] for j in range(block))
        means.append(sum(sample[:n])/n)
    means.sort()
    return {'mean_excess_bar_return':sum(differences)/n,'lower':means[int(iterations*alpha/2)],
            'upper':means[min(iterations-1,int(iterations*(1-alpha/2)))],'block_bars':block,'iterations':iterations,
            'family_adjusted_alpha':alpha,'seed':seed}


def _returns(summary,initial):
    values=[initial]+[r['equity'] for r in summary['equity_curve']]
    return [b/a-1 for a,b in zip(values,values[1:]) if a>0]


def validate_dataset(dataset):
    if dataset.get('schema')!=1:raise ValueError('Research dataset schema must be 1')
    series=dataset.get('candles')
    variants=dataset.get('variants')
    if not isinstance(series,dict) or not series or not isinstance(variants,list) or len(variants)<2:
        raise ValueError('Need aligned candles plus baseline and at least one recorded variant')
    if len({v['id'] for v in variants})!=len(variants):raise ValueError('Duplicate variant identity')
    if dataset.get('baseline') not in {v['id'] for v in variants}:raise ValueError('Baseline missing')
    # Validate the full clock before slicing. Otherwise a longer asset history or
    # malformed training prefix can silently disappear when n=min(lengths).
    width = bar_seconds(dataset.get('bar', '1H')) * 1000
    clocks = []
    for inst, rows in series.items():
        if not isinstance(rows, list) or not rows or any(not isinstance(r, dict) for r in rows):
            raise ValueError('Research candle series must be nonempty lists')
        times = [r.get('timestamp') for r in rows]
        if any(not isinstance(t, str) or not t for t in times) or len(set(times)) != len(times):
            raise ValueError('Research candle identities must be unique')
        stamps = [number(r.get('ts_ms'), positive=True) for r in rows]
        if any(b-a != width for a,b in zip(stamps, stamps[1:])):
            raise ValueError('Research clock must be ordered and gap-free')
        clocks.append((times, stamps))
    if any(clock != clocks[0] for clock in clocks[1:]):
        raise ValueError('All research instruments must use the complete same clock')
    by_inst={inst:{r['timestamp']:r for r in rows} for inst,rows in series.items()}
    for variant in variants:
        if set(variant.get('features',[]))-FEATURES:raise ValueError('Unknown feature flag')
        if not variant.get('strategy_hash'):raise ValueError('Frozen strategy artifact hash required')
        if 'memory' in variant.get('features',[]) and not variant.get('memory_hash'):raise ValueError('Frozen memory revision required')
        if 'llm' in variant.get('features',[]) and not (variant.get('model') and variant.get('prompt_hash')):
            raise ValueError('LLM comparison requires recorded model/prompt provenance')
        if set(variant.get('signals',{}))!=set(series):raise ValueError('Every variant must cover the same instrument universe, including explicit empty lists')
        for inst,signals in variant['signals'].items():
            seen=set()
            for signal in signals:
                stamp=signal['timestamp']
                if stamp in seen:raise ValueError('Duplicate variant signal time')
                seen.add(stamp)
                candle=by_inst[inst].get(stamp)
                if not candle:raise ValueError('Signal outside candle observation clock')
                # ts_ms is the close/decision timestamp, not the bar open.
                cutoff=number(candle.get('ts_ms'),positive=True)
                generated=number(signal.get('generated_at_ms'),positive=True)
                feature_at=number(signal.get('features_as_of_ms'),positive=True)
                if feature_at>generated or generated>cutoff:raise ValueError('Future/post-hoc signal cannot be used for forward ablation')
    return series,variants


def evaluate(dataset,*,minimum_test_trades=50,minimum_test_days=30,seed=20260906):
    series,variants=validate_dataset(dataset)
    bar=dataset.get('bar','1H');seconds=bar_seconds(bar)
    n=min(len(rows) for rows in series.values())
    baseline=dataset['baseline']; initial=number(dataset.get('initial_capital',10000),positive=True)
    policy=Policy(**dataset.get('risk_policy',{}))
    digest=hashlib.sha256(json.dumps(dataset,sort_keys=True,allow_nan=False).encode()).hexdigest()
    evaluation_config={'minimum_test_trades':minimum_test_trades,'minimum_test_days':minimum_test_days,'seed':seed}
    source_hash=hashlib.sha256(b''.join((ROOT/'scripts'/name).read_bytes() for name in ['research_lab.py','backtest_engine.py','risk_policy.py'])).hexdigest()
    run_hash=hashlib.sha256((digest+source_hash+json.dumps(evaluation_config,sort_keys=True)).encode()).hexdigest()
    split_train=int(n*.5);split_test=int(n*.75)
    report={'schema':1,'input_hash':digest,'run_hash':run_hash,'source_hash':source_hash,'evaluation_config':evaluation_config,'baseline':baseline,'bar':bar,'status':'insufficient_evidence',
        'auto_enable':False,'winner':None,'risk_policy':asdict(policy),'variants':{},'comparisons':[],
        'split':{'training':[0,split_train],'validation':[split_train,split_test],'untouched_test':[split_test,n]},
        'protocol':'frozen candidates; shared execution/risk policy; chronological holdout; paired moving-block bootstrap',
        'limitations':['No automatic strategy promotion','Repeated study selection still needs a new untouched time window',
                      'Forward paper fills are not actual exchange queue fills','No historical LLM reconstruction from future outcomes']}
    if n-split_test<max(48,math.ceil(minimum_test_days*86400/seconds)):
        report['reason']='Untouched test window is too short';return report
    real_data=all(r.get('source') in {'okx_closed','archived_exchange'} for rows in series.values() for r in rows)
    full_funding=all('funding_rate' in r for rows in series.values() for r in rows)
    model_costs_complete=all(s.get('model_cost_usdt') is not None for v in variants if 'llm' in v.get('features',[]) for rows in v['signals'].values() for s in rows)
    full_funding=full_funding and model_costs_complete
    report['model_costs_complete']=model_costs_complete
    metadata_complete=True
    try:
        for inst in series:
            raw=dataset.get('metadata',{})[inst]
            if raw.get('instId')!=inst:raise ValueError('Metadata identity mismatch')
            linear_metadata(raw)
    except (KeyError,ValueError,TypeError):metadata_complete=False
    real_data=real_data and metadata_complete
    # Dataset must attest actual decision-time archival, not merely supply backdated timestamps.
    forward=dataset.get('collection_complete') is True and all(v.get('provenance')=='forward_archived' for v in variants)
    for variant in variants:
        out={}
        for name,start,end in [('validation',split_train,split_test),('test',split_test,n)]:
            subset={k:v[start:end] for k,v in series.items()}
            times={r['timestamp'] for r in next(iter(subset.values()))}
            signals={k:[s for s in variant['signals'][k] if s['timestamp'] in times] for k in series}
            engine=BacktestEngine(initial_capital=initial,bar=bar,policy=policy,risk_per_trade_pct=policy.per_trade_equity_pct,
                maker_fee=dataset.get('maker_fee',.0002),taker_fee=policy.taker_fee,slippage=policy.slippage,
                metadata=dataset.get('metadata',{}))
            out[name]=asdict(engine.run_portfolio(subset,signals))
        out['features']=variant.get('features',[]);out['strategy_hash']=variant['strategy_hash']
        report['variants'][variant['id']]=out
    base=report['variants'][baseline]['test'];alpha=.05/max(1,len(variants)-1)
    eligible=[]
    for variant in variants:
        if variant['id']==baseline:continue
        test=report['variants'][variant['id']]['test']
        interval=paired_block_interval(_returns(test,initial),_returns(base,initial),seed=seed,
            block=max(2,round(86400/seconds)),alpha=alpha)
        enough=base['total_trades']>=minimum_test_trades and test['total_trades']>=minimum_test_trades
        passes=bool(real_data and full_funding and forward and enough and interval and interval['lower']>0 and test['max_drawdown_pct']<=base['max_drawdown_pct'])
        report['comparisons'].append({'variant':variant['id'],'vs':baseline,'interval':interval,
            'minimum_sample_met':enough,'cost_and_provenance_complete':real_data and full_funding and forward,
            'status':'candidate_for_manual_review' if passes else 'not_established'})
        if passes:eligible.append(variant['id'])
    report['status']='candidates_require_manual_review' if eligible else 'no_validated_incremental_advantage'
    if not(real_data and full_funding and forward) or any(not c['minimum_sample_met'] for c in report['comparisons']):
        report['status']='insufficient_evidence'
    report['candidate_ids']=eligible
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',required=True)
    parser.add_argument('--output',default='data/research_report.json');parser.add_argument('--seed',type=int,default=20260906)
    args=parser.parse_args();dataset=json.loads(Path(args.input).read_text(encoding='utf-8'))
    from scripts.strategy_evidence import _scrub
    if _scrub(dataset)!=dataset:raise ValueError('Credentials must not be included in research inputs')
    report=evaluate(dataset,seed=args.seed)
    archive=ROOT/'data'/'research_runs'/report['run_hash']
    archive.mkdir(parents=True,exist_ok=True,mode=0o700)
    import os
    for name,value in [('dataset.json',dataset),('report.json',report)]:
        target=archive/name
        content=json.dumps(value,sort_keys=True,ensure_ascii=False,indent=2,allow_nan=False)
        if target.exists():
            if target.read_text(encoding='utf-8')!=content:raise ValueError('Immutable research archive collision')
        else:
            fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(content)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print(json.dumps({'status':report['status'],'input_hash':report['input_hash'],'auto_enable':False}))

if __name__=='__main__':main()
