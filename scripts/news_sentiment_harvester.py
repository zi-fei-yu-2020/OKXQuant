#!/usr/bin/env python3
"""
OKX Crypto News & Black-Swan Circuit Breaker Harvester
Features:
1. Harvest high-impact crypto news from OKX (Golden Finance, BlockBeats, TechFlow, WallStreetCN)
2. Aggregate real-time multi-coin social & news sentiment (Bullish vs Bearish Ratio)
3. Detect Black-Swan / Extreme Macro Events and trigger Automatic Circuit Breaker (30-min opening freeze)
4. Push critical alerts to QQ Channel
"""

import os
import json
import time
import datetime
import subprocess
import re
import hashlib

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
NEWS_CACHE_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
CIRCUIT_BREAKER_FILE = os.path.join(DATA_DIR, "circuit_breaker.json")
import sys
if WORKSPACE_DIR not in sys.path: sys.path.insert(0, WORKSPACE_DIR)
from scripts.instrument_pool import load_instruments
TARGET_COINS = [item["name"] for item in load_instruments()]

# Institutional-Grade Extreme Black-Swan Regular Expressions
# Only trigger circuit breaker for existential, catastrophic, systemic market shocks
BLACK_SWAN_PATTERNS = [
    (r"(USDT|USDC|DAI).*(严重脱锚|脱锚幅度|depeg|脱锚超过|跌破0\.9[0-8])", "头部稳定币恶性脱锚危机"),
    (r"(币安|OKX|Coinbase|Kraken).*(暂停全部提现|停止提币|申请破产重组|破产倒闭|发生严重挤兑)", "主流中心化交易所崩盘挤兑"),
    (r"(以太坊主网|比特币网络|Solana网络|BNB Chain).*(遭遇51%攻击|全网瘫痪停机|紧急硬分叉回滚)", "顶级底层公链系统性故障/51%攻击"),
    (r"(全面取缔所有加密|宣布比特币非法|宣布数字货币交易非法|爆发核危机)", "国家级极端不可抗力/战争")
]

_COUNTRIES = r'(?:俄罗斯|乌克兰|美国|伊朗|以色列|中国|朝鲜|韩国|印度|巴基斯坦)'
_WAR_DECLARATION = re.compile(_COUNTRIES + r'.{0,12}(?:向|对)' + _COUNTRIES + r'.{0,8}宣战')
_NEGATED_EVENT = re.compile(r'(?:否认|辟谣|并未|没有|未曾|不会|不实|谣言|假设|如果|假如).{0,32}(?:脱锚|提现|提币|破产|挤兑|攻击|瘫痪|回滚|非法|核危机|宣战)', re.I)


def black_swan_reason(text):
    """Correct lexical false positives; not a news sentiment trading filter."""
    for sentence in re.split(r'[。！？!?；;\n]', text):
        if _NEGATED_EVENT.search(sentence):
            continue
        for pattern, name in BLACK_SWAN_PATTERNS:
            if re.search(pattern, sentence, re.I): return name
        if _WAR_DECLARATION.search(sentence): return '国家级极端不可抗力/战争'
    return None


def trigger_circuit_breaker(headline: str, keyword: str, *, event_id=None, source_at=None):
    from pathlib import Path
    from scripts.config_lock import configuration_write
    from scripts.public_market import atomic_json
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_ts = int(time.time())
    day = datetime.datetime.fromtimestamp(float(source_at or now_ts), tz_bj).strftime('%Y-%m-%d')
    key = hashlib.sha256((day + ':' + headline.strip() + ':' + keyword).encode()).hexdigest()
    with configuration_write(CIRCUIT_BREAKER_FILE):
        try:
            previous = json.loads(Path(CIRCUIT_BREAKER_FILE).read_text(encoding='utf8'))
            if not isinstance(previous, dict): previous = {}
        except (OSError,ValueError): previous = {}
        seen = list(previous.get('seen_events') or [])
        if key in seen: return False
        # Preserve the original expiry for legacy repeats instead of sliding
        # the same 30-minute event window forward on every harvest.
        if previous.get('headline') == headline and str(previous.get('triggered_at') or '').startswith(day):
            return False
        cb_data = {'active': True, 'triggered_at': datetime.datetime.fromtimestamp(now_ts,tz_bj).strftime('%Y-%m-%d %H:%M:%S'),
                   'expires_at_ts': now_ts + 1800, 'headline': headline, 'keyword': keyword,
                   'event_id': event_id, 'source_at': source_at, 'seen_events': (seen + [key])[-256:],
                   'action': '暂停新开仓 30 分钟，启动存量持仓保本防御'}
        atomic_json(CIRCUIT_BREAKER_FILE, cb_data)
    try:
        from qq_notifier import notify_circuit_breaker
        notify_circuit_breaker(headline, f'命中突发高危事件【{keyword}】')
    except Exception:
        pass
    print(f'黑天鹅事件已记录: {headline}')
    return True


def is_circuit_breaker_active():
    if os.path.exists(CIRCUIT_BREAKER_FILE):
        try:
            with open(CIRCUIT_BREAKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("active") and time.time() < data.get("expires_at_ts", 0):
                    return True, data
        except Exception:
            pass
    return False, {}

def fetch_and_analyze_news_sentiment():
    from scripts import news_connection, public_market
    deadline = time.monotonic() + 25
    with public_market.file_lock('independent-news-harvest', deadline):
        try:
            with open(NEWS_CACHE_FILE, encoding='utf8') as handle: previous=json.load(handle)
        except (OSError, ValueError): previous={}
        targets=[item['name'] for item in load_instruments()]
        payload=news_connection.collect(targets, previous)
        fresh=news_connection.for_strategy(payload) or {}
        now=time.time()
        for item in fresh.get('latest_news', []):
            if not 0<=now-item.get('source_at',0)<900: continue
            reason=black_swan_reason(item.get('title','')+' '+item.get('summary',''))
            if reason:
                trigger_circuit_breaker(item['title'], reason, event_id=item.get('id'), source_at=item.get('source_at'))
        active,info=is_circuit_breaker_active()
        payload['circuit_breaker']=info if active else {'active':False}
        if active: payload['macro_sentiment']='避险熔断中'
        from okxquant_backend import account_connections
        with account_connections.registry_guard():
            bindings=account_connections.load(); current=bindings['bindings'].get('news')
            generation=bindings['connections'].get(current,{}).get('generation',0)
            if payload.get('connection_id') and (current != payload['connection_id'] or generation != payload.get('connection_generation',0)):
                return {'connection_status':'binding_changed','updated_at':None}
            public_market.atomic_json(NEWS_CACHE_FILE,payload)
            news_scope = 'news:' + str(payload.get('connection_id') or 'unbound')
        # Archive after releasing the registry lock. The archive scope is the news binding,
        # not an arbitrary trading account, because news credentials are independently bound.
        try:
            from scripts import strategy_evidence
            event_id = 'news-' + str(payload.get('connection_id') or 'unbound') + '-' + str(payload.get('last_success_at') or payload.get('last_attempt_at'))
            strategy_evidence.best_effort(news_scope, 'news_snapshot', {
                'schema': 1, 'updated_at': payload.get('updated_at'),
                'last_success_at': payload.get('last_success_at'),
                'connection_status': payload.get('connection_status'),
                'macro_sentiment': payload.get('macro_sentiment'),
                'latest_news': payload.get('latest_news', []),
                'coins_sentiment': payload.get('coins_sentiment', {}),
            }, event_id=event_id)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning('News snapshot archive failed: %s', type(exc).__name__)
        return payload


if __name__ == '__main__':
    result=fetch_and_analyze_news_sentiment()
    print('News collection status='+result.get('connection_status','unknown')+'; last_success='+str(result.get('updated_at')))
