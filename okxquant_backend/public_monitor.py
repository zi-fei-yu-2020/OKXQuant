"""Secret redaction for explicitly public, read-only monitoring responses.

Financial display is intentional. Configuration, credentials, raw process logs,
and admin audit APIs are not made public by this projection. Never mutate caches.
"""
from __future__ import annotations
import re

_SENSITIVE = re.compile(r"password|passphrase|secret|(?:api.?key)|authorization|cookie|(?:access|refresh|session|setup|admin)[_-]?token|private[_-]?key|webhook|token$|OK-ACCESS-(?:KEY|SIGN|PASSPHRASE)|X-OKXQuant-(?:Session|Admin-Token)", re.I)
_ASSIGNMENT = re.compile(r'''(?ix)(["']?[\w.-]*(?:password|passphrase|secret(?:[_-]?key)?|api[_-]?key|authorization|cookie|(?:access|refresh|session|setup|admin)[_-]?token|private[_-]?key|webhook|token|OK-ACCESS-(?:KEY|SIGN|PASSPHRASE)|X-OKXQuant-(?:Session|Admin-Token))[\w.-]*["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}&]+)''')
_CLI_SECRET = re.compile(r'''(?ix)(--(?:api[_-]?key|secret[_-]?key|password|passphrase|(?:access|session)[_-]?token)\s+)(?:"[^"\n]*"|'[^'\n]*'|[^\s]+)''')
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[^\s,;\"']+")
_USERINFO = re.compile(r"(https?://)[^\s/@]+:[^\s/@]+@", re.I)
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)", re.S)


def _known_secrets():
    from scripts.okx_runtime import _load_dotenv, selected_environment
    values = [value for key, value in _load_dotenv().items() if _SENSITIVE.search(key)]
    env = selected_environment()
    values.extend(getattr(env, name, '') for name in ('api_key', 'secret_key', 'passphrase'))
    return tuple(sorted({v for v in values if isinstance(v, str) and len(v) >= 6}, key=len, reverse=True))


def public_payload(value):
    secrets = _known_secrets()
    known = re.compile('|'.join(re.escape(v) for v in secrets)) if secrets else None

    def clean(item):
        if isinstance(item, dict):
            return {str(k): '[REDACTED]' if _SENSITIVE.search(str(k)) else clean(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(v) for v in item]
        if isinstance(item, str):
            text = known.sub('[REDACTED]', item) if known else item
            text = _PRIVATE_KEY.sub('[REDACTED PRIVATE KEY]', text)
            text = _USERINFO.sub(r'\1[REDACTED]@', text)
            text = _BEARER.sub(r'\1 [REDACTED]', text)
            text = _CLI_SECRET.sub(r'\1[REDACTED]', text)
            return _ASSIGNMENT.sub(r'\1[REDACTED]', text)
        return item
    return clean(value)
