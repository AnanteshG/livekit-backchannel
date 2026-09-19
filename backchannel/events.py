"""One monotonic clock per process. Cross-process clocks must be aligned explicitly."""
from collections import deque
from time import perf_counter


class Timeline:
    def __init__(self, clock=perf_counter, limit=20000):
        self.clock = clock
        self.origin = clock()
        self.events = deque(maxlen=limit)
        self.dropped = 0
        self.listeners = []

    def emit(self, kind, **data):
        event = {"t": self.clock() - self.origin, "kind": kind, **data}
        if len(self.events) == self.events.maxlen:
            self.dropped += 1
        self.events.append(event)
        for listener in self.listeners:
            listener(event)
        return event

