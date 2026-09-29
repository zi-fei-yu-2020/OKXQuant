"""Daily loss uses scoped reconciled USDT NAV, not a historical display baseline."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import operational_status, risk_policy, strategy_evidence

BJ=timezone(timedelta(hours=8))
DAY=datetime(2026,9,29,16,38,tzinfo=BJ)
SCOPE='okx:demo:test-daily-anchor'
ANCHOR=16653.97032845406
EQUITY=16508.25799532003
LOSS=ANCHOR-EQUITY


def receipt(loss, scope=SCOPE, day='2026-09-29'):
    return {'environment_id':scope,'status':'closed','close_time':day+' 16:00:00',
            'net_pnl':-loss}


def store(path, scope=SCOPE, *, day='2026-09-29', anchor=ANCHOR, equity=EQUITY, currency='USDT'):
    state={'day':day,'at':DAY.timestamp(),'equity':equity,
           'day_anchor':anchor,'equity_currency':currency,
           'daily_drawdown':max(0,(anchor-equity)/anchor),'blocked':False}
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE equity_state(scope TEXT PRIMARY KEY,payload TEXT);'
                         'CREATE TABLE capital_pool_state(scope TEXT PRIMARY KEY,payload TEXT);'
                         'CREATE TABLE intents(scope TEXT,state TEXT);')
        db.execute('INSERT INTO equity_state VALUES (?,?)',(scope,json.dumps(state)))
    return state


class DailyAnchorTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.db=Path(temp.name)/'strategy_evidence.db'
        path_patch=patch.object(strategy_evidence,'DB_PATH',self.db)
        path_patch.start();self.addCleanup(path_patch.stop)
        baseline_patch=patch('okxquant_backend.account_baseline.load_account_baseline',return_value={
            'baseline_configured':True,'account_scope':SCOPE,'initial_capital':5000,
            'reset_time':'2026-09-13 00:05:56'})
        baseline_patch.start();self.addCleanup(baseline_patch.stop)

    def calculate(self, rows=None, *, now=DAY, scope=SCOPE):
        return risk_policy.ledger_daily_drawdown(risk_policy.Policy(),now=now,
            rows=rows if rows is not None else [receipt(LOSS)],scope=scope)

    def test_live_day_anchor_replaces_5000_historical_display_baseline(self):
        store(self.db)
        result=self.calculate()
        self.assertEqual(result['reason'],'lifecycle_ledger_daily_loss')
        self.assertEqual(result['basis'],'reconciled_usdt_day_equity_anchor')
        self.assertEqual(result['day_anchor'],ANCHOR)
        self.assertAlmostEqual(result['drawdown'],.008749405112429786)
        self.assertFalse(result['blocked'])
        self.assertAlmostEqual(result['net_pnl'],-LOSS)

    def test_three_percent_threshold_uses_daily_anchor(self):
        store(self.db)
        self.assertFalse(self.calculate([receipt(499)])["blocked"])
        self.assertTrue(self.calculate([receipt(500)])["blocked"])

    def test_missing_other_account_prior_day_and_usd_anchor_never_use_old_5000(self):
        for changes in ({'scope':'okx:demo:other'}, {'day':'2026-09-28'}, {'currency':'USD'}):
            with self.subTest(changes=changes):
                if self.db.exists():self.db.unlink()
                store(self.db,**changes)
                result=self.calculate()
                self.assertEqual(result['reason'],'equity_day_anchor_unavailable')
                self.assertFalse(result['blocked'])
                self.assertIsNone(result['drawdown'])

    def test_adjusted_external_cash_flow_changes_anchor_not_historical_baseline(self):
        store(self.db,anchor=ANCHOR+100,equity=EQUITY+100)
        result=self.calculate()
        self.assertAlmostEqual(result['drawdown'],LOSS/(ANCHOR+100))
        self.assertEqual(result['day_anchor'],ANCHOR+100)

    def test_next_day_requires_next_day_equity_anchor(self):
        store(self.db)
        tomorrow=DAY+timedelta(days=1)
        result=self.calculate([receipt(500,day='2026-09-30')],now=tomorrow)
        self.assertEqual(result['reason'],'equity_day_anchor_unavailable')
        self.assertFalse(result['blocked'])

    def test_operational_status_displays_day_equity_not_old_baseline(self):
        state=store(self.db)
        env=SimpleNamespace(identity=SCOPE,mode='demo')
        original=risk_policy.ledger_daily_drawdown
        with patch('scripts.risk_policy.load_policy',return_value=risk_policy.Policy()), \
             patch('scripts.execution_profiles.runtime',return_value={'execution':{'id':'standard'}}), \
             patch('scripts.risk_policy.ledger_daily_drawdown',side_effect=lambda policy,scope:
                   original(policy,now=DAY,rows=[receipt(LOSS)],scope=scope)):
            result=operational_status.risk_snapshot(self.db.parent,env=env,now=DAY.timestamp())
        self.assertAlmostEqual(result['daily_drawdown'],state['daily_drawdown'])
        self.assertFalse(result['daily_blocked'])

    def test_small300_pool_still_enforces_its_own_three_percent(self):
        store(self.db)
        env=SimpleNamespace(identity=SCOPE,mode='demo')
        with sqlite3.connect(self.db) as db:
            db.execute('INSERT INTO capital_pool_state VALUES (?,?)',
                (SCOPE+':execution:small300',json.dumps({
                    'checked_at':DAY.timestamp(),'drawdown':{
                        'day':'2026-09-29','at':DAY.timestamp(),
                        'daily_drawdown':.035,'blocked':True}})))
        original=risk_policy.ledger_daily_drawdown
        with patch('scripts.risk_policy.load_policy',return_value=risk_policy.Policy()), \
             patch('scripts.execution_profiles.runtime',return_value={'execution':{'id':'small300'}}), \
             patch('scripts.risk_policy.ledger_daily_drawdown',side_effect=lambda policy,scope:
                   original(policy,now=DAY,rows=[receipt(LOSS)],scope=scope)):
            result=operational_status.risk_snapshot(self.db.parent,env=env,now=DAY.timestamp())
        self.assertTrue(result['daily_blocked'])
        self.assertEqual(result['daily_drawdown'],.035)


if __name__=='__main__':unittest.main()
