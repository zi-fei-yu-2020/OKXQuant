import unittest
from unittest.mock import patch, MagicMock

from okxquant_backend import notifications


class NotificationsTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "OKXQUANT_NOTIFY_WEBHOOK_ENABLED": "1",
            "OKXQUANT_NOTIFICATION_WEBHOOK": "https://oapi.dingtalk.com/robot/send?access_token=mock",
            "OKXQUANT_NOTIFY_WECHAT_ENABLED": "1",
            "OKXQUANT_WECHAT_WEBHOOK": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=mock",
            "OKXQUANT_NOTIFY_TELEGRAM_ENABLED": "1",
            "OKXQUANT_TELEGRAM_BOT_TOKEN": "bot123456:mocktoken",
            "OKXQUANT_TELEGRAM_CHAT_ID": "12345678",
            "OKXQUANT_TELEGRAM_API_BASE": "https://custom-tg-proxy.example.com",
            "OKXQUANT_NOTIFY_QQ_ENABLED": "1",
            "OKXQUANT_QQ_APP_ID": "1905549905",
            "OKXQUANT_QQ_CLIENT_SECRET": "mocksecret",
            "OKXQUANT_QQ_OPENID": "MOCK_USER_OPENID",
        }

    def test_diagnose_ready_and_incomplete(self):
        diag = notifications.diagnose_channel("qq", self.env)
        self.assertEqual(diag["status"], "ready")

        incomplete_env = dict(self.env)
        incomplete_env["OKXQUANT_QQ_OPENID"] = ""
        diag2 = notifications.diagnose_channel("qq", incomplete_env)
        self.assertEqual(diag2["status"], "incomplete")
        self.assertIn("自动获取 OpenID", diag2["detail"])

    def test_send_webhook_smart_payload_dingtalk(self):
        with patch("okxquant_backend.notifications.validate_outbound_url", return_value="https://oapi.dingtalk.com/robot/send?access_token=mock"), \
             patch("okxquant_backend.notifications._post_json", return_value=(True, "HTTP 200", {"errcode": 0})) as mock_post:
            ok, detail = notifications.send_channel("webhook", "钉钉测试消息", self.env)
            self.assertTrue(ok)
            self.assertIn("accepted", detail)
            args, _ = mock_post.call_args
            self.assertEqual(args[1], {"msgtype": "text", "text": {"content": "钉钉测试消息"}})

    def test_send_webhook_smart_payload_feishu(self):
        env = dict(self.env)
        env["OKXQUANT_NOTIFICATION_WEBHOOK"] = "https://open.feishu.cn/open-apis/bot/v2/hook/mock"
        with patch("okxquant_backend.notifications.validate_outbound_url", return_value=env["OKXQUANT_NOTIFICATION_WEBHOOK"]), \
             patch("okxquant_backend.notifications._post_json", return_value=(True, "HTTP 200", {"code": 0})) as mock_post:
            ok, detail = notifications.send_channel("webhook", "飞书测试消息", env)
            self.assertTrue(ok)
            args, _ = mock_post.call_args
            self.assertEqual(args[1], {"msg_type": "text", "content": {"text": "飞书测试消息"}})

    def test_send_webhook_smart_payload_discord(self):
        env = dict(self.env)
        env["OKXQUANT_NOTIFICATION_WEBHOOK"] = "https://discord.com/api/webhooks/mock"
        with patch("okxquant_backend.notifications.validate_outbound_url", return_value=env["OKXQUANT_NOTIFICATION_WEBHOOK"]), \
             patch("okxquant_backend.notifications._post_json", return_value=(True, "HTTP 200", {})) as mock_post:
            ok, detail = notifications.send_channel("webhook", "Discord消息", env)
            self.assertTrue(ok)
            args, _ = mock_post.call_args
            self.assertEqual(args[1], {"content": "Discord消息"})

    def test_send_telegram_uses_custom_api_base(self):
        with patch("okxquant_backend.notifications._post_json", return_value=(True, "HTTP 200", {"ok": True, "result": {"message_id": 999}})) as mock_post:
            ok, detail = notifications.send_channel("telegram", "Telegram测试", self.env)
            self.assertTrue(ok)
            self.assertIn("accepted", detail)
            args, _ = mock_post.call_args
            self.assertTrue(args[0].startswith("https://custom-tg-proxy.example.com/botbot123456:mocktoken/sendMessage"))

    def test_send_qq_token_and_message_success(self):
        with patch("okxquant_backend.notifications._post_json") as mock_post:
            mock_post.side_effect = [
                (True, "HTTP 200", {"access_token": "valid_token_xyz"}),
                (True, "HTTP 200", {"id": "msg-12345"}),
            ]
            ok, detail = notifications.send_channel("qq", "QQ测试", self.env)
            self.assertTrue(ok)
            self.assertIn("accepted: id=msg-12345", detail)
            self.assertEqual(mock_post.call_count, 2)
            first_url = mock_post.call_args_list[0][0][0]
            second_url = mock_post.call_args_list[1][0][0]
            self.assertEqual(first_url, notifications.QQ_TOKEN_URL)
            self.assertIn("/v2/users/MOCK_USER_OPENID/messages", second_url)

    def test_send_qq_handles_11255_gracefully(self):
        with patch("okxquant_backend.notifications._post_json") as mock_post:
            mock_post.side_effect = [
                (True, "HTTP 200", {"access_token": "valid_token_xyz"}),
                (False, "HTTP 400 Bad Request", {"code": 11255, "message": "请求的资源不存在"}),
            ]
            ok, detail = notifications.send_channel("qq", "QQ测试", self.env)
            self.assertFalse(ok)
            self.assertIn("11255", detail)
            self.assertIn("自动获取 OpenID", detail)


if __name__ == "__main__":
    unittest.main()
