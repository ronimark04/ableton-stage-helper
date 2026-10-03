"""A simulated Live Set, close enough to Live's behaviour to test Stage Helper.

What it models (the parts of the Live Object Model Stage Helper uses):
  * the playhead runs in 1 ms engine steps at the song's tempo;
  * CuePoint.jump() while playing waits for the next Global Quantization step
    (sample-accurate, like Live); while stopped it moves the play position;
  * the arrangement loop wraps exactly at the loop end;
  * stop_playing() stops at once, and the play position returns to where
    playback was started, as in Live;
  * current_song_time, as seen by a script, lags the engine a little (it is
    updated about every 16 ms).
Everything the engine does is recorded in song.events for the tests to check.
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, os.path.join(HERE, 'fakes')):
    if path not in sys.path:
        sys.path.insert(0, path)

from StageHelper.show import NOTE_STEPS  # noqa: E402

REPORT_EVERY = 0.016


class FakeCInstance(object):
    """What Live hands a Control Surface script when it loads it."""

    def __init__(self, song):
        self._song = song
        self.log = []
        self.status = ''
        self.disconnected = False

    def song(self):
        return self._song


class FakeCue(object):
    def __init__(self, song, name, time):
        self._song = song
        self.name = name
        self.time = float(time)

    def jump(self):
        self._song.cue_jump(self)


class FakeSong(object):
    _next_ptr = 1

    def __init__(self, cues=(), tempo=120.0, signature=(4, 4), quantization=4):
        self.tempo = float(tempo)
        self.signature_numerator, self.signature_denominator = signature
        self.clip_trigger_quantization = quantization
        self.cue_points = [FakeCue(self, name, time) for name, time in cues]
        self.loop = False
        self.loop_start = 0.0
        self.loop_length = 16.0
        self.is_playing = False
        self.start_marker = 0.0
        self.engine_time = 0.0
        self.pending = None   # (boundary, target) of a quantized jump
        self.events = []      # ('jump', at, to) ('wrap', at, to) ('stop', at) ('start', at)
        self._reported = 0.0
        self._report_clock = 0.0
        self._live_ptr = FakeSong._next_ptr
        FakeSong._next_ptr += 1

    # The Live Object Model ----------------------------------------------------

    @property
    def current_song_time(self):
        return self._reported if self.is_playing else self.start_marker

    @current_song_time.setter
    def current_song_time(self, value):
        assert not self.is_playing, 'Stage Helper should not set the time while playing'
        self.start_marker = float(value)

    def start_playing(self):
        if not self.is_playing:
            self.is_playing = True
            self.engine_time = self._reported = self.start_marker
            self.events.append(('start', self.start_marker))

    def stop_playing(self):
        if self.is_playing:
            self.is_playing = False
            self.pending = None
            self.events.append(('stop', self.engine_time))

    def cue_jump(self, cue):
        if not self.is_playing:
            self.start_marker = cue.time
            return
        step = self.quantization_step()
        if step is None:
            self.events.append(('jump', self.engine_time, cue.time))
            self.engine_time = cue.time
        else:
            boundary = (math.floor(self.engine_time / step + 1e-9) + 1) * step
            self.pending = (boundary, cue.time)

    def jump_to_next_cue(self):
        """The ] key: the next locator, quantized like any locator jump."""
        later = sorted((c for c in self.cue_points if c.time > self.engine_time + 1e-6),
                       key=lambda c: c.time)
        if later:
            self.cue_jump(later[0])

    def jump_to_prev_cue(self):
        """The [ key: the previous locator."""
        earlier = sorted((c for c in self.cue_points if c.time < self.engine_time - 0.01),
                         key=lambda c: c.time)
        if earlier:
            self.cue_jump(earlier[-1])

    # Helpers for tests --------------------------------------------------------

    def jumps_to(self, time):
        return [e for e in self.events if e[0] == 'jump' and e[2] == time]

    def quantization_step(self):
        q = self.clip_trigger_quantization
        bar = self.signature_numerator * 4.0 / self.signature_denominator
        if q == 0:
            return None
        if q in (1, 2, 3, 4):
            return bar * {1: 8, 2: 4, 3: 2, 4: 1}[q]
        return NOTE_STEPS[q]

    def cue(self, name):
        return [c for c in self.cue_points if c.name == name][0]

    def add_cue(self, name, time):
        self.cue_points.append(FakeCue(self, name, time))

    def click(self, time):
        """The user clicks somewhere in the arrangement while playing (unquantized)."""
        self.engine_time = self._reported = float(time)
        self.pending = None

    def kinds(self, kind):
        return [e for e in self.events if e[0] == kind]

    def advance(self, seconds):
        """Run the engine for this long, in 1 ms steps."""
        steps = int(round(seconds / 0.001))
        for _ in range(steps):
            self._step(0.001)

    def _step(self, dt):
        self._report_clock += dt
        if self._report_clock >= REPORT_EVERY:
            self._report_clock = 0.0
            self._reported = self.engine_time
        if not self.is_playing:
            return
        t0 = self.engine_time
        t1 = t0 + dt * self.tempo / 60.0
        if self.pending is not None and t1 >= self.pending[0] - 1e-12:
            boundary, target = self.pending
            self.pending = None
            self.events.append(('jump', boundary, target))
            self.engine_time = target + (t1 - boundary)
            return
        if self.loop:
            end = self.loop_start + self.loop_length
            if t0 < end <= t1 + 1e-12:
                self.events.append(('wrap', end, self.loop_start))
                self.engine_time = self.loop_start + (t1 - end)
                return
        self.engine_time = t1
