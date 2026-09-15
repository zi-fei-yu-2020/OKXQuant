"""Explicit scalp/swing execution modes. Mode changes execution cadence and leverage ceilings; risk is still sized from stop distance."""
from copy import deepcopy
MODES = {
    'scalp': {'id':'scalp','entry_timeframe':'1M','confirmation_timeframe':'5M','bias_timeframe':'15M','default_leverage':10.0,'max_leverage':20.0,'max_holding_seconds':3600,'risk_per_trade_equity_pct':0.004,'time_exit_enabled':True},
    'swing': {'id':'swing','entry_timeframe':'15M','confirmation_timeframe':'1H','bias_timeframe':'4H','default_leverage':3.0,'max_leverage':5.0,'max_holding_seconds':172800,'risk_per_trade_equity_pct':0.005,'time_exit_enabled':False},
}
def mode_for(horizon): return deepcopy(MODES['scalp' if str(horizon).lower()=='scalp' else 'swing'])
def leverage_for(horizon, requested=None):
    mode=mode_for(horizon)
    try: value=float(requested)
    except (TypeError,ValueError): value=mode['default_leverage']
    if value<=0: value=mode['default_leverage']
    return min(value, mode['max_leverage'])
