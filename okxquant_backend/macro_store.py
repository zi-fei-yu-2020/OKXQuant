"""Macro-feed configuration. Keys live only in the existing encrypted vault."""
from pathlib import Path
import json
import re

from scripts.config_lock import configuration_write
from scripts.macro_market import digest

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / 'data' / 'macro_sources.json'
KEY_NAME = 'FMP_API_KEY'
DEFAULTS = {'official_enabled': True, 'fmp_enabled': False, 'calendar_timezone': '', 'revision': 0}


def load_settings():
    from scripts.okx_runtime import _load_dotenv
    config = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        if CONFIG_FILE.stat().st_size > 16384:
            raise ValueError('宏观数据配置无效')
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                raise ValueError()
            config.update({k: saved[k] for k in DEFAULTS if k in saved})
        except (ValueError, TypeError):
            raise ValueError('宏观数据配置无效') from None
    if (type(config['official_enabled']) is not bool or type(config['fmp_enabled']) is not bool
            or config['calendar_timezone'] not in {'', 'UTC', 'America/New_York'}
            or type(config['revision']) is not int or config['revision'] < 0):
        raise ValueError('宏观数据配置无效')
    config['api_key'] = str(_load_dotenv().get(KEY_NAME) or '')
    config['binding'] = digest({**config, 'api_key': digest(config['api_key'])})
    return config


def status():
    from scripts.macro_market import snapshot, read_cache
    config = load_settings()
    return {'configuration': {**{k: config[k] for k in DEFAULTS}, 'api_key_configured': bool(config['api_key'])},
            'snapshot': snapshot(read_cache(), config),
            'notes': ['官方收益率为日频参考，不是美联储政策利率或盘中债券报价。',
                      '指数报价不承诺实时；以报价时间和订阅权限为准，休市旧值不参与当前数值证据。',
                      'FMP日历无时区时间需先向供应商确认后配置；留空时不猜测。',
                      '日历覆盖不完整不代表没有事件风险；未新增开仓硬拦截。']}


def update(*, official_enabled, fmp_enabled, calendar_timezone, api_key=None, clear_api_key=False, expected_revision):
    from okxquant_gateway.secrets import save_secrets, delete_secrets, _atomic_write
    if calendar_timezone not in {'', 'UTC', 'America/New_York'}:
        raise ValueError('不支持的日历时区')
    if api_key is not None and not re.fullmatch(r'[A-Za-z0-9_.-]{8,256}', api_key):
        raise ValueError('API Key 格式不合法；不要提交掩码或空白')
    if api_key is not None and clear_api_key:
        raise ValueError('不能同时设置和删除 Key')
    with configuration_write(CONFIG_FILE, timeout=.1):
        old = load_settings()
        if old['revision'] != expected_revision:
            raise ValueError('配置已变化，请刷新后重试')
        if clear_api_key:
            # Environment fallback is not erased here. Disable FMP explicitly too.
            delete_secrets({KEY_NAME})
            fmp_enabled = False
        elif api_key is not None:
            save_secrets({KEY_NAME: api_key})
        value = {'official_enabled': bool(official_enabled), 'fmp_enabled': bool(fmp_enabled),
                 'calendar_timezone': calendar_timezone, 'revision': old['revision']+1}
        _atomic_write(CONFIG_FILE, json.dumps(value, ensure_ascii=False).encode())
    return status()
