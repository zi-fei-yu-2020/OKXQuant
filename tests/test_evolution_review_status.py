import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch
import self_improvement_engine as engine
from scripts.evolution_status import public_status


class EvolutionReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        values = {'DATA_DIR': str(self.root), 'WORKSPACE_DIR': str(self.root), 'LOGS_DIR': str(self.root),
                  'LOG_FILE': str(self.root / 'log'), 'AI_MEMORY_FILE': str(self.root / 'ai_trading_memory.json'),
                  'AI_MEMORY_MD_FILE': str(self.root / 'AI_TRADING_MEMORY.md'),
                  'REPORT_JSON_FILE': str(self.root / 'self_improvement_report.json'),
                  'LEDGER_JSON_FILE': str(self.root / 'trading_ledger.json'),
                  'EVOLUTION_LOCK_FILE': str(self.root / 'lock'), 'EVOLUTION_LAST_PROMPT_FILE':str(self.root/'last_prompt.txt')}
        for key, value in values.items(): self.stack.enter_context(patch.object(engine, key, value))
        self.stack.enter_context(patch('qq_notifier.notify_evolution_report'))
        self.write('ai_trading_memory.json', {'updated_at': '2026-09-06 20:00:05', 'core_lessons': ['Approved old lesson']})
        (self.root / 'AI_TRADING_MEMORY.md').write_text('APPROVED OLD MEMORY')

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf8')

    def test_review_no_change_succeeds_while_trade_writer_is_busy(self):
        from scripts import trade_lock
        with patch.object(engine,'load_closed_trades',return_value=[]),patch.object(engine,'call_llm_evolution_review',return_value={'change_status':'NO_CHANGE'}),patch.object(trade_lock,'writer',side_effect=TimeoutError('busy')) as writer:
            result=engine.run_self_evolution(force=True)
        writer.assert_not_called();self.assertEqual(result['review_status'],'success')
        self.assertEqual((self.root/'AI_TRADING_MEMORY.md').read_text(),'APPROVED OLD MEMORY')
        self.assertFalse((self.root/'memory_registry.db').exists())
        self.assertEqual(public_status(self.root)['active_memory_updated_at'],'2026-09-06 20:00:05')

    def test_pending_proposals_are_reported_from_registry_without_publishing(self):
        from scripts import trade_lock,memory_registry
        self.write('memory_candidates.json',{'candidates':[]})
        before=memory_registry.view(self.root)
        reply={'change_status':'ADD','memory_proposals':[{'action':'ADD','text':'观察量能与结构，不凭单个样本判断策略收益。'}]}
        with patch.object(engine,'load_closed_trades',return_value=[]),patch.object(engine,'call_llm_evolution_review',return_value=reply),patch.object(trade_lock,'writer',side_effect=TimeoutError('busy')) as writer:
            result=engine.run_self_evolution(force=True)
        writer.assert_not_called();self.assertEqual(result['pending_candidate_count'],1)
        self.assertEqual(public_status(self.root)['pending_candidates'],1)
        self.assertEqual(memory_registry.view(self.root)['prompt_hash'],before['prompt_hash'])
        self.assertEqual((self.root/'AI_TRADING_MEMORY.md').read_text(),'APPROVED OLD MEMORY')

    def test_failed_staging_preserves_validated_draft_previous_report_and_failure_phase(self):
        from scripts import memory_registry
        old={'timestamp':'2026-09-06 20:00:05','ledger_revision':'old'};self.write('self_improvement_report.json',old)
        review={'change_status':'ADD','memory_proposals':[{'action':'ADD','text':'观察量能与结构，不凭单个样本判断策略收益。'}]}
        with patch.object(engine,'load_closed_trades',return_value=[]),patch.object(engine,'call_llm_evolution_review',return_value=review),patch.object(memory_registry,'stage_review',side_effect=sqlite3.OperationalError('database is locked')):
            with self.assertRaises(RuntimeError):engine.run_self_evolution(force=True)
        self.assertEqual(json.loads((self.root/'self_improvement_report.json').read_text()),old)
        draft=json.loads((self.root/'self_improvement_review_draft.json').read_text())
        self.assertEqual(draft['status'],'report_fields_validated_not_published');self.assertEqual(draft['review'],review)
        self.assertEqual(public_status(self.root)['failure_stage'],'candidate_persistence')

    def test_report_write_failure_keeps_previous_report_and_derived_markdown(self):
        old={'timestamp':'2026-09-06 20:00:05'};self.write('self_improvement_report.json',old)
        (self.root/'self_improvement_review.md').write_text('OLD REPORT')
        atomic=engine.atomic_write_json
        def write(path,value):
            if path==engine.REPORT_JSON_FILE:raise OSError('disk unavailable')
            return atomic(path,value)
        with patch.object(engine,'load_closed_trades',return_value=[]),patch.object(engine,'call_llm_evolution_review',return_value={'change_status':'NO_CHANGE'}),patch.object(engine,'atomic_write_json',side_effect=write):
            with self.assertRaises(RuntimeError):engine.run_self_evolution(force=True)
        self.assertEqual(json.loads((self.root/'self_improvement_report.json').read_text()),old)
        self.assertEqual((self.root/'self_improvement_review.md').read_text(),'OLD REPORT')
        self.assertEqual(public_status(self.root)['failure_stage'],'report_persistence')

    def test_transport_error_is_reported_as_model_failure_not_storage_failure(self):
        from okxquant_backend.llm_transport import LLMRequestError
        with patch.object(engine,'load_closed_trades',return_value=[]),patch.object(engine,'call_llm_evolution_review',side_effect=LLMRequestError(503,2,'http_error')):
            with self.assertRaises(RuntimeError):engine.run_self_evolution(force=True)
        status=public_status(self.root)
        self.assertEqual(status['failure_stage'],'model_review');self.assertEqual(status['model_failure']['attempts'],2)
        self.assertEqual(status['model_failure']['http_status'],503)

    def test_failed_model_does_not_commit_revision_or_erase_success_and_can_retry(self):
        old = {'timestamp': '2026-09-06 20:00:05', 'ledger_revision': 'old', 'insights': ['prior report']}
        self.write('self_improvement_report.json', old)
        self.write('memory_candidates.json', {'candidates': [{'text': 'existing pending'}]})
        with patch.object(engine, 'load_closed_trades', return_value=[]), patch.object(engine, 'call_llm_evolution_review', side_effect=[{}, {'change_status': 'NO_CHANGE', 'diagnosis_insights': ['Reviewed without changing memory']}]) as model:
            with self.assertRaises(RuntimeError): engine.run_self_evolution()
            self.assertEqual(json.loads((self.root / 'self_improvement_report.json').read_text()), old)
            self.assertEqual(public_status(self.root)['status'], 'failed')
            self.assertEqual(json.loads((self.root / 'memory_candidates.json').read_text())['candidates'][0]['text'], 'existing pending')
            result = engine.run_self_evolution()
            self.assertEqual(result['review_status'], 'success')
            self.assertEqual(model.call_count, 2)
        self.assertEqual((self.root / 'AI_TRADING_MEMORY.md').read_text(), 'APPROVED OLD MEMORY')
        status = public_status(self.root)
        self.assertEqual(status['active_memory_updated_at'], '2026-09-06 20:00:05')
        self.assertEqual(status['insights'], ['Reviewed without changing memory'])
        self.assertNotEqual(status['last_success_at'], status['active_memory_updated_at'])

    def test_success_without_new_evidence_skips_model_but_updates_attempt_status(self):
        with patch.object(engine, 'load_closed_trades', return_value=[]), patch.object(engine, 'call_llm_evolution_review', return_value={'change_status': 'NO_CHANGE'}) as model:
            first = engine.run_self_evolution()
            second = engine.run_self_evolution()
        self.assertEqual(first, second)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(public_status(self.root)['status'], 'no_new_evidence')

    def test_invalid_review_arrays_fail_without_overwriting_memory(self):
        with patch.object(engine, 'load_closed_trades', return_value=[]), patch.object(engine, 'call_llm_evolution_review', return_value={'change_status': 'ADD', 'ai_long_term_memory': [123]}):
            with self.assertRaises(RuntimeError): engine.run_self_evolution()
        self.assertFalse((self.root / 'self_improvement_report.json').exists())
        self.assertEqual((self.root / 'AI_TRADING_MEMORY.md').read_text(), 'APPROVED OLD MEMORY')

    def test_canonical_net_pnl_and_trade_id_reach_review(self):
        self.write('trading_ledger.json', [{'id': 't1', 'status': 'closed', 'environment_id': __import__('scripts.memory_registry',fromlist=['scope_of']).scope_of(), 'inst': 'BTC', 'close_time': '2026-09-07 12:00:00', 'pnl': 5, 'net_pnl': -2, 'gross_pnl': 5, 'fee': -1}])
        row = engine.load_closed_trades()[0]
        self.assertEqual(row['net_pnl'], -2)
        self.assertEqual(row['trade_id'], 't1')

    def test_corrupt_source_retains_previous_report_and_marks_failed(self):
        old={'timestamp':'2026-09-06 20:00:05','total_trades':8}
        self.write('self_improvement_report.json',old)
        with patch.object(engine,'load_closed_trades',side_effect=ValueError('corrupt')), patch.object(engine,'call_llm_evolution_review') as model:
            with self.assertRaises(RuntimeError):engine.run_self_evolution()
            model.assert_not_called()
        self.assertEqual(json.loads((self.root/'self_improvement_report.json').read_text()),old)
        self.assertEqual(public_status(self.root)['status'],'failed')

    def test_feedback_is_computed_from_rows_not_model_claims_and_survives_no_change(self):
        rows=[{'trade_id':'a','net_pnl':1,'gross_pnl':2,'fee':-.1,'time':'2026-09-08 10:00:00'}]
        answer={'change_status':'NO_CHANGE','evidence_feedback':{'fee_cost':999}}
        with patch.object(engine,'load_closed_trades',return_value=rows), patch.object(engine,'call_llm_evolution_review',return_value=answer) as model:
            first=engine.run_self_evolution();second=engine.run_self_evolution()
        self.assertEqual(model.call_count,1)
        self.assertEqual(first,second)
        self.assertEqual(first['evidence_feedback']['fee_cost'],.1)
        self.assertIsNone(first['profit_factor'])
        self.assertEqual(public_status(self.root)['evidence_feedback']['settled_samples'],1)

    def test_embedded_review_markdown_wins_over_stale_derived_file(self):
        from scripts.memory_registry import scope_of
        self.write('self_improvement_report.json',{'account_scope':scope_of(),'timestamp':'2026-09-08 20:00:00','review_markdown':'CURRENT REPORT'})
        (self.root/'self_improvement_review.md').write_text('STALE REPORT')
        self.assertEqual(public_status(self.root)['review_markdown'],'CURRENT REPORT')

    def test_latest_failed_job_does_not_hide_previous_successful_report(self):
        self.write('self_improvement_report.json', {'timestamp': '2026-09-06 20:00:05', 'total_trades': 8})
        with sqlite3.connect(self.root / 'okxquant_gateway.db') as db:
            db.execute('CREATE TABLE job_runs(id INTEGER,job_name TEXT,status TEXT,started_at TEXT,finished_at TEXT,return_code INTEGER)')
            db.execute("INSERT INTO job_runs VALUES (1,'self_improvement','failed','2026-09-07 20:00:00','2026-09-07 20:00:01',1)")
        value = public_status(self.root)
        self.assertEqual(value['status'], 'failed')
        self.assertEqual(value['last_success_at'], '2026-09-06 20:00:05')
        self.assertEqual(value['sample_size'], 8)

    def test_report_from_other_account_is_not_presented_as_current_review(self):
        self.write('self_improvement_report.json', {'account_scope':'foreign-account','timestamp':'2026-09-07 20:00:05','total_trades':99,'insights':['other account conclusion']})
        value=public_status(self.root)
        self.assertEqual(value['status'],'other_scope_report')
        self.assertIsNone(value['last_success_at'])
        self.assertEqual(value['insights'],[])
        self.assertEqual(value['review_markdown'],'')

    def test_missing_data_is_unknown_not_fake_optimization(self):
        value = public_status(self.root)
        self.assertEqual(value['status'], 'not_run')
        self.assertIsNone(value['win_rate'])
        self.assertIsNone(value['last_success_at'])
        self.assertEqual(value['insights'], [])


if __name__ == '__main__': unittest.main()
