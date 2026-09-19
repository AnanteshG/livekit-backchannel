"""Do not silently pair failures, mix clocks, or substitute missing observations with zero."""
import random
import statistics


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def difference(events, start, end):
    a = next((x['t'] for x in events if x['kind'] == start), None)
    b = next((x['t'] for x in events if x['kind'] == end), None)
    return None if a is None or b is None else (b - a) * 1000


def run_metrics(events, speech_end):
    # All event times must already be mapped to the replay client's clock.
    real = [x['t'] for x in events if x['kind'] == 'response_audio_received']
    decisions = {x['decision']: x['t'] for x in events if x['kind'] == 'bc_decision'}
    acks = [x for x in events if x['kind'] == 'bc_audio_received']
    latency = [(x['t'] - decisions[x['decision']]) * 1000 for x in acks
               if x.get('decision') in decisions]
    return {
        'response_ms': (real[0] - speech_end) * 1000 if real else None,
        'llm_ttft_ms': difference(events, 'llm_request', 'llm_first_token'),
        'tts_first_ms': difference(events, 'tts_request', 'tts_first_audio'),
        'eot_ms': (next((x['t'] for x in events if x['kind'] == 'eot_detected'),
                       speech_end) - speech_end) * 1000
                  if any(x['kind'] == 'eot_detected' for x in events) else None,
        'stt_final_ms': (max(x['t'] for x in events if x['kind'] == 'stt_final')
                         - speech_end) * 1000
                        if any(x['kind'] == 'stt_final' for x in events) else None,
        'bc_latency_ms': latency,
        'backchannels': len(acks),
        'eot_collisions': sum(x['t'] >= speech_end - 0.4 for x in acks),
        'cancelled': sum(x['kind'] == 'bc_cancelled' for x in events),
        'user_overlap_ms': sum(x.get('user_overlap_ms', 0) for x in events
                               if x['kind'] == 'bc_audio_received_end'),
        'response_overlap_ms': sum(x.get('response_overlap_ms', 0) for x in events
                                   if x['kind'] == 'bc_audio_received_end'),
        'premature_response': bool(real and real[0] < speech_end),
    }


def summarize(runs):
    pair_candidates = {}
    for run in runs:
        if not run.get('warmup') and run['status'] == 'ok' and run['metrics']['response_ms'] is not None:
            pair_candidates.setdefault(run['pair_id'], {})[run['mode']] = run
    complete = {key for key, value in pair_candidates.items()
                if set(value) == {'baseline', 'enabled'}}
    by_mode = {}
    for mode in ('baseline', 'enabled'):
        group = [r for r in runs if r['mode'] == mode and not r.get('warmup')]
        good = [r for r in group if r['pair_id'] in complete and r['status'] == 'ok'
                and r['metrics']['response_ms'] is not None]
        responses = [r['metrics']['response_ms'] for r in good]
        bc = [v for r in good for v in r['metrics']['bc_latency_ms']]
        by_mode[mode] = {
            'runs': len(group), 'valid': len(good), 'excluded': len(group) - len(good),
            'failures': sum(r['status'] != 'ok' or r['metrics']['response_ms'] is None
                            for r in group),
            'response_p50_ms': percentile(responses, .5),
            'response_p95_ms': percentile(responses, .95),
            'bc_p50_ms': percentile(bc, .5), 'bc_p95_ms': percentile(bc, .95),
            **{key: sum(r['metrics'][key] for r in good) for key in
               ('backchannels', 'eot_collisions', 'cancelled', 'user_overlap_ms',
                'response_overlap_ms', 'premature_response')},
            **{key: percentile([r['metrics'][key] for r in good
                                if r['metrics'][key] is not None], .5)
               for key in ('llm_ttft_ms', 'tts_first_ms', 'eot_ms', 'stt_final_ms')},
        }
    pairs = {}
    for run in runs:
        if not run.get('warmup') and run['status'] == 'ok':
            pairs.setdefault(run['pair_id'], {})[run['mode']] = run
    deltas = []
    by_scenario = {}
    for pair in pairs.values():
        if set(pair) != {'baseline', 'enabled'}:
            continue
        a, b = pair['baseline'], pair['enabled']
        if a['audio_sha256'] != b['audio_sha256']:
            raise ValueError('Cannot pair different audio files')
        x, y = a['metrics']['response_ms'], b['metrics']['response_ms']
        if x is not None and y is not None:
            delta = y - x
            deltas.append(delta)
            by_scenario.setdefault(a['scenario'], []).append(delta)
    rng = random.Random(41)
    # Stratify by scenario so resampling doesn't change the scenario mix.
    boot = [statistics.mean([rng.choice(v) for v in by_scenario.values()
                             for _ in range(len(v))]) for _ in range(2000)] if deltas else []
    return {'modes': by_mode, 'complete_pairs': len(deltas),
            'paired_mean_delta_ms': statistics.mean(deltas) if deltas else None,
            'paired_mean_ci95_ms': [percentile(boot, .025), percentile(boot, .975)],
            'per_scenario_delta_ms': {k: statistics.mean(v) for k, v in by_scenario.items()},
            'regression_threshold_ms': 30,
            'regression_flag': bool(boot and percentile(boot, .025) > 30),
            'interpretation': 'Descriptive results; small samples cannot establish equivalence.'}
