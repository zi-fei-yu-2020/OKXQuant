"""Bounded, credential-redacted evidence for entry command failures."""
import json
import re


def submission_diagnostics(result, env):
    message = str(result.get('stderr') or result.get('stdout') or '')
    # Redact known credentials first, even when embedded in a URL/command.
    for name in ('api_key', 'secret_key', 'passphrase'):
        value = getattr(env, name, None)
        if isinstance(value, str) and value:
            message = message.replace(value, '[REDACTED]')
    message = re.sub(r'(?i)(Bearer\s+)\S+', r'\1[REDACTED]', message)
    message = re.sub(r'''(?ix)(["']?(?:api[_-]?key|secret[_-]?key|passphrase|authorization|access[_-]?token|password)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)''', r'\1[REDACTED]', message)
    message = re.sub(r'https?://[^\s]+', '[URL]', message)
    match = re.search(r'(?i)(?:OKX|Code:|sCode["\s]*[:=]["\s]*|"code"\s*:\s*")\s*(\d{5})', message)
    payload = result.get('data')
    code = str(payload.get('code')) if isinstance(payload, dict) and payload.get('code') is not None else None
    if code in (None, '0'):
        rows = payload.get('data', payload) if isinstance(payload, dict) else payload
        if isinstance(rows, dict):
            rows = [rows]
        if isinstance(rows, list):
            code = next((str(r['sCode']) for r in rows if isinstance(r, dict) and str(r.get('sCode','0')) != '0'), code)
    if code in (None,'0') and match:
        code = match.group(1)
    return {'returncode': result.get('returncode'), 'error_type': result.get('error_type') or 'EntryCommandFailed',
            'exchange_code': code, 'message': message[:600],
            'transport_ok': result.get('ok') is True, 'outcome': 'unknown_until_reconciled'}


def accepted_order_id(payload, client_id, inst_id):
    """Validate one command receipt, not a fill or proof of cloud protection.

    CLI versions return a row, a row list, or an exchange envelope. Missing
    optional echo fields are allowed; contradictory identity/status never is.
    """
    if isinstance(payload, dict):
        if str(payload.get('code', '0')) != '0' or str(payload.get('sCode', '0')) != '0':
            return None
        if payload.get('clOrdId') not in (None, '', client_id) or payload.get('instId') not in (None, '', inst_id):
            return None
        payload = payload.get('data', payload)
    if isinstance(payload, list):
        if len(payload) != 1:
            return None
        payload = payload[0]
    if not isinstance(payload, dict):
        return None
    if str(payload.get('sCode', '0')) != '0' or str(payload.get('code', '0')) != '0':
        return None
    if payload.get('clOrdId') not in (None, '', client_id) or payload.get('instId') not in (None, '', inst_id):
        return None
    order_id = payload.get('ordId') or payload.get('orderId')
    if isinstance(order_id, bool) or not isinstance(order_id, (str, int)) or not str(order_id).strip():
        return None
    return str(order_id)
