"""Reset local stop/peak history across exchange position lifecycles.
Never changes a cloud stop. New/legacy trackers must adopt verified exchange
protection again; missing identity forbids local dynamic management.
"""
import copy

class TrackerSnapshot(dict):
    """A damaged file is UNKNOWN local state, not permission to overwrite it."""
    def __init__(self, *args, readable=True, error_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.readable = readable
        self.error_type = error_type


def identity(position, scope):
    pid=str(position.get('posId') or '')
    created=str(position.get('cTime') or '')
    if not pid or not created:return None
    return {'scope':scope,'instId':position.get('instId'),
            'side':position.get('posSide') or position.get('side'),'posId':pid,'cTime':created}


def reconcile(trackers,key,position,scope):
    if not getattr(trackers, 'readable', True):return 'unknown'
    current=identity(position,scope)
    if current is None:return 'unknown'
    previous=trackers.get(key)
    if previous is None:return 'new'
    if previous.get('positionIdentity')==current:return 'same'
    if not retire(trackers, key, scope,
                  reason='legacy_identity_unverified' if not previous.get('positionIdentity') else 'position_identity_changed',
                  current_identity=current):
        # Callers already treat unknown identity as non-authoritative local state;
        # cloud protection/hard-stop verification continues, with no entry changes.
        return 'unknown'
    return 'reset'


def retire(trackers, key, scope, *, reason, current_identity=None):
    """Durably retain pending writes and entry/exit context before retiring a tracker."""
    previous = trackers.get(key)
    if previous is None:
        return True
    from scripts.strategy_evidence import best_effort
    saved = best_effort(scope, 'tracker_lifecycle_reset', {
        'key': key, 'previous': copy.deepcopy(previous), 'current_identity': current_identity,
        'reason': reason, 'cloud_stop_changed': False})
    if saved is None:
        return False
    trackers.pop(key, None)
    return True
