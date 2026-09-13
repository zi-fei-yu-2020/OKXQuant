"""Separate observable streaks; legacy final-WAIT history is not reinterpreted."""
from copy import deepcopy

VERSION = 'wait-diagnostics-v2'
KEYS = ('no_program_plans', 'model_all_wait', 'audit_incomplete', 'audited_wait_with_plans')


def advance(previous, cache, now):
    if previous is not None:
        if not isinstance(previous, dict) or previous.get('version') != VERSION:
            raise ValueError('Invalid WAIT diagnostics version')
        for key in KEYS:
            value = previous.get('streaks', {}).get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError('Invalid WAIT diagnostics count')
        if type(previous.get('observed_rounds')) is not int or previous['observed_rounds'] < 0:
            raise ValueError('Invalid WAIT diagnostics rounds')
    state = deepcopy(previous) if previous is not None else {
        'version': VERSION, 'since': now, 'observed_rounds': 0, 'streaks': {key: 0 for key in KEYS}}
    decisions = [row.get('decision') or {} for row in cache.values()]
    catalogs = [d.get('entry_plans') for d in decisions]
    plans_known = bool(decisions) and all(isinstance(c, dict) and isinstance(c.get('plans'), list) and not c.get('error') for c in catalogs)
    plans = sum(len(c['plans']) for c in catalogs) if plans_known else None
    actions = [d.get('model_action') for d in decisions]
    model_known = bool(actions) and all(a in ('WAIT', 'BUY_LONG', 'SELL_SHORT') for a in actions)
    all_model_wait = all(a == 'WAIT' for a in actions) if model_known else None
    incomplete = sum(d.get('decision_status') == 'incomplete' or not d.get('contract_valid') and d.get('decision_status') != 'execution_rejected' for d in decisions)
    all_audited_wait = bool(decisions) and all(d.get('decision_status') == 'audited_wait' and d.get('contract_valid') for d in decisions)
    outcomes = {'no_program_plans': plans == 0 if plans_known else None,
                'model_all_wait': all_model_wait, 'audit_incomplete': incomplete > 0,
                'audited_wait_with_plans': plans > 0 and all_audited_wait if plans_known else None}
    for key, outcome in outcomes.items():
        old = state['streaks'].get(key)
        state['streaks'][key] = None if outcome is None else ((old or 0) + 1 if outcome else 0)
    state['observed_rounds'] += 1
    state['last_cycle'] = {'program_plans': plans, 'model_entry_proposals': sum(a in ('BUY_LONG','SELL_SHORT') for a in actions) if model_known else None,
                           'incomplete': incomplete, 'audited_wait': sum(d.get('decision_status') == 'audited_wait' for d in decisions),
                           'repair_corrected': sum((d.get('wait_repair') or {}).get('status') == 'corrected' for d in decisions)}
    return state
