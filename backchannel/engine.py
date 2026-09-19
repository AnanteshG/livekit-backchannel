"""Single-flight, context-free acknowledgements. No dependency on LiveKit internals."""
import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from .events import Timeline


class AudioSink(Protocol):
    async def play(self, audio: bytes, started) -> None: ...
    def clear(self) -> None: ...


@dataclass(frozen=True)
class Policy:
    min_speech: float = 2.4
    cooldown: float = 4.5
    min_words: int = 5
    max_eot_risk: float = 0.55
    transcript_ttl: float = 2.0
    prepare_timeout: float = 0.65
    max_per_turn: int = 3


class BackchannelEngine:
    def __init__(self, provider, sink: AudioSink, timeline=None, policy=None,
                 enabled=True, clock=perf_counter):
        self.provider, self.sink = provider, sink
        self.clock = clock
        self.timeline = timeline or Timeline(clock)
        self.policy = policy or Policy()
        self.enabled = enabled
        self.speaking = False
        self.agent_busy = False
        self.closed = False
        self.turn = 0
        self.generation = 0
        self.speech_started = self.clock()
        self.transcript_at = float('-inf')
        self.text = ''
        self.eot_risk = 0.0
        self.last_attempt = float('-inf')
        self.count = 0
        self.task = None
        self.sequence = 0

    def user_started(self):
        if self.closed or self.speaking:
            return
        self.speaking = True
        self.speech_started = self.clock()
        self.text = ''
        self.transcript_at = float('-inf')
        self.eot_risk = 0.0
        self.timeline.emit('user_speech_start', turn=self.turn)

    def user_stopped(self):
        if not self.speaking:
            return
        self.speaking = False
        self.timeline.emit('user_speech_stop', turn=self.turn)
        self.cancel('user_stopped')

    def turn_completed(self):
        self.user_stopped()
        self.cancel('turn_completed')
        self.timeline.emit('eot_detected', turn=self.turn)
        self.turn += 1
        self.count = 0
        self.text = ''

    def transcript(self, text, final=False, confidence=None):
        self.text = text
        self.transcript_at = self.clock()
        # This is a deliberately conservative heuristic, NOT a model probability.
        self.eot_risk = 0.85 if text.rstrip().endswith(('.', '?', '!')) else 0.1
        if confidence is not None and confidence < 0.6:
            self.eot_risk = 1.0
        self.timeline.emit('stt_final' if final else 'stt_interim', text=text,
                           turn=self.turn)
        self.risk(self.eot_risk, source='punctuation_confidence_heuristic')

    def risk(self, value, source='external'):
        self.eot_risk = value
        self.timeline.emit('eot_risk', value=value, source=source, turn=self.turn)
        if value >= self.policy.max_eot_risk:
            self.cancel('eot_risk')

    def set_agent_busy(self, busy):
        self.agent_busy = busy
        if busy:
            self.cancel('normal_response_priority')

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.cancel('disabled')
        self.timeline.emit('mode', enabled=enabled)

    def eligible(self):
        now, p = self.clock(), self.policy
        return (not self.closed and self.enabled and self.speaking and not self.agent_busy
                and now - self.speech_started >= p.min_speech
                and now - self.last_attempt >= p.cooldown
                and now - self.transcript_at <= p.transcript_ttl
                and len(self.text.split()) >= p.min_words
                and self.eot_risk < p.max_eot_risk and self.count < p.max_per_turn)

    def tick(self):
        # A storm of transcript events can never create more than one audio job.
        if self.task is not None or not self.eligible():
            return
        self.last_attempt = self.clock()  # also rate-limit failures/cancellations
        self.sequence += 1
        decision = self.sequence
        generation = self.generation
        self.timeline.emit('bc_decision', decision=decision, turn=self.turn)
        self.task = asyncio.create_task(self._play(generation, decision), name='backchannel')
        self.task.add_done_callback(self._done)

    def _done(self, task):
        # Cancellation may happen before the coroutine executes its try/finally.
        if self.task is task:
            if task.cancelled():
                self.timeline.emit('bc_cancelled', decision=self.sequence, had_audio=False)
            self.task = None

    def still_valid(self, generation):
        return (generation == self.generation and not self.closed and self.enabled
                and self.speaking and not self.agent_busy
                and self.eot_risk < self.policy.max_eot_risk
                and self.clock() - self.transcript_at <= self.policy.transcript_ttl)

    async def _play(self, generation, decision):
        started = False

        def on_started():
            nonlocal started
            if not started:
                started = True
                self.count += 1
                self.timeline.emit('bc_audio_submitted', decision=decision, turn=self.turn)

        try:
            async with asyncio.timeout(self.policy.prepare_timeout) as preparation:
                audio = await self.provider()
            # A provider may catch cancellation and return anyway. Expiration
            # must still reject its audio even when the timeout did not raise.
            if preparation.expired():
                raise TimeoutError('Acknowledgement preparation expired')
            if not self.still_valid(generation):
                self.timeline.emit('bc_discarded', decision=decision)
                return
            await self.sink.play(audio, on_started)
            self.timeline.emit('bc_audio_end', decision=decision)
        except asyncio.CancelledError:
            self.timeline.emit('bc_cancelled', decision=decision, had_audio=started)
            raise
        except Exception as error:
            self.timeline.emit('bc_failed', decision=decision, error=type(error).__name__)
        finally:
            # No successor can start until the cancelled predecessor is fully drained.
            self.sink.clear()
            self.task = None

    def cancel(self, reason):
        self.generation += 1
        if self.task is not None:
            self.timeline.emit('bc_cancel_requested', reason=reason, decision=self.sequence)
            self.task.cancel()
            self.sink.clear()

    async def run(self):
        try:
            while not self.closed:
                self.tick()
                await asyncio.sleep(0.05)
        finally:
            await self.aclose()

    async def aclose(self):
        self.closed = True
        task = self.task
        self.cancel('shutdown')
        if task:
            await asyncio.gather(task, return_exceptions=True)
        self.sink.clear()
