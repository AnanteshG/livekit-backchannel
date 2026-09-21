"""Preloaded acknowledgement variety with a repeatable per-session sequence."""
from .audio import ROOT, pcm

# Keep verbal prompting occasional. Fixed order makes replay comparisons repeatable.
ORDER = ('listening', 'mmm', 'uh-huh', 'listening', 'go-on', 'mmm')
LABELS = {'listening': 'mm-hmm', 'mmm': 'mmm', 'uh-huh': 'uh-huh', 'go-on': 'go on'}


class Acknowledgements:
    def __init__(self):
        self.clips = {name: pcm(ROOT / f'scenarios/audio/{name}.wav') for name in ORDER}
        self.index = 0

    def next_clip(self, allow_verbal=True):
        name = ORDER[self.index % len(ORDER)]
        self.index += 1
        if not allow_verbal and name == 'go-on':
            name = 'listening'
        return LABELS[name], self.clips[name]
