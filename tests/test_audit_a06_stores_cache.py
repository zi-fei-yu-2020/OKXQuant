"""A06 file persistence, schedule timing, and account-cache regressions."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from okxquant_backend import settings_store as store, schedule_store, scheduler, macro_status, account_baseline
from scripts import sync_web_data as sync, generate_snapshots as snapshots
from scripts.okx_runtime import OKXEnvironment


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for patcher in (patch.object(store, "ENV_FILE", self.root / "runtime.env"), patch.object(store, "refresh_settings"), patch.dict(os.environ, {}, clear=True), patch.object(schedule_store,"SCHEDULE_FILE",self.root / "schedule.json")):
            patcher.start(); self.addCleanup(patcher.stop)

    def test_telegram_api_base_roundtrip(self):
        store.update_env({"OKXQUANT_TELEGRAM_API_BASE":"https://proxy.test"})
        self.assertIn("OKXQUANT_TELEGRAM_API_BASE=https://proxy.test", store.ENV_FILE.read_text())
        self.assertEqual(os.environ["OKXQUANT_TELEGRAM_API_BASE"], "https://proxy.test")

    def test_duplicate_keys_cannot_override_new_save(self):
        store.ENV_FILE.write_text("LLM_MODEL=old\n# keep\nLLM_MODEL=stale\nUNMANAGED=value\n")
        store.update_env({"LLM_MODEL":"new"})
        self.assertEqual(store.ENV_FILE.read_text().count("LLM_MODEL="), 1)
        self.assertIn("LLM_MODEL=new", store.ENV_FILE.read_text())
        self.assertIn("UNMANAGED=value", store.ENV_FILE.read_text())

    def test_bool_is_persisted_in_runtime_format(self):
        store.update_env({"OKXQUANT_NOTIFY_QQ_ENABLED":True})
        self.assertEqual(os.environ["OKXQUANT_NOTIFY_QQ_ENABLED"], "1")

    def test_multiline_rejected_without_partial_write(self):
        store.ENV_FILE.write_text("LLM_MODEL=before\n")
        for value in ("bad\nOKXQUANT_OKX_ENV=live", "bad\rvalue", "bad\x00value"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                store.update_env({"LLM_MODEL":value, "LLM_BASE_URL":"https://valid.test"})
            self.assertEqual(store.ENV_FILE.read_text(), "LLM_MODEL=before\n")
            self.assertNotIn("LLM_BASE_URL", os.environ)

    def test_concurrent_saves_do_not_lose_unrelated_fields(self):
        keys = ["LLM_MODEL", "LLM_BASE_URL", "OKXQUANT_TELEGRAM_CHAT_ID", "OKXQUANT_TELEGRAM_API_BASE", "OKXQUANT_QQ_APP_ID"]
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda key: store.update_env({key:"value-"+key}), keys))
        content = store.ENV_FILE.read_text()
        for key in keys:
            self.assertIn(f"{key}=value-{key}", content)

    def test_remove_creates_parent_and_removes_all_duplicates(self):
        nested = self.root / "missing" / "runtime.env"
        with patch.object(store,"ENV_FILE",nested):
            store.remove_env({"LLM_MODEL"})
            self.assertTrue(nested.exists())
            nested.write_text("LLM_MODEL=first\nLLM_MODEL=second\nOTHER=ok\n")
            store.remove_env({"LLM_MODEL"})
            self.assertEqual(nested.read_text(), "OTHER=ok\n")

    def test_persisted_env_has_private_permissions(self):
        store.update_env({"LLM_MODEL":"model"})
        self.assertEqual(store.ENV_FILE.stat().st_mode & 0o777, 0o600)

    def test_malformed_schedule_falls_back_without_crash(self):
        for payload in ([], None, "bad", {"briefing_times":None}, {"briefing_times":["25:00"],"backup_time":[]}, {"timezone":"UTC"}):
            schedule_store.SCHEDULE_FILE.write_text(json.dumps(payload))
            self.assertEqual(schedule_store.load_schedule(), schedule_store.DEFAULT_SCHEDULE)

    def test_default_schedule_has_no_mutable_alias(self):
        first = schedule_store.load_schedule()
        first["briefing_times"].append("12:00")
        self.assertEqual(schedule_store.load_schedule()["briefing_times"], ["08:00", "20:00"])

    def test_delayed_daily_job_catches_up_once(self):
        now = datetime(2026, 9, 19, 8, 7, tzinfo=timezone(timedelta(hours=8)))
        self.assertTrue(scheduler.due_daily(now,"08:00",None))
        self.assertFalse(scheduler.due_daily(now,"08:00",now))
        self.assertFalse(scheduler.due_daily(now,"20:00",None))
        self.assertTrue(scheduler.due_daily(now.replace(hour=20),"20:00",now))
        self.assertFalse(scheduler.due_daily(now,"25:00",None))
        self.assertFalse(scheduler.due_daily(now,None,None))

    def test_job_timeout_does_not_kill_scheduler(self):
        with patch.object(scheduler.subprocess,"run",side_effect=subprocess.TimeoutExpired("offline-job",600)):
            scheduler.run_script("daily_briefing")

    def test_malformed_macro_validation_does_not_crash_projection(self):
        for invalid in ([], ["invalid"], "bad"):
            self.assertEqual(macro_status.project({},[],validation=invalid,now=100)["status"], "empty")


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = OKXEnvironment("demo", "a06-key", "a06-secret", "a06-pass")
        for target in (sync, snapshots):
            patcher=patch.object(target,"selected_environment",return_value=self.env)
            patcher.start();self.addCleanup(patcher.stop)
        for target,name,filename in ((sync,"DATA_DIR",None),(sync,"DATA_JSON_PATH","trading_data.json"),(sync,"LEDGER_JSON_FILE","trading_ledger.json"),(sync,"SNAPSHOTS_JSON_FILE","snapshots.json"),(sync,"LOG_FILE","absent.log"),(snapshots,"DATA_DIR",None),(snapshots,"SNAPSHOTS_FILE","snapshots.json"),(account_baseline,"BASELINE_FILE","baseline.json")):
            patcher=patch.object(target,name,(self.root/filename) if target is account_baseline else str(self.root/filename) if filename else str(self.root))
            patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(sync,"load_instruments",return_value=[]);patcher.start();self.addCleanup(patcher.stop)
        self.balance=[{"details":[{"ccy":"USDT","eq":"0","availEq":"0","cashBal":"0","upl":"0"}]}]

    def responses(self, command, **kwargs):
        if "auth status" in command:return {"status":"logged_in"}
        if " balance " in command:return self.balance
        if " positions " in command:return []
        raise AssertionError("Unexpected private request")

    def test_zero_balance_is_not_replaced_with_old_account_equity(self):
        (self.root/"snapshots.json").write_text(json.dumps([{"equity":99999,"environment_id":"different"}]))
        with patch.object(sync,"run_json_cmd",side_effect=self.responses):sync.generate_trading_data()
        data=json.loads((self.root/"trading_data.json").read_text())
        self.assertEqual(data["account"]["total_eq"],0)
        self.assertEqual(data["account_source_id"],self.env.identity)
        self.assertEqual(data["snapshots"],[])
        self.assertEqual(data["auth"]["userCode"],"")

    def test_unknown_failed_balance_does_not_overwrite_previous_cache(self):
        cache=self.root/"trading_data.json";cache.write_text("previous")
        self.balance=None
        with patch.object(sync,"run_json_cmd",side_effect=self.responses),self.assertRaises(ValueError):sync.generate_trading_data()
        self.assertEqual(cache.read_text(),"previous")

    def test_failed_positions_does_not_publish_false_flat_account(self):
        cache=self.root/"trading_data.json";cache.write_text("previous")
        def response(command,**kwargs):
            return None if " positions " in command else self.responses(command,**kwargs)
        with patch.object(sync,"run_json_cmd",side_effect=response),self.assertRaises(ValueError):sync.generate_trading_data()
        self.assertEqual(cache.read_text(),"previous")

    def test_unscoped_and_other_account_ledger_rows_are_excluded(self):
        rows=[{"environment_id":self.env.identity,"id":1},{"id":2},{"environment_id":"other","id":3}]
        (self.root/"trading_ledger.json").write_text(json.dumps(rows))
        with patch.object(sync,"run_json_cmd",side_effect=self.responses):sync.generate_trading_data()
        self.assertEqual(json.loads((self.root/"trading_data.json").read_text())["trades"],rows[:1])

    def test_account_switch_during_collection_does_not_publish(self):
        other=OKXEnvironment("live","other","secret","pass")
        with patch.object(sync,"selected_environment",side_effect=[self.env,other]),patch.object(sync,"run_json_cmd",side_effect=self.responses),self.assertRaises(ValueError):sync.generate_trading_data()
        self.assertFalse((self.root/"trading_data.json").exists())

    def test_pool_is_reloaded_every_sync_cycle(self):
        with patch.object(sync,"run_json_cmd",side_effect=self.responses),patch.object(sync,"load_instruments",return_value=[]) as load:
            sync.generate_trading_data();sync.generate_trading_data()
            self.assertEqual(load.call_count,2)

    def test_net_short_positions_are_counted(self):
        def response(command,**kwargs):
            if " positions " in command:return [{"instId":"BTC-USDT-SWAP","pos":"-1","posSide":"net"}]
            return self.responses(command,**kwargs)
        with patch.object(sync,"run_json_cmd",side_effect=response):sync.generate_trading_data()
        self.assertEqual(json.loads((self.root/"trading_data.json").read_text())["positions_summary"]["short_count"],1)

    def test_failed_cli_command_stdout_is_not_trusted(self):
        from types import SimpleNamespace
        with patch.object(sync.subprocess,"run",return_value=SimpleNamespace(returncode=1,stdout='[{"eq":999}]')):
            self.assertIsNone(sync.run_json_cmd("offline-command"))

    def test_snapshot_does_not_invent_baseline_or_bill_equity(self):
        (self.root/"baseline.json").write_text('{}')
        with patch.object(snapshots,"run_json_cmd",return_value=self.balance) as run:
            data=snapshots.generate_live_snapshots()
        self.assertEqual(run.call_count,1)
        self.assertEqual(len(data),1)
        self.assertEqual(data[0]["total_eq"],0)
        self.assertIsNone(data[0]["pnl"])
        self.assertIsNone(data[0]["roi"])
        self.assertEqual(data[0]["environment_id"],self.env.identity)

    def test_scoped_baseline_computes_real_zero_equity_loss(self):
        (self.root/"baseline.json").write_text(json.dumps({"initial_capital":100,"account_scope":self.env.identity}))
        with patch.object(snapshots,"run_json_cmd",return_value=self.balance):data=snapshots.generate_live_snapshots()
        self.assertEqual(data[-1]["pnl"],-100)
        self.assertEqual(data[-1]["roi"],-100)

    def test_snapshot_failure_preserves_existing_file(self):
        path=self.root/"snapshots.json";path.write_text("previous")
        with patch.object(snapshots,"run_json_cmd",return_value=None),self.assertRaises(ValueError):snapshots.generate_live_snapshots()
        self.assertEqual(path.read_text(),"previous")

    def test_snapshots_reject_nonfinite_equity(self):
        for value in ("nan","inf","-inf"):
            balance=[{"details":[{"ccy":"USDT","eq":value}]}]
            with patch.object(snapshots,"run_json_cmd",return_value=balance),self.assertRaises(ValueError):snapshots.generate_live_snapshots()

    def test_qq_old_gateway_cannot_restore_previous_bot_id(self):
        # Transport is not exercised and is optional in the isolated review venv.
        import sys
        from types import ModuleType
        with patch.dict(sys.modules, {"websockets": ModuleType("websockets")}):
            from okxquant_backend import qq_gateway_daemon as gateway
        with patch.object(gateway,"_get_credentials",return_value=("new-bot","secret","")),patch("okxquant_backend.settings_store.update_env") as update,patch("okxquant_gateway.secrets.delete_secrets") as delete:
            self.assertFalse(gateway._save_openid("old-bot","old-openid"))
            update.assert_not_called();delete.assert_not_called()

    def test_qq_new_bot_does_not_inherit_old_openid(self):
        from okxquant_backend import qq_bind
        with patch("okxquant_backend.notifications._env",return_value={"OKXQUANT_QQ_APP_ID":"old-bot"}),patch("okxquant_backend.settings_store.update_env") as update,patch("okxquant_backend.settings_store.remove_env"),patch("okxquant_gateway.secrets.save_secrets"),patch("okxquant_gateway.secrets.delete_secrets") as delete:
            qq_bind._persist("new-bot","secret","")
            self.assertEqual(update.call_args.args[0]["OKXQUANT_QQ_OPENID"],"")
            delete.assert_called_once_with(["OKXQUANT_QQ_OPENID"])

    def test_blank_balance_is_unknown_not_zero(self):
        for value in (None, "", True):
            self.balance=[{"details":[{"ccy":"USDT","eq":value}]}]
            with self.subTest(value=value),patch.object(sync,"run_json_cmd",side_effect=self.responses),self.assertRaises(ValueError):
                sync.generate_trading_data()
            self.assertFalse((self.root/"trading_data.json").exists())

    def test_malformed_account_marker_is_not_adopted(self):
        from scripts.dashboard_stats import scoped_rows
        self.assertEqual(scoped_rows([{"environment_id":[self.env.identity]}],self.env.identity),[])

    def test_strategy_status_ignores_unowned_legacy_account_reports(self):
        from okxquant_backend import strategy_status as status
        (self.root/"data").mkdir()
        for name,payload in (("memory_candidates.json",{"candidates":["other-account-memory"]}), ("research_report.json",{"scope":"other-account","status":"ready"})):
            (self.root/"data"/name).write_text(json.dumps(payload))
        with patch.object(status,"ROOT",self.root),patch.object(status,"DB_PATH",self.root/"absent.db"),patch.object(status,"selected_environment",return_value=self.env),patch("scripts.memory_registry.view",return_value={"managed":False}):
            result=status.strategy_status()
        self.assertEqual(result["memory_candidates"],[])
        self.assertEqual(result["research_status"],"insufficient_evidence")

    def test_snapshot_uses_a07_v2_account_baseline_map_not_last_projection(self):
        document={"schema_version":2,"account_scope":"okx:live:other","initial_capital":9999,
                  "baselines":{self.env.identity:{"account_scope":self.env.identity,"initial_capital":250}}}
        (self.root/"baseline.json").write_text(json.dumps(document))
        with patch.object(snapshots,"run_json_cmd",return_value=self.balance):
            result=snapshots.generate_live_snapshots()
        self.assertEqual(result[-1]["pnl"],-250)
        self.assertTrue(result[-1]["baseline_configured"])

    def test_live_snapshot_does_not_adopt_unscoped_legacy_baseline(self):
        live=OKXEnvironment("live","a06-live-key","secret","pass")
        (self.root/"baseline.json").write_text('{"initial_capital":9999}')
        with patch.object(snapshots,"selected_environment",return_value=live),patch.object(snapshots,"run_json_cmd",return_value=self.balance):
            result=snapshots.generate_live_snapshots()
        self.assertIsNone(result[-1]["pnl"])
        self.assertIsNone(result[-1]["roi"])
        self.assertFalse(result[-1]["baseline_configured"])
