"""Offline brain regressions. Run under the main thread's isolated WSL runner.

Load the real brain under a private module name with external dependencies replaced
before import (including settings), so neither credentials nor production data are read.
Only temporary prompt/cache files and the real POSIX cycle lock touch the filesystem.
"""
import copy
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import urllib.request

import scripts
from scripts import trading_prompt, wait_audit, risk_policy


INST = "BTC-USDT-SWAP"


def module(name, **members):
    result = ModuleType(name)
    result.__dict__.update(members)
    return result


def proposal():
    return {"contract_version": trading_prompt.VERSION, "macro_assessment": "THIS-CYCLE",
        "position_management": [], "pending_orders_management": [], "decisions": {INST: {
            "action": "BUY_LONG", "confidence": 79, "entry_price": 100,
            "stop_loss_price": 95, "take_profit_price": 115, "margin_usdt": 10, "leverage": 3,
            "summary_reason": "Observed structure supports a candidate, not an order",
            "supporting_evidence": [
                {"ref": "/macro_4h", "value": "4H_MACRO_BULL", "interpretation": "Observed structure"},
                {"ref": "/price", "value": 100, "interpretation": "Observed current price"}],
            "counter_evidence_status": "none_observed", "counter_evidence": [],
            "uncertainty": "Execution and event risk remain",
            "invalidation": {"price": 95, "timeframe": "1H", "condition": "Price breaks support"},
            "valid_for_seconds": 120}}}


class BrainRegressions(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(sys, "path", list(sys.path)))
        self.stack.enter_context(patch.dict("os.environ", {}, clear=True))
        self.network = self.stack.enter_context(patch.object(urllib.request, "urlopen",
            side_effect=AssertionError("Network forbidden")))
        self.connect = self.stack.enter_context(patch.object(socket.socket, "connect",
            side_effect=AssertionError("Network forbidden")))
        self.create_connection = self.stack.enter_context(patch.object(socket, "create_connection",
            side_effect=AssertionError("Network forbidden")))
        self.market = module("public_market", begin_signal_frame=Mock(),
            _selected=Mock(return_value=SimpleNamespace(mode="demo", identity="offline-test")),
            signal_as_of=Mock(return_value=1_800_000_000.), smart_money_overview=Mock(return_value=[]),
            get_json=Mock(return_value={}), signal_json=Mock(return_value={}),
            signal_indicators=Mock(return_value={}))
        self.cli = Mock(return_value=SimpleNamespace(returncode=0, stdout="[]", stderr=""))
        self.llm = Mock(return_value=(json.dumps(proposal()), "", {}, 1))
        self.council = Mock(return_value=(proposal(), {"total_duration_ms": 1}))
        self.council_config = Mock(return_value={"enabled": True})
        self.telemetry = MagicMock()
        self.evidence = module("scripts.strategy_evidence", best_effort=Mock(), record_decisions=Mock())
        self.writer = Mock(side_effect=AssertionError("Trading forbidden"))
        self.barrier = Mock(side_effect=AssertionError("Trading forbidden"))
        mods = {
            "okx_runtime": module("okx_runtime", replace_cli_prefix=Mock(side_effect=lambda c: c)),
            "public_market": self.market,
            "instrument_support": module("instrument_support", trading_universe=Mock()),
            "algo_reader": module("algo_reader", command_barrier=self.barrier),
            "instrument_pool": module("instrument_pool", load_instruments=Mock(return_value=[])),
            "prompt_library": module("prompt_library", active_profile=Mock(return_value={"id": "offline"})),
            "factor_library": module("factor_library", update_factor_library=Mock()),
            "calculus_engine": module("calculus_engine", calculate_multi_timeframe=Mock(return_value={"valid": False})),
            "subprocess": module("subprocess", run=self.cli),
            "okxquant_backend": module("okxquant_backend", __path__=[]),
            "okxquant_backend.config": module("okxquant_backend.config", settings=None),
            "okxquant_backend.llm_manager": module("okxquant_backend.llm_manager",
                get_active_llm_runtime=Mock(return_value={"model": "offline", "api_key": "FAKE", "base_url": "https://example.invalid"}),
                execute_llm_request=self.llm),
            "okxquant_backend.council_manager": module("okxquant_backend.council_manager",
                load_council_config=self.council_config, execute_council_debate=self.council),
            "okxquant_backend.interceptor_manager": module("okxquant_backend.interceptor_manager",
                run_interceptor_pipeline=Mock(side_effect=lambda p, d, c: (d["action"], "", 3.))),
            "okxquant_gateway": module("okxquant_gateway", __path__=[]),
            "okxquant_gateway.telemetry": module("okxquant_gateway.telemetry", ModelCallTelemetry=Mock(return_value=self.telemetry)),
            "scripts.strategy_evidence": self.evidence,
            "scripts.trade_lock": module("scripts.trade_lock", writer=self.writer),
            "scripts.capital_pool": module("scripts.capital_pool", status=Mock(return_value={"enabled": False})),
        }
        self.stack.enter_context(patch.dict(sys.modules, mods))
        for name in ("strategy_evidence", "trade_lock", "capital_pool"):
            self.stack.enter_context(patch.object(scripts, name, mods["scripts." + name], create=True))
        spec = importlib.util.spec_from_file_location("_offline_brain_regression",
            Path(__file__).resolve().parents[1] / "scripts" / "ai_brain_trader.py")
        self.brain = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.brain)
        b = self.brain
        self.stack.enter_context(patch.object(b, "DATA_DIR", str(self.root)))
        for name in ("AI_DECISION_CACHE_FILE", "AI_POSITION_MANAGEMENT_FILE", "AI_LAST_PROMPT_FILE",
                     "AI_DECISION_HISTORY_FILE", "CALCULUS_SNAPSHOT_FILE", "AI_BRAIN_LOCK_FILE",
                     "FACTOR_LIBRARY_FILE", "NEWS_SENTIMENT_FILE", "AI_MEMORY_MD_FILE", "AI_MEMORY_FILE",
                     "PROMPT_OVERRIDE_FILE"):
            self.stack.enter_context(patch.object(b, name, str(self.root / name)))
        self.stack.enter_context(patch.object(b, "get_cpa_client_config", return_value=("https://example.invalid", "FAKE")))
        self.stack.enter_context(patch.object(b, "get_user_prompt_override", return_value=""))
        self.stack.enter_context(patch.object(risk_policy, "load_policy", return_value=risk_policy.Policy()))
        self.stack.enter_context(patch.object(wait_audit, "prepare", return_value={}))
        self.stack.enter_context(patch.object(wait_audit, "commit", return_value={"status": "ready"}))
        # Use the real package constructor too; every market method is an in-memory stub.
        self.p = b.fetch_single_instrument_package({"instId": INST, "name": "BTC", "type": "crypto", "precision": 2})
        self.p.update(price=100., bidPx=99.9, askPx=100.1, chg24h=1., oiUsd=1000.,
            takerNetUsd=50., data_quality="valid", macro_4h="4H_MACRO_BULL",
            structure_1h="1H_SWING_BULL", adx_1h=25., atr_1h=2.)
        b.support.trading_universe.return_value = ([self.p], {"items": {INST: {"can_open": True}}})
        self.stack.enter_context(patch.object(b, "fetch_single_instrument_package", return_value=self.p))
        executor = MagicMock()
        executor.__enter__.return_value.map.side_effect = lambda fn, rows: map(fn, rows)
        self.stack.enter_context(patch.object(b, "ThreadPoolExecutor", return_value=executor))

    def run_cycle(self):
        Path(self.brain.AI_DECISION_CACHE_FILE).write_text(json.dumps({"OLD": {"decision": {"action": "BUY_LONG"}}}), encoding="utf-8")
        result = self.brain.execute_batch_ai_brain_cycle(active_positions_detail=[], usdt_available=1000)
        self.assertIsNotNone(result, self.brain.get_last_inference_error())
        self.assertEqual(self.brain.get_last_inference_error(), "")
        self.assertEqual(set(result), {INST})
        self.assertEqual(result, json.loads(Path(self.brain.AI_DECISION_CACHE_FILE).read_text(encoding="utf-8")))
        self.assertEqual(result[INST]["macro_assessment"], "THIS-CYCLE")
        self.assertEqual(json.loads(Path(self.brain.AI_DECISION_HISTORY_FILE).read_text(encoding="utf-8"))[0]["macro_assessment"], "THIS-CYCLE")
        self.assertNotEqual(json.loads((self.root / "trading_output_validation.json").read_text(encoding="utf-8"))["status"], "rejected")
        self.cli.assert_called_once()
        self.assertIn("swap orders", self.cli.call_args.args[0])
        self.writer.assert_not_called()
        self.barrier.assert_not_called()
        self.network.assert_not_called()
        self.connect.assert_not_called()
        self.create_connection.assert_not_called()
        return result

    def prompt_data(self):
        return json.JSONDecoder().raw_decode(self.council.call_args.kwargs["market_prompt"])[0]

    def test_council_success_returns_this_cycle_and_finishes_success_without_fallback(self):
        output = copy.deepcopy(self.council.return_value[0])
        result = self.run_cycle()
        self.assertEqual(result[INST]["decision"]["action"], "BUY_LONG")
        self.assertTrue(result[INST]["decision"]["contract_valid"])
        self.council.assert_called_once()
        self.llm.assert_not_called()
        self.telemetry.finish.assert_called_once_with("success", None,
            output_chars=len(trading_prompt.canonical(output)))
        self.assertEqual(self.council.return_value[0], output)

    def test_single_model_and_council_fallback_keep_raw_text_length(self):
        for enabled in (False, True):
            with self.subTest(council_enabled=enabled):
                self.council_config.return_value = {"enabled": enabled}
                self.council.side_effect = RuntimeError("offline council failure")
                self.cli.reset_mock(); self.llm.reset_mock(); self.telemetry.reset_mock()
                self.run_cycle()
                self.llm.assert_called_once()
                self.telemetry.finish.assert_called_once_with("success", {"usage": {}},
                    output_chars=len(self.llm.return_value[0]))

    def test_missing_smart_money_never_becomes_neutral_facts_in_actual_cycle(self):
        self.assertFalse(self.p["smart_money"]["valid"])
        for source in ([], [{"ccy": "ETH"}], [{"ccy": "BTC"}], [{"ccy": "BTC", "notional": None}], RuntimeError("offline missing")):
            with self.subTest(source=source):
                self.p["smart_money"] = {"valid": True, "weighted_long_pct": 50., "net_flow_usdt": "0 U"}
                self.market.smart_money_overview.side_effect = source if isinstance(source, Exception) else None
                self.market.smart_money_overview.return_value = source
                self.cli.reset_mock(); self.telemetry.reset_mock()
                result = self.run_cycle()
                self.assertFalse(result[INST]["smart_money"]["valid"])
                data = self.prompt_data()
                self.assertFalse(any(k.startswith("/smart_money/") for k in data["facts"][INST]))
                self.assertIn("加权做多占比=UNKNOWN", data["runtime_data"]["market_matrix"])
                self.assertIn("当前多空净名义敞口=UNKNOWN", data["runtime_data"]["market_matrix"])
                self.assertNotIn("24H净流入=", data["runtime_data"]["market_matrix"])
        self.llm.assert_not_called()

    def test_smart_money_reference_is_checked_again_in_actual_cycle(self):
        for source, action in (([], "WAIT"),
                ([{"ccy": "BTC", "notional": {"netNotionalUsdt": 0}}], "WAIT"),
                ([{"ccy": "BTC", "longShortRatio": {"weightedLongRatio": .5}}], "BUY_LONG")):
            with self.subTest(source=source):
                output = proposal()
                output["decisions"][INST]["supporting_evidence"] = [
                    {"ref": "/price", "value": 100, "interpretation": "Observed current price"},
                    {"ref": "/smart_money/weighted_long_pct", "value": 50., "interpretation": "Claimed flow observation"}]
                self.council.return_value = (output, {})
                self.market.smart_money_overview.return_value = source
                self.cli.reset_mock(); self.telemetry.reset_mock()
                result = self.run_cycle()
                self.assertEqual(result[INST]["decision"]["action"], action)
                self.assertEqual(result[INST]["decision"]["contract_valid"], action == "BUY_LONG")
                facts = self.prompt_data()["facts"][INST]
                self.assertEqual("/smart_money/weighted_long_pct" in facts, action == "BUY_LONG")
        self.llm.assert_not_called()

    def test_invalid_wait_gets_one_bounded_correction_before_any_write(self):
        from test_wait_repair import macro_wait
        fixed=macro_wait();bad=copy.deepcopy(fixed);bad['wait_audit']['short']['code']='position_constraint'
        initial=proposal();initial['decisions'][INST]=bad
        self.council_config.return_value={'enabled':False}
        self.llm.side_effect=[(json.dumps(initial),'',{},1),(json.dumps({'decisions':{INST:fixed}}),'',{},1)]
        result=self.run_cycle()
        self.assertEqual(result[INST]['decision']['decision_status'],'audited_wait')
        self.assertEqual(result[INST]['decision']['wait_repair']['status'],'corrected')
        self.assertEqual(self.llm.call_count,2)
        self.assertEqual(self.llm.call_args.kwargs['max_attempts'],1)
        self.assertEqual(self.llm.call_args.kwargs['timeout'],20)
        self.writer.assert_not_called();self.barrier.assert_not_called()
        events=[c for c in self.evidence.best_effort.call_args_list if c.args[1]=='wait_audit_repair']
        self.assertEqual(len(events),1)
        self.assertEqual(events[0].args[2]['report']['original_waits'][INST]['value'],bad)

    def test_correction_transport_failure_keeps_incomplete_without_call_loop(self):
        from test_wait_repair import macro_wait
        bad=macro_wait();bad['wait_audit']['short']['code']='position_constraint'
        initial=proposal();initial['decisions'][INST]=bad
        self.council_config.return_value={'enabled':False}
        self.llm.side_effect=[(json.dumps(initial),'',{},1),TimeoutError('fixture')]
        result=self.run_cycle()
        self.assertEqual(result[INST]['decision']['decision_status'],'incomplete')
        self.assertEqual(result[INST]['decision']['wait_repair']['status'],'failed')
        self.assertEqual(self.llm.call_count,2)
        self.writer.assert_not_called();self.barrier.assert_not_called()

    def test_invalid_council_output_is_rejected_before_model_directed_writes(self):
        output = proposal()
        del output["contract_version"]
        output["pending_orders_management"] = [{"instId": INST, "ordId": "123", "action": "CANCEL"}]
        self.cli.return_value.stdout = json.dumps([{"instId": INST, "ordId": "123"}])
        self.council.return_value = (output, {})
        Path(self.brain.AI_DECISION_CACHE_FILE).write_text(json.dumps({"OLD": {}}), encoding="utf-8")
        result = self.brain.execute_batch_ai_brain_cycle(active_positions_detail=[], usdt_available=1000)
        self.assertIsNone(result)
        self.assertEqual(json.loads(Path(self.brain.AI_DECISION_CACHE_FILE).read_text(encoding="utf-8")), {})
        self.assertEqual(json.loads((self.root / "trading_output_validation.json").read_text(encoding="utf-8"))["status"], "rejected")
        self.telemetry.finish.assert_called_once()
        self.assertEqual(self.telemetry.finish.call_args.args, ("failed",))
        self.assertIsInstance(self.telemetry.finish.call_args.kwargs["error"], trading_prompt.ContractError)
        self.llm.assert_not_called()
        self.evidence.record_decisions.assert_not_called()
        self.writer.assert_not_called()
        self.barrier.assert_not_called()
        self.cli.assert_called_once()
        self.assertIn("swap orders", self.cli.call_args.args[0])
        self.network.assert_not_called()

    def test_invalid_smart_money_sections_are_isolated(self):
        for item in (None, [], {}, {"valid": False, "longShortRatio": {"weightedLongRatio": .5}},
                     {"longShortRatio": {"valid": False, "weightedLongRatio": .5}},
                     {"longShortRatio": [], "notional": {"netNotionalUsdt": "NaN", "smartMoneyLongAvgEntry": 0}}):
            with self.subTest(item=item):
                sm = self.brain.normalize_smart_money(item)
                self.assertFalse(sm["valid"])
                self.assertTrue(all(value == "UNKNOWN" for key, value in sm.items() if key != "valid"))
        sm = self.brain.normalize_smart_money({"longShortRatio": None, "notional": {"netNotionalUsdt": -20000}})
        self.assertTrue(sm["valid"])
        self.assertEqual(sm["weighted_long_pct"], "UNKNOWN")
        self.assertEqual(sm["net_flow_usdt"], "-2.0\u4e07 U")

    def test_partial_invalid_fields_do_not_drop_valid_smart_money_observations(self):
        for bad in (None, "", "bad", True, "NaN", "Infinity", -0.1, 1.1):
            with self.subTest(bad=bad):
                sm = self.brain.normalize_smart_money({"longShortRatio": {"weightedLongRatio": bad},
                    "notional": {"netNotionalUsdt": 0, "smartMoneyLongAvgEntry": "99.123456789"}})
                self.assertTrue(sm["valid"])
                self.assertEqual(sm["weighted_long_pct"], "UNKNOWN")
                self.assertEqual(sm["net_flow_usdt"], "0.0 U")
                self.assertEqual(sm["avg_long_entry"], "99.123456789")
        self.market.smart_money_overview.return_value = [{"ccy": "BTC",
            "longShortRatio": {"weightedLongRatio": "0.5"},
            "notional": {"netNotionalUsdt": "0", "smartMoneyLongAvgEntry": "99.125", "smartMoneyShortAvgEntry": "101.25"},
            "winRate": {"avgLongWinRate": 0}}]
        result = self.run_cycle()
        sm = result[INST]["smart_money"]
        self.assertTrue(sm["valid"])
        self.assertEqual(sm["top_win_rate"], "多胜率0.0%")
        facts = self.prompt_data()["facts"][INST]
        for key, value in (("weighted_long_pct", 50.), ("net_flow_usdt", "0.0 U"),
                           ("avg_long_entry", 99.125), ("avg_short_entry", 101.25)):
            self.assertEqual(facts["/smart_money/" + key], {"value": value, "group": "flow"})
        self.llm.assert_not_called()
