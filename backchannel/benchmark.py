"""Credential-free engine simulation. Never presented as a LiveKit latency benchmark."""
import argparse
import asyncio
import hashlib
import json
import random
from dataclasses import asdict
from pathlib import Path

from .acknowledgements import Acknowledgements
from .audio import ROOT, pcm
from .engine import BackchannelEngine, Policy
from .events import Timeline
from .metrics import run_metrics, summarize


class VirtualClock:
    def __init__(self):
        self.now = 0.0
        self.waiters = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.now + seconds, future))
        await future

    async def advance(self, now):
        self.now = now
        waiting, self.waiters = self.waiters, []
        for deadline, future in waiting:
            if future.done():
                continue
            if deadline <= now + 1e-9:
                future.set_result(None)
            else:
                self.waiters.append((deadline, future))
        for _ in range(4):
            await asyncio.sleep(0)


def scenarios():
    return json.loads((ROOT / 'scenarios/manifest.json').read_text())


def verify_audio(spec):
    path = ROOT / 'scenarios' / spec['file']
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec['sha256']:
        raise ValueError(f"Audio checksum mismatch: {spec['id']}")
    return pcm(path)


async def simulate(spec, enabled, seed, pair_id):
    verify_audio(spec)
    clock = VirtualClock()
    timeline = Timeline(clock)
    rng = random.Random(seed)
    end = spec['speech_end']
    eot, llm, tts = .5, rng.uniform(.12, .23), rng.uniform(.12, .28)
    clips = Acknowledgements()

    class Sink:
        def clear(self):
            pass

        async def play(self, audio, started):
            started()
            await clock.sleep(.04)
            decision = engine.sequence
            start = clock()
            timeline.emit('bc_audio_received', decision=decision, synthetic=True)
            try:
                await clock.sleep(len(audio) / 32000)
            finally:
                timeline.emit('bc_audio_received_end', decision=decision,
                              user_overlap_ms=max(0, min(clock(), end) - start) * 1000,
                              response_overlap_ms=0, synthetic=True)

    async def provider():
        await clock.sleep(.08)
        phrase, audio = clips.next_clip()
        timeline.emit('bc_clip_selected', decision=engine.sequence, phrase=phrase,
                      duration_ms=len(audio) / 32)
        return audio

    engine = BackchannelEngine(provider, Sink(), timeline, enabled=enabled, clock=clock)
    events = []
    for segment in spec['segments']:
        events.append((segment['start'], 'start', None))
        events.append((segment['end'], 'stop', None))
        words = segment['text'].split()
        duration = segment['end'] - segment['start']
        for step in range(1, max(2, int(duration / .4))):
            t = segment['start'] + step * .4
            if t >= segment['end']:
                break
            n = max(1, min(len(words), int(len(words) * (t - segment['start']) / duration)))
            events.append((t, 'transcript', ' '.join(words[:n]).rstrip('.?!')))
    if spec['id'] == 'approaching_eot':
        events.append((max(.3, end - 1.3), 'risk', .9))
    events.extend([(end + .1, 'final', spec['segments'][-1]['text']),
                   (end + eot, 'eot', None), (end + eot, 'llm_request', None),
                   (end + eot + llm, 'llm_first_token', None),
                   (end + eot + llm, 'tts_request', None),
                   (end + eot + llm + tts, 'tts_first_audio', None),
                   (end + eot + llm + tts + .04, 'response_audio_received', None)])
    events.sort(key=lambda x: x[0])
    index = 0
    for frame in range(int((end + 2) / .02) + 1):
        now = frame * .02
        # Apply input edges BEFORE resolving audio waits at the same instant.
        clock.now = now
        while index < len(events) and events[index][0] <= now + 1e-9:
            _, kind, value = events[index]
            index += 1
            if kind == 'start':
                engine.user_started()
            elif kind == 'stop':
                engine.user_stopped()
            elif kind == 'transcript':
                engine.transcript(value, confidence=.3 if spec.get('noise') else .98)
                if spec['id'] == 'approaching_eot' and now > end - 1.3:
                    engine.risk(.9, source='scripted_test_signal')
            elif kind == 'risk':
                engine.risk(value, source='scripted_test_signal')
            elif kind == 'final':
                engine.transcript(value, final=True)
            elif kind == 'eot':
                engine.turn_completed()
                engine.set_agent_busy(True)
            else:
                timeline.emit(kind, synthetic=True)
        engine.tick()
        await clock.advance(now)
    await engine.aclose()
    result = {'scenario': spec['id'], 'label': spec['label'], 'pair_id': pair_id,
              'mode': 'enabled' if enabled else 'baseline', 'audio_sha256': spec['sha256'],
              'measurement_kind': 'simulation', 'status': 'ok', 'warmup': False,
              'speech_end': end, 'events': list(timeline.events)}
    result['metrics'] = run_metrics(result['events'], end)
    return result


async def main(repeats=5, output=None):
    runs = []
    rng = random.Random(41)
    for spec in scenarios():
        for repeat in range(repeats):
            order = [False, True]
            rng.shuffle(order)
            for enabled in order:
                runs.append(await simulate(spec, enabled, repeat + 41,
                                           f"{spec['id']}-{repeat}"))
    report = {'schema_version': 1, 'measurement_kind': 'simulation',
              'description': 'Scripted signals and virtual provider delays; NOT LiveKit measurements.',
              'policy': asdict(Policy()), 'repeats': repeats, 'seed': 41,
              'summary': summarize(runs), 'runs': runs}
    path = Path(output) if output else ROOT / 'results/simulation.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error('Use at least two repetitions')
    asyncio.run(main(args.repeats, args.output))

