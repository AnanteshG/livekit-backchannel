"""Repeatable streaming benchmark for the committed acoustic fixtures."""
import argparse
import json
import time
import wave
from pathlib import Path

import numpy as np

from .acoustics import AcousticConfig, ProsodyModel, text_sentiment
from .audio import ROOT


def percentile(values, q):
    return float(np.percentile(values, q)) if values else None


def run_fixture(spec, repeats=5):
    path = ROOT / 'scenarios/acoustic' / spec['file']
    with wave.open(str(path), 'rb') as source:
        assert (source.getframerate(), source.getnchannels(), source.getsampwidth()) == (16000, 1, 2)
        audio = source.readframes(source.getnframes())
    config, model = AcousticConfig(), ProsodyModel()
    window_bytes = int(config.window_seconds * config.sample_rate) * 2
    minimum_bytes = int(config.minimum_seconds * config.sample_rate) * 2
    stride_bytes = int(config.stride_seconds * config.sample_rate) * 2
    traces, inference = [], []
    for repeat in range(repeats):
        smooth = None
        for end in range(minimum_bytes, len(audio) + 1, stride_bytes):
            window = audio[max(0, end - window_bytes):end]
            seconds = len(window) / (config.sample_rate * 2)
            started = time.perf_counter()
            prediction = model.predict(window, seconds, end)
            measured = (time.perf_counter() - started) * 1000
            inference.append(measured)
            raw = np.array([prediction.frustration, prediction.uncertainty, prediction.energy])
            smooth = raw if smooth is None else config.smoothing * raw + (1-config.smoothing) * smooth
            if repeat == 0:
                traces.append({'at_seconds': end / (config.sample_rate * 2),
                               'frustration': float(smooth[0]),
                               'uncertainty': float(smooth[1]), 'energy': float(smooth[2]),
                               'confidence': prediction.confidence,
                               'inference_ms': measured})
    return {'id': spec['id'], 'text': spec['text'], 'delivery': spec['delivery'],
            'duration_seconds': spec['duration_seconds'], 'trace': traces,
            'final': traces[-1] if traces else None,
            'text_baseline': text_sentiment(spec['text']),
            'inference_p50_ms': percentile(inference, 50),
            'inference_p95_ms': percentile(inference, 95)}


def main(repeats, output):
    specs = json.loads((ROOT / 'scenarios/acoustic/manifest.json').read_text())
    runs = [run_fixture(spec, repeats) for spec in specs]
    times = [point['inference_ms'] for run in runs for point in run['trace']]
    same = {run['id']: run for run in runs if run['id'].startswith('same_')}
    report = {
        'schema_version': 1, 'model': 'prosody-dsp-v1', 'audio_only': True,
        'window_seconds': 1.5, 'minimum_seconds': .75, 'stride_seconds': .25,
        'smoothing_ema_alpha': .35, 'repeats': repeats,
        'inference_p50_ms': percentile(times, 50), 'inference_p95_ms': percentile(times, 95),
        'earliest_signal_ms': 750,
        'same_words_comparison': {
            'text_baseline_equal': same['same_positive']['text_baseline'] == same['same_frustrated']['text_baseline'],
            'acoustic_frustration_delta': same['same_frustrated']['final']['frustration'] - same['same_positive']['final']['frustration'],
            'acoustic_energy_delta': same['same_frustrated']['final']['energy'] - same['same_positive']['final']['energy'],
        },
        'limitations': [
            'Synthetic voices test repeatability, not accuracy on natural human speech.',
            'DSP scores are uncalibrated acoustic cues, not emotion probabilities.',
            'Effective detection latency is at least the audio accumulated plus inference time.',
        ],
        'runs': runs,
    }
    Path(output).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('inference_p50_ms', 'inference_p95_ms',
                                                    'same_words_comparison')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', default=str(ROOT / 'results/acoustic.json'))
    args = parser.parse_args()
    main(args.repeats, args.output)
