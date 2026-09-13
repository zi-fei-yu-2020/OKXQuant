#!/usr/bin/env python3
"""Causal, marked-to-market execution replay. Missing data never becomes synthetic profit.

The built-in baseline is explicitly NOT a historical replay of the live LLM. Supply
frozen timestamped decisions for replay. No online model is called by this module.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass, field, asdict, replace
import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
import urllib.request
from typing import Any, Dict, List, Optional

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.risk_policy import Policy, RiskRejected, order_plan, number, floor_step, update_equity_state
from scripts.signal_data import closed_candles, bar_seconds
from scripts.instrument_pool import load_instruments

@dataclass
class TradeRecord:
    symbol: str
    entry_time: str
    exit_time: str
    direction: str
    entry_price: float
    exit_price: float
    size: float
    pnl_usd: float
    pnl_pct: float
    exit_reason: str
    r_multiple: float
    fees: float=0
    funding: float=0
    decision_id: str=''

@dataclass
class BacktestSummary:
    symbol: str
    total_trades: int=0
    winning_trades: int=0
    losing_trades: int=0
    win_rate_pct: float=0
    profit_factor: Optional[float]=None
    initial_equity: float=0
    final_equity: float=0
    total_return_pct: float=0
    max_drawdown_pct: float=0
    sharpe_ratio: Optional[float]=None
    sortino_ratio: Optional[float]=None
    calmar_ratio: Optional[float]=None
    avg_r_multiple: float=0
    gatekeeper_filtered_count: int=0
    equity_curve: List[Dict[str,Any]]=field(default_factory=list)
    recent_trades: List[Dict[str,Any]]=field(default_factory=list)
    trades: List[Dict[str,Any]]=field(default_factory=list)
    open_positions: List[Dict[str,Any]]=field(default_factory=list)
    pending_orders: List[Dict[str,Any]]=field(default_factory=list)
    assumptions: List[str]=field(default_factory=list)
    status: str='insufficient_data'
    strategy_kind: str=''
    input_hash: str=''
    funding_complete: bool=False
    operating_costs_usdt: float=0
    replay_leverage: float=3
    entry_evaluations: int=0
    accepted_orders: int=0
    filled_entries: int=0
    expired_orders: int=0
    rejection_reasons: Dict[str,int]=field(default_factory=dict)


def fetch_okx_candles(inst_id,bar='1H',limit=100):
    if not 1<=int(limit)<=300: raise ValueError('Recent candles limited to 300; import archived data for long studies')
    from urllib.parse import urlencode
    url='https://www.okx.com/api/v5/market/candles?'+urlencode({'instId':inst_id,'bar':bar,'limit':limit})
    req=urllib.request.Request(url,headers={'User-Agent':'OKXQuant-Research/2'})
    with urllib.request.urlopen(req,timeout=10) as response: payload=json.load(response)
    if str(payload.get('code'))!='0': raise ValueError('Historical candle source unavailable')
    rows=closed_candles(payload.get('data',[]),bar,limit=limit)
    width=bar_seconds(bar)*1000
    return [{'symbol':inst_id,'timestamp':datetime.datetime.fromtimestamp((int(r[0])+width)/1000,datetime.timezone.utc).isoformat(),
             'ts_ms':int(r[0])+width,'open':float(r[1]),'high':float(r[2]),'low':float(r[3]),'close':float(r[4]),
             'volume':float(r[5]),'source':'okx_closed','confirm':True} for r in reversed(rows)]


def performance(curve,initial,bar):
    if not curve: return {'final_equity':initial,'total_return_pct':0,'max_drawdown_pct':0,'sharpe_ratio':None,'sortino_ratio':None,'calmar_ratio':None}
    values=[initial]+[number(p['equity']) for p in curve]
    peak=initial; dd=0
    for value in values: peak=max(peak,value);dd=max(dd,(peak-value)/peak if peak>0 else 0)
    returns=[b/a-1 for a,b in zip(values,values[1:]) if a>0]
    annual=365*86400/bar_seconds(bar); mean=sum(returns)/len(returns) if returns else 0
    variance=sum((r-mean)**2 for r in returns)/max(1,len(returns)-1)
    downside=sum(min(0,r)**2 for r in returns)/len(returns) if returns else 0
    years=len(curve)/annual
    annual_return=math.expm1(math.log(values[-1]/initial)/years) if values[-1]>0 and years>=1/12 else None
    return {'final_equity':round(values[-1],8),'total_return_pct':round((values[-1]/initial-1)*100,6),
            'max_drawdown_pct':round(dd*100,6),'sharpe_ratio':mean/math.sqrt(variance)*math.sqrt(annual) if variance>0 else None,
            'sortino_ratio':mean/math.sqrt(downside)*math.sqrt(annual) if downside>0 else None,
            'calmar_ratio':annual_return/dd if annual_return is not None and dd>0 else None}


def baseline_signals(candles):
    out=[]
    for i in range(20,len(candles)):
        history=[number(c['close'],positive=True) for c in candles[:i+1]]
        short=sum(history[-5:])/5;long=sum(history[-20:])/20;current=history[-1]
        action='BUY' if current>short>long else 'SELL' if current<short<long else 'WAIT'
        if action=='WAIT': continue
        out.append({'timestamp':candles[i]['timestamp'],'action':action,'confidence':.8,'rr':3,
                    'atr':max(current*.012,max(history[-14:])-min(history[-14:]))})
    return out

class BacktestEngine:
    def __init__(self,initial_capital=10000.,risk_per_trade_pct=.005,maker_fee=.0002,taker_fee=.0005,
                 slippage=.001,min_confidence_gate=.75,min_rr_gate=2.,*,bar='1H',participation=.01,
                 order_ttl_bars=2,policy=None,metadata=None,leverage=3,max_positions=None,capital_ceiling=None,asset_margin_fraction=None,total_margin_fraction=None):
        self.initial_capital=number(initial_capital,positive=True)
        self.risk_per_trade_pct=number(risk_per_trade_pct,positive=True)
        self.maker_fee=number(maker_fee);self.taker_fee=number(taker_fee);self.slippage=number(slippage)
        self.min_confidence_gate=min_confidence_gate;self.min_rr_gate=min_rr_gate;self.bar=bar
        bar_seconds(bar)
        self.participation=number(participation,positive=True);self.order_ttl_bars=int(order_ttl_bars)
        if min(self.maker_fee,self.taker_fee,self.slippage)<0 or self.participation>1 or self.order_ttl_bars<1:
            raise ValueError('Invalid execution assumptions')
        self.policy=policy or Policy(per_trade_equity_pct=risk_per_trade_pct,taker_fee=max(taker_fee,1e-12),slippage=max(slippage,1e-12),minimum_net_rr=min_rr_gate)
        self.leverage=number(leverage,positive=True)
        if self.leverage>self.policy.max_leverage:raise ValueError('Replay leverage exceeds declared risk policy')
        if max_positions is not None and (isinstance(max_positions,bool) or not isinstance(max_positions,int) or max_positions<1):raise ValueError('Invalid replay position cap')
        self.max_positions=max_positions
        self.capital_ceiling=number(capital_ceiling,positive=True) if capital_ceiling is not None else None
        self.asset_margin_fraction=asset_margin_fraction;self.total_margin_fraction=total_margin_fraction
        for fraction in (asset_margin_fraction,total_margin_fraction):
            if fraction is not None and not 0<number(fraction)<1:raise ValueError('Invalid replay allocation fraction')
        self.metadata=metadata or {}
        self.capital=self.initial_capital

    def run(self,candle_series,signals=None):
        symbol=candle_series[0].get('symbol','PORTFOLIO') if candle_series else 'UNKNOWN'
        return self.run_portfolio({symbol:candle_series},None if signals is None else {symbol:signals})

    def run_portfolio(self,series,signals=None):
        self.capital=self.initial_capital
        symbol=next(iter(series)) if len(series)==1 else 'SHARED_EQUITY_PORTFOLIO'
        summary=BacktestSummary(symbol=symbol,initial_equity=self.initial_capital,final_equity=self.initial_capital,
            strategy_kind='explicit_frozen_signals' if signals is not None else 'deterministic_ma_baseline_not_live_llm', replay_leverage=self.leverage)
        summary.assumptions=['signals observed at candle close; orders active no earlier than next bar',
            'OHLC stop-first on ambiguous bars; newly filled limit stops are checked conservatively',
            'trailing stops computed at close become active next bar; R uses immutable initial risk',
            'volume is contracts; missing volume prohibits limit fills; queue modeled by participation cap',
            'partial fills supported; remaining entry canceled when its position exits',
            'expired_orders counts expired unfilled remainders, including partially filled orders',
            'protective TP/SL are market exits (taker fee and adverse slippage), matching attached -1 orders',
            'open positions marked, not force-closed at end; shared cash/risk budget',
            'known model costs deducted from economic NAV; trade-level win rate/PF exclude this shared overhead',
            'observed marked-equity daily/peak drawdown gates block new submissions; existing exits remain active',
            'no liquidation simulator; not approval for live trading']
        if not series or any(len(rows)<20 for rows in series.values()): return summary
        # Require aligned timestamps for a portfolio: never substitute BTC or forward-fill missing assets.
        times=[r['timestamp'] for r in next(iter(series.values()))]
        if len(set(times))!=len(times): raise ValueError('Duplicate candle times')
        for rows in series.values():
            if [r['timestamp'] for r in rows]!=times: raise ValueError('Portfolio candles must be aligned, complete and ordered')
            ms=[r.get('ts_ms') for r in rows]
            if all(x is not None for x in ms) and any(b-a!=bar_seconds(self.bar)*1000 for a,b in zip(ms,ms[1:])):
                raise ValueError('Candle timestamp gap/order mismatch')
            for c in rows:
                o,h,l,cl=[number(c[k],positive=True) for k in ('open','high','low','close')]
                if not l<=min(o,cl)<=max(o,cl)<=h: raise ValueError('Malformed OHLC')
                if c.get('confirm') is False: raise ValueError('Unclosed replay candle')
        maps={inst:{s['timestamp']:dict(s) for s in (baseline_signals(rows) if signals is None else signals.get(inst,[]))} for inst,rows in series.items()}
        summary.input_hash=hashlib.sha256(json.dumps({'series':series,'signals':signals,'policy':asdict(self.policy),'bar':self.bar,
            'maker_fee':self.maker_fee,'taker_fee':self.taker_fee,'slippage':self.slippage,'participation':self.participation,
            'metadata':self.metadata,'ttl':self.order_ttl_bars,'leverage':self.leverage,'max_positions':self.max_positions,'capital_ceiling':self.capital_ceiling,
            'asset_margin_fraction':self.asset_margin_fraction,'total_margin_fraction':self.total_margin_fraction},sort_keys=True,allow_nan=False).encode()).hexdigest()
        summary.funding_complete=all('funding_rate' in c for rows in series.values() for c in rows)
        if not summary.funding_complete: summary.assumptions.append('funding data missing: no financing-performance claim')
        positions={};pending={};trades=[];curve=[];last_marks={};filtered=0;equity_state=None
        def reject(reason):
            summary.rejection_reasons[reason]=summary.rejection_reasons.get(reason,0)+1
        def ct(inst): return float(self.metadata.get(inst,{}).get('ctVal',1))*float(self.metadata.get(inst,{}).get('ctMult') or 1)
        def marked(): return self.capital+sum((last_marks.get(k,p['entry'])-p['entry'])*p['direction']*p['size']*ct(k) for k,p in positions.items())
        def close(inst,p,price,reason,stamp,taker=True):
            fee=abs(price*p['size']*ct(inst))*(self.taker_fee if taker else self.maker_fee)
            gross=(price-p['entry'])*p['direction']*p['size']*ct(inst)
            net=gross-p['entry_fee']-fee+p['funding']
            self.capital+=gross-fee
            trades.append(TradeRecord(inst,p['time'],stamp,'LONG' if p['direction']==1 else 'SHORT',p['entry'],price,p['size'],net,
                net/(p['entry']*p['size']*ct(inst))*100,reason,net/p['initial_risk'] if p['initial_risk']>0 else 0,
                p['entry_fee']+fee,p['funding'],p.get('decision_id','')))
            del positions[inst];pending.pop(inst,None)
        for index,stamp in enumerate(times):
            for inst in sorted(series):
                c=series[inst][index];o,h,l,cl=[float(c[k]) for k in ('open','high','low','close')]
                last_marks[inst]=o
                p=positions.get(inst)
                if p:
                    funding=-p['direction']*p['size']*ct(inst)*o*number(c.get('funding_rate',0))
                    self.capital+=funding;p['funding']+=funding
                    stop_hit=l<=p['stop'] if p['direction']==1 else h>=p['stop']
                    tp_hit=h>=p['tp'] if p['direction']==1 else l<=p['tp']
                    if stop_hit:
                        exit_px=(min(o,p['stop'])*(1-self.slippage) if p['direction']==1 else max(o,p['stop'])*(1+self.slippage))
                        close(inst,p,exit_px,'STOP_LOSS',stamp)
                    elif tp_hit: close(inst,p,p['tp']*(1-p['direction']*self.slippage),'TAKE_PROFIT',stamp,True)
                order=pending.get(inst)
                if order:
                    if index-order['created']>self.order_ttl_bars: pending.pop(inst);order=None;summary.expired_orders+=1
                    if order:
                        direction=order['direction'];limit=order['limit']
                        crossing=limit is None or (o<=limit if direction==1 else o>=limit)
                        touched=crossing or (l<limit if direction==1 else h>limit)
                        capacity=number(c.get('volume',0))*self.participation if limit is not None else order['remaining']
                        fill=min(order['remaining'],max(0,capacity)) if touched else 0
                        if fill>0:
                            price=o*(1+direction*self.slippage) if crossing else limit
                            if limit is not None: price=min(price,limit) if direction==1 else max(price,limit)
                            if not (order['stop']<price<order['tp'] if direction==1 else order['tp']<price<order['stop']):
                                pending.pop(inst);fill=0
                            if fill:
                                initial_unit_risk=ct(inst)*abs(price-order['stop'])+ct(inst)*((price+max(order['stop'],order['tp']))*self.taker_fee+price*2*self.slippage)
                                fill=floor_step(min(fill,order['risk_remaining']/max(initial_unit_risk,1e-12),order['margin_remaining']*order['leverage']/(price*ct(inst))),
                                                self.metadata.get(inst,{}).get('lotSz',1e-8))
                            if fill:
                                fee=price*fill*ct(inst)*(self.taker_fee if crossing else self.maker_fee)
                                self.capital-=fee
                                initial_unit_risk=ct(inst)*abs(price-order['stop'])+ct(inst)*((price+max(order['stop'],order['tp']))*self.taker_fee+price*2*self.slippage)
                                old=positions.get(inst)
                                if old:
                                    total=old['size']+fill;old['entry']=(old['entry']*old['size']+price*fill)/total;old['size']=total
                                    old['entry_fee']+=fee;old['initial_risk']+=fill*initial_unit_risk
                                else:
                                    summary.filled_entries+=1
                                    positions[inst]={'entry':price,'size':fill,'stop':order['stop'],'tp':order['tp'],'direction':direction,
                                        'entry_fee':fee,'funding':0.,'leverage':order['leverage'],'initial_risk':fill*initial_unit_risk,'time':stamp,'decision_id':order.get('decision_id','')}
                                order['remaining']-=fill
                                order['risk_remaining']-=fill*initial_unit_risk
                                order['margin_remaining']-=fill*price*ct(inst)/order['leverage']
                                if order['remaining']<=1e-12: pending.pop(inst,None)
                                p=positions[inst]
                                if (l<=p['stop'] if direction==1 else h>=p['stop']):
                                    exit_px=(min(o,p['stop'])*(1-self.slippage) if direction==1 else max(o,p['stop'])*(1+self.slippage))
                                    close(inst,p,exit_px,'STOP_LOSS_FILL_BAR',stamp)
                last_marks[inst]=cl
            # Trailing only uses information known at this close; never same-bar retrospectively.
            for inst,p in positions.items():
                distance=p['initial_risk']/(p['size']*ct(inst))
                if (last_marks[inst]-p['entry'])*p['direction']>=distance:
                    p['stop']=max(p['stop'],p['entry']) if p['direction']==1 else min(p['stop'],p['entry'])
            # Known external model costs are economic strategy expenses, including WAIT calls.
            for inst in series:
                observed=maps[inst].get(stamp)
                if observed and observed.get('model_cost_usdt') is not None:
                    cost=number(observed['model_cost_usdt'])
                    if cost<0:raise ValueError('Invalid operating cost')
                    self.capital-=cost;summary.operating_costs_usdt+=cost
            equity=marked();curve.append({'time':stamp,'equity':equity})
            if equity<=0: break
            observed_at=next(iter(series.values()))[index].get('ts_ms')
            observed_at=float(observed_at)/1000 if observed_at is not None else (index+1)*bar_seconds(self.bar)
            equity_state=update_equity_state(equity_state,equity=equity,at=observed_at,cash_flow=0,complete=True,policy=self.policy)
            for inst in sorted(series):
                sig=maps[inst].get(stamp)
                if not sig: continue
                action=str(sig.get('action','WAIT')).upper()
                if action=='WAIT':continue
                summary.entry_evaluations+=1
                if equity_state['blocked']:
                    filtered+=1;reject('Observed daily/peak drawdown gate');continue
                conf=number(sig.get('confidence',0));conf=conf/100 if sig.get('confidence_scale')=='percent' or conf>1 else conf
                if conf<self.min_confidence_gate or action not in {'BUY','SELL','BUY_LONG','SELL_SHORT'}:
                    filtered+=1;reject('Invalid action or research admission score');continue
                if inst in positions or inst in pending:
                    reject('Existing position or pending order');continue
                if self.max_positions is not None and len(set(positions)|set(pending))>=self.max_positions:
                    filtered+=1;reject('Position/pending slot cap');continue
                direction=1 if action in {'BUY','BUY_LONG'} else -1
                entry=number(sig.get('entry_price') or series[inst][index]['close'],positive=True)
                atr=number(sig.get('atr',entry*.012),positive=True)
                stop=number(sig.get('stop_loss_price') or entry-direction*atr*2,positive=True)
                tp=number(sig.get('take_profit_price') or entry+direction*abs(entry-stop)*number(sig.get('rr',3),positive=True),positive=True)
                side='long' if direction==1 else 'short'
                exposure={'total':0.,'long':0.,'short':0.,'group':0.}
                margin=0
                for key,p in positions.items():
                    risk=p['size']*ct(key)*(abs(last_marks[key]-p['stop'])+last_marks[key]*(2*self.taker_fee+2*self.slippage))
                    exposure['total']+=risk;exposure['long' if p['direction']==1 else 'short']+=risk
                    margin+=last_marks[key]*p['size']*ct(key)/p['leverage']
                for key,q in pending.items():
                    risk=q['remaining']*q['unit_risk'];exposure['total']+=risk;exposure['long' if q['direction']==1 else 'short']+=risk
                    margin+=q['remaining']*q['intended_entry']*ct(key)/q['leverage']
                exposure['group']=exposure['total']
                meta=self.metadata.get(inst) or {'instId':inst,'ctType':'linear','settleCcy':'USDT','state':'live','ctVal':1,'lotSz':'0.00000001','minSz':'0.00000001','tickSz':'0.00000001'}
                risk_equity=min(equity,self.capital_ceiling) if self.capital_ceiling is not None else equity
                available=max(0,equity-margin)
                effective_policy=self.policy
                if self.asset_margin_fraction is not None:effective_policy=replace(effective_policy,single_asset_margin_usdt=min(effective_policy.single_asset_margin_usdt,risk_equity*self.asset_margin_fraction))
                if self.total_margin_fraction is not None:available=min(available,max(0,risk_equity*self.total_margin_fraction-margin))
                try:
                    plan=order_plan(metadata=meta,side=side,entry=round(entry,8),stop=round(stop,8),take_profit=round(tp,8),
                        requested_size=number(sig.get('size',1e12),positive=True),budget_usdt=risk_equity*self.risk_per_trade_pct,
                        equity=risk_equity,available=available,leverage=self.leverage,policy=effective_policy,portfolio=exposure)
                except RiskRejected as exc: filtered+=1;reject(str(exc));continue
                summary.accepted_orders+=1
                pending[inst]={'created':index,'remaining':plan['size'],'direction':direction,'stop':stop,'tp':tp,
                    'limit':entry if sig.get('entry_price') else None,'intended_entry':entry,'unit_risk':plan['risk_usdt']/plan['size'],'risk_remaining':plan['risk_usdt'],
                    'margin_remaining':plan['margin_usdt'],'leverage':plan['leverage'],'decision_id':sig.get('decision_id','')}
        wins=[t for t in trades if t.pnl_usd>0];losses=[t for t in trades if t.pnl_usd<=0]
        gross_profit=sum(t.pnl_usd for t in wins);gross_loss=-sum(t.pnl_usd for t in losses)
        summary.total_trades=len(trades);summary.winning_trades=len(wins);summary.losing_trades=len(losses)
        summary.win_rate_pct=len(wins)/len(trades)*100 if trades else 0
        summary.profit_factor=gross_profit/gross_loss if gross_loss>0 else None
        summary.avg_r_multiple=sum(t.r_multiple for t in trades)/len(trades) if trades else 0
        summary.gatekeeper_filtered_count=filtered;summary.equity_curve=curve;summary.trades=[asdict(t) for t in trades];summary.recent_trades=list(reversed(summary.trades[-10:]))
        summary.open_positions=[{'symbol':key,**value,'mark':last_marks[key]} for key,value in positions.items()]
        summary.pending_orders=[{'symbol':key,**value} for key,value in pending.items()]
        for key,value in performance(curve,self.initial_capital,self.bar).items(): setattr(summary,key,value)
        summary.status='exploratory' if summary.funding_complete else 'exploratory_missing_funding'
        return summary


def run_full_portfolio_backtest(bar='1H',limit=100,capital_per_asset=10000.,symbols=None):
    symbols=symbols or [i['instId'] for i in load_instruments()]
    series={};errors={}
    for inst in symbols:
        try:
            rows=fetch_okx_candles(inst,bar,limit)
            if len(rows)<20: raise ValueError('Insufficient closed candles')
            series[inst]=rows
        except Exception as exc: errors[inst]=type(exc).__name__+': '+str(exc)
    payload={'updated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'bar':bar,'limit':limit,
             'active_symbols':symbols,'by_symbol':{},'errors':errors,'synthetic_fallback':False,'strategy_kind':'deterministic_ma_baseline_not_live_llm'}
    if errors:
        payload.update(status='unavailable',portfolio=asdict(BacktestSummary('PORTFOLIO',initial_equity=capital_per_asset*len(symbols),final_equity=capital_per_asset*len(symbols))))
        return payload
    for inst,rows in series.items():payload['by_symbol'][inst]=asdict(BacktestEngine(initial_capital=capital_per_asset,bar=bar).run(rows))
    try: result=BacktestEngine(initial_capital=capital_per_asset*len(symbols),bar=bar).run_portfolio(series)
    except ValueError as exc: payload.update(status='unaligned_data',errors={'portfolio':str(exc)},portfolio=None);return payload
    payload.update(status=result.status,portfolio=asdict(result));return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol',default='ALL');parser.add_argument('--bar',default='1H');parser.add_argument('--limit',type=int,default=100)
    parser.add_argument('--capital',type=float,default=10000);parser.add_argument('--output',default='data/backtest_report.json')
    args=parser.parse_args()
    report=run_full_portfolio_backtest(args.bar,args.limit,args.capital,None if args.symbol=='ALL' else [args.symbol])
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'status':report['status'],'output':str(path),'errors':report['errors']},ensure_ascii=False))

if __name__=='__main__':main()
