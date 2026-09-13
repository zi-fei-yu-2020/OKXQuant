import copy
from dataclasses import asdict
import unittest
from unittest.mock import patch
from scripts.backtest_engine import BacktestEngine, run_full_portfolio_backtest, performance
from scripts.signal_data import closed_candles, SignalDataError


def candles(n=20):
    return [{'symbol':'TEST','timestamp':str(i),'ts_ms':(i+1)*3600000,'open':100.,'high':100.,'low':100.,'close':100.,'volume':100.,'funding_rate':0.} for i in range(n)]

def engine(**kw):
    return BacktestEngine(initial_capital=10000,risk_per_trade_pct=.02,taker_fee=0,maker_fee=0,slippage=0,**kw)


def signal(at='17',**kw):
    return {'timestamp':at,'action':'BUY','confidence':1.,'rr':3.,'atr':10.,**kw}

class StrategyReplayTests(unittest.TestCase):
    def test_unrealized_loss_is_in_equity_and_drawdown(self):
        c=candles();c[-1].update(low=84,close=85)
        r=engine().run(c,[signal()])
        self.assertAlmostEqual(r.final_equity,9850.1498503,places=4)
        self.assertAlmostEqual(r.max_drawdown_pct,1.498501,places=4)
        self.assertEqual(len(r.open_positions),1)

    def test_same_bar_low_cannot_be_rewritten_to_breakeven_from_future_high(self):
        c=candles();c[-1].update(high=120,low=75)
        r=engine().run(c,[signal()]);self.assertEqual(r.total_trades,1)
        self.assertEqual(r.trades[0]['exit_price'],80)
        self.assertAlmostEqual(r.trades[0]['r_multiple'],-1,places=5)

    def test_gap_through_stop_uses_worse_open(self):
        c=candles();c[-1].update(open=70,high=75,low=65,close=70)
        r=engine().run(c,[signal()]);self.assertEqual(r.trades[0]['exit_price'],70)
        self.assertLess(r.trades[0]['r_multiple'],-1)

    def test_order_not_filled_at_same_close_as_signal(self):
        c=candles();r=engine().run(c,[signal('19')])
        self.assertEqual(r.open_positions,[]);self.assertEqual(len(r.pending_orders),1)
        self.assertEqual(r.final_equity,10000)

    def test_explicit_empty_signals_remain_empty_not_baseline(self):
        c=candles(60)
        for i,row in enumerate(c):row.update(open=100+i,high=100+i,low=100+i,close=100+i)
        r=engine().run(c,[])
        self.assertEqual(r.total_trades,0);self.assertEqual(r.open_positions,[])
        self.assertEqual(r.strategy_kind,'explicit_frozen_signals')

    def test_funding_applied_to_held_position_and_not_faked(self):
        c=candles();c[-1]['funding_rate']=.001
        r=engine().run(c,[signal()]);self.assertAlmostEqual(r.final_equity,9999.000999,places=4)
        self.assertTrue(r.funding_complete)
        del c[-1]['funding_rate'];r=engine().run(c,[signal()])
        self.assertEqual(r.status,'exploratory_missing_funding')

    def test_limit_partial_fills_respect_volume_and_original_risk(self):
        c=candles(23)
        for row in c[18:]:row.update(open=101,high=102,low=99,close=101)
        r=engine().run(c,[signal(entry_price=100,stop_loss_price=90,take_profit_price=130)])
        self.assertAlmostEqual(r.open_positions[0]['size'],2)
        self.assertEqual(r.pending_orders,[])
        self.assertGreater(r.open_positions[0]['initial_risk'],0)

    def test_wait_model_cost_is_not_treated_as_free(self):
        r=engine().run(candles(),[{'timestamp':'18','action':'WAIT','model_cost_usdt':25}])
        self.assertEqual(r.final_equity,9975)
        self.assertEqual(r.operating_costs_usdt,25)
        self.assertEqual(r.total_trades,0)

    def test_engine_resets_on_reuse(self):
        e=engine();c=candles();c[-1].update(low=75)
        self.assertEqual(asdict(e.run(c,[signal()])),asdict(e.run(c,[signal()])))

    def test_real_data_failure_returns_unavailable_not_sine_wave(self):
        with patch('scripts.backtest_engine.fetch_okx_candles',return_value=[]):
            report=run_full_portfolio_backtest(symbols=['TEST'])
        self.assertEqual(report['status'],'unavailable');self.assertFalse(report['synthetic_fallback'])
        self.assertEqual(report['by_symbol'],{})

    def test_portfolio_requires_alignment_and_uses_shared_equity(self):
        a=candles();b=copy.deepcopy(a)
        for row in b:row['symbol']='OTHER'
        b[-1]['timestamp']='missing'
        with self.assertRaises(ValueError):engine().run_portfolio({'TEST':a,'OTHER':b},{'TEST':[],'OTHER':[]})
        b[-1]['timestamp']=a[-1]['timestamp']
        r=engine().run_portfolio({'TEST':a,'OTHER':b},{'TEST':[],'OTHER':[]})
        self.assertEqual(r.initial_equity,10000);self.assertEqual(r.final_equity,10000)
        self.assertEqual(r.symbol,'SHARED_EQUITY_PORTFOLIO')

    def test_actual_interval_controls_annualization_no_99_sentinel(self):
        curve=[{'equity':v} for v in [100,101,99,102]]
        hourly=performance(curve,100,'1H');four=performance(curve,100,'4H')
        self.assertAlmostEqual(hourly['sharpe_ratio'],four['sharpe_ratio']*2)
        self.assertIsNone(performance([{'equity':100}],100,'1H')['sharpe_ratio'])

class ClosedBarContractTests(unittest.TestCase):
    def row(self,ts,confirm='1'):return [str(ts),'100','101','99','100','10','0','0',confirm]
    def test_unclosed_future_bar_excluded_at_common_asof(self):
        rows=[self.row(7200000,'0'),self.row(3600000),self.row(0)]
        out=closed_candles(rows,'1H',as_of_ms=7200001)
        self.assertEqual([r[0] for r in out],['3600000','0'])
    def test_missing_confirmation_gap_and_stale_feed_fail_closed(self):
        for rows,at in [([self.row(0)[:6]],3600000),([self.row(7200000),self.row(0)],10800000),([self.row(0)],12000000)]:
            with self.assertRaises(SignalDataError):closed_candles(rows,'1H',as_of_ms=at)

if __name__=='__main__':unittest.main()
