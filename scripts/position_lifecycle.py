"""Reset local stop/peak history across exchange position lifecycles.
Never changes a cloud stop. New/legacy trackers must adopt verified exchange
protection again; missing identity forbids local dynamic management.
"""
import copy

def identity(position, scope):
    pid=str(position.get('posId') or '')
    created=str(position.get('cTime') or '')
    if not pid or not created:return None
    return {'scope':scope,'instId':position.get('instId'),
            'side':position.get('posSide') or position.get('side'),'posId':pid,'cTime':created}


def reconcile(trackers,key,position,scope):
    current=identity(position,scope)
    if current is None:return 'unknown'
    previous=trackers.get(key)
    if previous is None:return 'new'
    if previous.get('positionIdentity')==current:return 'same'
    # Keep a scoped immutable diagnostic before removing stale local authority.
    from scripts.strategy_evidence import best_effort
    best_effort(scope,'tracker_lifecycle_reset',{'key':key,'previous':copy.deepcopy(previous),'current_identity':current,
        'reason':'legacy_identity_unverified' if not previous.get('positionIdentity') else 'position_identity_changed',
        'cloud_stop_changed':False})
    trackers.pop(key,None)
    return 'reset'
