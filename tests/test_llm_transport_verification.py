import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from fastapi import HTTPException
from fastapi.testclient import TestClient
from okxquant_backend import app as api
from okxquant_backend import llm_transport_policy as policy
from okxquant_backend import llm_transport_verification as verification
from okxquant_backend import llm_manager
from okxquant_backend.llm_transport import LLMRequestError


class TransportVerificationTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        for obj,name,value in [(policy,'CONFIG_PATH',self.root/'settings.json'),(verification,'JOBS_PATH',self.root/'jobs.json'),(verification,'_EXECUTOR',Mock())]:
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
        self.ctx={'provider_id':'p','model':'m','base_url':'https://fixture.invalid','api_key':'VERY-SECRET-KEY','api_format':'openai_chat','reasoning_effort':'high','reasoning_type':'standard_effort'}
        p=patch.object(policy,'selected_context',side_effect=lambda *args:dict(self.ctx));p.start();self.addCleanup(p.stop)

    def run_probe(self,ok=True):
        job=verification.start('p','m','stream')
        def result(**kwargs):
            self.assertEqual(kwargs['model'],'m');self.assertEqual(kwargs['reasoning_effort'],'high')
            self.assertEqual(kwargs['max_attempts'],1)
            kwargs['transport_diagnostics'].update(completion_seen=True,heartbeat_count=2,first_byte_ms=0,total_ms=150000)
            return ('{"ok":true}' if ok else '{"not_ok":true}'),' ',{},150000
        with patch.object(llm_manager,'execute_llm_request',side_effect=result) as call:
            verification._run(job['job_id'],dict(self.ctx),{**policy.DEFAULT_POLICY,'mode':'stream'})
        self.assertEqual(call.call_count,1)
        return verification.get_job(job['job_id'])

    def test_verification_is_async_and_duplicate_submit_does_not_start_another_request(self):
        first=verification.start('p','m','stream');second=verification.start('p','m','stream')
        self.assertEqual(first['job_id'],second['job_id']);self.assertEqual(first['status'],'queued')
        self.assertEqual(verification._EXECUTOR.submit.call_count,1)
        self.assertNotIn('VERY-SECRET-KEY',verification.JOBS_PATH.read_text())
        self.assertNotIn('fixture.invalid',verification.JOBS_PATH.read_text())

    def test_success_persists_only_safe_capability_receipt(self):
        result=self.run_probe()
        self.assertTrue(result['ok']);self.assertEqual(result['status'],'completed')
        self.assertEqual(result['state']['effective_mode'],'stream')
        self.assertEqual(result['diagnostics']['first_byte_ms'],0)
        self.assertNotIn('VERY-SECRET-KEY',str(result))

    def test_bad_json_contract_does_not_verify_stream_compatibility(self):
        result=self.run_probe(False)
        self.assertFalse(result['ok']);self.assertEqual(result['state']['capabilities']['stream']['status'],'failed')
        self.assertEqual(result['state']['effective_mode'],'json')

    def test_ambiguous_timeout_is_not_retried_or_classified_as_unsupported(self):
        job=verification.start('p','m','stream')
        with patch.object(llm_manager,'execute_llm_request',side_effect=LLMRequestError(524,1,'http_error')) as call:
            verification._run(job['job_id'],dict(self.ctx),{**policy.DEFAULT_POLICY,'mode':'stream'})
        self.assertEqual(call.call_count,1)
        result=verification.get_job(job['job_id']);self.assertFalse(result['ok'])
        self.assertEqual(result['state']['capabilities']['stream']['status'],'failed')

    def test_rotated_connection_during_probe_cannot_save_old_receipt(self):
        old=dict(self.ctx);job=verification.start('p','m','stream')
        def reply(**kwargs):self.ctx['api_key']='NEW-SECRET';return '{"ok":true}','',{},1
        with patch.object(llm_manager,'execute_llm_request',side_effect=reply):verification._run(job['job_id'],old,{**policy.DEFAULT_POLICY,'mode':'stream'})
        result=verification.get_job(job['job_id']);self.assertFalse(result['ok'])
        self.assertEqual(result['error']['category'],'connection_changed');self.assertFalse(policy.CONFIG_PATH.exists())

    def test_expired_job_is_unknown_without_replay_and_read_does_not_write(self):
        job=verification.start('p','m','stream');before=verification.JOBS_PATH.read_bytes()
        with patch.object(verification.time,'time',return_value=job['expires_at']+1):result=verification.get_job(job['job_id'])
        self.assertFalse(result['ok']);self.assertEqual(result['error']['category'],'verification_expired')
        self.assertEqual(before,verification.JOBS_PATH.read_bytes());self.assertEqual(verification._EXECUTOR.submit.call_count,1)

    def test_capacity_bound_prevents_unbounded_paid_parallel_jobs(self):
        verification.start('p','m','stream');verification.start('p','m','json')
        self.ctx['model']='m2'
        with self.assertRaises(RuntimeError):verification.start('p','m2','stream')
        self.assertEqual(verification._EXECUTOR.submit.call_count,2)

    def test_expired_worker_cannot_replace_newer_unsupported_receipt(self):
        job=verification.start('p','m','stream')
        def reply(**kwargs):
            with __import__('scripts.config_lock',fromlist=['configuration_write']).configuration_write(verification.JOBS_PATH):
                jobs=verification._jobs();jobs[job['job_id']]['expires_at']=0;verification._save(jobs)
            policy.record_verification(self.ctx,'stream',False,{},'stream_unsupported')
            return '{"ok":true}','',{},1
        with patch.object(llm_manager,'execute_llm_request',side_effect=reply):verification._run(job['job_id'],dict(self.ctx),{**policy.DEFAULT_POLICY,'mode':'stream'})
        self.assertEqual(verification.get_job(job['job_id'])['error']['category'],'verification_expired')
        self.assertEqual(policy.capability_state(self.ctx)['stream']['status'],'unsupported')
        self.assertEqual(policy.status(self.ctx)['effective_mode'],'json')

    def test_probe_passes_frozen_reasoning_type(self):
        self.ctx['reasoning_type']='standard_effort';job=verification.start('p','m','stream')
        with patch.object(llm_manager,'execute_llm_request',return_value=('{"ok":true}','',{},1)) as request:
            verification._run(job['job_id'],dict(self.ctx),{**policy.DEFAULT_POLICY,'mode':'stream'})
        self.assertEqual(request.call_args.kwargs['reasoning_type'],'standard_effort')

    def test_unknown_job_does_not_create_a_journal(self):
        self.assertIsNone(verification.get_job('missing'));self.assertFalse(verification.JOBS_PATH.exists())

    def test_api_auth_before_any_probe_or_write(self):
        with TestClient(api.app) as client,patch.object(api,'require_superadmin',side_effect=HTTPException(403,'reader')),patch.object(verification,'start') as start,patch.object(policy,'save_policy') as save:
            self.assertEqual(client.post('/api/v1/admin/llm/transport/verify',json={'provider_id':'p','model_id':'m','mode':'stream'}).status_code,403)
            self.assertEqual(client.put('/api/v1/admin/llm/transport',json={'provider_id':'p','model_id':'m','policy':{'mode':'auto'}}).status_code,403)
            self.assertEqual(client.post('/api/v1/admin/llm/test',json={'model':'m'}).status_code,403)
            self.assertEqual(client.post('/api/v1/admin/llm/fetch-models',json={'provider_id':'p'}).status_code,403)
            start.assert_not_called();save.assert_not_called()
        with TestClient(api.app) as client,patch.object(api,'require_admin_header',side_effect=HTTPException(401,'login')):
            self.assertEqual(client.get('/api/v1/admin/llm/transport?provider_id=p&model_id=m').status_code,401)
            self.assertEqual(client.get('/api/v1/admin/llm/transport/verification/missing').status_code,401)

    def test_admin_api_returns_job_immediately_and_does_not_activate_model(self):
        with TestClient(api.app) as client,patch.object(api,'require_superadmin',return_value={'id':1}),patch.object(api,'audit_record'),patch.object(api,'activate_provider_model') as activate:
            response=client.post('/api/v1/admin/llm/transport/verify',json={'provider_id':'p','model_id':'m','mode':'stream'})
            self.assertEqual(response.status_code,202,response.text);self.assertEqual(response.json()['status'],'queued');activate.assert_not_called()
