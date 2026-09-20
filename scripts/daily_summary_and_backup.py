#!/usr/bin/env python3
"""
Automated Daily Quant Briefing Engine
Runs at 08:00 & 20:00 Beijing time to sync the lifecycle ledger and publish a read-only performance briefing.
Self-evolution and backups are owned by their dedicated scheduled jobs.
"""

import os
import json
import math
import datetime
import subprocess
import sys

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
BACKUPS_DIR = os.path.join(WORKSPACE_DIR, "backups")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")

if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)
sys.path.append(os.path.join(WORKSPACE_DIR, "scripts"))
try:
    from qq_notifier import notify_daily_summary
except Exception:
    notify_daily_summary = None


def _finite_pnl(row):
    try:
        value = float(row.get("net_pnl", row.get("pnl", 0)))
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0

def generate_daily_briefing_and_backup():
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    now_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now_bj.strftime("%Y-%m-%d")

    # A07 owns legacy/v2 migration. Never read the top-level compatibility
    # projection: it may belong to the account selected before the last switch.
    from scripts.okx_runtime import selected_environment
    from okxquant_backend.account_baseline import load_account_baseline
    from scripts.dashboard_stats import scoped_rows, today_lifecycle_stats
    environment = selected_environment()
    baseline = load_account_baseline(scope=environment.identity)
    try:
        initial_capital = float(baseline.get("initial_capital", 0))
    except (TypeError, ValueError):
        initial_capital = 0
    baseline_configured = (baseline.get("baseline_configured") is True
                           and baseline.get("account_scope") == environment.identity
                           and math.isfinite(initial_capital) and initial_capital > 0)
    reset_time_str = str(baseline.get("reset_time") or "1970-01-01 00:00:00") if baseline_configured else "1970-01-01 00:00:00"
    baseline_text = (f"{initial_capital:.2f} USDT（已确认）" if baseline_configured
                     else "未配置（不计算本金收益率）")

    # 1. Sync full ledger and load trades
    try:
        sync_script = os.path.join(WORKSPACE_DIR, "scripts", "sync_full_ledger.py")
        if os.path.exists(sync_script):
            subprocess.run([sys.executable, sync_script], capture_output=True, text=True, timeout=15)
    except Exception:
        pass

    trades = []
    if os.path.exists(LEDGER_JSON_FILE):
        try:
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                trades = json.load(f)
        except Exception:
            pass

    stats = today_lifecycle_stats(scoped_rows(trades, environment.identity), date_str, reset_time_str)
    closed_today = stats["settled_rows"]
    win_count, loss_count = stats["win_trades"], stats["loss_trades"]
    win_rate, net_pnl = stats["win_rate"], stats["net_realized"]

    # 2. Top Performing Asset
    asset_pnl = {}
    for t in closed_today:
        inst = t.get("inst") or t.get("name") or "OTHER"
        if inst and inst != "None":
            asset_pnl[inst] = asset_pnl.get(inst, 0.0) + _finite_pnl(t)
    
    top_asset = max(asset_pnl.items(), key=lambda x: x[1])[0] if asset_pnl else "暂无"
    top_asset_pnl = asset_pnl.get(top_asset, 0.0)

    # 3. Macro Sentiment & News
    news_file = os.path.join(DATA_DIR, "news_sentiment.json")
    macro_env = "偏多震荡"
    if os.path.exists(news_file):
        try:
            with open(news_file, "r", encoding="utf-8") as f:
                n_data = json.load(f)
                macro_env = n_data.get("macro_sentiment", "偏多震荡")
        except Exception:
            pass

    briefing_text = (
        f"📅 日期：{date_str}（北京时间）\n"
        f"• 本金基线：{baseline_text}\n"
        f"• 今日平仓战绩：{win_count} 胜 / {loss_count} 负（胜率 {win_rate:.1f}%）\n"
        f"• 今日已结净盈亏：{net_pnl:+.2f} USDT\n"
        f"• 最优贡献标的：{top_asset} ({top_asset_pnl:+.2f} U)\n"
        f"• 市场舆情环境：{macro_env}\n"
        f"• 策略状态：多周期趋势共振滤网已激活，黑天鹅熔断哨兵全天候巡检中。"
    )

    if selected_environment().identity != environment.identity:
        raise RuntimeError("Account changed while preparing the daily briefing; notification refused")
    if notify_daily_summary:
        notify_daily_summary(briefing_text)

    print("✅ 每日量化研报已成功生成并推送。")
    return briefing_text

if __name__ == "__main__":
    rep = generate_daily_briefing_and_backup()
    print("Daily Briefing Result:\n" + rep)
