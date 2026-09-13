import unittest
from scripts.backtest_engine import BacktestEngine, BacktestSummary


class BacktestEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = BacktestEngine(
            initial_capital=10000.0,
            risk_per_trade_pct=0.02,
            min_confidence_gate=0.70,
            min_rr_gate=1.5,
        )

    def test_empty_or_short_series_returns_zero_summary(self):
        res = self.engine.run([])
        self.assertIsInstance(res, BacktestSummary)
        self.assertEqual(res.total_trades, 0)
        self.assertEqual(res.initial_equity, 10000.0)

    def test_interceptor_filtering_and_risk_metrics(self):
        # Construct synthetic trend series
        candles = []
        base = 60000.0
        for i in range(100):
            p = base + (i * 100) if i < 50 else base + 5000 - ((i - 50) * 120)
            candles.append({
                "symbol": "ETH-USDT-SWAP",
                "timestamp": f"2026-09-01T{i:02d}:00:00Z",
                "open": p - 20,
                "high": p + 60,
                "low": p - 40,
                "close": p,
                "volume": 500.0,
            })

        signals = [
            # Signal 1: High confidence, high RR -> should trigger LONG
            {
                "timestamp": "2026-09-01T10:00:00Z",
                "action": "BUY",
                "confidence": 0.85,
                "rr": 2.5,
                "atr": 100.0,
            },
            # Signal 2: Low confidence (< 0.70) -> Gatekeeper must block
            {
                "timestamp": "2026-09-01T12:00:00Z",
                "action": "BUY",
                "confidence": 0.55,
                "rr": 2.0,
                "atr": 100.0,
            }
        ]

        summary = self.engine.run(candles, signals=signals)
        self.assertIsInstance(summary, BacktestSummary)
        self.assertGreaterEqual(summary.gatekeeper_filtered_count, 1)
        self.assertIn(summary.symbol, ["ETH-USDT-SWAP", "PORTFOLIO"])


if __name__ == "__main__":
    unittest.main()
