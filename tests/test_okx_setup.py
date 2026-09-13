"""Tests for OKX CLI install and runtime diagnostic endpoints."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from okxquant_backend.okx_setup import (
    diagnose_okx_runtime,
    install_okx_cli,
    check_node_npm,
    LATEST_VERSION,
    start_oauth_device_login,
    oauth_status,
)


class OkxInstallTests(unittest.TestCase):
    def test_check_node_npm_returns_paths_without_secrets(self):
        with patch("okxquant_backend.okx_setup.shutil.which", side_effect=["/usr/bin/node", "/usr/bin/npm", "/usr/local/bin/okx"]), \
             patch("okxquant_backend.okx_setup._run", side_effect=[
                 {"ok": True, "returncode": 0, "stdout": "v20.10.0", "stderr": ""},
                 {"ok": True, "returncode": 0, "stdout": "10.2.3", "stderr": ""},
                 {"ok": True, "returncode": 0, "stdout": "1.4.5", "stderr": ""},
             ]):
            result = check_node_npm()
        self.assertTrue(result["ready"])
        self.assertEqual(result["node_path"], "/usr/bin/node")
        self.assertEqual(result["node_version"], "20.10.0")
        self.assertEqual(result["npm_version"], "10.2.3")
        self.assertTrue(result["okx_installed"])
        self.assertTrue(result["okx_supported"])

    def test_check_node_npm_when_missing(self):
        with patch("okxquant_backend.okx_setup.shutil.which", return_value=None):
            result = check_node_npm()
        self.assertFalse(result["ready"])
        self.assertFalse(result["node_installed"])
        self.assertFalse(result["npm_installed"])

    def test_install_aborts_when_node_npm_missing(self):
        with patch("okxquant_backend.okx_setup.check_node_npm", return_value={
            "ready": False, "node_installed": False, "node_path": "",
            "node_version": "", "npm_installed": False, "npm_path": "", "npm_version": "",
        }):
            result = install_okx_cli()
        self.assertFalse(result["ok"])
        self.assertIn("Node.js/npm", result["detail"])

    def test_install_success_verifies_binary_and_version(self):
        responses = [
            {"ok": True, "returncode": 0, "stdout": "1.4.4", "stderr": ""},  # existing okx --version
            {"ok": True, "returncode": 0, "stdout": "", "stderr": ""},  # npm install
            {"ok": True, "returncode": 0, "stdout": "1.4.5", "stderr": ""},  # okx --version
        ]
        with patch("okxquant_backend.okx_setup.check_node_npm", return_value={
            "ready": True, "node_installed": True, "node_path": "/usr/bin/node",
            "node_version": "20.10.0", "npm_installed": True, "npm_path": "/usr/bin/npm",
            "npm_version": "10.2.3",
        }), patch("okxquant_backend.okx_setup._run", side_effect=responses), \
             patch("okxquant_backend.okx_setup.shutil.which", return_value="/usr/local/bin/okx"):
            result = install_okx_cli()
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], "1.4.5")
        self.assertEqual(result["path"], "/usr/local/bin/okx")
        self.assertEqual(result["previous_version"], "1.4.4")
        self.assertTrue(result["restart_gateway_recommended"])

    def test_transient_okx_503_is_reported_as_upstream_not_auth_failure(self):
        responses = [
            {"ok": True, "returncode": 0, "stdout": "1.4.5", "stderr": ""},
            {"ok": True, "returncode": 0, "stdout": '{"profiles":{}}', "stderr": ""},
            {"ok": True, "returncode": 0, "stdout": '{"status":"logged_in","site":"global","scopes":["market:read","demo:read","demo:trade"]}', "stderr": ""},
            {"ok": False, "returncode": 1, "stdout": "", "stderr": "HTTP 503 Service temporarily unavailable"},
            {"ok": False, "returncode": 1, "stdout": "", "stderr": "HTTP 503 Service temporarily unavailable"},
        ]
        with patch("okxquant_backend.okx_setup.shutil.which", return_value="/usr/local/bin/okx"), patch("okxquant_backend.okx_setup._run", side_effect=responses):
            status=diagnose_okx_runtime("demo",False)
        self.assertFalse(status["ready"])
        self.assertTrue(status["degraded"])
        self.assertTrue(any("私有接口" in item for item in status["issues"]))
        self.assertTrue(any("无需重新安装" in item for item in status["steps"]))

    def test_demo_oauth_503_is_distinguished_from_auth_failure(self):
        responses = [
            {"ok": True, "returncode": 0, "stdout": "1.4.4", "stderr": ""},
            {"ok": True, "returncode": 0, "stdout": '{"profiles":{}}', "stderr": ""},
            {"ok": True, "returncode": 0, "stdout": '{"status":"logged_in","site":"global","scopes":["market:read","demo:read","demo:trade","live:read"]}', "stderr": ""},
            {"ok": False, "returncode": 1, "stdout": "", "stderr": "Update available for @okx_ai/okx-trade-cli: 1.4.4 -> 1.4.5\nRun: npm install -g @okx_ai/okx-trade-cli\n\nError: HTTP 503 from OKX: Service temporarily unavailable. Code: 50001"},
            {"ok": True, "returncode": 0, "stdout": "[]", "stderr": ""},
        ]
        with patch("okxquant_backend.okx_setup.shutil.which", return_value="/usr/local/bin/okx"), patch("okxquant_backend.okx_setup._run", side_effect=responses):
            status = diagnose_okx_runtime("demo", False)
        self.assertFalse(status["ready"])
        self.assertTrue(status["degraded"])
        self.assertTrue(status["demo_oauth_unavailable"])
        self.assertTrue(status["live_control_probe"]["ok"])
        self.assertNotIn("Update available", status["read_probe"]["detail"])
        self.assertTrue(any("OAuth 授权本身正常" in item for item in status["issues"]))
        self.assertTrue(any("模拟盘 API Key" in item for item in status["steps"]))
        self.assertTrue(any("不会自动切换 LIVE" in item for item in status["steps"]))

    def test_oauth_device_login_returns_public_code_fields(self):
        responses=[
            {"ok":True,"returncode":0,"stdout":'{"profiles":{}}',"stderr":""},
            {"ok":True,"returncode":0,"stdout":'{"status":"not_logged_in","site":"global"}',"stderr":""},
            {"ok":True,"returncode":0,"stdout":'{"verificationUri":"https://www.okx.com/device","userCode":"ABCD-EFGH","expiresIn":600}',"stderr":""},
        ]
        with patch("okxquant_backend.okx_setup.shutil.which",return_value="/usr/local/bin/okx"),patch("okxquant_backend.okx_setup._run",side_effect=responses):
            result=start_oauth_device_login("global")
        self.assertEqual(result["status"],"pending")
        self.assertEqual(result["user_code"],"ABCD-EFGH")
        self.assertNotIn("access_token",result)

    def test_oauth_login_does_not_restart_when_already_logged_in(self):
        responses=[
            {"ok":True,"returncode":0,"stdout":'{"profiles":{}}',"stderr":""},
            {"ok":True,"returncode":0,"stdout":'{"status":"logged_in","site":"global","scopes":["demo:read","demo:trade"]}',"stderr":""},
        ]
        with patch("okxquant_backend.okx_setup.shutil.which",return_value="/usr/local/bin/okx"),patch("okxquant_backend.okx_setup._run",side_effect=responses) as run:
            result=start_oauth_device_login("global")
        self.assertEqual(result["status"],"already_logged_in")
        self.assertEqual(run.call_count,2)

    def test_oauth_login_rejects_api_key_precedence(self):
        response={"ok":True,"returncode":0,"stdout":'{"profiles":{"demo":{"api_key":"configured"}}}',"stderr":""}
        with patch("okxquant_backend.okx_setup.shutil.which",return_value="/usr/local/bin/okx"),patch("okxquant_backend.okx_setup._run",return_value=response):
            with self.assertRaisesRegex(RuntimeError,"API Key Profile"):
                start_oauth_device_login("global")

    def test_oauth_status_only_exposes_safe_identity_fields(self):
        response={"ok":True,"returncode":0,"stdout":'{"status":"logged_in","site":"global","scopes":["demo:read"],"accessToken":"secret"}',"stderr":""}
        with patch("okxquant_backend.okx_setup.shutil.which",return_value="/usr/local/bin/okx"),patch("okxquant_backend.okx_setup._run",return_value=response):
            result=oauth_status()
        self.assertEqual(result["status"],"logged_in")
        self.assertNotIn("accessToken",result)
        self.assertEqual(result["account_label"],"")

    def test_install_fails_when_npm_returns_error(self):
        with patch("okxquant_backend.okx_setup.check_node_npm", return_value={
            "ready": True, "node_installed": True, "node_path": "/usr/bin/node",
            "node_version": "20.10.0", "npm_installed": True, "npm_path": "/usr/bin/npm",
            "npm_version": "10.2.3",
        }), patch("okxquant_backend.okx_setup._run", return_value={
            "ok": False, "returncode": 1, "stdout": "", "stderr": "EACCES: permission denied",
        }):
            result = install_okx_cli()
        self.assertFalse(result["ok"])
        self.assertIn("EACCES", result["detail"])


if __name__ == "__main__":
    unittest.main()
