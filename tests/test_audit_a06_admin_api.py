"""A06 route boundaries and configuration/strategy API contracts (offline only)."""
from __future__ import annotations
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from okxquant_backend import app as api
from okxquant_backend.admin_auth import AdminAuthStore
from scripts import prompt_library as profiles


class AdminContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.auth = AdminAuthStore(self.root / "admin.db")
        self.auth.initialize_from_legacy("A06TestPassword12345")
        for target, value in (("admin_auth", self.auth), ("DATA_DIR", self.root)):
            patcher = patch.object(api, target, value)
            patcher.start(); self.addCleanup(patcher.stop)
        self.client = TestClient(api.app)
        login = self.client.post("/api/v1/admin/auth/login", json={"username":"admin", "password":"A06TestPassword12345"})
        self.assertEqual(login.status_code, 200, login.text)
        self.headers = {"X-OKXQuant-Session": login.json()["session_token"]}
        for target in ("audit_record", "refresh_settings"):
            patcher = patch.object(api, target)
            patcher.start(); self.addCleanup(patcher.stop)

    def request(self, method, path, payload=None):
        return self.client.request(method, "/api/v1/admin/" + path, headers=self.headers, json=payload)

    def test_every_installed_admin_route_has_unauthenticated_boundary(self):
        exempt = {"/api/v1/admin/auth/status", "/api/v1/admin/auth/login", "/api/v1/admin/login", "/api/v1/admin/logout", "/api/v1/admin/auth/logout"}
        visited = set()
        # OpenAPI flattens included routers even when FastAPI keeps lazy router
        # wrappers in app.routes (account-center and memory publication APIs).
        for template, operations in api.app.openapi()["paths"].items():
            if not template.startswith("/api/v1/admin/") or template in exempt:
                continue
            for method, operation in operations.items():
                if method not in {"get", "post", "put", "delete", "patch"}: continue
                path = template
                for param in operation.get("parameters", []):
                    if param["in"] != "path": continue
                    value = "1" if param.get("schema", {}).get("type") == "integer" else "a06-example"
                    path = re.sub(r"\{" + re.escape(param["name"]) + r"(?::[^}]+)?\}", value, path)
                with self.subTest(method=method, path=template):
                    response = self.client.request(method, path, json={})
                    self.assertIn(response.status_code, (401, 403, 422), response.text)
                    self.assertIn("no-store", response.headers.get("cache-control", ""))
                visited.add((method, template))
        self.assertGreater(len(visited), 100)
        print(f"A06 unauthenticated route boundaries checked: {len(visited)}")

    def test_persisted_configuration_rejects_control_characters_before_writes(self):
        for path, payload in (
            ("config", {"llm_model":"legit\nOKXQUANT_OKX_ENV=live"}),
            ("notifications", {"telegram_chat_id":"user\rOTHER=1"}),
            ("channels/telegram/toggle", {"enabled":True,"telegram_bot_token":"abc\x00def"}),
        ):
            with self.subTest(path=path), patch.object(api, "update_env") as env, patch.object(api, "save_secrets") as secret:
                response = self.request("PUT", path, payload)
                self.assertEqual(response.status_code, 422, response.text)
                env.assert_not_called(); secret.assert_not_called()

    def test_invalid_notification_urls_are_rejected_before_writes(self):
        for value in ("javascript:alert(1)", "http://", "https://name:password@host.test", "https://host.test:bad"):
            with self.subTest(value=value), patch.object(api, "update_env") as env:
                response = self.request("PUT", "notifications", {"telegram_api_base":value})
                self.assertEqual(response.status_code, 422, response.text)
                env.assert_not_called()

    def test_masked_notification_get_values_are_not_saved_as_credentials(self):
        current = {"OKXQUANT_WECHAT_WEBHOOK":"https://host.test/hook?key=original", "OKXQUANT_NOTIFY_WECHAT_ENABLED":"1"}
        with patch.object(api, "notification_env", return_value=current), patch.object(api, "save_secrets") as secrets, patch.object(api, "update_env") as env:
            response = self.request("PUT", "notifications", {"wechat_enabled":True, "wechat_webhook":"https://host.test/hook?key=********iginal"})
            self.assertEqual(response.status_code, 200, response.text)
            secrets.assert_not_called()
            self.assertEqual(env.call_args.args[0]["OKXQUANT_NOTIFY_WECHAT_ENABLED"], "1")

    def test_toggle_masked_webhook_preserves_existing_secret(self):
        with patch.object(api, "notification_env", return_value={"OKXQUANT_NOTIFICATION_WEBHOOK":"https://host.test/original"}), patch.object(api, "save_secrets") as secrets, patch.object(api, "update_env") as env:
            response = self.request("PUT", "channels/webhook/toggle", {"enabled":True, "webhook_url":"https://host********iginal"})
            self.assertEqual(response.status_code, 200, response.text)
            secrets.assert_not_called()
            env.assert_called_once_with({"OKXQUANT_NOTIFY_WEBHOOK_ENABLED":"1"})

    def test_partial_notification_save_preserves_other_channels_and_destinations(self):
        current = {"OKXQUANT_NOTIFY_QQ_ENABLED":"1", "OKXQUANT_QQ_APP_ID":"bot", "OKXQUANT_QQ_CLIENT_SECRET":"secret", "OKXQUANT_QQ_OPENID":"destination"}
        with patch.object(api, "notification_env", return_value=current), patch.object(api, "update_env") as env:
            response = self.request("PUT", "notifications", {"telegram_api_base":"https://proxy.test"})
            self.assertEqual(response.status_code, 200, response.text)
            values = env.call_args.args[0]
            self.assertEqual(values["OKXQUANT_NOTIFY_QQ_ENABLED"], "1")
            self.assertNotIn("OKXQUANT_QQ_OPENID", values)
            self.assertEqual(values["OKXQUANT_TELEGRAM_API_BASE"], "https://proxy.test")

    def test_whitespace_credentials_do_not_report_channel_enabled(self):
        with patch.object(api, "notification_env", return_value={}), patch.object(api, "save_secrets") as secret, patch.object(api, "update_env") as env:
            response = self.request("PUT", "notifications", {"webhook_enabled":True,"webhook_url":"   "})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["enabled"]["webhook"])
            self.assertTrue(response.json()["warnings"])
            secret.assert_not_called()
            self.assertEqual(env.call_args.args[0]["OKXQUANT_NOTIFY_WEBHOOK_ENABLED"], "0")

    def test_qq_destination_update_removes_legacy_encrypted_override(self):
        with patch.object(api, "notification_env", return_value={}), patch.object(api, "delete_secrets") as delete, patch.object(api, "update_env") as env:
            response = self.request("PUT", "notifications", {"qq_openid":"replacement"})
            self.assertEqual(response.status_code, 200, response.text)
            delete.assert_called_once_with(["OKXQUANT_QQ_OPENID"])
            self.assertEqual(env.call_args.args[0]["OKXQUANT_QQ_OPENID"], "replacement")

    def test_qq_capture_timeout_is_bounded(self):
        for timeout in (0, -1, 301, 100000):
            with self.subTest(timeout=timeout):
                response = self.request("POST", "notifications/qq/capture-openid/start", {"timeout":timeout})
                self.assertEqual(response.status_code, 422, response.text)

    def test_profile_schema_accepts_modules_and_optional_execution_binding(self):
        for mode in ("standard", "small300"):
            payload = api.PromptProfileUpdateRequest(name="profile", editor_mode="modules", execution_profile=mode)
            self.assertEqual(payload.execution_profile, mode)
        self.assertNotIn("execution_profile", api.PromptProfileUpdateRequest(name="profile").model_dump(exclude_unset=True))
        response = self.request("PUT", "prompt-profiles/test", {"name":"profile", "execution_profile":"arbitrary"})
        self.assertEqual(response.status_code, 422)

    def test_profile_partial_save_does_not_overwrite_binding_or_activate(self):
        profile = {"id":"draft", "execution_profile":"small300"}
        with patch.object(api, "update_profile", return_value=profile) as update, patch.object(api, "validate_profile", return_value={"valid":True}), patch.object(api, "activate_profile") as activate:
            response = self.request("PUT", "prompt-profiles/draft", {"name":"renamed", "editor_mode":"modules"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(update.call_args.args[1], {"name":"renamed", "editor_mode":"modules"})
            activate.assert_not_called()
            self.assertFalse(response.json()["activation_changed"])
            self.assertFalse(response.json()["decision_generation_triggered"])

    def test_real_profile_save_preserves_modules_binding_and_active_id(self):
        with patch.object(profiles, "LIBRARY_FILE", self.root / "profiles.json"):
            draft = profiles.create_profile("A06 draft", source_id="stable")
            before = profiles.active_profile()["id"]
            response = self.request("PUT", f"prompt-profiles/{draft['id']}", {"name":"A06 saved", "editor_mode":"modules", "execution_profile":"small300", "pipelines":{"trading_system":[{"id":"preference", "source":"custom", "title":"Preference", "content":"Observe market structure"}]}})
            self.assertEqual(response.status_code, 200, response.text)
            saved = profiles.get_profile(draft["id"])
            self.assertEqual(saved["execution_profile"], "small300")
            self.assertEqual(saved["editor_mode"], "modules")
            self.assertEqual(saved["pipelines"]["trading_system"][0]["content"], "Observe market structure")
            self.assertEqual(profiles.active_profile()["id"], before)
            response = self.request("PUT", f"prompt-profiles/{draft['id']}", {"name":"Again"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(profiles.get_profile(draft["id"])["execution_profile"], "small300")

    def test_activation_does_not_fabricate_fresh_decisions_or_resize_positions(self):
        with patch.object(profiles, "LIBRARY_FILE", self.root / "profiles.json"):
            draft = profiles.create_profile("A06 activation", source_id="small300")
            old_cache = self.root / "ai_brain_decisions.json"
            old_cache.write_text('{"old":"cache"}')
            response = self.request("POST", f"prompt-profiles/{draft['id']}/activate")
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(data["effective_for"], "next_fresh_decision")
            self.assertFalse(data["existing_positions_resized"])
            self.assertFalse(data["decision_generation_triggered"])
            self.assertEqual(data["existing_positions_protection"], "unchanged")
            self.assertEqual(data["decision_cache_status"], "not_regenerated")
            from scripts.execution_profiles import runtime
            self.assertEqual(data["execution_settings"], runtime(profiles.get_profile(draft["id"])))
            self.assertEqual(old_cache.read_text(), '{"old":"cache"}')

    def test_rejected_activation_returns_conflict_not_success(self):
        with patch.object(api, "activate_profile", side_effect=ValueError("disabled profile")):
            response = self.request("POST", "prompt-profiles/missing/activate")
            self.assertEqual(response.status_code, 409)

    def test_private_ledger_cache_is_filtered_to_selected_account(self):
        from types import SimpleNamespace
        rows = [{"environment_id":"current", "id":1}, {"environment_id":"other", "id":2}, {"id":3}, {"environment_id":"current", "account_source_id":"other", "id":4}]
        with patch.object(api, "read_json", return_value=rows), patch("scripts.okx_runtime.selected_environment", return_value=SimpleNamespace(identity="current")):
            response = self.client.get("/api/v1/cache/ledger", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), rows[:1])

    def test_unknown_notification_channel_is_404(self):
        with patch.object(api, "update_env") as update:
            response = self.request("PUT", "channels/unknown/toggle", {"enabled":True})
            self.assertEqual(response.status_code, 404)
            update.assert_not_called()

    def test_invalid_schedule_preserves_file(self):
        with patch.object(api, "save_schedule") as save:
            for times in ([], ["25:00"], ["10:99"], ["08:00"] * 7):
                response = self.request("PUT", "notifications/schedule", {"briefing_times":times})
                self.assertIn(response.status_code, (400, 422), response.text)
            save.assert_not_called()

    def test_valid_schedule_is_normalized_and_keeps_other_jobs(self):
        from okxquant_backend import schedule_store
        with patch.object(schedule_store, "SCHEDULE_FILE", self.root / "schedule.json"):
            response = self.request("PUT", "notifications/schedule", {"briefing_times":["20:00","08:00","20:00"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["briefing_times"], ["08:00","20:00"])
            self.assertEqual(response.json()["backup_time"], "02:00")
            self.assertEqual(schedule_store.load_schedule()["briefing_times"], ["08:00","20:00"])

    def test_failed_notification_test_does_not_claim_sent(self):
        with patch.object(api, "test_channel", return_value={"telegram":"failed: offline"}):
            response = self.request("POST", "notifications/test", {"channel":"telegram", "confirmation":"SEND TEST TELEGRAM"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["sent"])
            self.assertFalse(response.json()["accepted"])
            self.assertTrue(response.json()["attempted"])
            self.assertEqual(response.json()["result"]["status"], "failed")

    def test_accepted_notification_test_is_not_read_receipt(self):
        with patch.object(api, "test_channel", return_value={"telegram":"accepted: HTTP 200"}):
            response = self.request("POST", "notifications/test", {"channel":"telegram", "confirmation":"SEND TEST TELEGRAM"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["accepted"])
            self.assertEqual(response.json()["result"]["status"], "accepted")

    def test_runtime_response_does_not_expose_llm_credentials(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(api,"runtime_overview",return_value={}))
            stack.enter_context(patch.object(api,"read_json",side_effect=lambda name, default: default))
            stack.enter_context(patch.object(api,"get_active_llm_runtime",return_value={"model":"model", "api_key":"a06-test-secret", "unexpected_secret":"never", "reasoning_effort":"high"}))
            for target in ("scripts.wait_audit.public_status", "scripts.entry_opportunities.public_status", "scripts.decision_reporting.execution_history", "scripts.scenario_shadow.public_status", "scripts.capital_pool.status"):
                stack.enter_context(patch(target,return_value={}))
            response=self.request("GET","runtime")
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn("a06-test-secret",response.text)
        self.assertNotIn("unexpected_secret",response.text)
        self.assertTrue(response.json()["llm_runtime"]["api_key_configured"])
        self.assertEqual(response.json()["decision_cache_status"],"historical_not_activation_evidence")

    def test_legacy_custom_save_updates_again_without_losing_other_profiles(self):
        with patch.object(profiles,"LIBRARY_FILE",self.root/"profiles.json"):
            unrelated=profiles.create_profile("Unrelated")
            for text in ("First preference", "Second preference"):
                response=self.request("PUT","prompt-library",{"active_style":"custom","trading_system":text})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(profiles.active_profile()["trading_system"],text)
                self.assertEqual(profiles.get_profile(unrelated["id"])["name"],"Unrelated")
            response=self.request("PUT","prompt-library",{"active_style":"small300"})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(profiles.active_profile()["id"],"small300")
            before=(self.root/"profiles.json").read_bytes()
            response=self.request("PUT","prompt-library",{"active_style":"aggressive"})
            self.assertEqual(response.status_code,422)
            self.assertEqual((self.root/"profiles.json").read_bytes(),before)

    def test_qq_bind_http_start_and_poll_preserve_frontend_contract(self):
        from okxquant_backend import qq_bind
        task={"task_id":"task-a06","connect_url":"https://example.test/qq-bind","expires_in":180}
        with patch.object(qq_bind,"create_bind_task",return_value=task) as start:
            response=self.request("POST","notifications/qq/bind/start",{})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()["task_id"],task["task_id"])
            self.assertEqual(response.json()["connect_url"],task["connect_url"])
            self.assertIn("qr_data_uri",response.json())
            start.assert_called_once_with()
        for state in ("pending","awaiting_message","bound"):
            result={"task_id":"task-a06","status":state,"app_id":"bot-a06","openid":"OPEN" if state=="bound" else ""}
            with patch.object(qq_bind,"poll_bind_task",return_value=result) as poll:
                response=self.request("GET","notifications/qq/bind/task-a06")
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json(),result)
                poll.assert_called_once_with("task-a06")

    def test_expired_qq_bind_http_poll_is_gone(self):
        with patch("okxquant_backend.qq_bind.poll_bind_task",side_effect=RuntimeError("expired")):
            response=self.request("GET","notifications/qq/bind/expired-task")
            self.assertEqual(response.status_code,410,response.text)

    def test_qq_capture_http_start_and_poll_preserve_frontend_contract(self):
        capture={"capture_id":"cap-a06","app_id":"bot-a06","status":"listening","expires_in":60}
        with patch("okxquant_backend.qq_bind.start_openid_capture",return_value=capture) as start:
            response=self.request("POST","notifications/qq/capture-openid/start",{"app_id":"bot-a06","client_secret":"test-only-secret","timeout":60})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json(),capture)
            start.assert_called_once_with(app_id="bot-a06",client_secret="test-only-secret",timeout=60)
        captured={**capture,"status":"captured","openid":"OPEN"}
        with patch("okxquant_backend.qq_bind.poll_openid_capture",return_value=captured) as poll:
            response=self.request("GET","notifications/qq/capture-openid/cap-a06")
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json(),captured)
            poll.assert_called_once_with("cap-a06")

    def test_qq_bind_and_capture_routes_do_not_run_for_operator(self):
        created=self.request("POST","users",{"username":"operator","password":"A06OperatorPassword123","role":"admin"})
        self.assertEqual(created.status_code,200,created.text)
        login=self.client.post("/api/v1/admin/auth/login",json={"username":"operator","password":"A06OperatorPassword123"})
        self.headers={"X-OKXQuant-Session":login.json()["session_token"]}
        with patch("okxquant_backend.qq_bind.create_bind_task") as bind,patch("okxquant_backend.qq_bind.start_openid_capture") as capture:
            for path in ("notifications/qq/bind/start","notifications/qq/capture-openid/start"):
                response=self.request("POST",path,{})
                self.assertEqual(response.status_code,403,response.text)
            bind.assert_not_called();capture.assert_not_called()
