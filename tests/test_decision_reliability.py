import json
import unittest
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime, timezone
from scripts import trading_prompt as contract, model_json, wait_counters
from scripts.decision_history import today_records, current_day

class JSONDiagnosticsTests(unittest.TestCase):
    def test_duplicate_keys_report_exact_safe_contract_path(self):
        raw='{"decisions":{"BTC-USDT-SWAP":{"wait_audit":{"long":{"code":"no_setup","code":"confirmation_pending"}}}}}'
        with self.assertRaises(contract.DuplicateJSONKeyError) as err:contract.parse_response(raw)
        self.assertEqual(err.exception.diagnostics['json_path'],'/decisions/BTC-USDT-SWAP/wait_audit/long/code')
    def test_unknown_key_names_are_not_echoed_into_public_errors(self):
        secret='untrusted-private-fragment'
        with self.assertRaises(contract.DuplicateJSONKeyError) as err:contract.parse_response(json.dumps({secret:1})[:-1]+',"'+secret+'":2}')
        self.assertNotIn(secret,str(err.exception));self.assertIn('_unrecognized_field',str(err.exception))
    def test_distinct_objects_may_repeat_field_names(self):
        self.assertEqual(contract.parse_response('{"a":{"value":1},"b":{"value":2}}'),{'a':{'value':1},'b':{'value':2}})
    def test_list_path_is_preserved_and_no_last_key_wins(self):
        with self.assertRaises(contract.DuplicateJSONKeyError) as err:contract.parse_response('{"supporting_evidence":[{"ref":"a","ref":"b"}]}')
        self.assertEqual(err.exception.diagnostics['json_path'],'/supporting_evidence/0/ref')
    def test_syntax_report_has_location_not_content_and_no_retry(self):
        report={};calls=[]
        def generate():calls.append(1);return '{"decisions": {"x": 1, invalid: 2}}'
        with self.assertRaises(contract.ContractError):model_json.decode_with_regeneration(generate,report=report)
        self.assertEqual(calls,[1]);self.assertFalse(report['attempted'])
        detail=report['failures'][0]
        self.assertEqual(detail['category'],'invalid_json_syntax');self.assertGreater(detail['json_offset'],0)
        self.assertNotIn('invalid:',json.dumps(detail));self.assertIn('sha256',detail)
    def test_nonfinite_empty_and_extra_documents_still_rejected(self):
        for value in ('{"x":NaN}','{"x":1e999}','{} {}','[]',''):
            with self.subTest(value=value),self.assertRaises(contract.ContractError):contract.parse_response(value)

class FailureContinuityTests(unittest.TestCase):
    def test_whole_cycle_failure_breaks_all_wait_streak(self):
        old={'version':wait_counters.VERSION,'observed_rounds':43,'streaks':{'no_program_plans':0,'model_all_wait':43,'audit_incomplete':0,'audited_wait_with_plans':2}}
        result=wait_counters.advance_failure(old,1000)
        self.assertEqual(old['streaks']['model_all_wait'],43)
        self.assertIsNone(result['streaks']['model_all_wait']);self.assertIsNone(result['streaks']['no_program_plans'])
        self.assertEqual(result['streaks']['audit_incomplete'],1);self.assertEqual(result['observed_rounds'],44)
        self.assertEqual(result['failed_rounds_total'],1)
    def test_next_audited_wait_starts_new_streak_not_old_43(self):
        failed=wait_counters.advance_failure(None,1000)
        row={'decision':{'action':'WAIT','model_action':'WAIT','contract_valid':True,'decision_status':'audited_wait','entry_plans':{'plans':[]}}}
        result=wait_counters.advance(failed,{'BTC':row},1100)
        self.assertEqual(result['streaks']['model_all_wait'],1);self.assertEqual(result['streaks']['audit_incomplete'],0)

class TodayHistoryTests(unittest.TestCase):
    def test_shanghai_midnight_is_not_utc_midnight(self):
        now=datetime(2026,10,10,0,0,tzinfo=timezone.utc)
        rows=[{'time':'2026-10-09 23:59:59','id':'old'}, {'time':'2026-10-10 00:00:00','id':'new'}, {'time':'2026-10-09T16:01:00Z','id':'utc_today'}, {'time':'2026-10-11 00:00:00','id':'future'}, {'time':'broken'}]
        self.assertEqual(current_day(now),'2026-10-10')
        self.assertEqual([r['id'] for r in today_records(rows,now=now)],['utc_today','new'])
    def test_empty_and_invalid_time_do_not_invent_today_records(self):
        self.assertEqual(today_records([None,{}, {'time':'broken'}]),[])
        with self.assertRaises(ValueError):today_records([],now='broken')


class FailurePersistenceTests(unittest.TestCase):
    def test_failure_is_idempotent_and_can_replace_a_partially_committed_same_frame(self):
        from scripts import wait_audit as audit
        scope='okx:demo:failure-fixture'
        with tempfile.TemporaryDirectory() as temp,patch.object(audit,'DATA',Path(temp)),patch.dict('os.environ',{'INITIAL_CAPITAL':''}):
            state={'scope':scope,'version':audit.VERSION,'items':{},'streak':43,'frame_id':'previous','diagnostics':{'version':wait_counters.VERSION,'observed_rounds':43,'streaks':{'no_program_plans':0,'model_all_wait':43,'audit_incomplete':0,'audited_wait_with_plans':1}}}
            audit._atomic(audit._path(scope),state)
            out=audit.record_failure(scope,frame_id='failed',reason='strict parser rejected',now=1000)
            self.assertEqual(out['status'],'incomplete');self.assertIsNone(out['diagnostics']['streaks']['model_all_wait'])
            saved=audit._path(scope).read_bytes();audit.record_failure(scope,frame_id='failed',reason='same error',now=1001)
            self.assertEqual(audit._path(scope).read_bytes(),saved)
            state=audit._load(scope);state['frame_id']='partial';state['diagnostics']['last_cycle']={'status':'success'};state['diagnostics']['observed_rounds']=45
            audit._atomic(audit._path(scope),state)
            out=audit.record_failure(scope,frame_id='partial',reason='archive failed',now=1100)
            self.assertEqual(out['diagnostics']['observed_rounds'],45)

class TodayHistoryEndpointTests(unittest.TestCase):
    def test_endpoint_filters_today_before_limiting_and_retains_other_history_for_explicit_queries(self):
        import dashboard.app as dash
        from scripts import decision_history
        now=datetime(2026,10,10,0,0,tzinfo=timezone.utc)
        rows=[{'id':'old','time':'2026-10-09 23:59:59'},{'id':'today','time':'2026-10-10 00:00:01'},{'id':'utc','time':'2026-10-09T17:00:00Z'}]
        real=decision_history.today_records
        with patch.object(dash,'_read_ai_history_records',return_value=rows),patch('scripts.okx_runtime.selected_environment',return_value=SimpleNamespace(identity='okx:demo:fixture')),patch.object(decision_history,'today_records',side_effect=lambda r,**kw:real(r,now=now)),patch.object(decision_history,'current_day',return_value='2026-10-10'):
            response=asyncio.run(dash.get_ai_history(limit=512,today_only=True));data=json.loads(response.body)
            self.assertEqual(data['total'],2);self.assertEqual(data['day'],'2026-10-10');self.assertEqual(data['items'][0]['history_id'],'utc')
            self.assertEqual(json.loads(asyncio.run(dash.get_ai_history()).body)['total'],3)


if __name__=='__main__':unittest.main()
