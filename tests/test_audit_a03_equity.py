"""USD -> USDT observation compatibility without erasing equity history."""
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import entry_gateway as gateway, capital_pool, strategy_evidence as evidence
from scripts import risk_policy as risk

AT = 1700000000.


def balance(eq, usd, at):
    return {'totalEq': str(usd), 'uTime': str(int(at*1000)),
            'details': [{'ccy':'USDT', 'eq':str(eq), 'availEq':str(eq), 'liab':'0'}]}


class EquityCurrencyTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(evidence, 'DB_PATH', root/'e.db'))
        self.stack.enter_context(patch.object(capital_pool, 'CONFIG_FILE', root/'pool.json'))
        self.observe=self.stack.enter_context(patch.object(capital_pool, 'observe_existing'))
        self.env=SimpleNamespace(identity='a03-equity', mode='demo')
        self.policy=risk.Policy(daily_drawdown_pct=.2, peak_drawdown_pct=.3)
        self.bills=[]
        self.read=self.stack.enter_context(patch.object(gateway, '_request', side_effect=self.request))

    def request(self, method, path, params, env):
        self.assertEqual((method,path), ('GET','/api/v5/account/bills'))
        return self.bills

    def save(self, state):
        with evidence.connection() as db:
            db.execute('INSERT OR REPLACE INTO equity_state VALUES (?,?)', (self.env.identity,evidence.canonical(state)))

    def state(self):
        with evidence.connection() as db:
            return json.loads(db.execute('SELECT payload FROM equity_state WHERE scope=?',(self.env.identity,)).fetchone()[0])

    def legacy(self):
        first=risk.update_equity_state(None,equity=1000,at=AT-60,cash_flow=0,complete=True,policy=self.policy)
        old=risk.update_equity_state(first,equity=950,at=AT,cash_flow=0,complete=True,policy=self.policy)
        old.update(external_flow_total=37,external_flow_origin=AT-600)
        self.save(old)
        return old

    def guard(self, eq, usd, at):
        return gateway.equity_guard(self.env,balance(eq,usd,at),self.policy)

    def test_initial_and_fx_only_observations_use_usdt(self):
        first=self.guard(300,330,AT)
        second=self.guard(300,270,AT+1)
        self.assertEqual(first['equity'],300)
        self.assertEqual(second['equity_currency'],'USDT')
        self.assertEqual(second['peak_drawdown'],0)
        self.assertEqual(second['daily_drawdown'],0)
        self.assertFalse(second['blocked'])
        self.assertEqual(self.observe.call_args.args[1]['equity'],300)

    def test_usdt_deposit_and_fx_change_do_not_create_profit_or_loss(self):
        self.guard(300,330,AT)
        self.bills=[{'billId':'deposit','ts':str(int((AT+1)*1000)), 'type':'1','ccy':'USDT','balChg':'100'}]
        state=self.guard(400,360,AT+1)
        self.assertEqual(state['equity'],400)
        self.assertEqual(state['external_flow_total'],100)
        self.assertEqual(state['day_anchor'],400)
        self.assertEqual(state['peak_drawdown'],0)

    def test_same_timestamp_ignores_usd_repricing_but_checks_usdt(self):
        self.guard(300,330,AT)
        state=self.guard(300,270,AT)
        self.assertEqual(state['equity'],300)
        self.read.assert_not_called()
        with self.assertRaisesRegex(risk.RiskRejected,'Contradictory'):
            self.guard(299,270,AT)

    def test_same_timestamp_peak_only_block_is_recomputed_for_current_beijing_day(self):
        old=risk.update_equity_state(None,equity=900,at=AT,cash_flow=0,complete=True,policy=self.policy)
        old.update(equity_currency='USDT',peak=1200.,peak_drawdown=.25,blocked=True)
        self.save(old)
        with patch.object(risk,'beijing_day',return_value='2026-09-23'):
            state=self.guard(900,900,AT)
        self.assertFalse(state['blocked'])
        self.assertEqual(state['daily_drawdown'],0)
        self.assertAlmostEqual(state['peak_drawdown'],.25)
        self.assertEqual(state['day'],'2026-09-23')
        self.assertFalse(self.state()['blocked'])

    def test_same_timestamp_legacy_migration_preserves_history_and_ratios(self):
        old=self.legacy()
        observed_day=risk.beijing_day(AT)
        with patch.object(risk,'beijing_day',return_value=observed_day):
            state=self.guard(900,950,AT)
        self.assertEqual(state['equity'],900)
        self.assertAlmostEqual(state['peak_drawdown'],old['peak_drawdown'])
        self.assertAlmostEqual(state['daily_drawdown'],old['daily_drawdown'])
        self.assertEqual(state['external_flow_total'],37)
        self.assertEqual(state['external_flow_origin'],AT-600)
        self.assertEqual(state['currency_migration']['legacy_state'],old)
        self.assertFalse(state['currency_migration']['interval_unattributed'])
        self.assertEqual(self.state(),state)
        self.read.assert_not_called()

    def test_unattributed_bridge_preserves_loss_instead_of_using_current_fx(self):
        old=self.legacy()
        state=self.guard(900,810,AT+1)
        self.assertAlmostEqual(state['peak_drawdown'],old['peak_drawdown'])
        self.assertAlmostEqual(state['daily_drawdown'],old['daily_drawdown'])
        self.assertFalse(state['blocked'])
        self.assertTrue(state['currency_migration']['interval_unattributed'])
        self.assertEqual(state['currency_migration']['legacy_state'],old)
        # All subsequent changes are measured in actual USDT, not bridged again.
        next_state=self.guard(890,1000,AT+2)
        self.assertGreater(next_state['peak_drawdown'],state['peak_drawdown'])
        self.assertEqual(next_state['currency_migration'],state['currency_migration'])

    def test_bridge_retains_flow_origin_and_records_intervening_deposit(self):
        old=self.legacy()
        self.bills=[{'billId':'deposit','ts':str(int((AT+1)*1000)), 'type':'1','ccy':'USDT','balChg':'100'}]
        state=self.guard(1000,800,AT+1)
        self.assertEqual(state['external_flow_total'],137)
        self.assertEqual(state['external_flow_origin'],old['external_flow_origin'])
        self.assertAlmostEqual(state['daily_drawdown'],old['daily_drawdown'])

    def test_matching_pool_snapshot_preserves_real_pnl_across_migration(self):
        old=self.legacy()
        pool={'scope':self.env.identity,'currency':'USDT','at':AT,'account_equity':900}
        with evidence.connection() as db:
            db.execute('INSERT INTO capital_pool_state VALUES (?,?)',(self.env.identity,evidence.canonical(pool)))
        state=self.guard(890,810,AT+1)
        self.assertAlmostEqual(state['peak'],old['peak']*900/old['equity'])
        self.assertGreater(state['peak_drawdown'],old['peak_drawdown'])
        self.assertFalse(state['currency_migration']['interval_unattributed'])
        self.assertEqual(state['currency_migration']['method'],'same_timestamp_pool_observation')

    def test_unit_migration_clears_legacy_peak_only_block_but_preserves_peak_telemetry(self):
        old=self.legacy()
        old['peak']=1200.
        old['peak_drawdown']=.25
        old['blocked']=True
        self.save(old)
        state=self.guard(900,810,AT+1)
        self.assertFalse(state['blocked'])
        self.assertAlmostEqual(state['daily_drawdown'],old['daily_drawdown'])
        self.assertGreater(state['peak_drawdown'],risk.Policy().peak_drawdown_pct)
        self.assertEqual(state['currency_migration']['legacy_state'],old)

    def test_daily_loss_still_blocks_above_three_percent(self):
        policy=risk.Policy(daily_drawdown_pct=.03,peak_drawdown_pct=.08)
        old=risk.update_equity_state(None,equity=1000,at=AT-1,cash_flow=0,complete=True,policy=policy)
        old['equity_currency']='USDT'
        self.save(old)
        with self.assertRaisesRegex(risk.RiskRejected,'Daily equity drawdown circuit breaker'):
            gateway.equity_guard(self.env,balance(960,960,AT),policy)
        self.assertTrue(self.state()['blocked'])

    def test_backwards_timestamp_never_overwrites_legacy_history(self):
        old=self.legacy()
        with self.assertRaises(risk.RiskRejected):self.guard(900,810,AT-1)
        self.assertEqual(self.state(),old)

    def test_missing_usdt_equity_is_not_replaced_by_available_or_usd(self):
        snapshot=balance(300,330,AT)
        del snapshot['details'][0]['eq']
        with self.assertRaises(risk.RiskRejected):gateway.equity_guard(self.env,snapshot,self.policy)
