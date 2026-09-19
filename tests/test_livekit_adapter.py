import asyncio
from types import SimpleNamespace

from livekit import rtc
from livekit.agents import Agent, stt

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
