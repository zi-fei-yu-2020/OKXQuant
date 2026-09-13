import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from scripts import capital_pool as pool, strategy_evidence as evidence, entry_gateway
from scripts.risk_policy import Policy, RiskRejected, order_plan, update_equity_state
from scripts.okx_runtime import OKXEnvironment
from test_strategy_risk import META


def config(**kwargs):
    return pool.Config(**{'enabled':True,'dedicated_account_confirmed':True,'virtual_cap_acknowledged':True,**kwargs})


def observation(equity=5000,at=100,flow=0):
    return {'equity':equity,'equity_currency':'USDT','at':at,'external_flow_total':flow,'external_flow_origin':100}


class PoolMathTests(unittest.TestCase):
    def setUp(self):self.cfg=config();self.policy=Policy();self.scope='demo:unit'
    def first(self):return pool.advance(None,self.cfg,self.scope,observation(),flat=True,policy=self.policy)

    def test_defaults_off_and_live_needs_separate_opt_in(self):
        self.assertFalse(pool.Config().enabled)
        with self.assertRaises(RiskRejected):config(environment='live')
        self.assertTrue(config(environment='live',allow_live=True).enabled)
        for kw in ({'enabled':'true'},{'dedicated_account_confirmed':False},{'budget_usdt':True},{'max_active_instruments':True}):
            with self.assertRaises(RiskRejected):config(**kw)

    def test_initial_budget_is_300_not_whole_5000_account(self):
        state=self.first();self.assertEqual(state['risk_equity'],300)
        self.assertEqual(state['account_equity'],5000)
        self.assertEqual(state['pool_nav'],300)

    def test_cannot_allocate_equity_that_does_not_exist(self):
        state=pool.advance(None,self.cfg,self.scope,observation(200),flat=True,policy=self.policy)
        self.assertEqual(state['initial_budget'],200)
        self.assertEqual(state['risk_equity'],200)

    def test_initialization_requires_flat_account(self):
        with self.assertRaises(RiskRejected):pool.advance(None,self.cfg,self.scope,observation(),flat=False,policy=self.policy)

    def test_losses_survive_restart_and_deposit(self):
        state=pool.advance(self.first(),self.cfg,self.scope,observation(4997,101),flat=False,policy=self.policy)
        self.assertEqual(state['pool_nav'],297)
        state=json.loads(json.dumps(state))
        deposit=pool.advance(state,self.cfg,self.scope,observation(5497,102,500),flat=False,policy=self.policy)
        self.assertEqual(deposit['pool_nav'],297)
        self.assertEqual(deposit['strategy_pnl_since_allocation'],-3)

    def test_withdrawal_does_not_fake_loss_but_actual_equity_caps_risk(self):
        state=pool.advance(self.first(),self.cfg,self.scope,observation(100,101,-4900),flat=False,policy=self.policy)
        self.assertEqual(state['pool_nav'],300)
        self.assertEqual(state['risk_equity'],100)

    def test_profits_do_not_automatically_raise_risk_budget(self):
        state=pool.advance(self.first(),self.cfg,self.scope,observation(5020,101),flat=False,policy=self.policy)
        self.assertEqual(state['pool_nav'],320);self.assertEqual(state['risk_equity'],300)

    def test_pool_drawdown_uses_300_not_5000(self):
        state=pool.advance(self.first(),self.cfg,self.scope,observation(4990,101),flat=False,policy=self.policy)
        self.assertTrue(state['drawdown']['blocked'])
        self.assertAlmostEqual(state['drawdown']['daily_drawdown'],10/300)

    def test_deposit_cannot_clear_blocked_pool(self):
        state=pool.advance(self.first(),self.cfg,self.scope,observation(4990,101),flat=False,policy=self.policy)
        state=pool.advance(state,self.cfg,self.scope,observation(9990,102,5000),flat=False,policy=self.policy)
        self.assertTrue(state['drawdown']['blocked']);self.assertEqual(state['pool_nav'],290)

    def test_depleted_pool_never_resets_to_300(self):
        state=pool.advance(self.first(),self.cfg,self.scope,observation(4600,101),flat=False,policy=self.policy)
        self.assertEqual(state['risk_equity'],0);self.assertTrue(state['drawdown']['blocked'])

    def test_allocation_change_requires_review_not_new_id_reset(self):
        for changed in (config(budget_usdt=400),config(allocation_id='reset-attempt')):
            with self.assertRaises(RiskRejected):pool.advance(self.first(),changed,self.scope,observation(4990,101),flat=True,policy=self.policy)

    def test_same_timestamp_is_idempotent_but_contradictions_fail(self):
        first=self.first()
        self.assertIs(pool.advance(first,self.cfg,self.scope,observation(),flat=False,policy=self.policy),first)
        with self.assertRaises(RiskRejected):pool.advance(first,self.cfg,self.scope,observation(4999,100),flat=False,policy=self.policy)
        with self.assertRaises(RiskRejected):pool.advance(first,self.cfg,self.scope,observation(at=99),flat=False,policy=self.policy)

    def test_incomplete_cashflow_observation_cannot_initialize(self):
        with self.assertRaises(RiskRejected):pool.advance(None,self.cfg,self.scope,{'equity':5000,'at':100},flat=True,policy=self.policy)

    def test_account_flow_accumulator_is_not_trading_profit(self):
        first=update_equity_state(None,equity=1000,at=100,cash_flow=0,complete=True)
        second=update_equity_state(first,equity=1500,at=101,cash_flow=500,complete=True)
        self.assertEqual(second['external_flow_total'],500)
        self.assertEqual(second['peak_drawdown'],0)


class PoolAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        for module,key,value in ((pool,'CONFIG_FILE',self.root/'pool.json'),(evidence,'DB_PATH',self.root/'e.db')):
            p=patch.object(module,key,value);p.start();self.addCleanup(p.stop)
        self.env=OKXEnvironment('demo','fake','fake','fake');self.cfg=config();self.policy=Policy()
        pool.CONFIG_FILE.write_text(json.dumps(asdict(self.cfg)))
        self.balance={'totalEq':'5000','details':[{'ccy':'USDT','eq':'5000','liab':'0'}]}
        self.meta={**META,'lotSz':'.01','minSz':'.01'}

    def admit(self,positions=None,orders=None,inst='NEW',obs=None,available=5000):
        return pool.admit(self.env,obs or observation(),self.balance,positions or [],orders or [],{self.meta['instId']:self.meta},
            inst_id=inst,available=available,policy=self.policy,leverage_reader=lambda inst,side:3)

    def test_disabled_mode_preserves_account_sizing_and_has_no_pool_state(self):
        pool.CONFIG_FILE.write_text(json.dumps({'enabled':False}))
        result=self.admit();self.assertFalse(result.enabled);self.assertEqual(result.equity,5000)
        self.assertIsNone(pool._state(self.env.identity))

    def test_300_budget_and_percentage_margin_caps(self):
        result=self.admit()
        self.assertEqual(result.equity,300);self.assertEqual(result.policy.single_asset_margin_usdt,60)
        self.assertEqual(result.available,150)
        plan=order_plan(metadata=self.meta,side='long',entry=100,stop=94,take_profit=125,requested_size=100,
                        budget_usdt=15,equity=result.equity,available=result.available,leverage=3,policy=result.policy)
        self.assertLessEqual(plan['risk_usdt'],1.5);self.assertLessEqual(plan['margin_usdt'],60)

    def test_pending_orders_use_slots_and_remaining_size_margin(self):
        self.admit()
        order={'instId':self.meta['instId'],'posSide':'long','sz':'3','accFillSz':'1','px':'100','lever':'5'}
        result=self.admit(orders=[order])
        self.assertEqual(result.detail['reserved_margin'],40)
        self.assertEqual(result.available,110)
        positions=[{'instId':'OTHER','pos':'1','imr':'10'}]
        with self.assertRaises(RiskRejected):self.admit(positions,orders=[order])

    def test_unknown_position_or_pending_margin_is_not_guessed(self):
        self.admit()
        with self.assertRaises(RiskRejected):self.admit(positions=[{'instId':'OTHER','pos':'1'}])
        with self.assertRaises(RiskRejected):self.admit(orders=[{'instId':'UNKNOWN','sz':'1','px':'100','lever':'3'}])

    def test_environment_state_isolation(self):
        self.admit()
        self.assertIsNone(pool._state('another-account'))
        self.assertEqual(pool.status(OKXEnvironment('live','fake','fake','fake'))['status'],'environment_mismatch')

    def test_existing_guard_updates_pool_and_raises_on_pool_loss(self):
        self.admit()
        with self.assertRaises(RiskRejected):pool.observe_existing(self.env,observation(4990,101),self.policy)
        self.assertEqual(pool.status(self.env)['status'],'blocked')
        self.assertEqual(pool._state(self.env.identity)['pool_nav'],290)

    def test_guard_never_initializes_unknown_nonflat_account(self):
        self.assertIsNone(pool.observe_existing(self.env,observation(),self.policy))
        self.assertIsNone(pool._state(self.env.identity))

    def test_foreign_assets_and_borrowing_are_not_attributed_to_strategy(self):
        for detail in ({'ccy':'BTC','eq':'.1'},{'ccy':'USDT','eq':'5000','liab':'1'}):
            self.balance['details']=[detail]
            with self.assertRaises(RiskRejected):self.admit()

    def test_malformed_config_blocks_new_exposure(self):
        pool.CONFIG_FILE.write_text('{"enabled":true,"budget_typo":300}')
        with self.assertRaises(RiskRejected):self.admit()
        self.assertEqual(pool.status(self.env)['status'],'error')

    def test_gateway_keeps_actual_3x_and_300_cap_without_exchange_writes(self):
        identity=evidence.append(self.env.identity,'decision',{'instrument':self.meta['instId'],'features':{'structure_1h':'1H_SWING_BULL'},'decision':{
            'action':'BUY_LONG','contract_version':'trading-evidence-v1','contract_valid':True,'valid_until':time.time()+300}})
        def private(method,path,params,env):
            self.assertEqual(method,'GET')
            if path.endswith('/positions') or path.endswith('/orders-pending'):return []
            if path.endswith('/balance'):return [{**self.balance,'uTime':str(int(time.time()*1000)),
                'details':[{'ccy':'USDT','eq':'5000','availEq':'5000','liab':'0'}]}]
            if path.endswith('/leverage-info'):return [{'posSide':'long','lever':'3'}]
            raise AssertionError('Unexpected endpoint '+path)
        def public(url,**kwargs):return {'data':[self.meta] if '/instruments?' in url else [{'last':'100','ts':str(int(time.time()*1000))}]}
        with patch.object(entry_gateway,'_request',side_effect=private),patch.object(entry_gateway.public_market,'get_json',side_effect=public):
            plan,client=entry_gateway.prepare(self.env,inst_id=self.meta['instId'],side='long',entry=100,stop=94,take_profit=125,
                requested_size=100,budget=15,decision_id=identity,decision_at=time.time())
        self.assertEqual(plan['leverage'],3);self.assertLessEqual(plan['risk_usdt'],1.5)
        self.assertEqual(plan['capital_pool']['risk_equity'],300)
        self.assertEqual(evidence.unresolved(self.env.identity)[0][0],client)

    def test_cross_zero_margin_field_does_not_hide_real_imr(self):
        self.admit()
        result=self.admit(positions=[{'instId':self.meta['instId'],'pos':'1','imr':'60','margin':'0'}],inst=self.meta['instId'])
        self.assertEqual(result.detail['reserved_margin'],60)
        with self.assertRaises(RiskRejected):
            order_plan(metadata=self.meta,side='long',entry=100,stop=94,take_profit=125,requested_size=100,
                       budget_usdt=15,equity=result.equity,available=result.available,leverage=3,policy=result.policy,existing_margin=60)

    def test_disabled_reenabled_pool_does_not_reset_losses(self):
        self.admit();pool.observe_existing(self.env,observation(4997,101),self.policy)
        pool.CONFIG_FILE.write_text(json.dumps({'enabled':False}))
        self.assertFalse(pool.status(self.env)['enabled'])
        pool.CONFIG_FILE.write_text(json.dumps(asdict(self.cfg)))
        self.assertEqual(pool.status(self.env)['pool_nav'],297)

    def test_equivalent_budget_number_not_a_new_allocation(self):
        self.assertEqual(config(budget_usdt=300).allocation_fingerprint,config(budget_usdt=300.0).allocation_fingerprint)

    def test_flat_guard_initialization_does_not_need_order_candidate(self):
        state=pool.initialize_flat(self.env,observation(),self.balance,[],[],self.policy)
        self.assertEqual(state['risk_equity'],300)
        self.assertEqual(evidence.unresolved(self.env.identity),[])

    def test_guard_does_not_initialize_while_pending_or_held(self):
        self.assertIsNone(pool.initialize_flat(self.env,observation(),self.balance,[],[{'instId':'X','sz':'1'}],self.policy))
        self.assertIsNone(pool.initialize_flat(self.env,observation(),self.balance,[{'instId':'X','pos':'1'}],[],self.policy))
        self.assertIsNone(pool._state(self.env.identity))

    def test_same_balance_version_refreshes_check_time_not_pnl(self):
        self.admit()
        before=pool._state(self.env.identity)
        with patch.object(pool.time,'time',return_value=before['checked_at']+60):pool.observe_existing(self.env,observation(),self.policy)
        after=pool._state(self.env.identity)
        self.assertEqual(after['pool_nav'],before['pool_nav'])
        self.assertEqual(after['at'],before['at'])
        self.assertGreater(after['checked_at'],before['checked_at'])

    def test_changed_allocation_is_not_advertised_active(self):
        self.admit()
        pool.CONFIG_FILE.write_text(json.dumps(asdict(config(budget_usdt=400))))
        self.assertEqual(pool.status(self.env)['status'],'error')

    def test_corrupt_fingerprint_is_not_advertised_active(self):
        self.admit();state=pool._state(self.env.identity);del state['allocation_fingerprint']
        with evidence.connection() as db:db.execute('UPDATE capital_pool_state SET payload=? WHERE scope=?',(json.dumps(state),self.env.identity))
        self.assertEqual(pool.status(self.env)['status'],'error')

    def test_allocation_snapshot_records_config_fingerprint(self):
        budget=self.admit()
        self.assertEqual(budget.config_signature,pool.config_signature())
        pool.CONFIG_FILE.write_text(json.dumps(asdict(config(max_active_instruments=1))))
        self.assertNotEqual(budget.config_signature,pool.config_signature())

    def test_key_rotation_does_not_automatically_create_fresh_300_budget(self):
        self.admit();rotated=OKXEnvironment('demo','different-key','fake','fake')
        self.assertEqual(pool.status(rotated)['status'],'error')
        with self.assertRaises(RiskRejected):pool.initialize_flat(rotated,observation(),self.balance,[],[],self.policy)
        self.assertIsNone(pool._state(rotated.identity))

    def test_total_usd_valuation_change_is_not_usdt_strategy_loss(self):
        self.admit()
        changed={'totalEq':'4900','details':[{'ccy':'USDT','eq':'5000','liab':'0'}]}
        pool.observe_existing(self.env,observation(4900,101),self.policy,balance=changed)
        self.assertEqual(pool._state(self.env.identity)['pool_nav'],300)

    def test_usdt_loss_is_not_hidden_by_converted_total_usd_gain(self):
        self.admit()
        changed={'totalEq':'5096','details':[{'ccy':'USDT','eq':'4997','liab':'0'}]}
        pool.observe_existing(self.env,observation(5096,101),self.policy,balance=changed)
        self.assertEqual(pool._state(self.env.identity)['pool_nav'],297)

    def test_unknown_currency_breakdown_cannot_initialize_allocation(self):
        for details in ([],[{'ccy':'USDT'}]):
            with self.assertRaises(RiskRejected):pool.initialize_flat(self.env,observation(),{'totalEq':5000,'details':details},[],[],self.policy)

    def test_lost_flow_history_is_not_treated_as_a_new_budget(self):
        self.admit()
        changed={**observation(5000,200),'external_flow_origin':200}
        with self.assertRaises(RiskRejected):pool.observe_existing(self.env,changed,self.policy)
        self.assertEqual(pool._state(self.env.identity)['external_flow_origin'],100)
