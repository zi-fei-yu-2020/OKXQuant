"""Explicit DEMO sampling policy; do not apply this to live or the legacy AI engine."""
from dataclasses import replace

VERSION = 'demo-scalp-cost-policy-v1'
MINIMUM_NET_RR = 1.2


def descriptor():
    return {'version': VERSION, 'minimum_net_rr': MINIMUM_NET_RR,
            'purpose': 'demo_forward_sampling_not_validated_profitability'}


def parameters(base):
    """Pure candidate contract; preserve all declared fees, slippage and exposure caps."""
    return {**base, 'minimum_net_rr': MINIMUM_NET_RR}


def execution_policy(base, env):
    if env.mode != 'demo':
        from scripts.risk_policy import RiskRejected
        raise RiskRejected('DEMO sampling policy cannot authorize live orders')
    return replace(base, minimum_net_rr=MINIMUM_NET_RR)
