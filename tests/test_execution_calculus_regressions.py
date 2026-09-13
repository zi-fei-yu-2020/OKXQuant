"""Offline regression coverage for the real execution fetch and portfolio admission.

Run via scripts/run_tests.py under WSL/Linux, never raw discovery in a live checkout.
All market/account/model/order boundaries are mocked; writes stay in a temporary dir.
"""
from contextlib import ExitStack
import copy
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_factor_trader as trader
import calculus_engine
from scripts import trade_lock, wait_audit


NOW = 1_788_825_600.0
ITEM = {"instId": "TEST-USDT-SWAP", "name": "TEST", "type": "crypto",
        "base_sz": 2, "precision": 2, "ctVal": 0.01}
MISSING = object()


def candle_fixture():
    """Deliberately distinct O/H/L/C/V values expose any one-column shift."""
    result = {}
    for tf, count, step in (("15m", 45, 900_000), ("1H", 35, 3_600_000), ("4H", 25, 14_400_000)):
        rows = []
        for i in range(count):
            close = 100 + i * 0.2 + i * i * 0.01
            rows.append([str(int(NOW * 1000) - (count - i) * step),
                         str(close - 0.4), str(close + 2), str(close - 3),
                         str(close), str(200 + i * 7), "9999", "8888", "1"])
        result[tf] = list(reversed(rows))
    return result


def valid_calculus(acceleration=0.0, continuation=50.0, breakdown=50.0):
    # Match the engine's aggregate contract: probability_theory has no valid flag.
    return {"valid": True, "acceleration": acceleration,
            "probability_theory": {"continuation_prob_pct": continuation,
                                   "breakdown_prob_pct": breakdown}}


class ExecutionFetchCalculusTests(unittest.TestCase):
    def fetch(self, raw, *, calculator_error=None, quote=None):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            stack.enter_context(patch.object(trader, "NEWS_SENTIMENT_FILE", str(Path(tmp) / "absent.json")))
            stack.enter_context(patch.object(trader, "load_adaptive_config", return_value={}))
            stack.enter_context(patch.object(trader.time, "time", return_value=NOW))
            fetch = stack.enter_context(patch.object(trader, "fetch_signal_candles",
                side_effect=lambda inst, bar, limit: raw[bar]))
            ticker = stack.enter_context(patch.object(trader.market, "get_json", return_value={
                "code": "0", "data": [quote if quote is not None else {"ts": str(int(NOW * 1000)), "last": "130",
                                        "bidPx": "129.99", "askPx": "130.01"}]}))
            calculator = stack.enter_context(patch.object(calculus_engine, "calculate_multi_timeframe",
                wraps=calculus_engine.calculate_multi_timeframe, side_effect=calculator_error))
            factors = trader.fetch_single_instrument_data(ITEM, [], 1000)
            self.assertEqual(fetch.call_args_list, [call(ITEM["instId"], "15m", 45),
                call(ITEM["instId"], "1H", 35), call(ITEM["instId"], "4H", 25)])
            return factors, calculator.call_args_list, ticker.call_count

    def test_actual_fetch_strips_timestamp_and_metadata_without_reversing_or_mutating(self):
        raw = candle_fixture()
        before = copy.deepcopy(raw)
        expected = {tf.upper(): [[float(value) for value in row[1:6]] for row in rows]
                    for tf, rows in raw.items()}
        # Existing brain-style OHLCV input still uses the unchanged shared engine.
        reference = calculus_engine.calculate_multi_timeframe(expected)
        factors, calls, ticker_calls = self.fetch(raw)
        self.assertEqual(calls, [call(expected)])
        self.assertEqual(raw, before)
        self.assertTrue(factors["market_data_valid"])
        self.assertTrue(factors["calculus"]["valid"])
        self.assertEqual(factors["calculus"], reference)
        self.assertEqual(ticker_calls, 1)
        self.assertEqual(factors["signal_close"], float(raw["15m"][0][4]))
        self.assertEqual(factors["vol_15m"], float(raw["15m"][0][5]))
        for tf, rows in expected.items():
            chronological = list(reversed(rows))
            direct = calculus_engine.calculate_calculus(
                [r[3] for r in chronological], [r[1] for r in chronological],
                [r[2] for r in chronological], [r[4] for r in chronological])
            self.assertEqual(factors["calculus"]["timeframes"][tf], direct)

    def test_empty_fetch_and_calculator_failure_cannot_supply_scale_in_evidence(self):
        for raw, error in (({"15m": [], "1H": [], "4H": []}, None),
                           (candle_fixture(), RuntimeError("mock calculator unavailable"))):
            with self.subTest(error=error):
                factors, _, _ = self.fetch(raw, calculator_error=error)
                self.assertFalse(factors["calculus"]["valid"])
                for side in ("long", "short"):
                    self.assertFalse(trader._scale_in_calculus_gate(factors["calculus"], side)[0])
                if not raw["15m"]:
                    self.assertFalse(factors["market_data_valid"])
                    self.assertEqual(factors["sz"], 0)

    def test_bad_realtime_quote_never_overwrites_signal_price_or_authorizes_entry(self):
        base = {"ts": str(int(NOW*1000)), "last": "130", "bidPx": "129.99", "askPx": "130.01"}
        for change in ({"last":"0"},{"last":"nan"},{"last":"inf"},{"bidPx":"0"},
                       {"askPx":"120"},{"ts":"0"},{"last":""}):
            with self.subTest(change=change):
                raw = candle_fixture()
                factors, _, _ = self.fetch(raw, quote={**base, **change})
                self.assertFalse(factors["market_data_valid"])
                self.assertEqual(factors["sz"], 0)
                self.assertEqual(factors["quote_as_of_ms"], 0)
                self.assertEqual(factors["price"], float(raw["15m"][0][4]))

    def test_partial_frames_cannot_crash_portfolio_arithmetic(self):
        for timeframe in ("15m", "1H", "4H"):
            for count in (0, 1, 5, 14, 19):
                with self.subTest(timeframe=timeframe, count=count):
                    raw = candle_fixture()
                    raw[timeframe] = raw[timeframe][:count]
                    factors, calls, _ = self.fetch(raw)
                    self.assertFalse(factors["market_data_valid"])
                    self.assertFalse(factors["calculus"]["valid"])
                    self.assertEqual(factors["sz"], 0)
                    self.assertEqual(calls, [])

    def test_missing_or_nonfinite_ohlcv_is_not_silently_filtered_into_valid_calculus(self):
        # 4H extraction otherwise only reads closes, so exercise the actual adapter.
        for column, bad in ((1, "nan"), (2, "inf"), (3, "-inf"), (5, "nan"), (5, None)):
            with self.subTest(column=column, bad=bad):
                raw = candle_fixture()
                raw["4H"][0][column] = bad
                factors, calls, _ = self.fetch(raw)
                self.assertFalse(factors["calculus"]["valid"])
                self.assertEqual(calls, [])
        raw = candle_fixture()
        raw["4H"][0] = raw["4H"][0][:5]
        factors, calls, _ = self.fetch(raw)
        self.assertFalse(factors["calculus"]["valid"])
        self.assertEqual(calls, [])


class ExecutionScaleInCalculusTests(unittest.TestCase):
    def exercise(self, side, calculus=MISSING, *, initial=False, position_changes=None,
                 tracker=None, confidence=0):
        """Run the actual decorated portfolio; intercept before any order execution."""
        inst = ITEM["instId"]
        position = {"instId": inst, "posSide": side, "side": side, "pos": 1,
                    "upl": 2, "uplRatio": 0.02, "avgPx": 100, "margin": 10}
        position.update(position_changes or {})
        positions = [] if initial else [position]
        factors = {**ITEM, "sz": 2, "price": 100, "bidPx": 99.99, "askPx": 100.01,
                   "atr": 2, "rsi": 50, "risk_per_trade_usd": 15, "market_data_valid": True,
                   "position": None if initial else copy.deepcopy(position)}
        if calculus is not MISSING:
            factors["calculus"] = copy.deepcopy(calculus)
        decision = {"action": "BUY_LONG" if side == "long" else "SELL_SHORT",
                    "confidence": confidence, "summary_reason": "offline fixture",
                    "entry_price": 100, "take_profit_price": 106 if side == "long" else 94,
                    "stop_loss_price": 98 if side == "long" else 102}
        cache = {inst: {"name": "TEST", "decision": decision,
                        "decision_id": "MOCK_DECISION", "data_as_of": NOW}}
        env = SimpleNamespace(mode="demo", identity="mock-execution-calculus")
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            for key, value in {"WORKSPACE_DIR": str(root), "DATA_DIR": str(root),
                    "LOGS_DIR": str(root), "LOG_FILE": str(root / "trader.log"),
                    "TRADER_LOCK_FILE": str(root / "trader.lock"),
                    "TRADER_SLOT_FILE": str(root / "slot.json")}.items():
                stack.enter_context(patch.object(trader, key, value))
            stack.enter_context(patch.object(trade_lock, "PATH", root / "writer.lock"))
            stack.enter_context(patch.object(wait_audit, "public_status", return_value={}))
            stack.enter_context(patch.object(trader.strategy_evidence, "best_effort"))
            stack.enter_context(patch.object(trader, "TARGET_INSTRUMENTS", [ITEM]))
            stack.enter_context(patch.object(trader, "freeze_okx_environment", return_value=env))
            stack.enter_context(patch.object(trader, "unfreeze_okx_environment"))
            stack.enter_context(patch.object(trader.market, "_selected", return_value=env))
            stack.enter_context(patch.object(trader.market, "begin_signal_frame"))
            stack.enter_context(patch.object(trader, "okx_private_command", side_effect=lambda cmd: cmd))
            stack.enter_context(patch.object(trader, "clean_stale_open_orders", return_value=(True, "")))
            stack.enter_context(patch.object(trader, "query_positions", return_value=(True, positions, "")))
            stack.enter_context(patch.object(trader, "run_cmd_result", return_value={
                "ok": True, "data": [], "stdout": "", "stderr": ""}))
            stack.enter_context(patch.object(trader, "run_json_cmd", return_value=[{
                "details": [{"ccy": "USDT", "availBal": "1000"}]}]))
            stack.enter_context(patch.object(trader, "fetch_single_instrument_data",
                side_effect=lambda *args: copy.deepcopy(factors)))
            stack.enter_context(patch.object(trader, "load_trackers",
                return_value={f"{inst}_{side}": tracker or {}}))
            for name in ("save_trackers", "manage_position_tp_and_trailing", "execute_ai_position_management"):
                stack.enter_context(patch.object(trader, name))
            stack.enter_context(patch.object(trader, "prune_trackers", return_value=0))
            stack.enter_context(patch.object(trader, "is_circuit_breaker_active", return_value=(False, "")))
            brain = stack.enter_context(patch.object(trader, "execute_batch_ai_brain_cycle", return_value=cache))
            stack.enter_context(patch.object(trader, "load_adaptive_config", return_value={}))
            stack.enter_context(patch.object(trader, "evaluate_asset_signal",
                return_value=(0, "HOLD", [], "test", "test")))
            stack.enter_context(patch.object(trader.support, "pool_support",
                return_value={"items": {inst: {"can_open": True}}}))
            submit = stack.enter_context(patch.object(trader, "submit_protected_limit_order",
                return_value=(False, "mock boundary: no order sent")))
            process = stack.enter_context(patch.object(trader.subprocess, "run",
                side_effect=AssertionError("No subprocess is allowed")))
            stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
            trader.execute_portfolio()
            brain.assert_called_once()
            process.assert_not_called()
            self.assertTrue((root / "trading_state.json").exists(), "portfolio must finish, not abort")
            return submit.call_args_list

    def test_actual_scale_in_denies_missing_invalid_and_malformed_calculus(self):
        cases = [MISSING, None, {}, [], {"valid": False},
                 {"valid": False, "acceleration": 0, "probability_theory": {
                     "continuation_prob_pct": 100, "breakdown_prob_pct": 100}},
                 {"acceleration": 0, "probability_theory": {}},
                 {"valid": True}, {"valid": True, "acceleration": 0},
                 {"valid": True, "acceleration": 0, "probability_theory": None},
                 {"valid": True, "acceleration": 0, "probability_theory": []},
                 {"valid": True, "acceleration": 0, "probability_theory": {}},
                 {"valid": True, "probability_theory": valid_calculus()["probability_theory"]}]
        for flag in (None, "true", 1):
            case = valid_calculus()
            case["valid"] = flag
            cases.append(case)
        case = valid_calculus()
        case["probability_theory"]["valid"] = False
        cases.append(case)
        for side in ("long", "short"):
            for i, case in enumerate(cases):
                with self.subTest(side=side, case=i):
                    self.assertEqual(self.exercise(side, case, confidence=100), [])

    def test_actual_scale_in_denies_nonfinite_missing_and_zero_probability(self):
        for side, key in (("long", "continuation_prob_pct"), ("short", "breakdown_prob_pct")):
            for field in ("acceleration", key):
                for bad in (None, "", "not-numeric", float("nan"), float("inf"), float("-inf"), True):
                    with self.subTest(side=side, field=field, bad=bad):
                        case = valid_calculus()
                        target = case if field == "acceleration" else case["probability_theory"]
                        target[field] = bad
                        self.assertEqual(self.exercise(side, case), [])
            for probability in (0, "0", 39.9, -1, 101):
                with self.subTest(side=side, probability=probability):
                    case = valid_calculus()
                    case["probability_theory"][key] = probability
                    self.assertEqual(self.exercise(side, case), [])
            case = valid_calculus()
            del case["probability_theory"][key]
            self.assertEqual(self.exercise(side, case), [])

    def test_actual_scale_in_retains_directional_thresholds_and_zero_acceleration(self):
        for side, boundary, outside in (("long", -0.25, -0.2501), ("short", 0.25, 0.2501)):
            for acceleration in (boundary, 0.0, "0"):
                with self.subTest(side=side, acceleration=acceleration):
                    case = valid_calculus(acceleration,
                        continuation=40 if side == "long" else 0,
                        breakdown=40 if side == "short" else 0)
                    calls = self.exercise(side, case, confidence=0)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[0].args[2], side)
                    self.assertEqual(calls[0].kwargs["decision_id"], "MOCK_DECISION")
            self.assertEqual(self.exercise(side, valid_calculus(outside)), [])

    def test_other_scale_in_risk_gates_are_not_relaxed(self):
        for side in ("long", "short"):
            for kwargs in ({"position_changes": {"upl": -2, "uplRatio": -0.02}},
                           {"position_changes": {"margin": trader.MAX_SINGLE_ASSET_MARGIN}},
                           {"tracker": {"scale_count": trader.MAX_SCALE_IN_COUNT}}):
                with self.subTest(side=side, kwargs=kwargs):
                    self.assertEqual(self.exercise(side, valid_calculus(), **kwargs), [])
            # A breakeven stop must not bypass missing calculus evidence either.
            self.assertEqual(self.exercise(side, tracker={"trailingStopPx": 100},
                position_changes={"upl": 0, "uplRatio": 0}), [])

    def test_initial_brain_entry_is_not_subject_to_scale_in_calculus_gate(self):
        for side in ("long", "short"):
            for case in (MISSING, None, {"valid": False}):
                for confidence in (0, 100):
                    with self.subTest(side=side, case=case, confidence=confidence):
                        calls = self.exercise(side, case, initial=True, confidence=confidence)
                        self.assertEqual(len(calls), 1)
                        self.assertEqual(calls[0].args[2], side)


if __name__ == "__main__":
    unittest.main()
