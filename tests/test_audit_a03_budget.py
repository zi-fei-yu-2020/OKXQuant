"""small300 caller ceiling vs actual 1M risk allocation and fresh reservations."""
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts import entry_gateway, demo_scalp, entry_candidates, execution_profiles as profiles
from scripts import risk_policy as risk, strategy_evidence as evidence, trade_lock, capital_pool
from scripts.okx_runtime import OKXEnvironment
from test_demo_scalp_policy import sampling_package
from test_strategy_risk import META


class Small300BudgetTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(evidence,'DB_PATH',self.root/'e.db'))
        self.stack.enter_context(patch.object(trade_lock,'PATH',self.root/'lock'))
        self.stack.enter_context(patch.object(capital_pool,'CONFIG_FILE',self.root/'pool.json'))
        self.env=OKXEnvironment('demo','a03-fake','fake','fake')
        self.policy=risk.Policy(**{k:v for k,v in profiles.SMALL_300.items() if k in risk.Policy.__dataclass_fields__})

    def prepare(self, budget=15, change_pending=False):
        package=sampling_package()
        now=package['data_as_of']+8
        candidate=entry_candidates.catalog(package,vars(self.policy))['plans'][0]
        row=demo_scalp.materialize(package,candidate,now,self.policy)
        binding=profiles.runtime({'id':'small300'})
        meta={**META,'instId':package['instId'],'tickSz':'0.00000001','lotSz':'0.001','minSz':'0.001'}
        pending_reads=[]
        writes=[]
        def private(method,path,params,env):
            if method!='GET': writes.append((method,path));raise AssertionError('No write expected')
            if path.endswith('/positions'):return []
            if path.endswith('/orders-pending'):
                pending_reads.append(params)
                if change_pending and len(pending_reads)==2:
                    return [{'ordId':'competing','instId':'OTHER-USDT-SWAP','posSide':'long','sz':'1','accFillSz':'0','px':'100'}]
                return []
            if path.endswith('/balance'):
                return [{'totalEq':'5500','uTime':str(int(now*1000)),
                         'details':[{'ccy':'USDT','eq':'5000','availEq':'5000','liab':'0'}]}]
            if path.endswith('/leverage-info'):return [{'posSide':'long','lever':'10'}]
            raise AssertionError(path)
        def public(url,**kw):
            return {'data':[meta] if '/instruments?' in url else [{'last':str(package['price']),'ts':str(int(now*1000))}]}
        with patch.object(entry_gateway.time,'time',return_value=now), \
             patch.object(demo_scalp,'enabled',return_value=True), \
             patch.object(profiles,'runtime',return_value=binding), \
             patch.object(risk,'load_policy',return_value=self.policy), \
             patch.object(risk,'ledger_daily_drawdown',return_value={'blocked':False}), \
             patch.object(entry_gateway,'_request',side_effect=private), \
             patch.object(entry_gateway.public_market,'get_json',side_effect=public):
            did=evidence.append(self.env.identity,'decision',{'instrument':package['instId'],
                'features':package,'decision':row['decision'],'as_of_ms':int(package['data_as_of']*1000),
                'position_basis':{'size':0},'execution_profile_signature':binding['signature']})
            plan,cid=entry_gateway.prepare(self.env,inst_id=package['instId'],side='long',
                entry=candidate['entry_price'],stop=candidate['stop_loss_price'],take_profit=candidate['take_profit_price'],
                requested_size=1000,budget=budget,decision_id=did,decision_at=package['data_as_of'],horizon='scalp')
        self.assertEqual(writes,[])
        return plan,cid,pending_reads

    def test_fixed_15u_is_a_ceiling_not_small300_actual_risk(self):
        plan,cid,reads=self.prepare()
        self.assertGreater(plan['risk_usdt'],0)
        # Existing scalp mode is 0.4% of actual 300 USDT risk equity: no retuning.
        self.assertLessEqual(plan['risk_usdt'],300*.004)
        self.assertLess(plan['risk_usdt'],15)
        self.assertEqual(plan['capital_pool']['execution_allocation']['risk_equity'],300)
        self.assertEqual(reads,[{'instType':'SWAP'},{'instType':'SWAP'}])
        self.assertEqual(evidence.unresolved(self.env.identity)[0][0],cid)

    def test_caller_budget_below_mode_ceiling_is_also_respected(self):
        plan,_,reads=self.prepare(budget=.5)
        self.assertLessEqual(plan['risk_usdt'],.5)
        self.assertEqual(len(reads),2)

    def test_enabled_virtual_allocation_rechecks_competing_pending_before_intent(self):
        with self.assertRaisesRegex(risk.RiskRejected,'Pending reservations changed'):
            self.prepare(change_pending=True)
        self.assertEqual(evidence.unresolved(self.env.identity),[])

    def test_legacy_enabled_demo_configuration_never_silently_enables_live(self):
        path=self.root/'demo-scalp.json'
        path.write_text(json.dumps({'enabled':True,'version':'demo-scalp-v2'}))
        with patch.object(demo_scalp,'CONFIG',path), patch('scripts.okx_runtime._load_dotenv',return_value={}):
            self.assertTrue(demo_scalp.enabled(self.env))
            self.assertFalse(demo_scalp.enabled(OKXEnvironment('live','a03-fake','fake','fake')))
