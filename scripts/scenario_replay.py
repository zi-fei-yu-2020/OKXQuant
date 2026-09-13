"""Offline causal replay of the frozen scenario shadow engine.

Already-inspected historical data is retrospective diagnosis, NOT a new holdout.
Backtest-only signal adaptation lives here, never in the live shadow monitor.
"""
from bisect import bisect_right
from collections import Counter
from dataclasses import asdict
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import scenario_entry as engine
from scripts.entry_research import resample, round_price, prepare_series, compact
from scripts.backtest_engine import BacktestEngine
from scripts.risk_policy import Policy
from scripts.research_data import digest


def validate_dataset(dataset):
    expected = digest({k: dataset[k] for k in ('series', 'metadata', 'start_ms', 'end_ms')})
    if dataset.get('capture_hash') != expected:
        raise ValueError('Research capture hash mismatch')
    if not dataset.get('funding_complete'):
        raise ValueError('Funding coverage incomplete; cannot claim net-cost replay')
    for rows in dataset['series'].values():
        if [r['ts_ms'] for r in rows] != list(range(dataset['start_ms'] + engine.WIDTH, dataset['end_ms'] + 1, engine.WIDTH)):
            raise ValueError('Incomplete research window')
        engine.validate_bars(rows, engine.WIDTH, dataset['end_ms'])


def frames_for(rows, instrument, scope, start, end):
    hourly = [{**r, 'confirm': True} for r in resample(rows, 60)]
    macro = [{**r, 'confirm': True} for r in resample(rows, 240)]
    ht, mt = [r['ts_ms'] for r in hourly], [r['ts_ms'] for r in macro]
    for index, row in enumerate(rows):
        at = row['ts_ms']
        if not start < at <= end:
            continue
        hi, mi = bisect_right(ht, at), bisect_right(mt, at)
        yield {'scope': scope, 'instrument': instrument, 'at_ms': at, 'entry_supported': True,
               '5m': rows[max(0, index - 19):index + 1], '1H': hourly[max(0, hi - 20):hi], '4H': macro[max(0, mi - 20):mi]}


def generate(dataset, *, start=None, end=None, spec=None):
    spec = spec or engine.Spec()
    start = dataset['evaluation_start_ms'] if start is None else start
    end = dataset['end_ms'] if end is None else end
    scope = 'research:' + dataset['capture_hash'][:16]
    events, definitions, signals = [], {}, {name: {} for name in ('trend_pullback', 'range_breakout')}
    for inst, rows in dataset['series'].items():
        state = engine.initial_state(scope, spec)
        for frame in frames_for(rows, inst, scope, start, end):
            state = engine.advance(state, frame, spec)
            for event in state['events']:
                if event['at_ms'] != frame['at_ms']:
                    continue
                events.append(event)
                if event.get('definition'):
                    definitions[event['candidate_id']] = event['definition']
                if event['kind'] != 'triggered_research':
                    continue
                definition = definitions[event['candidate_id']]
                side = definition['side']
                tick = dataset['metadata'][inst]['tickSz']
                # Research adapter only. No record_decisions/production decision authority.
                signal = {'timestamp': frame['5m'][-1]['timestamp'], 'action': 'BUY_LONG' if side == 'long' else 'SELL_SHORT',
                          'entry_price': round_price(event['price'], tick, up=side == 'long'),
                          'stop_loss_price': round_price(definition['stop'], tick, up=side == 'short'),
                          'take_profit_price': round_price(definition['target'], tick, up=side == 'short'),
                          'atr': definition['atr_1h'], 'decision_id': event['candidate_id'],
                          'features_as_of_ms': event['at_ms'], 'generated_at_ms': event['at_ms'],
                          'provenance': 'scenario_retrospective_backtest_only', 'counterfactual': True,
                          'order_authorized': False, 'model_cost_usdt': 0,
                          'score_semantics': 'No LLM score in a deterministic research experiment'}
                signals[definition['scenario']].setdefault(inst, []).append(signal)
        print(inst + ' shadow replay complete', flush=True)
    return events, signals


def evaluate(dataset, *, start=None, end=None):
    validate_dataset(dataset)
    spec = engine.Spec()
    start = dataset['evaluation_start_ms'] if start is None else start
    end = dataset['end_ms'] if end is None else end
    if not dataset['start_ms'] <= start < end <= dataset['end_ms']:
        raise ValueError('Invalid replay evaluation window')
    events, signals = generate(dataset, start=start, end=end, spec=spec)
    counts = Counter(e['kind'] for e in events)
    reasons = Counter(e.get('reason') for e in events if e.get('reason'))
    variants = {}
    series = {inst: [r for r in rows if start < r['ts_ms'] <= end] for inst, rows in prepare_series(dataset).items()}
    policy = Policy()
    for name, proposals in signals.items():
        bt = BacktestEngine(initial_capital=5000, bar='5m', policy=policy, risk_per_trade_pct=policy.per_trade_equity_pct,
                            min_confidence_gate=0, taker_fee=policy.taker_fee, slippage=policy.slippage,
                            order_ttl_bars=1, metadata=dataset['metadata'], leverage=3)
        summary = bt.run_portfolio(series, proposals)
        variants[name] = compact(summary)
        variants[name]['research_trigger_count'] = sum(len(r) for r in proposals.values())
    code_hash = hashlib.sha256(''.join((ROOT/'scripts'/name).read_text(encoding='utf8') for name in
                                     ('scenario_entry.py', 'scenario_replay.py', 'backtest_engine.py', 'risk_policy.py')).encode()).hexdigest()
    report = {'version': engine.VERSION, 'spec': asdict(spec), 'implementation_hash': code_hash,
              'capture_hash': dataset['capture_hash'], 'start_ms': start, 'end_ms': end,
              'event_counts': dict(counts), 'blocking_reasons': dict(reasons), 'variants': variants,
              'executed_orders': 0, 'promotion_approved': False,
              'evaluation_kind': 'retrospective_diagnostic_not_independent_holdout',
              'limitations': ['No historical or current production LLM replay; no model-call cost included.',
                             'Retrospective public prices do not establish historical demo support or account eligibility.',
                             'No portfolio combination of the two scenarios; each is an independent experiment.',
                             'Structural targets and higher frequency are hypotheses, not a demonstrated edge.',
                             'Trigger is not fill; the backtest uses one-bar limit expiry and conservative costs/stop paths.',
                             'Fresh unseen forward observation is still required before simulated execution.']}
    return report, events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start-ms', type=int)
    parser.add_argument('--end-ms', type=int)
    args = parser.parse_args()
    report, events = evaluate(json.loads(args.input.read_text(encoding='utf8')), start=args.start_ms, end=args.end_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
    args.output.with_suffix('.events.json').write_text(json.dumps(events, ensure_ascii=False, allow_nan=False), encoding='utf8')
    print(json.dumps({'event_counts': report['event_counts'], 'promotion_approved': False, 'executed_orders': 0}))


if __name__ == '__main__':
    main()
