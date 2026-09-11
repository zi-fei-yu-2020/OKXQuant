#!/usr/bin/env python3
"""Independent deterministic position protection. Never runs an LLM or opens exposure."""
import argparse
import datetime
import json
import os
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
for path in (ROOT,ROOT/'scripts'):
    if str(path) not in sys.path:sys.path.insert(0,str(path))
from scripts.trade_lock import writer
from scripts.risk_policy import monotonic_stop
from scripts import strategy_evidence as evidence



def read_positions(env):
    from okxquant_backend.okx_trade_service import _request
    if env.configured:
        return _request('GET','/api/v5/account/positions',{'instType':'SWAP'},env)
    import ai_factor_trader as trader
    ok,positions,error=trader.query_positions()
    if not ok:raise RuntimeError('Guard position read unknown')
    return positions


def observe_equity(env,positions=None):
    from okxquant_backend.okx_trade_service import _request
    from scripts.entry_gateway import equity_guard
    from scripts.risk_policy import load_policy
    rows=_request('GET','/api/v5/account/balance',{},env)
    if len(rows)!=1:raise ValueError('Guard equity read unknown')
    policy=load_policy();observation=equity_guard(env,rows[0],policy)
    from scripts import capital_pool
    if positions is not None and not capital_pool.active_positions(positions) and capital_pool.needs_initialization(env):
        pending=_request('GET','/api/v5/trade/orders-pending',{'instType':'SWAP'},env)
        capital_pool.initialize_flat(env,observation,rows[0],positions,pending,policy)
    return observation

def run_guard(*, observe_only=False):
    import ai_factor_trader as trader
    import public_market as market
    from okx_runtime import freeze_environment,unfreeze_environment
    from scripts.algo_reader import read_algo_orders
    env=freeze_environment()
    actions=[]
    try:
        with writer(timeout=5):
            positions=read_positions(env)
            if not observe_only:
                try: observe_equity(env,positions)
                except Exception as exc: actions.append({'status':'equity_guard_blocked_or_unknown','category':type(exc).__name__})
            held=[p for p in positions if abs(float(p.get('pos') or 0))>0]
            trackers=trader.load_trackers()
            if not observe_only:
                # A verified flat account retires old peaks/stops even between trader cycles.
                live_keys={f"{p['instId']}_{p.get('posSide')}" for p in held}
                for old_key in list(trackers):
                    if old_key not in live_keys:trackers.pop(old_key,None)
            # Never assume a configured observation pool contains all held instruments.
            try:
                catalog=market.get_json('https://www.okx.com/api/v5/public/instruments?instType=SWAP',simulated=env.simulated)['data'] if held else []
            except Exception:
                catalog=[]  # Public data failure must not disable private protection checks.
            from scripts.instrument_pool import from_okx_instrument
            items={i['instId']:from_okx_instrument(i) for i in catalog}
            market.begin_signal_frame()
            timestamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
            orders_unknown = False
            try:
                orders = read_algo_orders(env, priority='risk', force=True) if held else []
            except Exception:
                orders = []; orders_unknown = True
            for position in held:
                inst=position['instId'];side=position.get('posSide')
                if side not in {'long','short'}:
                    actions.append({'instrument':inst,'status':'unsupported_position_mode'});continue
                key=f'{inst}_{side}'
                if inst not in items:
                    same=[o for o in orders if o.get('instId')==inst]
                    protected=not orders_unknown and trader._live_oco_coverage(same,side)>=abs(float(position['pos']))*.999
                    actions.append({'instrument':inst,'status':'metadata_missing','coverage_confirmed':protected})
                    if not observe_only and not protected:
                        closed,detail=trader.close_position_confirmed(inst,side,abs(float(position['pos'])),exit_reason='independent_guard',position=position)
                        if closed:
                            trackers.pop(key,None)
                            trader.add_stop_cooldown(inst,side,'Independent guard safety exit')
                        actions.append({'instrument':inst,'status':'protection_unknown_exit','closed':closed})
                    continue
                item=items[inst]
                factors=trader.fetch_single_instrument_data(item,[position],0)
                mark=float(position.get('markPx') or 0)
                if mark>0:factors['price']=mark
                # Adopt existing complete exchange coverage for a newly discovered tracker,
                # rather than inventing a tighter initial stop from a new ATR sample.
                try:
                    matching=[o for o in orders if o.get('instId')==inst and o.get('posSide') in {side,'net'} and o.get('slTriggerPx') and o.get('tpTriggerPx') and o.get('side')==('sell' if side=='long' else 'buy')]
                    covered=trader._live_oco_coverage(matching,side)>=abs(float(position['pos']))*.999
                except Exception:
                    matching=[];covered=False
                if observe_only:
                    actions.append({'instrument':inst,'coverage_confirmed':covered,'market_data_valid':factors['market_data_valid']});continue
                if orders_unknown or (not covered and not factors.get('market_data_valid')):
                    # Market-factor loss does not disable fail-closed account protection.
                    closed,detail=trader.close_position_confirmed(inst,side,abs(float(position['pos'])),exit_reason='independent_guard',position=position)
                    actions.append({'instrument':inst,'status':'protection_unknown_exit','closed':closed})
                    if closed:
                        trackers.pop(key,None)
                        trader.add_stop_cooldown(inst,side,'Independent guard safety exit')
                    continue
                from scripts.position_lifecycle import reconcile as reconcile_lifecycle, identity as position_identity
                lifecycle=reconcile_lifecycle(trackers,key,position,env.identity)
                if lifecycle=='unknown':
                    if not covered:
                        closed,detail=trader.close_position_confirmed(inst,side,abs(float(position['pos'])),exit_reason='independent_guard',position=position)
                        actions.append({'instrument':inst,'status':'protection_unknown_exit','closed':closed})
                        if closed:trackers.pop(key,None)
                    actions.append({'instrument':inst,'status':'position_identity_unknown','coverage_confirmed':covered})
                    continue
                if key not in trackers and covered:
                    entry=float(position['avgPx'])
                    stop=(min if side=='long' else max)(float(o['slTriggerPx']) for o in matching)
                    trackers[key]={'instId':inst,'name':item['name'],'side':side,'entryPx':entry,
                        'positionIdentity':position_identity(position,env.identity),
                        'entryTs':int(float(position.get('cTime') or time.time()*1000)/1000),'entryTime':timestamp,
                        'initialSz':abs(float(position['pos'])),'currentSz':abs(float(position['pos'])),
                        'highWaterMark':mark,'lowWaterMark':mark,'trailingStopPx':stop,
                        'takeProfitPx':float(matching[0]['tpTriggerPx']),'exchangeStopPx':stop,'initialRiskStopPx':stop}
                    evidence.best_effort(env.identity,'position_protection_adoption',{'identity':position_identity(position,env.identity),
                        'orders':matching,'adopted_stop':stop,'source':'independent_guard','cloud_stop_changed':False})
                changed,detail=trader.manage_position_tp_and_trailing(factors,factors['position'],trackers,timestamp,actions)
                if key not in trackers:continue
                if trackers[key].get('pendingStopAmendment'):
                    actions.append({'instrument':inst,'status':'unresolved_stop_amendment_no_retry'});continue
                desired=float(trackers[key].get('localTrailingStopPx') or trackers[key].get('trailingStopPx') or 0)
                for algo in matching:
                    old=float(algo['slTriggerPx'])
                    if not monotonic_stop(side,old,desired,mark):continue
                    # Refresh immediately before each write. No blind order/amend retries.
                    try: fresh=read_algo_orders(env,priority='risk',force=True)
                    except Exception:
                        actions.append({'instrument':inst,'status':'stop_read_unknown_no_amend'});break
                    current=next((o for o in fresh if o.get('algoId')==algo['algoId']),None)
                    if not current or not monotonic_stop(side,float(current.get('slTriggerPx') or 0),desired,mark):continue
                    from scripts.protection_policy import rounded_stop
                    rounded=rounded_stop(side,desired,float(current['slTriggerPx']),mark,items[inst]['tickSz'])
                    if rounded is None:continue
                    desired=float(rounded)
                    # Write-ahead journal survives a crash between dispatch and read-back.
                    trackers[key]['pendingStopAmendment']={'algoId':algo['algoId'],'requestedStop':desired,
                        'positionIdentity':position_identity(position,env.identity),'source':'independent_guard'}
                    trader.save_trackers(trackers)
                    evidence.best_effort(env.identity,'guard_amendment_intent',{'instrument':inst,'algo_id':algo['algoId'],
                        'old_stop':float(current['slTriggerPx']),'requested_stop':desired,
                        'position_identity':position_identity(position,env.identity),
                        'activation':trackers[key].get('exitEvaluation')})
                    result=trader.run_cmd_result(trader.okx_private_command(f"okx swap algo amend --instId {inst} --algoId {algo['algoId']} --newSlTriggerPx {desired} --newSlOrdPx=-1 --json"))
                    verified=False
                    try:
                        check=read_algo_orders(env,priority='risk',force=True,timeout=6)
                        actual=next((float(o.get('slTriggerPx') or 0) for o in check if o.get('algoId')==algo['algoId']),0)
                        verified=(desired<=actual<mark if side=='long' else mark<actual<=desired) if actual else False
                    except Exception:pass
                    evidence.best_effort(env.identity,'guard_amendment',{'instrument':inst,'algo_id':algo['algoId'],'old_stop':old,'requested_stop':desired,'verified':verified,'transport_ok':result['ok']})
                    trackers[key]['stopAuthority']='cloud_confirmed' if verified else 'local_only_cloud_unconfirmed'
                    if verified:
                        trackers[key]['exchangeStopPx']=actual
                        trackers[key].pop('pendingStopAmendment',None)
                    else:
                        break  # Uncertain writes stay pending; never retry another segment blindly.
            if not observe_only:trader.save_trackers(trackers)
            result={'at':time.time(),'environment':env.mode,'observe_only':observe_only,'positions':len(held),'actions':actions}
            evidence.best_effort(env.identity,'position_guard',result)
            return result
    finally:unfreeze_environment()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--observe-only',action='store_true');args=parser.parse_args()
    from scripts.okx_runtime import _load_dotenv
    enabled=str(_load_dotenv().get('OKXQUANT_POSITION_GUARD_ENABLED','1')).lower()
    if enabled not in {'0','1','true','false','yes','no'}:raise ValueError('Invalid position guard enable flag')
    if enabled not in {'1','true','yes'} and not args.observe_only:
        print('Independent position guard disabled by operator');return
    print(json.dumps(run_guard(observe_only=args.observe_only),ensure_ascii=False))

if __name__=='__main__':main()
