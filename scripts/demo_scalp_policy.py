"""Unchanged minute sampling parameters; LIVE requires explicit bound consent.
The legacy descriptor is retained for candidate compatibility, not a profit claim.
"""
from dataclasses import replace

VERSION = 'demo-scalp-cost-policy-v2'
MINIMUM_NET_RR = 1.2


def descriptor():
    return {'version': VERSION, 'minimum_net_rr': MINIMUM_NET_RR,
            'purpose': 'demo_forward_sampling_fee_aware_payoff_v2_not_validated_profitability'}


def parameters(base):
    """Pure candidate contract; preserve all declared fees, slippage and exposure caps."""
    return {**base, 'minimum_net_rr': MINIMUM_NET_RR}


def execution_policy(base, env):
    if env.mode != 'demo':
        from scripts.strategy_engine_runtime import enabled
        if env.mode != 'live' or not enabled(env):
            from scripts.risk_policy import RiskRejected
            raise RiskRejected('Minute sampling policy requires explicit current LIVE consent')
    return replace(base, minimum_net_rr=MINIMUM_NET_RR)
