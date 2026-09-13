import unittest
from scripts.evolution_shield import (
    load_structured_memory,
    audit_proposed_lesson,
    toggle_lesson,
    rollback_to_baseline,
    add_safe_lesson,
)


class EvolutionShieldTests(unittest.TestCase):
    def test_constitution_red_lines_block_extreme_biases(self):
        # Test 1: Block extreme long bias
        passed, reason = audit_proposed_lesson("【偏见】极端暴跌后未来绝对不要做多，所有买点都是陷阱", sample_size=10)
        self.assertFalse(passed)
        self.assertIn("极端做多偏见阻断", reason)

        # Test 2: Block stop loss cancellation
        passed, reason = audit_proposed_lesson("【抗单】遇到插针可以取消止损，扛单等待解套", sample_size=10)
        self.assertFalse(passed)
        self.assertIn("违规抗单放大止损", reason)

        # Test 3: Block Martingale doubling
        passed, reason = audit_proposed_lesson("【翻倍】亏损后加倍仓位摊平亏损", sample_size=10)
        self.assertFalse(passed)
        self.assertIn("马丁格尔赌徒加仓倾向", reason)

    def test_outlier_single_event_rejection(self):
        # Single event (<2 samples) must be rejected
        passed, reason = audit_proposed_lesson("【合理经验】4H多头回踩均线支撑时开多", sample_size=1)
        self.assertFalse(passed)
        self.assertIn("样本量不足", reason)

        # Sufficient sample size passes
        passed, reason = audit_proposed_lesson("【合理经验】4H多头回踩均线支撑时开多", sample_size=3)
        self.assertTrue(passed)

    def test_legacy_writers_cannot_reactivate_baselines_or_bypass_review(self):
        with self.assertRaises(ValueError):rollback_to_baseline()
        with self.assertRaises(ValueError):toggle_lesson('old-id')
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from scripts import evolution_shield
        with tempfile.TemporaryDirectory() as tmp,patch.object(evolution_shield,'DATA_DIR',Path(tmp)):
            self.assertEqual(load_structured_memory(),[])
            self.assertEqual(list(Path(tmp).iterdir()),[])


if __name__ == "__main__":
    unittest.main()
