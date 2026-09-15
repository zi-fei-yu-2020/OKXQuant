"""Explicit scalp/swing execution modes."""
from copy import deepcopy
import hashlib,json
VERSION='strategy-modes-v2'
MODES={'scalp':{'id':'scalp','entry_timeframe':'1M','confirmation_timeframe':'5M','bias_timeframe':'15M','default_leverage':10.0,'max_leverage':20.0,'max_holding_seconds':3600,'risk_per_trade_equity_pct':0.004,'time_exit_enabled':True},'swing':{'id':'swing','entry_timeframe':'15M','confirmation_timeframe':'1H','bias_timeframe':'4H','default_leverage':3.0,'max_leverage':5.0,'max_holding_seconds':172800,'risk_per_trade_equity_pct':0.005,'time_exit_enabled':False}}
def mode_for(horizon):
 mode=deepcopy(MODES['scalp' if str(horizon).lower()=='scalp' else 'swing']); mode['version']=VERSION; mode['signature']=hashlib.sha256(json.dumps(mode,sort_keys=True).encode()).hexdigest()[:16]; return mode
def leverage_for(horizon,requested=None,confidence=None):
 mode=mode_for(horizon)
 try: value=float(requested)
 except (TypeError,ValueError): value=mode['default_leverage']
 if value<=0:value=mode['default_leverage']
 if requested is None and str(horizon).lower()=='scalp' and confidence is not None:
  try:value=20.0 if float(confidence)>=85 else 12.0 if float(confidence)>=70 else 10.0
  except (TypeError,ValueError):pass
 return min(value,mode['max_leverage'])
