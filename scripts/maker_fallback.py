"""Bounded Maker-first entry routing. Unknown write/read outcomes never trigger a resend."""
from __future__ import annotations
import time

WAIT_SECONDS = 2.5


def explicit_exchange_rejection(result, diagnostics):
    """A complete business rejection is safe to fallback; transport is not."""
    return (result.get('returncode') == 0 and result.get('data') is not None
            and diagnostics.get('exchange_code') not in (None, '', '0'))


def wait_once():
    time.sleep(WAIT_SECONDS)


def status_row(request, env, inst_id, order_id):
    rows = request('GET', '/api/v5/trade/order', {'instId': inst_id, 'ordId': str(order_id)}, env, timeout=8)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError('Maker order status is incomplete')
    row = rows[0]
    if str(row.get('instId') or '') != inst_id or str(row.get('ordId') or '') != str(order_id):
        raise RuntimeError('Maker order identity mismatch')
    return row


def cancel(request, env, inst_id, order_id):
    rows = request('POST', '/api/v5/trade/cancel-order', {'instId': inst_id, 'ordId': str(order_id)}, env, timeout=8)
    if not isinstance(rows, list):
        raise RuntimeError('Maker cancel response is incomplete')
    return rows
