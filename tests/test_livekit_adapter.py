import asyncio
from types import SimpleNamespace

from livekit import rtc
from livekit.agents import Agent, llm, stt

from backchannel.acoustics import AcousticPrediction
from backchannel.audio import LiveKitSink
from backchannel.worker import MeasuredAgent
from tests.test_engine import eligible, settle, setup


async def test_audio_sink_cancellation_clears_source():
    class Source:
        frames = 0
        cleared = False
        async def capture_frame(self, frame):
            assert isinstance(frame, rtc.AudioFrame)
            assert frame.sample_rate == 16000
            self.frames += 1
        async def wait_for_playout(self):
            pass
        def clear_queue(self):
            self.cleared = True
    source = Source()
    sink = LiveKitSink(source)
    started = []
    task = asyncio.create_task(sink.play(bytes(32000), lambda: started.append(True)))
    await asyncio.sleep(.03)
    task.cancel()
    sink.clear()
    await asyncio.gather(task, return_exceptions=True)
    frames = source.frames
    await asyncio.sleep(.03)
    assert source.frames == frames and frames < 50 and started == [True] and source.cleared


async def test_public_llm_hook_preserves_chunks_and_prioritizes_response(monkeypatch):
    e, c, s, timeline = setup()
    eligible(e, c)
    await settle()
    sentinel = SimpleNamespace()
    chunk = SimpleNamespace(delta=SimpleNamespace(content='Hello'))
    async def default(*args):
        yield sentinel
        yield chunk
    monkeypatch.setattr(Agent.default, 'llm_node', default)
    agent = MeasuredAgent(e, timeline)
    assert [x async for x in agent.llm_node(None, [], None)] == [sentinel, chunk]
    assert e.agent_busy and s.clears > 0
    assert len([x for x in timeline.events if x['kind']=='llm_first_token']) == 1
    await e.aclose()


async def test_public_stt_hook_uses_confidence_without_consuming_transcript(monkeypatch):
    e, c, s, timeline = setup()
    event = stt.SpeechEvent(type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                           alternatives=[stt.SpeechData(language='en', text='this is noise and not speech', confidence=.2)])
    async def default(*args):
        yield event
    monkeypatch.setattr(Agent.default, 'stt_node', default)
    agent = MeasuredAgent(e, timeline)
    c.now = 3
    assert [x async for x in agent.stt_node(None, None)] == [event]
    e.tick()
    await settle()
    assert s.plays == 0
    await e.aclose()


async def test_public_stt_hook_tees_pcm_to_acoustics(monkeypatch):
    e, _, _, timeline = setup()
    frame = rtc.AudioFrame(data=bytes(640), sample_rate=16000,
                           num_channels=1, samples_per_channel=320)
    event = SimpleNamespace()

    class Acoustics:
        chunks = []

        def push(self, pcm):
            self.chunks.append(pcm)

    async def audio():
        yield frame

    async def default(_agent, source, _settings):
        received = [item async for item in source]
        assert received == [frame]
        yield event

    acoustics = Acoustics()
    monkeypatch.setattr(Agent.default, 'stt_node', default)
    agent = MeasuredAgent(e, timeline, acoustics)
    assert [x async for x in agent.stt_node(audio(), None)] == [event]
    assert acoustics.chunks == [bytes(frame.data)]
    await e.aclose()


async def test_acoustic_context_reaches_llm_once_without_changing_history(monkeypatch):
    e, _, _, timeline = setup()
    agent = MeasuredAgent(e, timeline, SimpleNamespace(enabled=True))
    context = llm.ChatContext()
    context.add_message(role='user', content='Please help me with this problem.')
    received = []

    async def default(_agent, ctx, *_args):
        received.append(ctx)
        yield 'Let us take it one step at a time.'

    monkeypatch.setattr(Agent.default, 'llm_node', default)
    agent.receive_acoustic(AcousticPrediction(.9, .6, .8, .9, 1.5, 1, 1501, 7))
    await agent.on_user_turn_completed(context, None)
    assert [x async for x in agent.llm_node(context, [], None)]
    assert len(context.items) == 1
    assert len(received[0].items) == 2
    assert 'calm, patient tone' in received[0].items[-1].text_content
    assert any(event['kind'] == 'agent_acoustic_context' for event in timeline.events)
    assert [x async for x in agent.llm_node(context, [], None)]
    assert received[1] is context
    await e.aclose()


async def test_low_confidence_disabled_and_stale_cues_are_not_sent(monkeypatch):
    e, _, _, timeline = setup()
    acoustics = SimpleNamespace(enabled=True)
    agent = MeasuredAgent(e, timeline, acoustics)
    context = llm.ChatContext()

    async def default(_agent, ctx, *_args):
        assert ctx is context
        yield 'Normal answer'

    monkeypatch.setattr(Agent.default, 'llm_node', default)
    for confidence, enabled, stale in [(.2, True, False), (.9, False, False), (.9, True, True)]:
        acoustics.enabled = enabled
        agent.receive_acoustic(AcousticPrediction(.9, .6, .8, confidence, 1.5, 1, 1501, 7))
        if stale:
            agent.latest_cue = (agent.latest_cue[0], 0)
        await agent.on_user_turn_completed(context, None)
        assert [x async for x in agent.llm_node(context, [], None)]
    agent.clear_acoustic()
    assert agent.latest_cue is None and agent.response_cue is None
    assert not any(event['kind'] == 'agent_acoustic_context' for event in timeline.events)
    await e.aclose()
