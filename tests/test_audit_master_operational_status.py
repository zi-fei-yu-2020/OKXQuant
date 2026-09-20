import json,sqlite3,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from scripts import operational_status as s
from scripts.risk_policy import Policy

class OperationalProjectionAudit(unittest.TestCase):
    def test_snapshot_uses_configured_policy_not_default_constant(self):
        env=SimpleNamespace(mode='demo',identity='demo')
        with patch('scripts.execution_profiles.runtime',return_value={'execution':{'id':'standard'},'profile_id':'custom','signature':'sig'}),patch('scripts.risk_policy.load_policy',return_value=Policy(max_leverage=4,scalp_max_leverage=12)),patch('scripts.okx_runtime.selected_environment',return_value=env),patch('scripts.instrument_pool.load_instruments',return_value=[{},{}]):
            result=s.execution_snapshot()
        self.assertEqual(result['mode_limits']['swing']['max_leverage'],4)
        self.assertEqual(result['mode_limits']['scalp']['max_leverage'],12)
        self.assertIsNone(result['execution']['total_margin_usdt'])

    def test_unknown_and_known_false_and_foreign_scope_are_distinct(self):
        env=SimpleNamespace(mode='demo',identity='demo-a');now=time.time();day=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
        with tempfile.TemporaryDirectory() as d,patch('scripts.risk_policy.load_policy',return_value=Policy()),patch('scripts.execution_profiles.runtime',return_value={'execution':{'id':'standard'}}),patch('scripts.risk_policy.ledger_daily_drawdown',return_value={'reason':'baseline_unavailable'}):
            self.assertIsNone(s.risk_snapshot(d,env=env,now=now)['daily_blocked'])
            db=sqlite3.connect(Path(d)/'strategy_evidence.db')
            db.executescript('CREATE TABLE intents(scope TEXT,state TEXT);CREATE TABLE equity_state(scope TEXT,payload TEXT);CREATE TABLE capital_pool_state(scope TEXT,payload TEXT);')
            db.execute('INSERT INTO equity_state VALUES (?,?)',('demo-a',json.dumps({'day':day,'at':now,'daily_drawdown':.001})))
            db.execute('INSERT INTO equity_state VALUES (?,?)',('live-b',json.dumps({'day':day,'at':now,'daily_drawdown':.9})))
            db.execute('INSERT INTO intents VALUES (?,?)',('demo-a','unknown'));db.commit()
            r=s.risk_snapshot(d,env=env,now=now)
            self.assertFalse(r['daily_blocked']);self.assertEqual(r['daily_drawdown'],.001);self.assertEqual(r['unresolved_entries'],1)
            self.assertIsNone(s.risk_snapshot(d,env=env,now=now+181)['daily_blocked'])
            db.close()

    def test_monitoring_cache_is_not_reused_by_shared_http_cache(self):
        import asyncio
        from dashboard import app as dash
        with patch.object(dash,'monitoring_snapshot',return_value={}):
            self.assertEqual(asyncio.run(dash.get_all_data()).headers['cache-control'],'no-store')
            self.assertEqual(asyncio.run(dash.get_overview()).headers['cache-control'],'no-store')
