import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from scripts import entry_candidates, scalp_candidates, demo_scalp, demo_scalp_policy, entry_gateway
from scripts import risk_policy, strategy_evidence as evidence, trade_lock
from scripts.okx_runtime import OKXEnvironment
from test_demo_scalp import minute_package
from test_strategy_risk import META


def sampling_package():
    p=minute_package()
    p['entry_candles']['1M']['rows'][-2]['low']=96.5
    return p


class DemoPolicyTests(unittest.TestCase):
    def test_sample_rejected_at_two_is_allowed_at_one_point_two(self):
        p=sampling_package();base=risk_policy.Policy()
        self.assertFalse(scalp_candidates.catalog(p,vars(base))['plans'])
        plans=entry_candidates.catalog(p,vars(base))['plans']
        self.assertTrue(plans,plans)
        plan=plans[0]
        self.assertTrue(1.2 <= plan['net_rr'] < 2,plan['net_rr'])
        row=demo_scalp.materialize(p,plan,p['data_as_of'],base)
        self.assertTrue(row['decision']['contract_valid'])
        self.assertEqual(row['decision']['entry_policy'],demo_scalp_policy.descriptor())

    def test_only_rr_changes_not_costs_or_money_limits(self):
        original=risk_policy.Policy()
        env=OKXEnvironment('demo','fake','fake','fake')
        modified=demo_scalp_policy.execution_policy(original,env)
        self.assertEqual({k:v for k,v in vars(original).items() if k!='minimum_net_rr'},
                         {k:v for k,v in vars(modified).items() if k!='minimum_net_rr'})
        self.assertEqual(original.minimum_net_rr,2)
        with self.assertRaises(risk_policy.RiskRejected):
            demo_scalp_policy.execution_policy(original,OKXEnvironment('live','fake','fake','fake'))

    def test_full_gateway_uses_same_rr_and_durable_decision(self):
        p=sampling_package();base=risk_policy.Policy();now=p['data_as_of']+8
        plan=entry_candidates.catalog(p,vars(base))['plans'][0]
        row=demo_scalp.materialize(p,plan,now,base)
        env=OKXEnvironment('demo','fake','fake','fake')
        meta={**META,'instId':p['instId'],'tickSz':'0.00000001','lotSz':'0.001','minSz':'0.001'}
        exchange_leverage=[3.]
        def private(method,path,params,selected):
            if path.endswith('/set-leverage'):
                self.assertEqual(method,'POST');exchange_leverage[0]=float(params['lever']);return [{'lever':params['lever']}]
            self.assertEqual(method,'GET')
            if path.endswith('/positions') or path.endswith('/orders-pending'):return []
            if path.endswith('/balance'):return [{'totalEq':'5000','uTime':str(int(now*1000)),'details':[{'ccy':'USDT','availEq':'5000'}]}]
            if path.endswith('/leverage-info'):return [{'posSide':'long','lever':str(exchange_leverage[0])}]
            self.fail(path)
        def public(url,**kwargs):
            return {'data':[meta] if '/instruments?' in url else [{'last':str(p['price']),'ts':str(int(now*1000))}]}
        with tempfile.TemporaryDirectory() as temp,patch.object(evidence,'DB_PATH',Path(temp)/'ev.db'),patch.object(trade_lock,'PATH',Path(temp)/'lock'),             patch.object(entry_gateway.time,'time',return_value=now),patch.object(demo_scalp,'enabled',return_value=True),             patch.object(entry_gateway,'_request',side_effect=private),patch.object(entry_gateway.public_market,'get_json',side_effect=public),             patch.object(risk_policy,'load_policy',return_value=base):
            did=evidence.append(env.identity,'decision',{'instrument':p['instId'],'features':p,'decision':row['decision'],
                'as_of_ms':int(p['data_as_of']*1000),'position_basis':{'size':0}})
            kwargs=dict(inst_id=p['instId'],side='long',entry=plan['entry_price'],stop=plan['stop_loss_price'],take_profit=plan['take_profit_price'],
                        requested_size=5,budget=15,decision_id=did,decision_at=p['data_as_of'],horizon='scalp')
            actual,cid=entry_gateway.prepare(env,**kwargs)
            self.assertTrue(1.2<=actual['net_rr']<2,actual['net_rr'])
            self.assertLessEqual(actual['risk_usdt'],15)
            self.assertEqual(actual['leverage'],10)
            self.assertTrue(actual['leverage_verified'])
            self.assertEqual(exchange_leverage[0],10)
            self.assertEqual(actual['entry_policy'],demo_scalp_policy.descriptor())
            self.assertEqual(evidence.unresolved(env.identity)[0][0],cid)
            # A missing decision STILL fails before querying or submitting any order.
            with self.assertRaisesRegex(risk_policy.RiskRejected,'Decision evidence not found'):
                entry_gateway.prepare(env,**{**kwargs,'decision_id':'absent'})

    def test_legacy_candidate_engine_does_not_use_sampling_policy(self):
        from test_entry_candidates import package
        p=package()
        self.assertEqual(entry_candidates.catalog(p),entry_candidates._swing_catalog(p))
