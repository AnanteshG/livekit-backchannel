"""Bounded streaming acoustic cues, independent of transcript and response generation."""
from __future__ import annotations

import asyncio
import math
from collections import deque
from dataclasses import asdict, dataclass
from time import perf_counter

import numpy as np


@dataclass(frozen=True)
class AcousticConfig:
    sample_rate: int = 16000
    window_seconds: float = 1.5
    stride_seconds: float = 0.25
    minimum_seconds: float = 0.75
    smoothing: float = 0.35
    queue_size: int = 1


@dataclass(frozen=True)
class AcousticPrediction:
    frustration: float
    uncertainty: float
    energy: float
    confidence: float
    audio_seconds: float
    inference_ms: float
    effective_latency_ms: float
    window_id: int


class ProsodyModel:
    """Small CPU DSP baseline. Values are cues, never a person's emotional state."""

    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate

    @staticmethod
    def _clip(value):
        return float(np.clip(value, 0.0, 1.0))

    def predict(self, pcm: bytes, audio_seconds: float, window_id: int):
        started = perf_counter()
        signal = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
        frame = max(1, int(self.sample_rate * 0.025))
        hop = max(1, int(self.sample_rate * 0.010))
        if len(signal) < frame:
            signal = np.pad(signal, (0, frame - len(signal)))
        count = 1 + (len(signal) - frame) // hop
        frames = np.lib.stride_tricks.as_strided(
            signal, shape=(count, frame),
            strides=(signal.strides[0] * hop, signal.strides[0]), writeable=False)
        rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-10)
        db = 20 * np.log10(rms + 1e-6)
        voiced = db > -43
        voiced_ratio = float(np.mean(voiced))
        active_db = db[voiced] if np.any(voiced) else np.array([-60.0])
        energy = self._clip((float(np.mean(active_db)) + 48) / 32)
        pause_ratio = 1.0 - voiced_ratio
        variation = self._clip(float(np.std(active_db)) / 12)
        zcr = np.mean(np.abs(np.diff(np.signbit(frames), axis=1)), axis=1)
        roughness = self._clip(float(np.mean(zcr[voiced])) * 7 if np.any(voiced) else 0)
        # Interpretable acoustic proxies. No words or transcript are consumed.
        frustration = self._clip(0.70 * energy + 0.10 * roughness + 0.20 * variation)
        uncertainty = self._clip(0.52 * pause_ratio + 0.30 * variation + 0.18 * (1 - energy))
        confidence = self._clip((audio_seconds / 1.5) * min(1.0, voiced_ratio / 0.35))
        inference_ms = (perf_counter() - started) * 1000
        return AcousticPrediction(frustration, uncertainty, energy, confidence,
                                  audio_seconds, inference_ms,
                                  audio_seconds * 1000 + inference_ms, window_id)


class StreamingAcoustics:
    """Latest-window processor: bounded memory, one inference, stale work discarded."""

    def __init__(self, on_prediction, timeline=None, model=None, config=None, clock=perf_counter,
                 enabled=True):
        self.config = config or AcousticConfig()
        self.model = model or ProsodyModel(self.config.sample_rate)
        self.on_prediction = on_prediction
        self.timeline = timeline
        self.clock = clock
        self.max_bytes = int(self.config.sample_rate * self.config.window_seconds) * 2
        self.min_bytes = int(self.config.sample_rate * self.config.minimum_seconds) * 2
        self.stride_bytes = int(self.config.sample_rate * self.config.stride_seconds) * 2
        self.audio = bytearray()
        self.since_submit = 0
        self.queue = asyncio.Queue(maxsize=self.config.queue_size)
        self.worker = None
        self.closed = False
        self.enabled = enabled
        self.window_id = 0
        self.latest_submitted = 0
        self.smoothed = None
        self.dropped_windows = 0
        self.failures = 0
        self.inference_times = deque(maxlen=2000)

    def start(self):
        if self.worker is None:
            self.worker = asyncio.create_task(self._run(), name='acoustic-latest-window')

    def push(self, pcm: bytes):
        if self.closed or not self.enabled or not pcm:
            return
        self.audio.extend(pcm)
        if len(self.audio) > self.max_bytes:
            del self.audio[:-self.max_bytes]
        self.since_submit += len(pcm)
        if len(self.audio) < self.min_bytes or self.since_submit < self.stride_bytes:
            return
        self.since_submit %= self.stride_bytes
        self.window_id += 1
        self.latest_submitted = self.window_id
        item = (self.window_id, bytes(self.audio), self.clock())
        if self.queue.full():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                self.dropped_windows += 1
                self._emit('acoustic_window_dropped', dropped=self.dropped_windows)
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(item)

    def reset_turn(self):
        self.window_id += 1
        self.latest_submitted = self.window_id
        self.audio.clear()
        self.since_submit = 0
        self.smoothed = None
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.reset_turn()
        self._emit('acoustic_mode', enabled=enabled)

    def _emit(self, kind, **data):
        if self.timeline:
            self.timeline.emit(kind, **data)

    def _smooth(self, prediction):
        values = np.array([prediction.frustration, prediction.uncertainty, prediction.energy])
        if self.smoothed is None:
            self.smoothed = values
        else:
            a = self.config.smoothing
            self.smoothed = a * values + (1 - a) * self.smoothed
        return AcousticPrediction(*map(float, self.smoothed), prediction.confidence,
                                  prediction.audio_seconds, prediction.inference_ms,
                                  prediction.effective_latency_ms, prediction.window_id)

    async def _run(self):
        while True:
            item = await self.queue.get()
            if item is None:
                self.queue.task_done()
                return
            window_id, audio, submitted = item
            try:
                seconds = len(audio) / (self.config.sample_rate * 2)
                prediction = await asyncio.to_thread(self.model.predict, audio, seconds, window_id)
                self.inference_times.append(prediction.inference_ms)
                if window_id != self.latest_submitted:
                    self.dropped_windows += 1
                    self._emit('acoustic_stale_dropped', window=window_id,
                               latest=self.latest_submitted)
                    continue
                prediction = self._smooth(prediction)
                transport_ready_ms = (self.clock() - submitted) * 1000
                data = asdict(prediction) | {'queue_to_signal_ms': transport_ready_ms,
                                             'model': 'prosody-dsp-v1'}
                self._emit('acoustic_prediction', **data)
                result = self.on_prediction(prediction)
                if asyncio.iscoroutine(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.failures += 1
                self._emit('acoustic_failed', error=type(error).__name__, failures=self.failures)
            finally:
                self.queue.task_done()

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)

    def latency_summary(self):
        if not self.inference_times:
            return {'p50_ms': None, 'p95_ms': None}
        return {'p50_ms': float(np.percentile(self.inference_times, 50)),
                'p95_ms': float(np.percentile(self.inference_times, 95))}


POSITIVE = {'great', 'good', 'love', 'happy', 'thanks', 'yes'}
NEGATIVE = {'bad', 'hate', 'angry', 'frustrated', 'problem', 'wrong'}


def text_sentiment(text: str):
    """Deliberately simple comparison baseline; never used by the acoustic model."""
    words = {word.strip('.,!?').lower() for word in text.split()}
    score = sum(word in POSITIVE for word in words) - sum(word in NEGATIVE for word in words)
    return {'positive': 0.5 + 0.25 * math.tanh(score),
            'negative': 0.5 - 0.25 * math.tanh(score)}
