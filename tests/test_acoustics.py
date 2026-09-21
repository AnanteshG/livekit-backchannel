import asyncio
import time

import numpy as np

from backchannel.acoustics import (
    AcousticPrediction,
    ProsodyModel,
    StreamingAcoustics,
    text_sentiment,
)
from backchannel.engine import BackchannelEngine
from backchannel.events import Timeline
from tests.test_engine import Clock, Sink


def tone(seconds=1.5, amplitude=.3, sample_rate=16000):
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return (np.sin(2 * np.pi * 180 * t) * amplitude * 32767).astype('<i2').tobytes()


async def wait_for(predicate, timeout=.5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.005)


async def test_streaming_window_emits_before_turn_ends():
    predictions = []
    stream = StreamingAcoustics(predictions.append)
    stream.start()
    for offset in range(0, len(tone()), 640):
        stream.push(tone()[offset:offset + 640])
        await asyncio.sleep(0)
    await wait_for(lambda: predictions)
    assert .75 <= predictions[0].audio_seconds <= 1.5
    await stream.aclose()


def test_audio_model_uses_delivery_not_words():
    model = ProsodyModel()
    quiet = model.predict(tone(amplitude=.04), 1.5, 1)
    loud = model.predict(tone(amplitude=.7), 1.5, 2)
    assert loud.energy > quiet.energy
    assert loud.frustration > quiet.frustration


async def test_smoothing_limits_adjacent_jump():
    predictions = []
    stream = StreamingAcoustics(predictions.append)
    stream.start()
    for audio in (tone(amplitude=.03), tone(amplitude=.9)):
        stream.audio.clear()
        stream.since_submit = 0
        stream.push(audio)
        await wait_for(lambda: len(predictions) >= (1 if audio[0:1] else 0))
        await asyncio.sleep(.03)
    await wait_for(lambda: len(predictions) == 2)
    raw_high = ProsodyModel().predict(tone(amplitude=.9), 1.5, 2).energy
    assert predictions[0].energy < predictions[1].energy < raw_high
    await stream.aclose()


async def test_slow_inference_drops_stale_result_and_bounds_queue():
    class Slow:
        def predict(self, audio, seconds, window_id):
            time.sleep(.04)
            return AcousticPrediction(.2, .2, .2, 1, seconds, 40, seconds * 1000 + 40,
                                      window_id)
    events, timeline = [], Timeline()
    timeline.listeners.append(events.append)
    stream = StreamingAcoustics(lambda value: None, timeline=timeline, model=Slow())
    stream.start()
    stream.push(tone(.8))
    await asyncio.sleep(.005)
    stream.since_submit = stream.stride_bytes
    stream.push(tone(.3))
    stream.since_submit = stream.stride_bytes
    stream.push(tone(.3))
    await asyncio.sleep(.12)
    assert stream.queue.maxsize == 1
    assert any(e['kind'] in ('acoustic_stale_dropped', 'acoustic_window_dropped') for e in events)
    await stream.aclose()


async def test_failed_inference_is_isolated_and_recovers():
    class Flaky:
        calls = 0
        def predict(self, audio, seconds, window_id):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError('model offline')
            return AcousticPrediction(.2, .2, .2, 1, seconds, 1, seconds * 1000 + 1, window_id)
    predictions = []
    stream = StreamingAcoustics(predictions.append, model=Flaky())
    stream.start()
    stream.push(tone(.8))
    await asyncio.sleep(.03)
    stream.since_submit = stream.stride_bytes
    stream.push(tone(.3))
    await wait_for(lambda: predictions)
    assert stream.failures == 1 and not stream.worker.done()
    await stream.aclose()


async def test_stop_mid_window_clears_audio_and_invalidates_work():
    stream = StreamingAcoustics(lambda value: None)
    stream.start()
    stream.push(tone(.5))
    stream.reset_turn()
    assert not stream.audio and stream.queue.empty()
    await stream.aclose()


async def test_disabled_system_does_no_work():
    stream = StreamingAcoustics(lambda value: None, enabled=False)
    stream.start()
    stream.push(tone())
    assert not stream.audio and stream.window_id == 0
    await stream.aclose()


async def test_rapid_speech_silence_resets_are_safe():
    stream = StreamingAcoustics(lambda value: None)
    stream.start()
    for _ in range(20):
        stream.push(tone(.03))
        stream.reset_turn()
    assert not stream.audio and not stream.closed
    await stream.aclose()


def test_text_baseline_same_for_different_delivery():
    text = "Yeah, that's great."
    assert text_sentiment(text) == text_sentiment(text)


def test_high_frustration_changes_policy_without_blocking_response():
    clock, sink, timeline = Clock(), Sink(), Timeline(Clock())
    engine = BackchannelEngine(lambda: None, sink, timeline, clock=clock)
    engine.acoustic_state(.9, .9, .6)
    assert engine.high_frustration and engine.cooldown_multiplier == 1.75
    engine.set_agent_busy(True)
    assert engine.agent_busy


async def test_clean_shutdown_cancels_worker():
    stream = StreamingAcoustics(lambda value: None)
    stream.start()
    task = stream.worker
    await stream.aclose()
    assert stream.closed and task.done()
