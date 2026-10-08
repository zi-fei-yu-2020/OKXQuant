import unittest
from unittest.mock import patch
from okxquant_backend.public_monitor import public_payload


class PublicMonitorProjectionTests(unittest.TestCase):
    def test_recursive_credentials_and_log_text_are_redacted_without_changing_financials(self):
        source={'net_pnl':12.5,'input_tokens':123,'logs':[
            'inspection healthy', 'api_key="old-key" password=old-password',
            'Authorization: Bearer old-bearer', 'url https://user:old-pass@example.test/path?access_token=old-token',
            'opaque current-secret inside text', '--api-key historical-cli-key', 'OK-ACCESS-SIGN: historical-signature'],
            'evidence':{'API_KEY':'nested-key','session_token':'nested-session','token':'generic-token'},
            'private_key':'-----BEGIN PRIVATE KEY-----secret-----END PRIVATE KEY-----'}
        with patch('okxquant_backend.public_monitor._known_secrets',return_value=('current-secret',)):
            result=public_payload(source)
        text=str(result)
        for secret in ['old-key','old-password','old-bearer','old-pass','old-token','current-secret','nested-key','nested-session','historical-cli-key','historical-signature','generic-token']:
            self.assertNotIn(secret,text)
        self.assertEqual(result['net_pnl'],12.5);self.assertEqual(result['input_tokens'],123)
        self.assertEqual(result['logs'][0],'inspection healthy')
        self.assertEqual(source['evidence']['API_KEY'],'nested-key')

    def test_public_trade_and_history_details_are_redacted_and_still_scoped(self):
        from fastapi.testclient import TestClient
        import dashboard.app as dashboard
        import okxquant_backend.app as api
        from scripts.okx_runtime import selected_environment
        scope=selected_environment().identity
        row={'id':'public-trade','environment_id':scope,'status':'closed','net_pnl':10,'evidence':{'password':'hidden'}}
        history={'id':'public-history','scope':scope,'time':'2026-10-08 12:00','macro_assessment':'inspection healthy','secret_key':'hidden'}
        with TestClient(api.app) as client, patch.object(dashboard,'_read_ledger_rows',return_value=[row]), patch.object(dashboard,'_read_ai_history_records',return_value=[history]):
            for path in ['/api/trades/public-trade','/api/ai/history/public-history']:
                response=client.get(path)
                self.assertEqual(response.status_code,200,response.text)
                self.assertNotIn('hidden',response.text)
                self.assertIn('[REDACTED]',response.text)
