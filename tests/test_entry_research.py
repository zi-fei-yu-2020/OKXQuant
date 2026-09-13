import copy
from dataclasses import asdict
from datetime import datetime,timezone
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts import entry_research as study
from scripts.research_data import PublicCapture
from scripts.backtest_engine import BacktestEngine
from scripts.risk_policy import Policy
from test_strategy_replay import candles,signal


def fixture(n=1200):
    """Explicit synthetic unit fixture, never saved as research market data."""
    rows=[]
    for i in range(n):
        p=100+i*.02;at=(i+1)*300000
        rows.append({'symbol':'TEST','timestamp':datetime.fromtimestamp(at/1000,timezone.utc).isoformat(),'ts_ms':at,
                     'open':p-.01,'high':p+.01,'low':p-.02,'close':p,'volume':100,'funding_rate':0,'confirm':True})
    return rows


class EntryResearchTests(unittest.TestCase):
    def test_resampling_requires_full_bars_and_does_not_fill_gaps(self):
        rows=fixture(13)
        self.assertEqual(len(study.resample(rows,60)),1)
        self.assertEqual(study.resample(rows[:11],60),[])
        self.assertEqual(study.resample(rows[:5]+rows[6:12],60),[])

    def test_future_mutations_do_not_change_earlier_candidates(self):
        rows=fixture();meta={'tickSz':'.01'}
        for variant in study.SPEC['variants']:
            baseline=study.generate(rows,meta,variant)
            changed=copy.deepcopy(rows)
            for row in changed[1100:]:
                for field in ('open','high','low','close'):row[field]*=2
            cutoff=rows[1099]['ts_ms']
            expected=[s for s in baseline['signals'] if s['features_as_of_ms']<=cutoff]
            actual=[s for s in study.generate(changed,meta,variant)['signals'] if s['features_as_of_ms']<=cutoff]
            self.assertEqual(expected,actual)
        self.assertGreater(len(study.generate(rows,meta,'breakout_5m')['signals']),0)

    def test_triggers_are_later_than_setup_and_not_production_authority(self):
        result=study.generate(fixture(),{'tickSz':'.01'},'breakout_5m')
        for s in result['signals']:
            self.assertGreater(s['generated_at_ms'],s['setup_created_at_ms'])
            self.assertNotIn('contract_valid',s)
            self.assertNotIn('confidence',s)
            self.assertIn('not_production_authority',s['provenance'])
        ids=[s['decision_id'] for s in result['signals']]
        self.assertEqual(len(ids),len(set(ids)))

    def test_15m_variant_only_triggers_at_closed_15m_boundaries(self):
        signals=study.generate(fixture(),{'tickSz':'.01'},'breakout_15m')['signals']
        self.assertTrue(signals)
        self.assertTrue(all(s['features_as_of_ms']%900000==0 for s in signals))

    def test_funding_is_mapped_to_next_bar_open(self):
        rows=fixture(3);rows[0]['funding_rate']=.001
        dataset={'series':{'TEST':rows},'funding_complete':True}
        result=study.prepare_series(dataset)['TEST']
        self.assertEqual(result[0]['funding_rate'],0)
        self.assertEqual(result[1]['funding_rate'],.001)
        self.assertEqual(rows[0]['funding_rate'],.001)

    def test_capture_rejects_private_endpoints_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=PublicCapture(directory)
            with self.assertRaises(ValueError):capture.read('/api/v5/trade/order',{})

    def test_capture_missing_candles_is_error_not_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=PublicCapture(directory)
            with patch.object(capture,'read',return_value=[]):
                with self.assertRaises(ValueError):capture.candles('TEST',0,600000)


class ReplayLeverageTests(unittest.TestCase):
    def test_same_notional_same_pnl_and_leverage_reported(self):
        rows=candles();rows[-1].update(high=102,close=102)
        values=[]
        for leverage in (1,3,5):
            result=BacktestEngine(initial_capital=10000,leverage=leverage,maker_fee=0,taker_fee=0,slippage=0).run(rows,
                [signal(entry_price=100,stop_loss_price=95,take_profit_price=115,size=1)])
            self.assertEqual(result.replay_leverage,leverage)
            self.assertEqual(result.open_positions[0]['leverage'],leverage)
            values.append(result.final_equity)
        self.assertEqual(len(set(values)),1)

    def test_position_cap_includes_pending_reservations(self):
        rows=candles();other=copy.deepcopy(rows)
        for r in other:r['symbol']='OTHER'
        result=BacktestEngine(max_positions=1).run_portfolio({'TEST':rows,'OTHER':other},{'TEST':[signal()],'OTHER':[signal()]})
        self.assertEqual(result.accepted_orders,1)
        self.assertEqual(result.rejection_reasons.get('Position/pending slot cap'),1)

    def test_leverage_above_declared_policy_is_not_silently_accepted(self):
        with self.assertRaises(ValueError):BacktestEngine(leverage=10)

    def test_risk_rejection_keeps_reason_and_never_counts_fill(self):
        result=BacktestEngine().run(candles(),[signal(entry_price=100,stop_loss_price=99,take_profit_price=101)])
        self.assertEqual(result.filled_entries,0)
        self.assertIn('Net-of-cost R:R below policy',result.rejection_reasons)

    def test_pool_replay_risk_and_margin_limits_do_not_expand_with_account(self):
        rows=candles()
        result=BacktestEngine(initial_capital=300,capital_ceiling=300,asset_margin_fraction=.2,total_margin_fraction=.5,max_positions=2).run(rows,
            [signal(entry_price=100,stop_loss_price=98,take_profit_price=106)])
        self.assertLessEqual(result.open_positions[0]['initial_risk'],1.5)
        self.assertLessEqual(result.open_positions[0]['entry']*result.open_positions[0]['size']/3,60)

    def test_daily_drawdown_blocks_new_submission_but_exits_continue(self):
        rows=candles(24)
        rows[19].update(open=90,high=90,low=85,close=90)
        policy=Policy(daily_drawdown_pct=.001)
        result=BacktestEngine(policy=policy).run(rows,[signal('17',entry_price=100,stop_loss_price=95,take_profit_price=115),signal('20')])
        self.assertGreater(result.rejection_reasons.get('Observed daily/peak drawdown gate',0),0)
        self.assertGreater(result.total_trades,0)
