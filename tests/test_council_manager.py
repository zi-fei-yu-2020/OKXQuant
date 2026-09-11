import unittest
from unittest.mock import patch, MagicMock
from okxquant_backend.council_manager import (
    load_council_config,
    save_council_config,
    reset_role_template,
    DEFAULT_PRESET_TEMPLATES,
    execute_council_debate,
)


class TestCouncilManager(unittest.TestCase):
    def test_load_and_save_council_config(self):
        cfg = load_council_config()
        self.assertIn("enabled", cfg)
        self.assertIn("roles", cfg)
        self.assertIn("trader_trend", cfg["roles"])
        self.assertIn("trader_momentum", cfg["roles"])
        self.assertIn("trader_quant", cfg["roles"])
        self.assertIn("cio", cfg["roles"])

        original_enabled = cfg["enabled"]
        cfg["enabled"] = not original_enabled
        saved = save_council_config(cfg)
        self.assertEqual(saved["enabled"], not original_enabled)

        # Restore original
        cfg["enabled"] = original_enabled
        save_council_config(cfg)

    def test_reset_role_template(self):
        cfg = reset_role_template("trader_trend")
        self.assertEqual(
            cfg["roles"]["trader_trend"]["prompt"],
            DEFAULT_PRESET_TEMPLATES["trader_trend"]["prompt"]
        )

    def test_consensus_mode_and_suites(self):
        from okxquant_backend.council_manager import get_preset_suites, apply_preset_suite
        suites = get_preset_suites()
        self.assertGreaterEqual(len(suites), 1)
        suite_ids = [s["id"] for s in suites]
        self.assertIn("hedge_fund_desk", suite_ids)

        # Apply hedge fund desk suite
        updated = apply_preset_suite("hedge_fund_desk")
        self.assertEqual(updated["consensus_mode"], "weighted")
        self.assertIn("trader_trend", updated["roles"])
        self.assertIn("trader_momentum", updated["roles"])
        self.assertIn("cio", updated["roles"])

    def test_council_debate_execution_mocked(self):
        # Mock execute_llm_request to avoid making external HTTP calls
        mock_trader_return = ("BUY_LONG 80% 置信度，动能良好，建议 HOLD 现有 BTC 持仓", "", {}, 120)
        mock_cio_json = (
            '{"macro_assessment": "采纳 Trader A 稳健回踩方案，资金充裕", "position_management": [{"instId": "BTC-USDT-SWAP", "action": "HOLD", "reasoning": "波段顺畅"}], "decisions": {"ETH-USDT-SWAP": {"action": "BUY_LONG", "confidence": 85, "limit_price": 2410.0, "stop_loss": 2350.0, "take_profit": 2530.0, "leverage": 3, "margin_usd": 150.0, "reasoning": "采纳交易员 A 顺势回踩买点"}}}',
            "",
            {},
            250
        )

        with patch("okxquant_backend.llm_manager.execute_llm_request") as mock_exec:
            mock_exec.side_effect = [
                mock_trader_return,
                mock_trader_return,
                mock_trader_return,
                mock_cio_json,
            ]

            brain_output, transcript = execute_council_debate(
                market_prompt="BTC: 77000, ETH: 2400",
                original_system_prompt="system prompt",
                timeout=15.0,
            )

            self.assertIn("decisions", brain_output)
            self.assertEqual(brain_output["macro_assessment"], "采纳 Trader A 稳健回踩方案，资金充裕")
            self.assertEqual(len(brain_output["position_management"]), 1)
            self.assertEqual(brain_output["position_management"][0]["action"], "HOLD")
            self.assertIn("council_transcript", brain_output)
            self.assertTrue(transcript["council_mode"])
            self.assertIn("advisors", transcript)
            self.assertIn("trader_trend", transcript["advisors"])
            self.assertIn("arbitrator", transcript)


if __name__ == "__main__":
    unittest.main()
