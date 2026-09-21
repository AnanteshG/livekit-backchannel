"""Paired LiveKit latency replay with acoustic processing OFF versus ON."""
import argparse
import asyncio
import json
import random
from pathlib import Path

import numpy as np

from .audio import ROOT
from .benchmark import scenarios
from .metrics import percentile
from .replay import replay


def summary(runs):
    pairs = {}
    for run in runs:
        if run['status'] == 'ok' and not run['warmup']:
            pairs.setdefault(run['pair_id'], {})[run['acoustic_enabled']] = run
    complete = [pair for pair in pairs.values() if len(pair) == 2]
    report = {'complete_pairs': len(complete)}
    for enabled, label in ((False, 'off'), (True, 'on')):
        selected = [pair[enabled] for pair in complete]
        response = [run['metrics']['response_ms'] for run in selected]
        report[label] = {
            'response_p50_ms': percentile(response, 50),
            'response_p95_ms': percentile(response, 95),
            'eot_p50_ms': percentile([run['metrics']['eot_ms'] for run in selected], 50),
            'llm_ttft_p50_ms': percentile([run['metrics']['llm_ttft_ms'] for run in selected], 50),
            'tts_first_p50_ms': percentile([run['metrics']['tts_first_ms'] for run in selected], 50),
        }
        if enabled:
            metrics = [run['acoustic_metrics'] for run in selected]
            report[label]['acoustic_inference_p50_ms'] = percentile(
                [m['inference_p50_ms'] for m in metrics], 50)
            report[label]['acoustic_ui_p50_ms'] = percentile(
                [m['signal_to_receiver_p50_ms'] for m in metrics], 50)
    deltas = [pair[True]['metrics']['response_ms'] - pair[False]['metrics']['response_ms']
              for pair in complete]
    report['paired_response_mean_delta_ms'] = float(np.mean(deltas)) if deltas else None
    return report


async def main(repeats, output):
    chosen = [spec for spec in scenarios()
              if spec['id'] in ('long_monologue', 'fast_speaker', 'noisy_audio')]
    schedule = [(spec, repeat) for repeat in range(repeats) for spec in chosen]
    random.Random(82).shuffle(schedule)
    runs = []
    for spec, repeat in schedule:
        order = [False, True]
        random.Random(82 + repeat).shuffle(order)
        for acoustic_enabled in order:
            run = await replay(spec, True, f"{spec['id']}-{repeat}",
                               acoustic_enabled=acoustic_enabled)
            runs.append(run)
            report = {'schema_version': 1, 'measurement_kind': 'livekit-acoustic-ab',
                      'repeats': repeats, 'summary': summary(runs), 'runs': runs}
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(report, indent=2) + '\n')
            temporary.replace(path)
            print(spec['id'], repeat, 'ON' if acoustic_enabled else 'OFF', run['status'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--output', default=str(ROOT / 'results/acoustic-livekit.json'))
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error('Use at least two repetitions')
    asyncio.run(main(args.repeats, args.output))
