#!/usr/bin/env python3
"""Opt-in forward ablation collector. LLM calls can cost money; never submits orders.

The collector consumes an archived feature snapshot, not the trading brain cycle.
Each feature variant receives the same initial snapshot. Failed cohorts are retained
as incomplete, never silently turned into WAIT/no-trade performance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import strategy_evidence as evidence, trading_prompt

VARIANTS=[('baseline',[]),('llm',['llm']),('llm_calculus',['llm','calculus']),
          ('llm_calculus_microstructure',['llm','calculus','microstructure']),
          ('llm_calculus_microstructure_memory',['llm','calculus','microstructure','memory'])]
SYSTEM=trading_prompt.BASE_SYSTEM



def baseline(package):
    closes=[float(c[3]) for c in reversed(package.get('recent_1h',[]))]
    result={'instId':package['instId'],'action':'WAIT','confidence':0}
    if len(closes)<8:return result
    price=float(package.get('price') or 0);atr=float(package.get('atr_1h') or package.get('atr') or 0)
    if price<=0 or atr<=0:return result
    fast=sum(closes[-3:])/3;slow=sum(closes[-8:])/8
    direction=1 if closes[-1]>fast>slow else -1 if closes[-1]<fast<slow else 0
    if not direction:return result
    return {**result,'action':'BUY_LONG' if direction==1 else 'SELL_SHORT','confidence':80,
            'entry_price':price,'stop_loss_price':price-direction*atr*2,'take_profit_price':price+direction*atr*6}


def collect(snapshot,call_model,*,model,scope):
    as_of=float(snapshot['as_of_ms'])
    if not 0<=time.time()*1000-as_of<=300000:raise ValueError('Shadow snapshot stale/future; no retrospective backdating')
    packages=snapshot['packages']; insts={p['instId'] for p in packages}
    if len(insts)!=len(packages) or not insts:raise ValueError('Invalid shadow universe')
    memory=snapshot.get('memory',[])
    output=[]; errors=[]
    for identity,features in VARIANTS:
        selected=[]
        for p in packages:
            allowed={'instId','price','recent_1h','recent_4h','atr_1h','atr','structure_1h','macro_4h','data_quality','environment_support'}
            if 'calculus' in features:allowed.add('calculus')
            if 'microstructure' in features:allowed.update({'bidPx','askPx','takerNetUsd','oiUsd','fundingRate','smart_money'})
            selected.append({k:p[k] for k in allowed if k in p})
        request={'as_of_ms':as_of,'packages':selected}
        if 'memory' in features:request['memory']=memory
        bundle=trading_prompt.compose({'id':'shadow-'+identity},request,selected)
        request['compiled_prompt']=bundle.user
        prompt_hash=hashlib.sha256(evidence.canonical({'system':bundle.system,'request':request}).encode()).hexdigest()
        try:
            if not features:
                decisions=[baseline(p) for p in packages]
            else:
                checked=trading_prompt.validate_response(call_model(bundle.system,request),selected)
                decisions=[{'instId':key,**value} for key,value in checked['decisions'].items()]
            if not isinstance(decisions,list) or {d.get('instId') for d in decisions}!=insts or len(decisions)!=len(insts):raise ValueError('Incomplete shadow output')
            evidence.canonical(decisions)  # Reject NaN/Infinity before declaring a cohort complete.
            for d in decisions:
                if d.get('action') not in {'WAIT','BUY_LONG','SELL_SHORT'}:raise ValueError('Invalid shadow action')
            output.append({'id':identity,'features':features,'model':model if features else '',
                'prompt_hash':prompt_hash,'memory_hash':hashlib.sha256(evidence.canonical(memory).encode()).hexdigest() if 'memory' in features else '',
                'strategy_hash':hashlib.sha256(Path(__file__).read_bytes()+Path(trading_prompt.__file__).read_bytes()+(bundle.system+identity).encode()).hexdigest(),
                'decisions':decisions,'features_as_of_ms':as_of,'generated_at_ms':int(time.time()*1000),'provenance':'forward_archived'})
        except Exception as exc:errors.append({'variant':identity,'error':type(exc).__name__})
    finished=int(time.time()*1000)
    result={'schema':1,'as_of_ms':as_of,'available_at_ms':finished,'variants':output,'errors':errors,
            'complete':not errors and finished-as_of<=300000,'executed_orders':0}
    evidence.append(scope,'shadow_cohort',result)
    return result


def dataset_from_cohorts(candles,cohorts,*,bar='1H',metadata=None,cost_ledger=None):
    variants={identity:{'id':identity,'features':features,'signals':{k:[] for k in candles}} for identity,features in VARIANTS}
    accepted=0;seen_times=set();incomplete=0
    rows=next(iter(candles.values())) if candles else []
    for cohort in sorted(cohorts,key=lambda x:x['available_at_ms']):
        if not cohort.get('complete'):
            incomplete+=1;continue
        available=cohort['available_at_ms']
        # All variants are held to a common availability clock. Next-bar execution
        # in the replay is deliberately conservative versus real intra-bar placement.
        candle=next((c for c in rows if c['ts_ms']>=available),None)
        if not candle or candle['timestamp'] in seen_times:continue
        seen_times.add(candle['timestamp']);accepted+=1
        for variant in cohort['variants']:
            dest=variants[variant['id']]
            for field in ('model','prompt_hash','memory_hash','strategy_hash','provenance'):
                if field in dest and dest[field]!=variant[field] and field in {'model','memory_hash','strategy_hash'}:
                    raise ValueError('Model/memory/strategy changed; split into separately versioned studies')
                dest[field]=variant[field]
            billed=(cost_ledger or {}).get(str(available)+':'+variant['id']) if variant['features'] else 0
            allocated_cost=float(billed)/len(variant['decisions']) if billed is not None else None
            for decision in variant['decisions']:
                dest['signals'][decision['instId']].append({**decision,'timestamp':candle['timestamp'],
                    'features_as_of_ms':variant['features_as_of_ms'],'generated_at_ms':variant['generated_at_ms'],
                    'confidence_scale':'percent','model_cost_usdt':allocated_cost,'decision_id':variant['prompt_hash']+':'+str(available)})
    if not accepted:raise ValueError('No complete forward cohorts match the candle window')
    return {'schema':1,'bar':bar,'baseline':'baseline','candles':candles,'variants':list(variants.values()),'metadata':metadata or {},'collection_complete':incomplete==0,'incomplete_cohorts':incomplete}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--snapshot',default='data/shadow_snapshot.json')
    parser.add_argument('--capture',action='store_true');parser.add_argument('--export-candles');parser.add_argument('--cost-ledger')
    parser.add_argument('--output',default='data/research_dataset.json');parser.add_argument('--bar',default='1H')
    parser.add_argument('--allow-llm-cost',action='store_true');parser.add_argument('--scope',default='shadow:research')
    args=parser.parse_args()
    if args.export_candles:
        raw=json.loads(Path(args.export_candles).read_text(encoding='utf-8'))
        cohorts=[e['payload'] for e in evidence.export_events(args.scope,'shadow_cohort')]
        costs=json.loads(Path(args.cost_ledger).read_text(encoding='utf-8')) if args.cost_ledger else None
        dataset=dataset_from_cohorts(raw.get('candles',raw),cohorts,bar=args.bar,metadata=raw.get('metadata',{}),cost_ledger=costs)
        output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(dataset,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        print(json.dumps({'output':str(output),'auto_enable':False}));return
    if args.capture:
        if str(ROOT/'scripts') not in sys.path:sys.path.insert(0,str(ROOT/'scripts'))
        import ai_brain_trader as brain
        from scripts.instrument_pool import load_instruments
        from concurrent.futures import ThreadPoolExecutor
        as_of=brain.market.begin_signal_frame()
        eligible,_=brain.support.trading_universe(load_instruments(),[],brain.market._selected().mode)
        if not eligible:raise ValueError('No verified trade-eligible shadow universe')
        with ThreadPoolExecutor(max_workers=4) as pool:packages=list(pool.map(brain.fetch_single_instrument_package,eligible))
        memory_path=ROOT/'data'/'AI_TRADING_MEMORY.md'
        snapshot={'as_of_ms':int(as_of*1000),'packages':packages,'memory':memory_path.read_text(encoding='utf-8') if memory_path.exists() else ''}
        output=Path(args.snapshot);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        if not args.allow_llm_cost:
            print(json.dumps({'captured':str(output),'model_calls':0,'executed_orders':0}));return
    if not args.allow_llm_cost:parser.error('Explicit --allow-llm-cost is required; four isolated model calls per cohort')
    from okxquant_backend.llm_manager import get_active_llm_runtime,execute_llm_request
    runtime=get_active_llm_runtime()
    def call(system,payload):
        text,_,_,_=execute_llm_request(messages=[{'role':'system','content':system},{'role':'user','content':evidence.canonical(payload)}],
            model=runtime['model'],base_url=runtime['base_url'],api_key=runtime['api_key'],api_format=runtime.get('api_format'),reasoning_effort=runtime.get('reasoning_effort'),temperature=0,response_format={'type':'json_object'},timeout=45)
        return trading_prompt.parse_response(text)
    snapshot=json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
    result=collect(snapshot,call,model=runtime['model'],scope=args.scope)
    print(json.dumps({'complete':result['complete'],'errors':result['errors'],'executed_orders':0}))

if __name__=='__main__':main()
