import os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts import horizon_allocation as allocation
from scripts.risk_policy import Policy,RiskRejected

class EntryQuantityControlTests(unittest.TestCase):
    def test_default_is_no_quantity_quotas(self):
        self.assertFalse(allocation.load_config('demo',{}).quantity_limits_enabled)
        self.assertFalse(allocation.quantity_limits_enabled({}))
        self.assertFalse(allocation.effective_config('demo',1,{}).quantity_limits_enabled)

    def test_many_fills_positions_and_pending_do_not_block_or_scan_quota_state(self):
        with patch.dict(os.environ,{'OKXQUANT_ENTRY_QUOTAS_ENABLED':'0'}), tempfile.TemporaryDirectory() as d, \
             patch.object(allocation,'STATUS_PATH',Path(d)/'status.json'), \
             patch.object(allocation,'_read_json',side_effect=AssertionError('quota ledger must not be scanned')):
            result=allocation.admit(SimpleNamespace(mode='demo',identity='account-a'),horizon='scalp',
                inst_id='NEW-USDT-SWAP',side='long',requested_budget=30,
                positions=[{'instId':str(i),'pos':'1'} for i in range(10)],pending=[{'sz':'1'}]*10,
                total_slot_limit=1)
            self.assertEqual(result['outcome'],'admitted')
            self.assertEqual(result['adjusted_budget_usdt'],8)
            self.assertEqual(result['quantity_control'],'daily_equity_circuit_only')

    def test_legacy_quotas_require_explicit_opt_in_and_change_signature(self):
        off=allocation.load_config('demo',{'OKXQUANT_ENTRY_QUOTAS_ENABLED':'0'})
        on=allocation.load_config('demo',{'OKXQUANT_ENTRY_QUOTAS_ENABLED':'1'})
        self.assertNotEqual(allocation.config_signature(off),allocation.config_signature(on))
        self.assertTrue(on.quantity_limits_enabled)

    def test_disabled_obsolete_quota_values_cannot_block_entries(self):
        config=allocation.load_config('demo',{'OKXQUANT_ENTRY_QUOTAS_ENABLED':'0',
            'OKXQUANT_SCALP_ACTIVE_LIMIT':'0','OKXQUANT_HORIZON_TOTAL_SLOTS':'broken'})
        self.assertFalse(config.quantity_limits_enabled)

    def test_bad_boolean_fails_closed(self):
        with self.assertRaises(RiskRejected):allocation.quantity_limits_enabled({'OKXQUANT_ENTRY_QUOTAS_ENABLED':'maybe'})

    def test_daily_equity_threshold_remains_three_percent(self):
        # Quantity policy never changes daily equity accounting or the threshold.
        self.assertEqual(Policy().daily_drawdown_pct,.03)
