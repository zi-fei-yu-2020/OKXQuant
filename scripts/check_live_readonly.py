"""Explicit, bounded integration checks against the configured DEMO account.

No order, strategy, notification, backup, restore, or scheduler operations.
Credentials are read from the normal local runtime store, never CLI arguments.
Only sanitized summaries are written to the Git-ignored UI artifact directory.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-demo-readonly', action='store_true')
    parser.add_argument('--model-json-test', action='store_true', help='One small paid model request, using the active model.')
    args = parser.parse_args()
    if not args.confirm_demo_readonly:
        parser.error('Explicit --confirm-demo-readonly is required.')
    os.environ['OKXQUANT_TESTING'] = '1'  # suppress background work, NOT network/data mocking
    from okxquant_backend.config import refresh_settings
    from scripts.okx_runtime import selected_environment
    from okxquant_backend.okx_read_service import read_private_resource
    from okxquant_backend.okx_client import OKXClient
    settings = refresh_settings()
    environment = selected_environment()
    if environment.mode != 'demo' or not environment.configured:
        parser.error('A fully configured DEMO credential group is required; LIVE is never tested.')
    output = ROOT / 'okxquant_frontend' / '.ui-artifacts' / 'integration-review'
    output.mkdir(parents=True, exist_ok=True)
    results = []
    secrets = [environment.api_key, environment.secret_key, environment.passphrase, settings.llm_api_key]

    def check(name, fn):
        start = time.monotonic()
        try:
            summary = fn()
            result = {'name': name, 'ok': True, **summary}
        except Exception as exc:
            error = str(exc)
            for secret in secrets:
                if secret:
                    error = error.replace(secret, '[REDACTED]')
            result = {'name': name, 'ok': False, 'error': error[:400]}
        result['duration_ms'] = round((time.monotonic() - start) * 1000)
        results.append(result)
        print(json.dumps(result, ensure_ascii=True), flush=True)

    def account(resource):
        rows = read_private_resource(resource, environment)
        return {'rows': len(rows), 'environment': environment.mode,
                **({'positive_equity': bool(rows and float(rows[0].get('totalEq') or 0) > 0)} if resource == 'balance' else {})}

    for resource in ('balance', 'positions', 'orders', 'bills'):
        check('okx-' + resource, lambda resource=resource: account(resource))
    check('okx-protection-orders', lambda: {'rows': len(read_private_resource('algos', environment, 'BTC-USDT-SWAP'))})
    client = OKXClient()
    def market(inst_id):
        rows = client.ticker(inst_id)
        if not rows or float(rows[0].get('last') or 0) <= 0:
            raise ValueError('No valid market price')
        return {'instrument': inst_id, 'valid_price': True}
    for asset in ('BTC', 'ETH', 'SOL', 'DOGE', 'SUI', 'LINK'):
        check('market-' + asset, lambda asset=asset: market(asset + '-USDT-SWAP'))
    def candles():
        rows = client.candles('BTC-USDT-SWAP', limit=10)
        if not rows:
            raise ValueError('No candles returned')
        return {'rows': len(rows)}
    check('market-candles', candles)

    def dashboard_cycle():
        import dashboard.app as dashboard
        # Sample the real updater once, but never replace the user's persisted cache.
        old_file = dashboard.DASHBOARD_CACHE_FILE
        with tempfile.TemporaryDirectory(prefix='okxquant-live-read-') as temporary:
            dashboard.DASHBOARD_CACHE_FILE = str(Path(temporary) / 'dashboard.json')
            try:
                dashboard.update_cache_cycle()
                data = dashboard.CACHE_DATA
                health = data.get('data_health', {})
                if health.get('status') != 'LIVE':
                    raise RuntimeError('Dashboard updater: ' + json.dumps(health, ensure_ascii=True))
                if data.get('okx_environment') != 'demo':
                    raise ValueError('Dashboard environment mismatch')
                return {'status': health['status'], 'environment': data['okx_environment'],
                        'account_loaded': data.get('account', {}).get('total_eq') is not None,
                        'baseline_configured': data.get('account', {}).get('baseline_configured'),
                        'cumulative_pnl_hidden': data.get('account', {}).get('cum_net_pnl') is None,
                        'positions': len(data.get('positions_summary', {}).get('items', [])),
                        'orders': len(data.get('pending_orders', [])),
                        'factors': len(data.get('factors', []))}
            finally:
                dashboard.DASHBOARD_CACHE_FILE = old_file
    check('dashboard-real-cache-cycle', dashboard_cycle)

    def diagnostic():
        from okxquant_backend.okx_setup import diagnose_okx_runtime
        result = diagnose_okx_runtime('demo', True)
        if not result.get('read_only_ready'):
            raise RuntimeError(result.get('read_probe', {}).get('detail', 'Read-only probe failed'))
        return {'read_only_ready': result['read_only_ready'], 'cli_ready': result['ready']}
    check('okx-readonly-diagnostic', diagnostic)

    if args.model_json_test:
        def model_json():
            from okxquant_backend.llm_manager import execute_llm_request, get_active_llm_runtime
            runtime = get_active_llm_runtime()
            content, reasoning, usage, latency = execute_llm_request(
                messages=[{'role': 'user', 'content': 'This is an integration format test, not a trading decision. Return only a JSON object with ok=true and action="WAIT". Do not call tools.'}],
                response_format={'type': 'json_object'}, reasoning_effort='high', timeout=30,
            )
            parsed = json.loads(content)
            if parsed.get('ok') is not True or parsed.get('action') != 'WAIT':
                raise ValueError('Unexpected diagnostic JSON schema')
            return {'model': runtime['model'], 'reasoning_effort_requested': 'high',
                    'json_schema_valid': True, 'reasoning_returned': bool(reasoning), 'model_latency_ms': latency}
        check('llm-structured-json', model_json)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'environment': 'demo',
              'scope': 'real read-only calls; no trading execution', 'results': results}
    (output / 'live-readonly.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(row['ok'] for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
