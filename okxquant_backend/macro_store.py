"""Configuration for keyless official macro sources only."""
from pathlib import Path
import json
from scripts.config_lock import configuration_write
from scripts.macro_market import digest, VERSION

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / 'data' / 'macro_sources.json'
DEFAULTS = {'official_enabled': True, 'revision': 0}


def load_settings():
    config = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        if CONFIG_FILE.stat().st_size > 16384:
            raise ValueError('宏观数据配置无效')
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                raise ValueError()
            # Legacy provider fields cannot reactivate removed functionality.
            config.update({k: saved[k] for k in DEFAULTS if k in saved})
        except (ValueError, TypeError):
            raise ValueError('宏观数据配置无效') from None
    if type(config['official_enabled']) is not bool or type(config['revision']) is not int or config['revision'] < 0:
        raise ValueError('宏观数据配置无效')
    config['binding'] = digest({'schema': VERSION, **config})
    return config


def status():
    from scripts.macro_market import snapshot, read_cache
    config = load_settings()
    return {'configuration': {k: config[k] for k in DEFAULTS},
            'snapshot': snapshot(read_cache(), config),
            'notes': ['仅采集无需 Key 的财政部收益率和 BEA/BLS 官方日程。',
                      '官方收益率为日频参考，不是美联储政策利率或盘中债券报价。',
                      '美股、美元指数及经济数据预期值和实际值尚未接入。',
                      '日历覆盖不完整不代表没有事件风险；未新增开仓硬拦截。']}


def update(*, official_enabled, expected_revision):
    from okxquant_gateway.secrets import _atomic_write
    with configuration_write(CONFIG_FILE, timeout=.1):
        old = load_settings()
        if old['revision'] != expected_revision:
            raise ValueError('配置已变化，请刷新后重试')
        value = {'official_enabled': bool(official_enabled), 'revision': old['revision']+1}
        _atomic_write(CONFIG_FILE, json.dumps(value, ensure_ascii=False).encode())
    return status()
