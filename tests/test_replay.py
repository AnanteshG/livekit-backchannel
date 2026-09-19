import asyncio

import pytest

from backchannel.benchmark import scenarios, simulate, verify_audio


def test_eight_verified_pcm_recordings():
    specs = scenarios()
    assert len(specs) == 8 and len({s['sha256'] for s in specs}) == 8
    for spec in specs:
        assert len(verify_audio(spec)) / 32000 == pytest.approx(spec['duration'])
        assert spec['speech_end'] < spec['duration']


async def test_replay_modes_share_inputs_but_not_acknowledgements():
    spec = next(s for s in scenarios() if s['id'] == 'long_monologue')
    a, b = await asyncio.gather(simulate(spec, False, 41, 'p'), simulate(spec, True, 41, 'p'))
    assert a['audio_sha256'] == b['audio_sha256']
    assert a['metrics']['backchannels'] == 0
    assert b['metrics']['backchannels'] > 1
    assert a['metrics']['response_ms'] == b['metrics']['response_ms']


async def test_stop_at_decision_cancels_pending_ack():
    spec = next(s for s in scenarios() if s['id'] == 'stop_at_decision')
    run = await simulate(spec, True, 41, 'p')
    assert run['metrics']['cancelled'] == 1
    assert run['metrics']['backchannels'] == 0


async def test_short_and_noisy_inputs_do_not_backchannel():
    for spec in scenarios():
        if spec['id'] in ('short_answer', 'noisy_audio'):
            run = await simulate(spec, True, 41, 'p')
            assert run['metrics']['backchannels'] == 0

