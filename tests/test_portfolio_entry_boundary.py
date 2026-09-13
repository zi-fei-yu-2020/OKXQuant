"""Exercise the real portfolio -> protected order -> final risk boundary, no exchange writes."""
from contextlib import ExitStack
import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import ai_factor_trader as trader
import ai_brain_trader as brain
from scripts import entry_candidates, trading_prompt, trade_lock, strategy_evidence, wait_audit, risk_policy
from scripts.okx_runtime import OKXEnvironment
from test_entry_candidates import package, selection


class PortfolioEntryBoundaryTests(unittest.TestCase):
    def exercise(self, side, score, *, pending=False, position=None, model_wait=False, demo_last=100, quote_delta=0):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root=Path(tmp);env=OKXEnvironment('demo','fake','fake','fake')
            p=package(side);p['askPx']+=quote_delta;p['bidPx']-=quote_delta;plan=entry_candidates.catalog(p)['plans'][0]
            raw=selection(plan);raw['confidence']=score
            decision=trading_prompt.candidate(p,raw,trading_prompt.facts_for(p))
            self.assertTrue(decision['contract_valid'])
            final,reason,_=brain.validate_and_filter_decision(p,decision,set(),{})
            self.assertEqual(final,plan['action'],reason)
            if model_wait:decision.update(action='WAIT',decision_status='audited_wait')
            f={**p,'market_data_valid':True,'calculus':{'valid':True,'acceleration':0.0,'probability_theory':{'continuation_prob_pct':60.0,'breakdown_prob_pct':60.0}},'type':'crypto','sz':16,'ctVal':.01,'precision':4,'atr':2.,'rsi':50.,'position':position,'risk_per_trade_usd':15}
            cache={p['instId']:{'name':'TEST','decision':decision,'decision_id':'TEST_DECISION','data_as_of':time.time()}}
            paths={'WORKSPACE_DIR':str(root),'DATA_DIR':str(root),'LOGS_DIR':str(root),'LOG_FILE':str(root/'trader.log'),
                   'TRADER_LOCK_FILE':str(root/'trader.lock'),'TRADER_SLOT_FILE':str(root/'slot.json')}
            for k,v in paths.items():stack.enter_context(patch.object(trader,k,v))
            stack.enter_context(patch.object(trade_lock,'PATH',root/'writer.lock'))
            stack.enter_context(patch.object(wait_audit,'DATA',root))
            stack.enter_context(patch.object(strategy_evidence,'DB_PATH',root/'evidence.db'))
            stack.enter_context(patch.object(trader,'TARGET_INSTRUMENTS',[{'instId':p['instId']}]))
            stack.enter_context(patch.object(trader,'freeze_okx_environment',return_value=env))
            stack.enter_context(patch.object(trader,'unfreeze_okx_environment'))
            stack.enter_context(patch.object(trader.market,'_selected',return_value=env))
            stack.enter_context(patch.object(trader,'okx_private_command',side_effect=lambda c:c))
            stack.enter_context(patch.object(trader,'clean_stale_open_orders',return_value=(True,'')))
            positions=[{**position,'instId':p['instId'],'pos':1,'posSide':position['side']}] if position else []
            stack.enter_context(patch.object(trader,'query_positions',return_value=(True,positions,'')))
            pending_rows=[{'instId':p['instId'],'state':'live','posSide':side}] if pending else []
            transport=stack.enter_context(patch.object(trader,'run_cmd_result',return_value={'ok':True,'data':pending_rows,'stdout':'','stderr':''}))
            stack.enter_context(patch.object(trader,'run_json_cmd',side_effect=lambda c: [{'details':[{'ccy':'USDT','availBal':'1000'}]}] if 'balance' in c else [{'last':str(demo_last)}]))
            stack.enter_context(patch.object(trader,'fetch_single_instrument_data',side_effect=lambda *a:copy.deepcopy(f)))
            stack.enter_context(patch.object(trader,'load_trackers',return_value={}))
            stack.enter_context(patch.object(trader,'save_trackers'))
            stack.enter_context(patch.object(trader,'prune_trackers',return_value=0))
            stack.enter_context(patch.object(trader,'manage_position_tp_and_trailing'))
            stack.enter_context(patch.object(trader,'execute_ai_position_management'))
            stack.enter_context(patch.object(trader,'is_circuit_breaker_active',return_value=(False,'')))
            stack.enter_context(patch.object(trader,'execute_batch_ai_brain_cycle',return_value=cache))
            stack.enter_context(patch.object(trader,'load_adaptive_config',return_value={}))
            stack.enter_context(patch.object(trader,'evaluate_asset_signal',return_value=(0,'HOLD',[],'test','test')))
            stack.enter_context(patch.object(trader.support,'pool_support',return_value={'items':{p['instId']:{'can_open':True}}}))
            stack.enter_context(patch.object(trader.support,'opening_status',return_value={'can_open':True}))
            risk=stack.enter_context(patch.object(trader.entry_gateway,'prepare',side_effect=risk_policy.RiskRejected('isolated final-risk boundary reached')))
            stack.enter_context(patch('sys.stdout',new_callable=io.StringIO))
            result = trader.execute_portfolio()
            self.assertTrue(result['completed'])
            self.assertFalse(any('swap place' in str(c) for c in transport.call_args_list))
            return risk.call_args_list

    def test_initial_long_and_short_low_scores_reach_actual_final_risk_boundary(self):
        for side in ('long','short'):
            for score in (0,47,79,80,100):
                calls=self.exercise(side,score)
                self.assertEqual(len(calls),1,(side,score))
                self.assertEqual(calls[0].kwargs['side'],side)
                self.assertEqual(calls[0].kwargs['decision_id'],'TEST_DECISION')

    def test_program_prices_are_not_rounded_by_stale_pool_precision(self):
        for side in ('long','short'):
            calls=self.exercise(side,0,quote_delta=.000024)
            self.assertEqual(len(calls),1)
            raw=calls[0].kwargs['entry']
            self.assertNotEqual(raw,round(raw,4))

    def test_wait_and_existing_pending_order_do_not_send_an_entry(self):
        for side in ('long','short'):
            self.assertEqual(self.exercise(side,100,pending=True),[])
            self.assertEqual(self.exercise(side,100,model_wait=True),[])

    def test_losing_position_still_cannot_scale_in_even_with_high_model_score(self):
        for side in ('long','short'):
            self.assertEqual(self.exercise(side,100,position={'side':side,'upl':-2,'uplRatio':-.02,'avgPx':100,'margin':10}),[])

    def test_profitable_scale_in_low_score_also_reaches_final_risk(self):
        for side in ('long','short'):
            calls=self.exercise(side,0,position={'side':side,'upl':2,'uplRatio':.02,'avgPx':100,'margin':10})
            self.assertEqual(len(calls),1,side)

    def test_program_geometry_is_not_translated_to_a_different_demo_price(self):
        for side in ('long','short'):
            self.assertEqual(self.exercise(side,100,demo_last=50),[])

    def test_real_gateway_rechecks_program_trigger_before_capital_admission(self):
        from scripts import entry_gateway
        from unittest.mock import MagicMock
        p=package();plan=entry_candidates.catalog(p)['plans'][0];now=p['data_as_of']+1
        env=OKXEnvironment('demo','fake','fake','fake')
        meta={'instId':p['instId'],'ctType':'linear','settleCcy':'USDT','state':'live','ctVal':'1','ctMult':'1','lotSz':'.1','minSz':'.1','tickSz':'.01'}
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            stack.enter_context(patch.object(strategy_evidence,'DB_PATH',Path(tmp)/'evidence.db'))
            decision={**selection(plan),'contract_version':trading_prompt.VERSION,'contract_valid':True,'valid_until':now+120}
            identity=strategy_evidence.append(env.identity,'decision',{'instrument':p['instId'],'decision':decision,'features':p,'position_basis':{'size':0}})
            stack.enter_context(patch('okxquant_backend.account_connections.assert_current'))
            stack.enter_context(patch.object(entry_gateway,'reconcile_intents'))
            stack.enter_context(patch.object(entry_gateway,'equity_guard',return_value={}))
            stack.enter_context(patch.object(entry_gateway.time,'time',return_value=now))
            def request(method,path,*args,**kwargs):
                self.assertEqual(method,'GET')
                if path.endswith('/balance'):return [{'totalEq':'1000','uTime':str(int(now*1000)),'details':[{'ccy':'USDT','availEq':'1000'}]}]
                if path.endswith('/leverage-info'):return [{'posSide':'long','lever':'3'}]
                return []
            stack.enter_context(patch.object(entry_gateway,'_request',side_effect=request))
            capital=stack.enter_context(patch.object(entry_gateway.capital_pool,'admit',side_effect=RuntimeError('reached capital admission')))
            kwargs=dict(inst_id=p['instId'],side='long',entry=plan['entry_price'],stop=plan['stop_loss_price'],take_profit=plan['take_profit_price'],requested_size=1,budget=15,decision_id=identity,decision_at=now)
            for current in (99,100):
                def public(url,**extra):return {'data':[meta]} if '/instruments?' in url else {'data':[{'last':str(current),'ts':str(int(now*1000))}]}
                with patch.object(entry_gateway.public_market,'get_json',side_effect=public):
                    if current==99:
                        with self.assertRaisesRegex(risk_policy.RiskRejected,'program_trigger_lost'):entry_gateway.prepare(env,**kwargs)
                        capital.assert_not_called()
                    else:
                        with self.assertRaisesRegex(RuntimeError,'reached capital admission'):entry_gateway.prepare(env,**kwargs)
                        capital.assert_called_once()

    def test_entry_section_has_no_hidden_model_confidence_comparison(self):
        source=Path(trader.__file__).read_text(encoding='utf8').split('def execute_portfolio():',1)[1]
        import re
        self.assertIsNone(re.search(r'ai_conf\s*(?:>=|<=|<|>)|(?:>=|<=|<|>)\s*ai_conf|MIN_SCALE_IN_CONFIDENCE',source))

if __name__=='__main__':unittest.main()
