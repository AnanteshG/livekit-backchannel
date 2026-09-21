"""Generate owned, repeatable speech fixtures for acoustic-expression evaluation."""
import hashlib
import json
import wave
from pathlib import Path

import av
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = [
    ('same_positive', "Yeah, that's great.", 'Sound genuinely pleased, warm and confident.'),
    ('same_frustrated', "Yeah, that's great.",
     'Sound clearly frustrated and skeptical, with tense energetic delivery.'),
    ('increasing_frustration',
     'I reported this once, then twice, and I am still waiting for a useful answer.',
     'Begin calm, become progressively more frustrated and emphatic toward the end.'),
    ('hesitant', 'I think we could possibly try the second option, if that makes sense.',
     'Sound hesitant and uncertain, with uneven pacing, fillers and short pauses.'),
    ('high_energy', 'This is exciting, we can launch the new workflow today.',
     'Speak quickly with high energy and excitement.'),
    ('flat', 'The update is complete and the report is ready.',
     'Use flat, low-energy, disengaged delivery with little pitch variation.'),
    ('long_change',
     'Let me explain the situation from the beginning. The first step worked, the next step '
     'failed twice, and now I need a clear solution before the deadline.',
     'Start calm and confident, become hesitant in the middle, then frustrated and emphatic.'),
    ('hinglish', 'Mujhe lagta hai yeh approach theek hai, but I am not fully sure yet.',
     'Natural Hindi and English code-switching, thoughtful and somewhat uncertain.'),
]


def convert(raw):
    samples = np.frombuffer(raw, dtype='<i2').reshape(1, -1)
    frame = av.AudioFrame.from_ndarray(samples, format='s16', layout='mono')
    frame.sample_rate = 24000
    resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
    frames = resampler.resample(frame) + resampler.resample(None)
    return np.concatenate([f.to_ndarray().reshape(-1) for f in frames]).astype('<i2')


def write(path, samples):
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(samples.tobytes())


def main():
    load_dotenv(ROOT / '.env')
    client = OpenAI()
    output = ROOT / 'scenarios/acoustic'
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for scenario_id, text, direction in SCENARIOS:
        path = output / f'{scenario_id}.wav'
        if path.exists():
            with wave.open(str(path), 'rb') as existing:
                samples = np.frombuffer(existing.readframes(existing.getnframes()), dtype='<i2')
        else:
            response = client.audio.speech.create(
                model='gpt-4o-mini-tts', voice='alloy', input=text,
                instructions=f'{direction} Keep the performance natural and conversational.',
                response_format='pcm')
            samples = convert(response.content)
            write(path, samples)
        manifest.append({'id': scenario_id, 'text': text, 'delivery': direction,
                         'file': path.name, 'duration_seconds': len(samples) / 16000,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'model': 'gpt-4o-mini-tts', 'voice': 'alloy'})
        if scenario_id == 'same_frustrated':
            rng = np.random.default_rng(41)
            noisy = np.clip(samples.astype(np.int32) + rng.normal(0, 900, len(samples)),
                            -32768, 32767).astype('<i2')
            noise_path = output / 'background_noise.wav'
            write(noise_path, noisy)
            manifest.append({'id': 'background_noise', 'text': text,
                             'delivery': direction + ' Seeded background noise.',
                             'file': noise_path.name,
                             'duration_seconds': len(noisy) / 16000,
                             'sha256': hashlib.sha256(noise_path.read_bytes()).hexdigest(),
                             'derived_from': 'same_frustrated', 'noise_seed': 41})
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Generated {len(manifest)} acoustic scenarios.')


if __name__ == '__main__':
    main()
