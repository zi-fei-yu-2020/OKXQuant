"""Offline, scoped WAIT diagnostics. Never repairs a decision into a trade."""
from collections import Counter
from datetime import datetime, timezone, timedelta
import argparse
import json
from pathlib import Path


def diagnose(snapshot, limit=16):
    scope = snapshot['scope']
    groups = {}
    for event in snapshot.get('events', []):
        payload = event['payload']
        key = payload.get('generated_at_ms')
        if key is None:
            continue
        groups.setdefault(key, {})[payload['instrument']] = payload
    cycles = []
    errors = Counter()
    statuses = Counter()
    triggered_waits = []
    for at in sorted(groups, reverse=True)[:limit]:
        rows = []
        for inst, payload in sorted(groups[at].items()):
            d = payload['decision']
            valid = bool(d.get('contract_valid'))
            status = d.get('decision_status', 'incomplete') if valid else 'incomplete'
            statuses[status] += 1
            reason = d.get('validation_reason') or d.get('summary_reason', '')
            if status == 'incomplete':
                errors[reason] += 1
            previous = d.get('previous_wait_review') or {}
            met = [side for side, result in previous.get('trigger_checks', {}).items() if result == 'met']
            if met and d.get('action') == 'WAIT':
                triggered_waits.append({'at_ms': at, 'instrument': inst, 'directions': met, 'status': status})
            rows.append({'instrument': inst, 'action': d.get('action'), 'status': status,
                         'reason': reason, 'model_reason': d.get('model_reason'),
                         'previous_trigger_met': met, 'previous_review_required': previous.get('required', False),
                         'audit': d.get('wait_audit')})
        cycles.append({'at_ms': at, 'time': datetime.fromtimestamp(at / 1000, timezone(timedelta(hours=8))).isoformat(), 'items': rows})
    return {'mode': 'read_only_diagnosis', 'scope': scope, 'captured_at': snapshot.get('captured_at'),
            'reported_streak': snapshot.get('wait', {}).get('no_entry_candidate_streak'),
            'cycles_analyzed': len(cycles), 'decision_counts': dict(statuses), 'incomplete_reasons': dict(errors),
            'trigger_met_but_wait': triggered_waits, 'cycles': cycles,
            'limits': ['Reconsideration triggers require re-evaluation; they are not entry authorizations.',
                       'Continued WAIT after a trigger does not by itself prove a missed profitable trade.',
                       'No 5m candles in archived decision features: cannot invent historical 5m confirmations.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = diagnose(json.loads(args.input.read_text(encoding='utf8')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({k: report[k] for k in ('cycles_analyzed', 'decision_counts', 'incomplete_reasons')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
