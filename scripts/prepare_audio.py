"""Package owned synthetic speech; preserve exact bytes and SHA256 for paired replay."""
import hashlib
import json
import random
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    with wave.open(str(path), 'rb') as f:
        assert (f.getframerate(), f.getnchannels(), f.getsampwidth()) == (16000, 1, 2)
        raw = f.readframes(f.getnframes())
    samples = list(struct.unpack('<' + 'h' * (len(raw) // 2), raw))
    active = [i for i, s in enumerate(samples) if abs(s) > 220]
    if not active:
        raise ValueError(f'No speech in {path}')
    return samples[max(0, active[0] - 160):min(len(samples), active[-1] + 161)]


def write(path, samples):
    with wave.open(str(path), 'wb') as f:
        f.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        f.writeframes(struct.pack('<' + 'h' * len(samples), *samples))


def main():
    out = ROOT / 'scenarios/audio'
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    rng = random.Random(41)
    for spec in json.loads((ROOT / 'scenarios/scripts.json').read_text()):
        samples, segments = [0] * 3200, []
        for i, text in enumerate(spec['parts']):
            part = read(ROOT / f"work/raw_audio/{spec['id']}-{i}.wav")
            start = len(samples) / 16000
            samples.extend(part)
            segments.append({'start': start, 'end': len(samples) / 16000, 'text': text})
            if i + 1 < len(spec['parts']):
                samples.extend([0] * (spec['pause_ms'] * 16))
        if 'truncate_seconds' in spec:
            samples = samples[:int(spec['truncate_seconds'] * 16000)]
            segments[-1]['end'] = len(samples) / 16000
            # Do not pretend the unspoken tail was transcribed in simulated replay.
            segments[-1]['text'] = 'I want to explain how we handled'
        end = len(samples) / 16000
        samples.extend([0] * 16000)
        if spec.get('noise'):
            samples = [max(-32768, min(32767, s + int(rng.gauss(0, 32768 * spec['noise']))))
                       for s in samples]
        path = out / (spec['id'] + '.wav')
        write(path, samples)
        manifest.append({**spec, 'file': 'audio/' + path.name,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'speech_end': end, 'duration': len(samples) / 16000,
                         'segments': segments,
                         'annotation': 'synthetic clip boundary, ~10ms trailing margin'})
    write(out / 'ack.wav', read(ROOT / 'work/raw_audio/ack.wav'))
    (ROOT / 'scenarios/manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Prepared {len(manifest)} replay fixtures and cached acknowledgement.')


if __name__ == '__main__':
    main()
