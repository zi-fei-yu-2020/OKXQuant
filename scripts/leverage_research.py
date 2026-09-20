#!/usr/bin/env python3
"""Fixed 300-USDT allocation comparison; no exchange leverage changes or orders."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.entry_research import evaluate
from scripts.risk_policy import Policy,order_plan


def evaluate_leverage(dataset):
    policy=Policy()
    capital=300.
    risk_cap=capital*policy.per_trade_equity_pct
    from scripts.capital_pool import Config
    report={'capital':capital,'per_trade_risk_cap':risk_cap,'levels':[1,3,5],
            'risk_policy':asdict(policy),'capital_model':asdict(Config()),
            'production_preset_applied':False,'study_basis':'fixed_allocation_control_not_active_small300','runs':{},'arithmetic_control':[],
            'capture_hash':dataset['capture_hash'],'executed_orders':0,'exchange_settings_changed':False,
            'limitations':['Margin-constrained runs can have different admitted sizes despite the same risk cap',
                'No liquidation simulator; results do not quantify liquidation safety',
                'Mechanical hypotheses failed prior held-out validation; not approved for production'],
            'decision':'Do not raise the production leverage ceiling on the basis of this study'}
    for leverage in report['levels']:
        result=evaluate(dataset,capital=capital,leverage=leverage,policy=policy,pool_profile=True)
        report['runs'][str(leverage)]={k:{'train':v['train'],'test':v['test']} for k,v in result['variants'].items()}
        # Explicit arithmetic fixture, not a historical market quotation.
        meta={'instId':'ARITHMETIC_ONLY','ctType':'linear','settleCcy':'USDT','state':'live','ctVal':1,'lotSz':'.01','minSz':'.01','tickSz':'.01'}
        plan=order_plan(metadata=meta,side='long',entry=100,stop=98,take_profit=106,requested_size=.6,budget_usdt=risk_cap,
                        equity=capital,available=capital*Config().total_margin_fraction,leverage=leverage,policy=policy)
        report['arithmetic_control'].append({k:plan[k] for k in ('leverage','size','risk_usdt','notional_usdt','margin_usdt','net_rr')})
        print(f'leverage={leverage} replay complete; exchange writes=0',flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();dataset=json.loads(Path(args.input).read_text(encoding='utf8'))
    report=evaluate_leverage(dataset);path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    print(json.dumps({'output':str(path),'executed_orders':0}))

if __name__=='__main__':main()
