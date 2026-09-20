"""Security/concurrency contracts; every network/process boundary is mocked."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from okxquant_backend import admin_auth, audit, account_routes, account_connections, net_security
from okxquant_backend import okx_trade_service as service, okx_client
from okxquant_gateway import secrets as vault
from scripts import okx_runtime


class CredentialSelectionTests(unittest.TestCase):
    def test_partial_specific_group_never_borrows_legacy_secret(self):
        env=okx_runtime.legacy_environment({"OKXQUANT_OKX_ENV":"live", "OKX_LIVE_API_KEY":"live-key",
            "OKX_API_KEY":"demo-key", "OKX_SECRET_KEY":"demo-secret", "OKX_PASSPHRASE":"demo-pass"})
        self.assertEqual(env.api_key, "live-key")
        self.assertFalse(env.configured)
        self.assertEqual((env.secret_key, env.passphrase), ("", ""))

    def test_explicit_empty_mapping_does_not_read_real_environment(self):
        with patch.object(okx_runtime, "_load_dotenv", side_effect=AssertionError("unexpected config read")):
            self.assertFalse(okx_runtime.legacy_environment({}).configured)

    def test_corrupt_vault_never_falls_back_to_inherited_credentials(self):
        with patch("okxquant_gateway.secrets.load_secrets",side_effect=ValueError("unreadable vault")),patch.dict(os.environ,{"OKX_API_KEY":"old"}):
            with self.assertRaises(ValueError):okx_runtime._load_dotenv()

    def test_partial_credentials_never_trigger_oauth_fallback(self):
        env=okx_runtime.OKXEnvironment("live","partial-key","","")
        with patch.object(service,"_run_cli") as cli:
            with self.assertRaises(RuntimeError):service._request_untracked("GET","/api/v5/account/positions",env=env)
        cli.assert_not_called()

    def test_cli_child_does_not_inherit_stale_generic_keys(self):
        selected=okx_runtime.OKXEnvironment("demo", "", "", "")
        child=selected.cli_env({"PATH":"test", "OKX_API_KEY":"live", "OKX_SECRET_KEY":"live", "OKX_PASSPHRASE":"live"})
        self.assertEqual(child["OKX_DEMO"], "1")
        for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"):
            self.assertNotIn(key, child)

    def test_cli_fallback_receives_explicit_selected_environment(self):
        env=okx_runtime.OKXEnvironment("demo", "", "", "")
        with patch.object(service.subprocess, "run") as run:
            run.return_value.returncode=0; run.return_value.stdout="[]"
            service._run_cli(["okx","--demo","account","positions","--json"], environment=env)
        self.assertEqual(run.call_args.kwargs["env"]["OKXQUANT_OKX_ENV"], "demo")
        self.assertNotIn("OKX_API_KEY", run.call_args.kwargs["env"])


class SecretConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        for name,path in (("KEY_FILE",root/"key"),("STORE_FILE",root/"secrets")):
            p=patch.object(vault,name,path);p.start();self.addCleanup(p.stop)

    def test_concurrent_first_saves_keep_one_key_and_all_fields(self):
        values={"OKX_DEMO_API_KEY":"D", "OKX_DEMO_SECRET_KEY":"DS", "OKX_DEMO_PASSPHRASE":"DP",
                "OKX_LIVE_API_KEY":"L", "OKX_LIVE_SECRET_KEY":"LS", "OKX_LIVE_PASSPHRASE":"LP"}
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda item:vault.save_secrets(dict([item])), values.items()))
        self.assertEqual(vault.load_secrets(),values)
        self.assertEqual(vault.KEY_FILE.stat().st_mode & 0o777,0o600)
        self.assertEqual(vault.STORE_FILE.stat().st_mode & 0o777,0o600)

    def test_save_does_not_destroy_corrupt_encrypted_store(self):
        vault.save_secrets({"OKX_API_KEY":"old"})
        vault.STORE_FILE.write_bytes(b"corrupted-ciphertext")
        with self.assertRaises(ValueError):vault.save_secrets({"OKX_SECRET_KEY":"new"})
        self.assertEqual(vault.STORE_FILE.read_bytes(),b"corrupted-ciphertext")

    def test_delete_does_not_destroy_store_whose_key_is_missing(self):
        vault.STORE_FILE.write_bytes(b"unreadable-without-key")
        with self.assertRaises(ValueError):vault.delete_secrets(["OKX_API_KEY"])
        self.assertEqual(vault.STORE_FILE.read_bytes(),b"unreadable-without-key")
        self.assertFalse(vault.KEY_FILE.exists())


class AuditAndAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        p=patch.object(audit,"AUDIT_FILE",self.root/"audit.jsonl");p.start();self.addCleanup(p.stop)

    def test_audit_is_private_redacted_and_concurrent_lines_remain_valid(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda i:audit.record("test","success",{"i":i,"nested":{"api_key":"DO-NOT-LOG","session_token":"TOKEN"}}),range(30)))
        self.assertTrue(all(results))
        rows=audit.recent(100)
        self.assertEqual(len(rows),30)
        self.assertNotIn("DO-NOT-LOG",audit.AUDIT_FILE.read_text())
        self.assertEqual(audit.AUDIT_FILE.stat().st_mode & 0o777,0o600)

    def test_failed_audit_does_not_make_completed_write_retryable(self):
        with patch.object(audit.os,"open",side_effect=OSError("disk unavailable")),self.assertLogs(audit.LOG,level="WARNING"):
            self.assertFalse(audit.record("close","success"))

    def test_concurrent_login_failures_reach_existing_five_attempt_lockout(self):
        store=admin_auth.AdminAuthStore(self.root/"admin.db")
        user=store.create_user("adminuser","StrongPassword123")
        barrier=threading.Barrier(6)
        def fail(_):
            barrier.wait(timeout=5)
            try:store.login("adminuser","wrong")
            except PermissionError:return True
            return False
        with ThreadPoolExecutor(max_workers=6) as pool:self.assertTrue(all(pool.map(fail,range(6))))
        with store.connect() as db:
            row=db.execute("SELECT failed_attempts,locked_until FROM admin_users WHERE id=?",(user["id"],)).fetchone()
        self.assertEqual(row["failed_attempts"],5)
        self.assertGreater(row["locked_until"],0)

    def test_account_route_does_not_accept_cookie_or_cross_origin_form_as_auth(self):
        app=FastAPI()
        def require(token):
            if token!="header-token":raise HTTPException(401,"header session required")
            return {"username":"admin"}
        account_routes.install(app,require,lambda *args:None)
        with TestClient(app) as client,patch.object(account_connections,"activate") as activate:
            result=client.post("/api/v1/admin/accounts/activate/live",json={"confirmation":"ACTIVATE LIVE"},
                headers={"Origin":"https://attacker.invalid","Cookie":"X-OKXQuant-Session=header-token"})
            self.assertEqual(result.status_code,401);activate.assert_not_called()
            result=client.post("/api/v1/admin/accounts/activate/live",json={"confirmation":"ACTIVATE LIVE"},headers={"X-OKXQuant-Session":"header-token"})
            self.assertEqual(result.status_code,200);activate.assert_called_once()


class OutboundTests(unittest.TestCase):
    def dns(self, address):return [(socket.AF_INET,socket.SOCK_STREAM,6,"",(address,443))]
    def test_all_dns_answers_are_checked_not_only_first(self):
        with patch.object(net_security.socket,"getaddrinfo",return_value=self.dns("8.8.8.8")+self.dns("127.0.0.1")):
            with self.assertRaises(ValueError):net_security.validate_outbound_url("https://storage.example")

    def test_private_storage_optin_still_forbids_local_and_metadata_addresses(self):
        for address in ("127.0.0.1","169.254.169.254","0.0.0.0","224.0.0.1"):
            with patch.object(net_security.socket,"getaddrinfo",return_value=self.dns(address)):
                with self.assertRaises(ValueError):net_security.validate_outbound_url("http://storage.example",allow_private=True)
        with patch.object(net_security.socket,"getaddrinfo",return_value=self.dns("10.1.2.3")):
            self.assertEqual(net_security.validate_outbound_url("http://storage.example",allow_private=True),"http://storage.example")

    def test_invalid_controls_credentials_and_empty_resolution_are_rejected(self):
        for value in ("https://user:password@example.com","https://example.com/\r\nInjected: true","file:///etc/passwd"):
            with self.assertRaises(ValueError):net_security.validate_outbound_url(value)
        with patch.object(net_security.socket,"getaddrinfo",return_value=[]):
            with self.assertRaises(ValueError):net_security.validate_outbound_url("https://empty.example")

    def test_signed_headers_are_not_forwardable_on_redirect(self):
        env=okx_runtime.OKXEnvironment("demo","K","S","P")
        with patch("urllib.request.urlopen",return_value=io.BytesIO(b'{"code":"0","data":[]}')) as wire:
            service._request_untracked("GET","/api/v5/account/balance",env=env)
        req=wire.call_args.args[0]
        redirected=urllib.request.HTTPRedirectHandler().redirect_request(req,None,302,"Found",{},"https://other.example/")
        self.assertFalse(any(k.lower().startswith("ok-access-") for k,v in redirected.header_items()))
        self.assertEqual(dict((k.lower(),v) for k,v in req.header_items())["ok-access-key"],"K")

    def test_private_transport_rejects_url_and_method_injection_before_sending(self):
        env=okx_runtime.OKXEnvironment("demo","K","S","P")
        with patch("urllib.request.urlopen") as wire:
            for method,path,selected in (("GET","https://evil.invalid",env),("DELETE","/api/v5/trade/order",env),
                                        ("GET","/api/v5/account/balance",replace(env,base_url="https://evil.invalid"))):
                with self.assertRaises(ValueError):service._request_untracked(method,path,env=selected)
        wire.assert_not_called()

    def test_structured_exchange_error_retains_code_but_redacts_credentials(self):
        env=okx_runtime.OKXEnvironment("live","PRIVATE-KEY","PRIVATE-SECRET","PRIVATE-PASS")
        body=json.dumps({"code":"50113","msg":"echo PRIVATE-KEY PRIVATE-SECRET PRIVATE-PASS","data":[]}).encode()
        with patch("urllib.request.urlopen",return_value=io.BytesIO(body)):
            with self.assertRaises(service.OKXAPIError) as caught:
                service._request_untracked("GET","/api/v5/account/positions",env=env)
        self.assertEqual(caught.exception.code,"50113")
        self.assertNotIn("PRIVATE-",str(caught.exception))

    def test_native_client_uses_same_validated_transport(self):
        selected=okx_runtime.OKXEnvironment("live","K","S","P")
        with patch.object(service,"_request_untracked",return_value=[{"totalEq":"10"}]) as send:
            value=okx_client.OKXClient()._send_once(selected,"GET","/api/v5/account/balance")
        self.assertEqual(value,[{"totalEq":"10"}])
        self.assertIs(send.call_args.args[3],selected)


class CloseIntentTests(unittest.TestCase):
    def setUp(self):
        self.env=okx_runtime.OKXEnvironment("demo","K","S","P",connection_id="connection",binding_version=1,account_scope="okx:demo:account:A")
        service._INTENTS.clear();self.addCleanup(service._INTENTS.clear)

    def test_same_account_rebinding_invalidates_old_manual_close_token(self):
        token,confirmation=service._create_intent(self.env,{"instId":"BTC-USDT-SWAP","pos":"2","posSide":"long","posId":"1"})
        with patch.object(service,"selected_environment",return_value=replace(self.env,binding_version=2)),patch.object(service,"_request") as wire:
            with self.assertRaises(ValueError):service.fast_close_confirmed(token,confirmation)
        wire.assert_not_called()
        with self.assertRaises(ValueError):service._consume_intent(token)

    def test_concurrent_consumption_is_exactly_once(self):
        token,_=service._create_intent(self.env,{"instId":"BTC-USDT-SWAP","pos":"2","posSide":"long"})
        def consume(_):
            try:service._consume_intent(token);return 1
            except ValueError:return 0
        with ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(consume,range(16))),1)


    def test_reversed_net_position_does_not_consume_old_direction_confirmation(self):
        original={"instId":"BTC-USDT-SWAP","pos":"2","posSide":"net","posId":"1"}
        token,confirmation=service._create_intent(self.env,original)
        with patch.object(service,"selected_environment",return_value=self.env),patch.object(service,"_request",return_value=[{**original,"pos":"-2"}]) as wire:
            with self.assertRaises(ValueError):service.fast_close_confirmed(token,confirmation)
        wire.assert_called_once()
        self.assertEqual(wire.call_args.args[0],"GET")

    def test_nonfinite_close_confirmation_size_is_rejected(self):
        for size in ("nan","inf","0"):
            with self.assertRaises(ValueError):service._create_intent(self.env,{"instId":"BTC-USDT-SWAP","pos":size})


class OAuthLinkTests(unittest.TestCase):
    def test_official_links_only_and_no_dns_or_network(self):
        for uri in ("https://www.okx.com/activate","https://okx.us/activate","https://www.okx.com.tr/activate"):
            self.assertEqual(net_security.validate_oauth_verification_uri(uri),uri)
        for uri in ("https://okx.com.evil.invalid/","https://user@www.okx.com/","http://www.okx.com/",
                    "https://127.0.0.1/","https://www.okx.com:8443/","https://www.okx.com/\nmalicious"):
            with self.subTest(uri=uri),self.assertRaises(ValueError):net_security.validate_oauth_verification_uri(uri)

    def test_legacy_setup_login_rejects_nonofficial_verification_url(self):
        from okxquant_backend import okx_setup
        def result(value):return {"ok":True,"returncode":0,"stdout":json.dumps(value),"stderr":""}
        responses=[result({"profiles":{}}),result({"status":"logged_out"}),
                   result({"verificationUri":"https://evil.invalid/login","userCode":"CODE","expiresIn":300})]
        with patch.object(okx_setup.shutil,"which",return_value="okx"),patch.object(okx_setup,"_run",side_effect=responses):
            with self.assertRaises(ValueError):okx_setup.start_oauth_device_login("global")
