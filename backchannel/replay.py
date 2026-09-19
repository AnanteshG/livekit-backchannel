"""Publish identical WAVs to fresh LiveKit rooms and measure returned audio locally."""
import argparse
import asyncio
import json
import os
import random
import uuid
from datetime import timedelta
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv
from livekit import api, rtc

from .audio import ROOT, rms
from .benchmark import scenarios, verify_audio
from .metrics import run_metrics, summarize

load_dotenv(ROOT / '.env')
REQUIRED = ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET',
            'DEEPGRAM_API_KEY', 'OPENAI_API_KEY')


def missing_credentials():
    return [key for key in REQUIRED if not os.getenv(key)]


def token(room, identity):
    return (api.AccessToken().with_identity(identity).with_ttl(timedelta(minutes=30))
            .with_grants(api.VideoGrants(room_join=True, room=room)).to_jwt())


async def replay(spec, enabled, pair_id, warmup=False):
    audio = verify_audio(spec)
    room_name = 'bc-' + uuid.uuid4().hex[:16]
    room = rtc.Room()
    lk = api.LiveKitAPI()
    ready = asyncio.Event()
    response = asyncio.Event()
    pongs = []
    raw_events, received, tasks = [], [], set()
    origin = None
    source = None
    status, error = 'ok', None
    last_decision = 0
    telemetry_dropped = 0

    @room.on('data_received')
    def data(packet):
        nonlocal last_decision, telemetry_dropped
        if packet.topic != 'lab.events':
            return
        payload = json.loads(packet.data)
        event = payload['event']
        telemetry_dropped = max(telemetry_dropped, payload.get('dropped', 0))
        if event['kind'] == 'ready':
            ready.set()
        elif event['kind'] == 'clock_pong':
            t2 = perf_counter()
            pongs.append((t2 - event['t0'],
                          event['server_time'] - (event['t0'] + t2) / 2))
        else:
            raw_events.append({**event, 'absolute': payload['origin'] + event['t']})
            if event['kind'] == 'bc_decision':
                last_decision = event['decision']

    # A standalone monitor makes end events available even when a track stops sending frames.
    async def monitor(track, name):
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        first = None
        last = None
        decision = None
        try:
            async for packet in stream:
                if origin is None:
                    continue
                now = perf_counter() - origin
                if rms(bytes(packet.frame.data)) < .008:
                    continue
                if first is None:
                    first = now
                    decision = last_decision if name == 'backchannel' else None
                    received.append({'t': now, 'kind': 'bc_audio_received'
                                     if name == 'backchannel' else 'response_audio_received',
                                     'decision': decision})
                    if name != 'backchannel':
                        response.set()
                elif name == 'backchannel' and last is not None and now - last > .6:
                    finish_interval(first, last, decision)
                    first, decision = now, last_decision
                    received.append({'t': now, 'kind': 'bc_audio_received',
                                     'decision': decision})
                last = now + packet.frame.samples_per_channel / packet.frame.sample_rate
        finally:
            if name == 'backchannel' and first is not None:
                finish_interval(first, last, decision)
            await stream.aclose()

    def finish_interval(start, end, decision):
        user_overlap = sum(max(0, min(end, seg['end']) - max(start, seg['start']))
                           for seg in spec['segments'])
        response_start = next((e['t'] for e in received
                               if e['kind'] == 'response_audio_received'), None)
        received.append({'t': end, 'kind': 'bc_audio_received_end', 'decision': decision,
                         'user_overlap_ms': user_overlap * 1000,
                         'response_overlap_ms': max(0, end - max(start, response_start)) * 1000
                         if response_start is not None else 0})

    @room.on('track_subscribed')
    def subscribed(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            task = asyncio.create_task(monitor(track, publication.name))
            tasks.add(task)

    try:
        await room.connect(os.environ['LIVEKIT_URL'], token(room_name, 'replay'))
        await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
            room=room_name, agent_name='backchannel-lab',
            metadata=json.dumps({'enabled': enabled})))
        await asyncio.wait_for(ready.wait(), timeout=60)
        for _ in range(5):
            await room.local_participant.publish_data(
                json.dumps({'t0': perf_counter()}).encode(), reliable=True, topic='lab.ping')
            await asyncio.sleep(.15)
        if not pongs:
            raise RuntimeError('No clock alignment samples')
        source = rtc.AudioSource(16000, 1, queue_size_ms=20)
        track = rtc.LocalAudioTrack.create_audio_track('replay-microphone', source)
        await room.local_participant.publish_track(track, rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_MICROPHONE))
        await asyncio.sleep(.5)  # give room input subscription a fixed settling interval
        origin = perf_counter()
        for offset in range(0, len(audio), 640):
            deadline = origin + offset / 32000
            await asyncio.sleep(max(0, deadline - perf_counter()))
            block = audio[offset:offset + 640]
            await source.capture_frame(rtc.AudioFrame(block, 16000, 1, len(block) // 2))
        await source.wait_for_playout()
        await asyncio.wait_for(response.wait(), timeout=30)
        await asyncio.sleep(2)
    except Exception as exc:
        status, error = 'failed', type(exc).__name__ + ': ' + str(exc)
    finally:
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        if any(isinstance(r, Exception) for r in results):
            status, error = 'failed', 'Audio receiver failed'
        if source:
            await source.aclose()
        await room.disconnect()
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
        finally:
            await lk.aclose()
    rtt, clock_offset = min(pongs) if pongs else (None, None)
    events = []
    if origin is not None:
        events.extend(received)
        if clock_offset is not None:
            events.extend({k: v for k, v in {**e, 't': e['absolute'] - clock_offset - origin}.items()
                           if k != 'absolute'} for e in raw_events)
        for seg in spec['segments']:
            events.extend([{'kind': 'input_speech_start', 't': seg['start']},
                           {'kind': 'input_speech_stop', 't': seg['end']}])
        # Join acknowledgements by aligned submission timing, not unordered data arrival.
        submits = [e for e in events if e['kind'] == 'bc_audio_submitted']
        for event in events:
            if event['kind'] == 'bc_audio_received':
                preceding = [e for e in submits if e['t'] <= event['t'] + (rtt or 0) / 2]
                event['decision'] = max(preceding, key=lambda e: e['t'])['decision'] if preceding else None
    events.sort(key=lambda e: e['t'])
    result = {'scenario': spec['id'], 'label': spec['label'], 'pair_id': pair_id,
              'mode': 'enabled' if enabled else 'baseline', 'measurement_kind': 'livekit',
              'audio_sha256': spec['sha256'], 'status': status, 'error': error,
              'warmup': warmup, 'events': events, 'speech_end': spec['speech_end'],
              'clock_uncertainty_ms': rtt * 500 if rtt else None,
              'telemetry_dropped': telemetry_dropped,
              'metrics': run_metrics(events, spec['speech_end'])}
    if telemetry_dropped or result['metrics']['response_ms'] is None:
        result['status'] = 'failed'
    return result


async def main(repeats, output):
    missing = missing_credentials()
    if missing:
        raise SystemExit('Missing environment variables: ' + ', '.join(missing))
    runs = []
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    specs = scenarios()
    rng = random.Random(41)
    schedule = [(spec, i) for i in range(repeats) for spec in specs]
    rng.shuffle(schedule)
    for spec, repeat in [(specs[0], -1), *schedule]:
        order = [False, True]
        rng.shuffle(order)
        for enabled in order:
            print(spec['id'], repeat, 'enabled' if enabled else 'baseline', flush=True)
            runs.append(await replay(spec, enabled, f"{spec['id']}-{repeat}", repeat == -1))
            report = {'schema_version': 1, 'measurement_kind': 'livekit',
                      'description': 'Audio replay through live providers; receiver PCM onset.',
                      'repeats': repeats, 'seed': 41, 'runs': runs,
                      'models': {k: os.getenv(k, d) for k, d in
                                 [('STT_MODEL', 'nova-3'), ('LLM_MODEL', 'gpt-4o-mini'),
                                  ('TTS_MODEL', 'gpt-4o-mini-tts'), ('TTS_VOICE', 'alloy')]},
                      'summary': summarize(runs)}
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(report, indent=2) + '\n')
            temp.replace(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', default=str(ROOT / 'results/livekit.json'))
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error('Use at least two repetitions')
    asyncio.run(main(args.repeats, args.output))
