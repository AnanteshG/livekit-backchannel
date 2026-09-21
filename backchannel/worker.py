"""LiveKit Agents integration using documented events, nodes and RTC audio APIs."""
import asyncio
import json
import os
from time import perf_counter

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, stt
from livekit.agents.voice import room_io
from livekit.plugins import deepgram, openai, silero

from .acknowledgements import Acknowledgements
from .acoustics import StreamingAcoustics, text_sentiment
from .audio import ROOT, LiveKitSink
from .engine import BackchannelEngine
from .events import Timeline

load_dotenv(ROOT / '.env')
server = AgentServer()


class MeasuredAgent(Agent):
    def __init__(self, engine, timeline):
        super().__init__(instructions=(
            'You are a thoughtful technical interviewer. Listen to the entire user turn. '
            'Respond naturally in one or two concise sentences. Do not add listening noises '
            'or fillers; a separate audio channel handles those.'))
        self.engine, self.timeline = engine, timeline
        self.request_sequence = 0

    async def on_user_turn_completed(self, turn_ctx, new_message):
        self.engine.turn_completed()
        self.engine.set_agent_busy(True)

    async def stt_node(self, audio, model_settings):
        async for event in Agent.default.stt_node(self, audio, model_settings):
            if isinstance(event, stt.SpeechEvent) and event.alternatives:
                if event.type in (stt.SpeechEventType.INTERIM_TRANSCRIPT,
                                  stt.SpeechEventType.FINAL_TRANSCRIPT):
                    best = event.alternatives[0]
                    # Some STT providers use zero to mean unknown, not low confidence.
                    confidence = best.confidence if best.confidence > 0 else None
                    self.engine.transcript(best.text,
                        final=event.type == stt.SpeechEventType.FINAL_TRANSCRIPT,
                        confidence=confidence)
                    if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                        self.timeline.emit('text_baseline', text=best.text,
                                           **text_sentiment(best.text))
            yield event

    async def llm_node(self, chat_ctx, tools, model_settings):
        self.engine.set_agent_busy(True)
        self.request_sequence += 1
        request = self.request_sequence
        self.timeline.emit('llm_request', request=request)
        first = True
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            text = (chunk if isinstance(chunk, str)
                    else getattr(getattr(chunk, 'delta', None), 'content', None))
            if first and text:
                self.timeline.emit('llm_first_token', request=request)
                first = False
            yield chunk

    async def tts_node(self, text, model_settings):
        self.request_sequence += 1
        request = self.request_sequence
        self.timeline.emit('tts_request', request=request)
        first = True
        async for frame in Agent.default.tts_node(self, text, model_settings):
            if first:
                self.engine.set_agent_busy(True)
                self.timeline.emit('tts_first_audio', request=request)
                first = False
            yield frame


@server.rtc_session(agent_name='backchannel-lab')
async def entrypoint(ctx: JobContext):
    await ctx.connect()
    participant = await ctx.wait_for_participant()
    metadata = json.loads(ctx.job.metadata or '{}')
    enabled = metadata.get('enabled', True) is True
    acoustic_enabled = metadata.get('acoustic_enabled', True) is True
    timeline = Timeline()
    queue = asyncio.Queue(maxsize=512)
    dropped = 0

    def enqueue(event):
        nonlocal dropped
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            dropped += 1

    timeline.listeners.append(enqueue)

    async def send_events():
        while True:
            event = await queue.get()
            payload = {'event': event, 'origin': timeline.origin, 'dropped': dropped}
            await ctx.room.local_participant.publish_data(
                json.dumps(payload).encode(), reliable=True, topic='lab.events')

    # Both modes have the same track/telemetry setup; only the policy switch differs.
    source = rtc.AudioSource(16000, 1, queue_size_ms=40)
    track = rtc.LocalAudioTrack.create_audio_track('backchannel', source)
    await ctx.room.local_participant.publish_track(track)
    clips = Acknowledgements()

    async def cached():
        phrase, audio = clips.next_clip(allow_verbal=not engine.high_frustration)
        timeline.emit('bc_clip_selected', decision=engine.sequence, phrase=phrase,
                      duration_ms=len(audio) / 32)
        return audio

    engine = BackchannelEngine(cached, LiveKitSink(source), timeline, enabled=enabled)

    def on_acoustic(prediction):
        engine.acoustic_state(prediction.frustration, prediction.confidence,
                              prediction.uncertainty)

    acoustics = StreamingAcoustics(on_acoustic, timeline=timeline, enabled=acoustic_enabled)
    acoustics.start()
    input_stream = rtc.AudioStream.from_participant(
        participant=participant, track_source=rtc.TrackSource.SOURCE_MICROPHONE,
        capacity=20, sample_rate=16000, num_channels=1, frame_size_ms=20)

    async def analyze_audio():
        try:
            async for event in input_stream:
                acoustics.push(bytes(event.frame.data))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            timeline.emit('acoustic_stream_failed', error=type(error).__name__)

    acoustic_reader = asyncio.create_task(analyze_audio(), name='acoustic-audio-reader')
    session = AgentSession(
        stt=deepgram.STT(model=os.getenv('STT_MODEL', 'nova-3'), interim_results=True),
        llm=openai.LLM(model=os.getenv('LLM_MODEL', 'gpt-4o-mini'), temperature=0),
        tts=openai.TTS(model=os.getenv('TTS_MODEL', 'gpt-4o-mini-tts'),
                       voice=os.getenv('TTS_VOICE', 'alloy')),
        vad=silero.VAD.load(min_silence_duration=0.12),
        turn_handling={
            'turn_detection': 'vad',
            'endpointing': {'mode': 'fixed', 'min_delay': 0.5, 'max_delay': 3.0},
            'preemptive_generation': {'enabled': False},
            'interruption': {'enabled': True, 'mode': 'vad', 'min_duration': 0.5,
                             'min_words': 2},
        },
    )

    @session.on('user_state_changed')
    def user_state(event):
        if event.new_state == 'speaking':
            engine.user_started()
        else:
            engine.user_stopped()
            acoustics.reset_turn()

    @session.on('agent_state_changed')
    def agent_state(event):
        engine.set_agent_busy(event.new_state in ('thinking', 'speaking'))
        timeline.emit('agent_state', state=event.new_state)

    @session.on('speech_created')
    def speech_created(event):
        engine.set_agent_busy(True)
        timeline.emit('response_created', source=event.source)

    @session.on('error')
    def error(event):
        timeline.emit('provider_error', recoverable=event.error.recoverable,
                      error=type(event.error).__name__)

    @ctx.room.on('data_received')
    def data(packet):
        if not packet.participant or packet.participant.identity != participant.identity:
            return
        try:
            message = json.loads(packet.data)
        except (ValueError, UnicodeDecodeError):
            return
        if packet.topic == 'lab.control' and isinstance(message.get('enabled'), bool):
            engine.set_enabled(message['enabled'])
            if isinstance(message.get('acoustic_enabled'), bool):
                acoustics.set_enabled(message['acoustic_enabled'])
        elif packet.topic == 'lab.ping' and isinstance(message.get('t0'), (int, float)):
            enqueue({'kind': 'clock_pong', 't0': message['t0'],
                     'server_time': perf_counter(), 't': perf_counter() - timeline.origin})

    sender = asyncio.create_task(send_events(), name='bounded-telemetry')
    ticker = asyncio.create_task(engine.run(), name='policy-ticker')
    cleaned = False

    async def cleanup():
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        await engine.aclose()
        await acoustics.aclose()
        acoustic_reader.cancel()
        ticker.cancel()
        sender.cancel()
        await asyncio.gather(ticker, sender, acoustic_reader, return_exceptions=True)
        await input_stream.aclose()
        await source.aclose()

    ctx.add_shutdown_callback(cleanup)
    try:
        # Match Deepgram/Silero directly. The SDK's default 24 kHz input and
        # cloud recorder otherwise use Soxr, which can assert in its Windows
        # FFT cache. Keep OpenAI's native 24 kHz response output unchanged.
        # Our own bounded event telemetry does not depend on cloud recording.
        await session.start(agent=MeasuredAgent(engine, timeline), room=ctx.room,
            record=False,
            room_options=room_io.RoomOptions(
                participant_identity=participant.identity,
                audio_input=room_io.AudioInputOptions(
                    sample_rate=16000, pre_connect_audio=False),
                audio_output=room_io.AudioOutputOptions(sample_rate=24000)))
        timeline.emit('ready', enabled=enabled, acoustic_enabled=acoustic_enabled)
    except BaseException:
        await cleanup()
        raise


if __name__ == '__main__':
    cli.run_app(server)
