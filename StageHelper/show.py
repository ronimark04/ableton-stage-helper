"""Stage Helper's show logic: what happens at STOP, +LOOP and >>> locators.

Locator names it reacts to (anything else is left alone):

    STOP           playback stops here (exactly "STOP", in capitals)
    Chorus +LOOP   the section from here to the next locator repeats until you
                   press "Leave loop" on the page (or press ] in Live)
    A >>> Song B   playback carries straight on at the locator named "Song B"
                   (anything before the >>> is just a note: ">>> Song B" works too)

This module talks to Live only through the Song object it is handed and never
imports Live, so it can be tested against a simulated song (see tests/).

Wherever it can, it leaves the timing to Live's audio engine:
  * a jump is requested most of a bar early with CuePoint.jump(). Live holds it
    until the next bar line (Global Quantization), which is the >>> locator.
  * a loop is the same thing: near the end of each pass we request a jump back
    to the +LOOP locator. Live's own Loop switch and loop brace are never used.
  * only STOP depends on how quickly we see the playhead pass the locator.
"""

import math

STOP_NAME = 'STOP'
LOOP_SUFFIX = '+LOOP'
JUMP_MARK = '>>>'

# Live.Song.Quantization values 0..13, in order.
QUANTIZATION_NAMES = ('None', '8 Bars', '4 Bars', '2 Bars', '1 Bar', '1/2', '1/2T',
                      '1/4', '1/4T', '1/8', '1/8T', '1/16', '1/16T', '1/32')
NO_QUANTIZATION = 0
ONE_BAR = 4
# The note-value settings: length of one step in beats.
NOTE_STEPS = {5: 2.0, 6: 4.0 / 3, 7: 1.0, 8: 2.0 / 3, 9: 0.5, 10: 1.0 / 3,
              11: 0.25, 12: 1.0 / 6, 13: 0.125}

EPS = 1e-6           # beats: positions closer than this count as the same
MISSED_JUMP = 0.1    # beats past a requested jump before we call it missed
LANDING = 2.0        # beats after a jump target where a jump counts as landed
SLACK = 0.5          # beats of jitter allowed between song time and the clock
LOOP_MARGIN = 0.05   # beats into a loop's last bar before we ask for the jump back
REFRESH_EVERY = 0.1  # seconds between re-reading the locators
PARK_FOR = 0.5       # seconds we keep the play position on a STOP after stopping
# Seconds the playhead position Live reports may trail the audio engine. Right
# before a jump point we can't tell from it whether Live has already jumped,
# so there we wait until it is certain instead of risking a jump a bar off.
UNSURE = 0.1


class Locator(object):
    """One arrangement locator, as Stage Helper understands it."""

    def __init__(self, cue, name, time):
        self.cue = cue
        self.name = name
        self.time = time
        self.kind = None     # 'STOP', 'LOOP', 'JUMP', or None for "leave alone"
        self.label = ''      # LOOP: the name without +LOOP. JUMP: the target's name
        self.end = None      # LOOP: where the section ends (the next locator)
        self.end_cue = None  # LOOP: the locator there
        self.target = None   # JUMP: the Locator to go to
        self.problem = ''    # shown on the page when something is off


def classify(name):
    """Return (kind, label) for a locator name."""
    if name == STOP_NAME:
        return 'STOP', ''
    if JUMP_MARK in name:
        return 'JUMP', name.split(JUMP_MARK, 1)[1].strip()
    if name.endswith(LOOP_SUFFIX):
        return 'LOOP', name[:-len(LOOP_SUFFIX)].strip()
    return None, ''


def name_key(name):
    """Jump targets match ignoring upper/lower case and extra spaces."""
    return ' '.join(name.split()).casefold()


def read_locators(song):
    """All locators in time order, with loop ends and jump targets worked out."""
    locators = [Locator(cue, str(cue.name).strip(), float(cue.time))
                for cue in song.cue_points]
    locators.sort(key=lambda locator: locator.time)

    by_name = {}
    for locator in locators:
        locator.kind, locator.label = classify(locator.name)
        by_name.setdefault(name_key(locator.name), []).append(locator)

    for index, locator in enumerate(locators):
        if locator.kind == 'LOOP':
            later = [other for other in locators[index + 1:]
                     if other.time > locator.time + EPS]
            if later:
                locator.end = later[0].time
                locator.end_cue = later[0].cue
            else:
                locator.problem = 'there is no locator after it to loop to'
        elif locator.kind == 'JUMP':
            matches = by_name.get(name_key(locator.label), []) if locator.label else []
            if not locator.label:
                locator.problem = 'no locator name after >>>'
            elif not matches:
                locator.problem = 'no locator is named "%s"' % locator.label
            else:
                locator.target = matches[0]
                if len(matches) > 1:
                    locator.problem = '%d locators are named "%s": using the first' % (
                        len(matches), locator.label)
    return locators


def song_identity(song):
    """Tells Live Sets apart, so loading another Set starts us afresh."""
    return getattr(song, '_live_ptr', None)


class _Loop(object):
    """The +LOOP section being repeated."""

    def __init__(self, section, label):
        self.start = section.time
        self.end = section.end
        self.label = label
        self.start_cue = section.cue
        self.end_cue = section.end_cue
        self.leave_after_wrap = False


class _PendingJump(object):
    """A jump requested from Live ahead of time. Live does it at `time`."""

    def __init__(self, cue, time, target_cue, target_time, target_name, is_loop=False):
        self.cue = cue                # the locator at the jump point
        self.time = time
        self.target_cue = target_cue
        self.target_time = target_time
        self.target_name = target_name
        self.is_loop = is_loop        # a loop's jump back to its start


class ShowController(object):
    """All of Stage Helper's behaviour.

    While the browser page is open, call tick() every few milliseconds from
    Live's main thread. notify(text, level) receives the messages for the page.
    """

    def __init__(self, notify):
        self._notify = notify
        self.active = False
        self._clear()

    def _clear(self):
        self._song_id = None
        self._locators = []
        self._stops = []
        self._loops = []
        self._jumps = []
        self._next_refresh = 0.0
        self._bar = 4.0
        self._beat = 1.0
        self._quantization = ONE_BAR
        self._was_playing = False
        self._prev = None       # (song time, clock) at the previous tick
        self._loop = None       # _Loop while repeating a +LOOP section
        self._dismissed = None  # (start, end) of a loop section not to loop again yet
        self._pending = None    # _PendingJump
        self._park = None       # (STOP locator, until) to hold the play position

    # Switching on and off ---------------------------------------------------

    def activate(self, song, now):
        self._clear()
        self.active = True
        self._song_id = song_identity(song)
        self._refresh(song, now)
        self._notify('Switched on.', 'info')

    def deactivate(self, song, reason):
        """Give Live back as if we had never been there. Never stops playback."""
        try:
            self._cancel_pending_jump(song)
        finally:
            self.active = False
            self._clear()
        self._notify('Switched off (%s).' % reason, 'info')

    def _cancel_pending_jump(self, song):
        # Live keeps one pending jump. Asking it to "jump" to the locator at the
        # jump point itself replaces ours, so playback simply carries on there.
        # Within UNSURE of that point the jump may already have happened, and
        # a "cancel" would then send the playhead back, so leave it be.
        pending, self._pending = self._pending, None
        if pending is not None and song.is_playing and \
                song.current_song_time < pending.time - self._beats(song, UNSURE):
            pending.cue.jump()

    # Commands from the page -------------------------------------------------

    def leave_loop(self, song):
        loop = self._loop
        if loop is None:
            self._notify('Not looping right now.', 'info')
            return
        if loop.end - song.current_song_time < self._beats(song, UNSURE):
            # Pressed right at the loop end: the jump back may have happened
            # already. Leaving after it is always clean: one more pass.
            loop.leave_after_wrap = True
            self._notify('That was right at the end of "%s": leaving after one more pass.'
                         % loop.label, 'info')
            return
        self._leave(song, loop)

    def _leave(self, song, loop):
        self._dismissed = (loop.start, loop.end)
        self._end_loop(song, cancel=True)
        self._notify('Leaving loop "%s": carrying on.' % loop.label, 'info')

    @staticmethod
    def _beats(song, seconds):
        return seconds * song.tempo / 60.0

    # The tick -----------------------------------------------------------------

    def tick(self, song, now):
        if not self.active:
            return
        if song_identity(song) != self._song_id:
            # Another Live Set was loaded. Start afresh without touching it.
            self._clear()
            self._song_id = song_identity(song)
        if now >= self._next_refresh:
            self._refresh(song, now)

        if not song.is_playing:
            if self._was_playing:
                self._was_playing = False
                self._on_stopped()
            self._hold_park(song, now)
            return
        if not self._was_playing:
            self._was_playing = True
            self._prev = None
            self._park = None

        t = float(song.current_song_time)
        natural, prev_t = self._moved_naturally(t, now, song.tempo)
        self._prev = (t, now)
        if self._pending is not None:
            natural = self._follow_pending_jump(t, natural)
        natural, prev_t = self._update_loop(song, t, prev_t, natural)
        if natural and self._check_stops(song, prev_t, t, now):
            return
        self._check_jumps(song, prev_t, t, natural)

    def _refresh(self, song, now):
        self._next_refresh = now + REFRESH_EVERY
        denominator = song.signature_denominator
        self._beat = 4.0 / denominator
        self._bar = song.signature_numerator * self._beat
        self._quantization = int(song.clip_trigger_quantization)
        self._locators = read_locators(song)
        self._stops = [l for l in self._locators if l.kind == 'STOP']
        self._loops = [l for l in self._locators if l.kind == 'LOOP' and l.end is not None]
        self._jumps = [l for l in self._locators if l.kind == 'JUMP']

    def _moved_naturally(self, t, now, tempo):
        """Did the playhead just play on since the last tick (not jump)?

        Compares how far it moved with how far it should have moved at this
        tempo, so a slow tick (Live busy) still counts as playing on, while a
        jump or a click elsewhere in the arrangement does not.
        """
        if self._prev is None:
            return False, None
        prev_t, prev_now = self._prev
        moved = t - prev_t
        expected = (now - prev_now) * tempo / 60.0
        natural = moved > -0.01 and abs(moved - expected) <= SLACK + 0.1 * expected
        return natural, prev_t

    def _on_stopped(self):
        # Live drops a pending jump when it stops. Play again inside a +LOOP
        # section and it loops again.
        self._prev = None
        self._pending = None
        self._dismissed = None
        self._loop = None

    def _hold_park(self, song, now):
        """After a STOP, keep the play position on it so Play carries on from there."""
        if self._park is None:
            return
        stop, until = self._park
        if now > until:
            self._park = None
        elif abs(song.current_song_time - stop.time) > EPS:
            stop.cue.jump()  # while stopped, this just moves the play position

    # Jumps --------------------------------------------------------------------

    def _jump_lead(self, early=False):
        """How many beats before a jump point to ask Live for the jump.

        Live performs a requested jump at the next Global Quantization step.
        Asking within the last step before the jump point makes that step the
        jump point itself. None means no quantization: jump when we get there.

        A loop asks early in its last bar (early=True). Live keeps only one
        pending jump, so pressing ] after that replaces the jump back.
        """
        if self._quantization == NO_QUANTIZATION:
            return None
        step = NOTE_STEPS.get(self._quantization)
        if step is None:
            # 1 Bar (and 2/4/8 Bars, where asking in the last bar is never early).
            return self._bar - (LOOP_MARGIN if early else min(1.0, self._bar / 4.0))
        return step / 2.0

    def _check_jumps(self, song, prev_t, t, natural):
        pending = self._pending
        if pending is not None:
            if natural and t >= pending.time + MISSED_JUMP:
                # Live played on through the jump point without jumping.
                self._pending = None
                if not pending.is_loop:  # (a missed loop is handled in _update_loop)
                    self._late_jump(pending.target_cue, pending.target_name)
            return

        lead = self._jump_lead()
        for jump in self._jumps:
            if natural and prev_t < jump.time - EPS and t >= jump.time - EPS \
                    and not self._wraps_before(song, prev_t, jump.time):
                # We are past a >>> locator without having asked for the jump:
                # no quantization, or Live was too busy for us to ask in time.
                if jump.target is None:
                    self._notify('Jump at bar %s did nothing: %s.' % (
                        self.bar_label(jump.time), jump.problem), 'warn')
                elif lead is None:
                    jump.target.cue.jump()
                    self._notify('Jumped to "%s".' % jump.target.name, 'info')
                else:
                    self._late_jump(jump.target.cue, jump.target.name)
                return

        if lead is None:
            return
        loop = self._loop
        if loop is not None:
            # While looping, the jump that matters is the one back to the start.
            if loop.end - self._jump_lead(early=True) <= t < loop.end - EPS \
                    and not self._wraps_before(song, t, loop.end):
                loop.start_cue.jump()
                self._pending = _PendingJump(loop.end_cue, loop.end, loop.start_cue,
                                             loop.start, loop.label, is_loop=True)
            return
        for jump in self._jumps:
            if jump.target is not None and jump.time - lead <= t < jump.time - EPS \
                    and not self._wraps_before(song, t, jump.time):
                jump.target.cue.jump()
                self._pending = _PendingJump(jump.cue, jump.time, jump.target.cue,
                                             jump.target.time, jump.target.name)
                return

    def _late_jump(self, target_cue, target_name):
        target_cue.jump()
        self._notify('Jump to "%s" was late (Live was busy), so it happens at the next '
                     'bar line.' % target_name, 'warn')

    def _follow_pending_jump(self, t, natural):
        """Notice when Live has performed the jump we asked for."""
        if natural:
            return True  # still on the way (a miss is handled in _check_jumps)
        pending, self._pending = self._pending, None
        if not pending.is_loop and pending.target_time - EPS <= t <= pending.target_time + LANDING:
            self._notify('Jumped to "%s".' % pending.target_name, 'info')
        # Landing anywhere else means the playhead was moved by hand (a click,
        # [ or ]), and Live replaces a pending jump with that one.
        return False

    # Loops --------------------------------------------------------------------

    def _update_loop(self, song, t, prev_t, natural):
        """Follow the +LOOP section we're in. Returns (natural, prev_t) for the
        STOP and >>> checks that come after it."""
        loop = self._loop
        if loop is not None:
            asked = self._pending is not None and self._pending.is_loop
            if not self._is_loop_section(loop.start, loop.end):
                self._end_loop(song, cancel=True)
                self._notify('Loop "%s" ended because its locators changed.' % loop.label, 'warn')
            elif loop.start - EPS <= t < loop.end - EPS:
                if loop.leave_after_wrap and loop.end - t > self._beats(song, UNSURE):
                    self._leave(song, loop)
            elif natural and prev_t >= loop.start - EPS:
                # Played on through the loop end without jumping back.
                if asked and t < loop.end + MISSED_JUMP:
                    return natural, prev_t  # Live will report the jump any moment
                if asked:
                    # Live dropped our jump back for another one: ] pressed in
                    # the last bar. It lands exactly on the next locator, so it
                    # looks like simply playing on.
                    self._end_loop(song, cancel=False)
                    self._notify('Left loop "%s".' % loop.label, 'info')
                    return natural, prev_t
                if self._jump_lead() is None:
                    # No Global Quantization: jump back right now. Treat it as
                    # pending, as Live still reports the old position for a moment.
                    loop.start_cue.jump()
                    self._pending = _PendingJump(loop.end_cue, loop.end, loop.start_cue,
                                                 loop.start, loop.label, is_loop=True)
                    return False, prev_t
                self._end_loop(song, cancel=False)
                self._notify('Loop "%s" could not repeat in time (Live was busy): '
                             'carrying on.' % loop.label, 'warn')
                # A STOP or >>> at the loop end still counts as reached.
                return natural, min(prev_t, loop.end - 2 * EPS)
            else:
                # Moved out by hand: ], [ or a click. Live dropped our jump back.
                self._end_loop(song, cancel=False)
                self._notify('Left loop "%s".' % loop.label, 'info')

        if self._dismissed is not None:
            start, end = self._dismissed
            if t < start - EPS or t >= end - EPS:
                self._dismissed = None

        if self._loop is None:
            section = self._loop_section_at(t)
            if section is not None and self._dismissed != (section.time, section.end):
                self._loop = _Loop(section, self._loop_name(section))
                self._notify('Looping "%s" (bar %s to %s) until you press Leave loop.' % (
                    self._loop.label, self.bar_label(section.time),
                    self.bar_label(section.end)), 'info')
        return natural, prev_t

    def _end_loop(self, song, cancel):
        """Stop repeating. cancel: also take back a jump back already requested."""
        self._loop = None
        if self._pending is not None and self._pending.is_loop:
            if cancel:
                self._cancel_pending_jump(song)
            self._pending = None

    def _loop_section_at(self, t):
        for section in self._loops:
            if section.time - EPS <= t < section.end - EPS:
                return section
        return None

    def _is_loop_section(self, start, end):
        return any(abs(s.time - start) < EPS and abs(s.end - end) < EPS for s in self._loops)

    def _loop_name(self, section):
        return section.label or 'bar %s' % self.bar_label(section.time)

    def _wraps_before(self, song, t, position):
        """Will Live's own Loop send the playhead back before it reaches position?"""
        if not song.loop:
            return False
        end = song.loop_start + song.loop_length
        return t < end - EPS and position >= end - EPS

    # STOP ---------------------------------------------------------------------

    def _check_stops(self, song, prev_t, t, now):
        pending = self._pending
        for stop in self._stops:
            if pending is not None and abs(stop.time - pending.time) < EPS:
                continue  # a loop is about to jump back from here
            if prev_t < stop.time - EPS and t >= stop.time - EPS \
                    and not self._wraps_before(song, prev_t, stop.time):
                song.stop_playing()
                self._pending = None
                self._park = (stop, now + PARK_FOR)
                self._notify('Stopped at STOP (bar %s).' % self.bar_label(stop.time), 'info')
                return True
        return False

    # For the page -------------------------------------------------------------

    def bar_label(self, time):
        """Song position as a bar number ("17"), or bar.beat off the bar line."""
        bar = math.floor(time / self._bar + EPS)
        rest = time - bar * self._bar
        if rest < 0.001:
            return '%d' % (bar + 1)
        return '%d.%d' % (bar + 1, math.floor(rest / self._beat + EPS) + 1)

    def snapshot(self, song):
        quantization = self._quantization
        loop = self._loop
        return {
            'type': 'state',
            'playing': bool(song.is_playing),
            'quantization': (QUANTIZATION_NAMES[quantization]
                             if 0 <= quantization < len(QUANTIZATION_NAMES) else str(quantization)),
            'quantizationOk': quantization == ONE_BAR,
            'loop': None if loop is None else {
                'name': loop.label, 'from': self.bar_label(loop.start),
                'to': self.bar_label(loop.end)},
            'locators': [self._describe(l) for l in self._locators if l.kind],
        }

    def _describe(self, locator):
        works = True
        if locator.kind == 'STOP':
            does = 'Stops playback'
        elif locator.kind == 'LOOP':
            works = locator.end is not None
            does = 'Loops to bar %s' % self.bar_label(locator.end) if works else ''
        else:
            works = locator.target is not None
            does = ('Jumps to "%s" (bar %s)' % (locator.target.name,
                                                 self.bar_label(locator.target.time))
                    if works else '')
        return {'kind': locator.kind, 'name': locator.name, 'bar': self.bar_label(locator.time),
                'does': does, 'problem': locator.problem, 'works': works}
