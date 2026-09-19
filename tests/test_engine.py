import asyncio
from dataclasses import replace

import pytest

from backchannel.engine import BackchannelEngine, Policy
from backchannel.events import Timeline


class Clock:
    now = 0.0
    def __call__(self):
        return self.now


class Sink:
    def __init__(self):
        self.plays = 0
        self.clears = 0
        self.release = asyncio.Event()

    async def play(self, audio, started):
        self.plays += 1
        started()
        await self.release.wait()

    def clear(self):
        self.clears += 1


async def cached():
    return b'audio'


def setup(provider=cached, **kwargs):
    clock = Clock()
    sink = Sink()
    timeline = Timeline(clock)
    engine = BackchannelEngine(provider, sink, timeline, clock=clock, **kwargs)
    engine.user_started()
    return engine, clock, sink, timeline


def eligible(engine, clock):
    clock.now += 3
    engine.transcript('I am describing an interesting problem and')
    engine.tick()


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_short_speech_has_no_ack():
    e, c, s, _ = setup()
    c.now = 1
    e.transcript('yes that is my answer')
    e.tick()
    await settle()
    assert s.plays == 0
    await e.aclose()


async def test_long_speech_and_storm_are_single_flight():
    e, c, s, t = setup()
    eligible(e, c)
    for _ in range(1000):
        e.tick()
    await settle()
    assert s.plays == 1
    assert len([x for x in t.events if x['kind'] == 'bc_decision']) == 1
    await e.aclose()


async def test_cooldown_then_repeat():
    e, c, s, _ = setup()
    s.release.set()
    eligible(e, c)
    await settle()
    c.now += 1
    e.tick()
    await settle()
    assert s.plays == 1
    c.now += 4
    e.transcript('I have more to say about this and')
    e.tick()
    await settle()
    assert s.plays == 2
    await e.aclose()


@pytest.mark.parametrize('action', ['stop', 'risk', 'response', 'disable', 'close'])
async def test_pending_audio_is_cancelled(action):
    gate = asyncio.Event()
    async def slow():
        await gate.wait()
        return b'audio'
    e, c, s, _ = setup(provider=slow)
    eligible(e, c)
    await settle()
    if action == 'stop':
        e.user_stopped()
    elif action == 'risk':
        e.risk(0.9)
    elif action == 'response':
        e.set_agent_busy(True)
    elif action == 'disable':
        e.set_enabled(False)
    else:
        await e.aclose()
    gate.set()
    await settle()
    assert s.plays == 0
    assert e.task is None
    await e.aclose()


async def test_playing_audio_yields_immediately_to_response():
    e, c, s, t = setup()
    eligible(e, c)
    await settle()
    e.set_agent_busy(True)
    assert s.clears > 0  # clear is synchronous: normal response never waits for TTS
    await settle()
    assert any(x['kind'] == 'bc_cancelled' and x['had_audio'] for x in t.events)
    await e.aclose()


@pytest.mark.parametrize('failure', ['slow', 'error'])
async def test_tts_failure_is_bounded(failure):
    async def provider():
        if failure == 'error':
            raise RuntimeError('provider unavailable')
        await asyncio.sleep(10)
    e, c, s, t = setup(provider=provider, policy=replace(Policy(), prepare_timeout=0.01))
    eligible(e, c)
    await asyncio.sleep(0.04)
    assert e.task is None and s.plays == 0
    assert any(x['kind'] == 'bc_failed' for x in t.events)
    await e.aclose()


async def test_rapid_transitions_do_not_resurrect_stale_audio():
    e, c, s, _ = setup()
    for _ in range(100):
        e.user_started()
        eligible(e, c)
        e.user_stopped()
        await settle()
    assert s.plays == 0
    await e.aclose()


async def test_baseline_and_stale_or_noisy_transcripts_suppress():
    for options in ({'enabled': False}, {}):
        e, c, s, _ = setup(**options)
        c.now = 3
        e.transcript('noise sounds like these words and', confidence=0.1)
        e.tick()
        await settle()
        assert s.plays == 0
        e.transcript('clear words that are now stale and')
        c.now += 3
        e.tick()
        await settle()
        assert s.plays == 0
        await e.aclose()


async def test_non_cooperative_provider_cannot_play_after_cancel():
    async def provider():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return b'stale'
    e, c, s, _ = setup(provider=provider)
    eligible(e, c)
    await settle()
    e.user_stopped()
    await settle()
    assert s.plays == 0
    await e.aclose()


async def test_provider_cannot_swallow_timeout_and_play_late_audio():
    async def provider():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return b'late audio'
    e, c, s, timeline = setup(
        provider=provider, policy=replace(Policy(), prepare_timeout=0.01))
    eligible(e, c)
    await asyncio.sleep(0.04)
    try:
        assert s.plays == 0
        assert e.task is None
        assert any(event['kind'] == 'bc_failed' and event['error'] == 'TimeoutError'
                   for event in timeline.events)
    finally:
        await e.aclose()
