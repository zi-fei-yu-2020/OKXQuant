import importlib.util
from pathlib import Path
import unittest
from scripts import trading_prompt
from okxquant_backend.interceptor_manager import run_interceptor_pipeline


class EntryScoreTests(unittest.TestCase):
    def package(self):
        return {'name': 'BTC', 'instId': 'BTC-USDT-SWAP', 'price': 100., 'macro_4h': '4H_MACRO_BULL',
                'structure_1h':'1H_SWING_BULL', 'adx_1h': 25., 'atr_1h': 2., 'data_quality': 'valid', 'environment_support': {'can_open': True}}

    def decision(self, score):
        return {'action': 'BUY_LONG', 'confidence': score, 'entry_price': 100., 'stop_loss_price': 95., 'take_profit_price': 115.,
                'summary_reason': 'A defined structural candidate with bounded invalidation',
                'supporting_evidence': [{'ref': '/macro_4h', 'value': '4H_MACRO_BULL', 'interpretation': 'Observed directional environment'},
                                        {'ref': '/price', 'value': 100., 'interpretation': 'Current price locates the entry'}],
                'counter_evidence_status': 'none_observed', 'counter_evidence': [], 'uncertainty': 'Residual event and liquidity risk remain',
                'invalidation': {'price': 95., 'timeframe': '1H', 'condition': 'Losing the reference support invalidates the setup'}, 'valid_for_seconds': 120}

    def test_same_evidence_has_same_entry_admissibility_for_every_valid_score(self):
        for symbol in ('BTC', 'DOGE'):
            for score in (0, 45, 60, 70, 74.9, 75, 78, 80, 100):
                with self.subTest(symbol=symbol, score=score):
                    p = self.package(); p.update(name=symbol, instId=symbol + '-USDT-SWAP')
                    validated = trading_prompt.candidate(p, self.decision(score), trading_prompt.facts_for(p))
                    self.assertTrue(validated['contract_valid'])
                    action, reason, _ = run_interceptor_pipeline(p, validated, {'active_inst_ids': set()})
                    self.assertEqual((action, reason), ('BUY_LONG', ''))

    def test_higher_score_cannot_bypass_existing_risk_or_evidence_gates(self):
        p = self.package(); d = self.decision(100)
        d['supporting_evidence'] = []
        self.assertFalse(trading_prompt.candidate(p, d, trading_prompt.facts_for(p))['contract_valid'])
        cases = [('macro_4h', '4H_MACRO_BEAR'), ('adx_1h', 15.), ('data_quality', 'invalid')]
        for key, value in cases:
            p = self.package(); p[key] = value
            self.assertEqual(run_interceptor_pipeline(p, self.decision(100), {})[0], 'WAIT')
        d = self.decision(100); d['take_profit_price'] = 101.
        self.assertEqual(run_interceptor_pipeline(self.package(), d, {})[0], 'WAIT')
        self.assertEqual(run_interceptor_pipeline(self.package(), self.decision(100), {'active_inst_ids': {'BTC-USDT-SWAP'}, 'active_position_sides': {'BTC-USDT-SWAP': 'short'}})[0], 'WAIT')

    def test_invalid_score_still_fails_and_wait_never_becomes_entry(self):
        path = Path(__file__).resolve().parents[1] / 'plugins/interceptors/02_confidence_gatekeeper.py'
        spec = importlib.util.spec_from_file_location('score_gate_test', path)
        plugin = importlib.util.module_from_spec(spec); spec.loader.exec_module(plugin)
        for bad in (None, True, float('nan'), float('inf'), -1, 101, 'unknown'):
            self.assertFalse(plugin.check_risk(self.package(), self.decision(bad), {})[0])
        self.assertEqual(run_interceptor_pipeline(self.package(), {'action': 'WAIT', 'confidence': 100}, {})[0], 'WAIT')


if __name__ == '__main__': unittest.main()
