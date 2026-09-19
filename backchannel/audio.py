import asyncio
import math
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pcm(path):
    with wave.open(str(path), 'rb') as f:
        if (f.getframerate(), f.getnchannels(), f.getsampwidth()) != (16000, 1, 2):
            raise ValueError('Expected 16kHz mono PCM16 WAV')
        return f.readframes(f.getnframes())


def rms(data):
    values = struct.unpack('<' + 'h' * (len(data) // 2), data)
    return math.sqrt(sum(x * x for x in values) / max(1, len(values))) / 32768


class LiveKitSink:
    """Independent track: never queues behind AgentSession speech or enters its context."""
    def __init__(self, source):
        self.source = source

    async def play(self, audio, started):
        from livekit import rtc
        for offset in range(0, len(audio), 640):
            block = audio[offset:offset + 640]
            await self.source.capture_frame(rtc.AudioFrame(block, 16000, 1, len(block) // 2))
            if offset == 0:
                started()
            # Cap producer look-ahead; cancellation can leave a network/jitter-buffer tail.
            await asyncio.sleep(0.02)
        await self.source.wait_for_playout()

    def clear(self):
        self.source.clear_queue()
