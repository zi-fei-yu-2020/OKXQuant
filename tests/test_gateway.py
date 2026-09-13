"""Gateway persistence and retry tests without external network calls."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from okxquant_gateway.events import GatewayEvent
from okxquant_gateway.store import GatewayStore


class GatewayStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_publish_creates_one_delivery_per_channel(self):
        event = GatewayEvent("trade.opened", "开仓", "BTC LONG", priority=90)
        self.store.publish(event, ["qq", "telegram"])
        stats = self.store.stats()
        self.assertEqual(stats["pending"], 2)
        due = self.store.claim_due()
        self.assertEqual({row["channel"] for row in due}, {"qq", "telegram"})

    def test_delivery_success_isolated_from_failure(self):
        event = GatewayEvent("risk.triggered", "风险", "spread", priority=100)
        self.store.publish(event, ["qq", "telegram"])
        due = self.store.claim_due()
        self.store.complete(due[0]["id"])
        self.store.fail(due[1]["id"], due[1]["attempts"], "timeout")
        stats = self.store.stats()
        self.assertEqual(stats["delivered"], 1)
        self.assertEqual(stats["retry"], 1)

    def test_processing_recovered_after_restart(self):
        event = GatewayEvent("briefing.ready", "简报", "daily")
        self.store.publish(event, ["webhook"])
        self.store.claim_due()
        self.assertEqual(self.store.stats()["processing"], 1)
        self.store.recover_processing()
        self.assertEqual(self.store.stats()["retry"], 1)

    def test_critical_event_requires_one_delivered_channel(self):
        event = GatewayEvent("trade.closed", "平仓", "done", priority=95)
        self.store.publish(event, ["qq", "telegram"])
        self.assertEqual(self.store.event_health()["critical_unmet"], 1)
        due = self.store.claim_due()
        self.store.complete(due[0]["id"])
        self.assertEqual(self.store.event_health()["critical_unmet"], 0)

    def test_failed_delivery_becomes_dead_letter(self):
        event = GatewayEvent("service.degraded", "服务", "down")
        self.store.publish(event, ["qq"])
        row = self.store.claim_due()[0]
        self.store.fail(row["id"], 5, "terminal", max_attempts=6)
        self.assertEqual(self.store.stats()["dead"], 1)
        self.assertTrue(self.store.replay_dead(row["id"]))
        self.assertEqual(self.store.stats()["pending"], 1)
        self.assertFalse(self.store.replay_dead(row["id"]))


if __name__ == "__main__":
    unittest.main()
