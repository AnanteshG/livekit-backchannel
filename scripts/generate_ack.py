"""Generate the cached listening sound offline; never called during a conversation.

Run with the project's virtual environment. Requires OPENAI_API_KEY in .env.
Uses PyAV and NumPy included with the LiveKit audio dependencies.
"""
import argparse
import hashlib
import json
import wave
from pathlib import Path

import av
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--clip', choices=('listening', 'mmm', 'uh-huh', 'go-on'),
                        default='listening')
    clip = parser.parse_args().clip
    phrases = {'listening': 'Mm-hmm.', 'mmm': 'Mmm.', 'uh-huh': 'Uh-huh.', 'go-on': 'Go on.'}
    load_dotenv(ROOT / '.env')
    instructions = (
        'Make one short, soft, natural listening acknowledgement. '
        'You are quietly listening while someone else continues talking. Warm and neutral, '
        'not a question, not an enthusiastic agreement, not a thinking sound. '
        'Under one second. Do not pronounce letters or add words. '
        'For Mmm use a gentle closed-mouth listening hum, not a puzzled or thinking sound. '
        'For Go on use a light encouraging tone, never commanding.'
    )
    response = OpenAI().audio.speech.create(
        model='gpt-4o-mini-tts', voice='alloy', input=phrases[clip],
        instructions=instructions, response_format='pcm',
    )
    samples = np.frombuffer(response.content, dtype='<i2').reshape(1, -1)
    frame = av.AudioFrame.from_ndarray(samples, format='s16', layout='mono')
    frame.sample_rate = 24000
    resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
    frames = resampler.resample(frame) + resampler.resample(None)
    values = np.concatenate([f.to_ndarray().reshape(-1) for f in frames]).astype(np.float64)
    active = np.flatnonzero(np.abs(values) > 220)
    if not active.size:
        raise ValueError('Generated clip is silent')
    values = values[max(0, active[0] - 320):active[-1] + 321]
    duration = len(values) / 16000
    if not 0.15 <= duration <= 1.2:
        raise ValueError(f'Clip is too short or long: {duration:.2f}s; inspect before using')
    # A quiet acknowledgement, with headroom and short fades to avoid clicks.
    values *= min(0.8, 0.32 * 32767 / np.max(np.abs(values)))
    fade = min(80, len(values) // 2)
    values[:fade] *= np.linspace(0, 1, fade)
    values[-fade:] *= np.linspace(1, 0, fade)
    path = ROOT / f'scenarios/audio/{clip}.wav'
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(values.astype('<i2').tobytes())
    metadata = dict(model='gpt-4o-mini-tts', voice='alloy', text=phrases[clip],
                    instructions=instructions, duration_seconds=duration,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    generated_audio=True)
    path.with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'Generated {clip}.wav: {duration:.3f}s, 16kHz mono PCM16')


if __name__ == '__main__':
    main()
