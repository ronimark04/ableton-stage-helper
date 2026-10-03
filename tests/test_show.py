"""Scenario tests for the show logic against the simulated Live in fake_live.py."""

import random
import unittest

from fake_live import FakeSong
from StageHelper.show import ShowController

BAR = 4.0  # beats in a 4/4 bar


class Rig(object):
    """A simulated Live Set with Stage Helper ticking about every 10 ms."""

    def __init__(self, cues, tempo=120.0, signature=(4, 4), quantization=4,
                 seed=1, activate=True):
        self.song = FakeSong(cues, tempo, signature, quantization)
        self.messages = []
        self.show = ShowController(lambda text, level: self.messages.append((level, text)))
        self.clock = 0.0
        self.next_tick = 0.0
        self.stalls = []  # (from, until): Live too busy to call us
        self._random = random.Random(seed)
        if activate:
            self.show.activate(self.song, self.clock)

    def play_from(self, time):
        self.song.start_marker = float(time)
        self.song.start_playing()

    def run(self, seconds, until=None):
        end = self.clock + seconds
        while self.clock < end - 1e-9:
            self.song.advance(0.001)
            self.clock += 0.001
            stalled = any(a <= self.clock < b for a, b in self.stalls)
            if self.clock >= self.next_tick and not stalled:
                self.show.tick(self.song, self.clock)
                self.next_tick = self.clock + 0.010 + self._random.uniform(-0.004, 0.004)
            if until is not None and until():
                return True
        return False

    def run_until_beat(self, beat, limit=120.0):
        assert self.run(limit, lambda: self.song.engine_time >= beat), 'never reached %s' % beat

    def press_leave(self):
        # As on the real page: the command is handled at the start of a tick.
        self.show.leave_loop(self.song)
        self.show.tick(self.song, self.clock)

    def seconds(self, beats):
        return beats * 60.0 / self.song.tempo

    def texts(self, level=None):
        return [t for l, t in self.messages if level is None or l == level]


class StopTests(unittest.TestCase):

    def test_stops_just_after_the_stop_locator(self):
        rig = Rig([('Song A', 0), ('STOP', 32), ('Song B', 36)])
        rig.play_from(0)
        rig.run(rig.seconds(40))
        (stop,) = rig.song.kinds('stop')
        self.assertGreaterEqual(stop[1], 32.0)
        self.assertLess(stop[1], 32.0 + 0.040 * rig.song.tempo / 60)  # under 40 ms late
        self.assertFalse(rig.song.is_playing)
        self.assertEqual(rig.song.start_marker, 32.0)  # Play carries on from the STOP
        self.assertIn('Stopped at STOP (bar 9).', rig.texts())

    def test_play_after_a_stop_continues_into_the_next_song(self):
        rig = Rig([('Song A', 0), ('STOP', 32), ('Song B', 36)])
        rig.play_from(28)
        rig.run(rig.seconds(6))
        rig.song.start_playing()
        rig.run(rig.seconds(8))
        self.assertEqual(len(rig.song.kinds('stop')), 1)
        self.assertTrue(rig.song.is_playing)
        self.assertGreater(rig.song.engine_time, 39.0)

    def test_starting_after_a_stop_does_not_stop(self):
        rig = Rig([('STOP', 32)])
        rig.play_from(32)
        rig.run(rig.seconds(4))
        self.assertTrue(rig.song.is_playing)

    def test_clicking_past_a_stop_does_not_stop(self):
        rig = Rig([('STOP', 32)])
        rig.play_from(24)
        rig.run(rig.seconds(4))
        rig.song.click(40)
        rig.run(rig.seconds(4))
        self.assertTrue(rig.song.is_playing)

    def test_stop_still_happens_when_live_was_busy(self):
        rig = Rig([('STOP', 32)])
        rig.play_from(24)
        rig.stalls.append((rig.seconds(7.5), rig.seconds(7.5) + 1.2))  # 1.2 s, across bar 9
        rig.run(rig.seconds(16))
        (stop,) = rig.song.kinds('stop')
        self.assertGreaterEqual(stop[1], 32.0)
        self.assertEqual(rig.song.start_marker, 32.0)

    def test_lowercase_stop_is_left_alone(self):
        rig = Rig([('Stop', 8), ('stop', 12), ('STOP me', 16)])
        rig.play_from(0)
        rig.run(rig.seconds(20))
        self.assertEqual(rig.song.kinds('stop'), [])

    def test_lives_own_loop_wraps_before_a_stop_at_its_end(self):
        rig = Rig([('A', 0), ('STOP', 24)])
        rig.song.loop, rig.song.loop_start, rig.song.loop_length = True, 0.0, 24.0
        rig.play_from(0)
        rig.run(rig.seconds(30))
        self.assertEqual(rig.song.kinds('wrap'), [('wrap', 24.0, 0.0)])
        self.assertEqual(rig.song.kinds('stop'), [])


class LoopTests(unittest.TestCase):
    CUES = [('Intro', 0), ('Vamp +LOOP', 8), ('Verse', 16), ('End', 32)]

    def test_loops_until_leave_then_carries_on(self):
        rig = Rig(self.CUES)
        rig.song.loop_start, rig.song.loop_length = 100.0, 4.0  # the user's own loop brace
        rig.play_from(0)
        rig.run(rig.seconds(8 + 3 * 8 + 2))  # three passes and a bit, now at beat 10
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 8.0)] * 3)
        # Live's own Loop switch and brace are never touched.
        self.assertFalse(rig.song.loop)
        self.assertEqual((rig.song.loop_start, rig.song.loop_length), (100.0, 4.0))
        self.assertEqual(rig.show.snapshot(rig.song)['loop'], {'name': 'Vamp', 'from': '3', 'to': '5'})

        rig.press_leave()
        rig.run(rig.seconds(10))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 8.0)] * 3)  # no more passes
        self.assertGreater(rig.song.engine_time, 18.0)
        self.assertTrue(rig.song.is_playing)
        self.assertIsNone(rig.show.snapshot(rig.song)['loop'])
        self.assertIn('Looping "Vamp" (bar 3 to 5) until you press Leave loop.', rig.texts())
        self.assertIn('Leaving loop "Vamp": carrying on.', rig.texts())

    def test_leave_in_the_last_bar_takes_back_the_jump_back(self):
        rig = Rig(self.CUES)
        rig.play_from(8)
        rig.run_until_beat(14)
        self.assertIsNotNone(rig.song.pending)  # the jump back is already asked for
        rig.press_leave()
        rig.run(rig.seconds(8))
        self.assertEqual(rig.song.jumps_to(8.0), [])
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 16.0)])  # plays straight on
        self.assertGreater(rig.song.engine_time, 18.0)

    def test_next_locator_key_moves_on(self):
        for press_at, lands_at in ((9.0, 12.0), (12.5, 16.0), (15.5, 16.0), (15.95, 16.0)):
            rig = Rig(self.CUES)
            rig.play_from(8)
            rig.run_until_beat(press_at)
            rig.song.jump_to_next_cue()  # the ] key
            rig.run(rig.seconds(8))
            self.assertEqual(rig.song.jumps_to(8.0), [], press_at)
            self.assertEqual(rig.song.jumps_to(16.0), [('jump', lands_at, 16.0)], press_at)
            self.assertIsNone(rig.show.snapshot(rig.song)['loop'], press_at)
            self.assertGreater(rig.song.engine_time, 18.0, press_at)
            self.assertIn('Left loop "Vamp".', rig.texts(), press_at)
            self.assertEqual(rig.texts('warn'), [], press_at)

    def test_previous_locator_key_restarts_and_keeps_looping(self):
        for press_at, lands_at in ((10.0, 12.0), (13.0, 16.0)):
            rig = Rig(self.CUES)
            rig.play_from(8)
            rig.run_until_beat(press_at)
            rig.song.jump_to_prev_cue()  # the [ key
            rig.run(rig.seconds(11))
            self.assertEqual(rig.song.kinds('jump')[:2],
                             [('jump', lands_at, 8.0), ('jump', 16.0, 8.0)], press_at)
            self.assertIsNotNone(rig.show.snapshot(rig.song)['loop'], press_at)

    def test_starting_inside_a_loop_section_loops_it(self):
        rig = Rig(self.CUES)
        rig.play_from(12)
        rig.run(rig.seconds(10))
        self.assertEqual(rig.song.kinds('jump')[0], ('jump', 16.0, 8.0))

    def test_stopping_ends_the_loop_and_playing_again_loops(self):
        rig = Rig(self.CUES)
        rig.play_from(8)
        rig.run(rig.seconds(2))
        rig.song.stop_playing()
        rig.run(0.1)
        self.assertIsNone(rig.show.snapshot(rig.song)['loop'])
        rig.song.start_playing()  # from bar 3 again
        rig.run(rig.seconds(10))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 8.0)])

    def test_loops_even_when_lives_loop_switch_is_on(self):
        rig = Rig(self.CUES)
        rig.song.loop, rig.song.loop_start, rig.song.loop_length = True, 64.0, 8.0
        rig.play_from(0)
        rig.run(rig.seconds(8 + 2 * 8 + 1))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 8.0)] * 2)
        self.assertEqual((rig.song.loop, rig.song.loop_start, rig.song.loop_length),
                         (True, 64.0, 8.0))
        self.assertEqual(rig.texts('warn'), [])

    def test_one_bar_loop(self):
        rig = Rig([('Vamp +LOOP', 8), ('Verse', 12)])
        rig.play_from(8)
        rig.run(rig.seconds(5 * 4 + 1))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 12.0, 8.0)] * 5)

    def test_stop_at_the_loop_end_waits_for_leave(self):
        rig = Rig([('A', 0), ('Vamp +LOOP', 8), ('STOP', 16), ('B', 20)])
        rig.play_from(8)
        rig.run(rig.seconds(2 * 8 + 2))
        self.assertEqual(rig.song.kinds('stop'), [])
        rig.press_leave()
        rig.run(rig.seconds(8))
        (stop,) = rig.song.kinds('stop')
        self.assertGreaterEqual(stop[1], 16.0)
        self.assertLess(stop[1], 16.1)

    def test_last_locator_loop_has_nothing_to_loop(self):
        rig = Rig([('Song', 0), ('Outro +LOOP', 8)])
        rig.play_from(6)
        rig.run(rig.seconds(10))
        self.assertEqual(rig.song.kinds('jump'), [])
        state = rig.show.snapshot(rig.song)
        self.assertFalse(state['locators'][0]['works'])

    def test_back_to_back_loops(self):
        rig = Rig([('A +LOOP', 0), ('B +LOOP', 8), ('C', 16)])
        rig.play_from(0)
        rig.run(rig.seconds(10))
        rig.press_leave()
        rig.run(rig.seconds(16))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 8.0, 0.0), ('jump', 16.0, 8.0)])

    def test_no_quantization_jumps_back_when_reached(self):
        rig = Rig(self.CUES, quantization=0)
        rig.play_from(8)
        rig.run(rig.seconds(2 * 8 + 1))
        jumps = rig.song.kinds('jump')
        self.assertEqual(len(jumps), 2)
        for _, at, to in jumps:
            self.assertEqual(to, 8.0)
            self.assertGreaterEqual(at, 16.0)
            self.assertLess(at, 16.1)

    def test_loop_survives_live_being_busy_for_a_second(self):
        rig = Rig(self.CUES)
        rig.play_from(8)
        start = rig.seconds(11.5 - 8)
        rig.stalls.append((start, start + 1.0))  # until beat 13.5
        rig.run(rig.seconds(10))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 8.0)])

    def test_loop_plays_on_when_live_was_busy_for_its_whole_last_bar(self):
        rig = Rig(self.CUES)
        rig.play_from(8)
        start = rig.seconds(11.9 - 8)
        rig.stalls.append((start, start + rig.seconds(4.6)))  # until beat 16.5
        rig.run(rig.seconds(14))
        self.assertEqual(rig.song.kinds('jump'), [])
        self.assertGreater(rig.song.engine_time, 20.0)
        self.assertTrue(any('could not repeat in time' in t for t in rig.texts('warn')))


class JumpTests(unittest.TestCase):

    def test_forward_jump_lands_exactly(self):
        rig = Rig([('Song A', 0), ('>>> Song B', 16), ('Filler', 20), ('Song B', 64)])
        rig.play_from(0)
        rig.run(rig.seconds(24))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 64.0)])
        self.assertGreater(rig.song.engine_time, 64.0)
        self.assertIn('Jumped to "Song B".', rig.texts())

    def test_backward_jump_lands_exactly(self):
        rig = Rig([('Song B', 0), ('Song A', 32), ('>>> Song B', 48)])
        rig.play_from(32)
        rig.run(rig.seconds(20))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 48.0, 0.0)])

    def test_text_before_the_arrows_is_just_a_note(self):
        rig = Rig([('Song A', 0), ('A >>> B', 16), ('B', 64)])
        rig.play_from(0)
        rig.run(rig.seconds(20))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 64.0)])
        self.assertIn('Jumped to "B".', rig.texts())

    def test_arrows_with_no_name_after_them_do_nothing(self):
        rig = Rig([('Song A', 0), ('Song A >>>', 16), ('Song B', 64)])
        rig.play_from(0)
        rig.run(rig.seconds(20))
        self.assertEqual(rig.song.kinds('jump'), [])
        self.assertIn('Jump at bar 5 did nothing: no locator name after >>>.', rig.texts('warn'))

    def test_target_names_ignore_case_and_spaces(self):
        rig = Rig([('>>>  song   b ', 8), ('Song B', 40)])
        rig.play_from(0)
        rig.run(rig.seconds(12))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 8.0, 40.0)])

    def test_missing_target_does_nothing_and_says_so(self):
        rig = Rig([('Song A', 0), ('>>> Song X', 16), ('Song B', 64)])
        rig.play_from(0)
        rig.run(rig.seconds(24))
        self.assertEqual(rig.song.kinds('jump'), [])
        self.assertTrue(rig.song.is_playing)
        self.assertIn('Jump at bar 5 did nothing: no locator is named "Song X".',
                      rig.texts('warn'))

    def test_jump_after_a_loop_waits_for_leave(self):
        rig = Rig([('Song A', 0), ('Outro +LOOP', 8), ('>>> Song B', 16), ('Song B', 64)])
        rig.play_from(0)
        rig.run(rig.seconds(8 + 2 * 8 + 1))  # two passes, now at beat 9
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 8.0)] * 2)
        rig.press_leave()
        rig.run(rig.seconds(10))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 8.0)] * 2 + [('jump', 16.0, 64.0)])

    def test_leaving_a_loop_at_the_last_moment_still_jumps_cleanly(self):
        for before_end in (3.5, 2.0, 0.5, 0.25, 0.1, 0.05, 0.02, 0.005):
            rig = Rig([('Song A', 0), ('Outro +LOOP', 8), ('>>> Song B', 16), ('Song B', 64)])
            rig.play_from(8)
            rig.run_until_beat(16 - before_end)
            rig.press_leave()
            rig.run(rig.seconds(20))
            self.assertEqual(rig.song.jumps_to(64.0), [('jump', 16.0, 64.0)], before_end)
            self.assertLessEqual(len(rig.song.jumps_to(8.0)), 1, before_end)  # one more pass at most
            self.assertEqual(len(rig.song.kinds('jump')), 1 + len(rig.song.jumps_to(8.0)), before_end)

    def test_leaving_just_after_the_loop_jumped_back_never_jumps_early(self):
        for after_jump in (0.0, 0.002, 0.005, 0.01, 0.02, 0.05, 0.2):
            rig = Rig([('Song A', 0), ('Outro +LOOP', 8), ('>>> Song B', 16), ('Song B', 64)])
            rig.play_from(8)
            assert rig.run(30, lambda: rig.song.kinds('jump'))
            rig.run(after_jump)
            rig.press_leave()
            rig.run(rig.seconds(20))
            # Live still reports "just before the end" for a moment after the
            # jump back. Asking for the next jump then would make it happen at bar 4.
            self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 8.0), ('jump', 16.0, 64.0)],
                             after_jump)

    def test_next_locator_key_in_a_loop_lands_on_a_jump_locator_and_plays_on(self):
        # A locator acts when playback runs into it, not when you jump onto it.
        rig = Rig([('Song A', 0), ('Outro +LOOP', 8), ('>>> Song B', 16), ('Song B', 64)])
        rig.play_from(8)
        rig.run_until_beat(10)
        rig.song.jump_to_next_cue()
        rig.run(rig.seconds(8))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 12.0, 16.0)])
        self.assertGreater(rig.song.engine_time, 18.0)

    def test_jump_into_a_loop_section_loops_there(self):
        rig = Rig([('A', 0), ('>>> B Intro +LOOP', 8), ('B Intro +LOOP', 32), ('B Verse', 40)])
        rig.play_from(0)
        rig.run(rig.seconds(8 + 12))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 8.0, 32.0), ('jump', 40.0, 32.0)])

    def test_jump_survives_live_being_busy_for_a_second(self):
        rig = Rig([('A', 0), ('>>> B', 16), ('B', 64)])
        rig.play_from(0)
        stall_start = rig.seconds(16 - 3.6)
        rig.stalls.append((stall_start, stall_start + 1.0))
        rig.run(rig.seconds(24))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 64.0)])

    def test_jump_after_a_long_stall_comes_a_bar_late_with_a_warning(self):
        rig = Rig([('A', 0), ('>>> B', 16), ('B', 64)])
        rig.play_from(0)
        stall_start = rig.seconds(16 - 3.8)
        rig.stalls.append((stall_start, stall_start + 2.0))
        rig.run(rig.seconds(26))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 20.0, 64.0)])
        self.assertTrue(any('late' in t for t in rig.texts('warn')))

    def test_no_quantization_jumps_when_reached(self):
        rig = Rig([('A', 0), ('>>> B', 16), ('B', 64)], quantization=0)
        rig.play_from(0)
        rig.run(rig.seconds(20))
        (jump,) = rig.song.kinds('jump')
        self.assertGreaterEqual(jump[1], 16.0)
        self.assertLess(jump[1], 16.1)
        self.assertEqual(jump[2], 64.0)
        self.assertFalse(rig.show.snapshot(rig.song)['quantizationOk'])

    def test_quarter_note_quantization_still_lands_exactly(self):
        rig = Rig([('A', 0), ('>>> B', 16), ('B', 64)], quantization=7)
        rig.play_from(0)
        rig.run(rig.seconds(20))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 64.0)])

    def test_three_four_at_a_slow_tempo(self):
        rig = Rig([('A', 0), ('>>> B', 12), ('B', 30)], tempo=72, signature=(3, 4))
        rig.play_from(0)
        rig.run(rig.seconds(15))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 12.0, 30.0)])

    def test_fast_tempo(self):
        rig = Rig([('A', 0), ('>>> B', 16), ('B', 64)], tempo=200)
        rig.play_from(0)
        rig.run(rig.seconds(20))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 64.0)])

    def test_duplicate_target_uses_the_first_and_warns(self):
        rig = Rig([('Song B', 0), ('>>> Song B', 16), ('Song B', 40)])
        rig.play_from(8)
        rig.run(rig.seconds(10))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 0.0)])
        jump_row = [l for l in rig.show.snapshot(rig.song)['locators'] if l['kind'] == 'JUMP'][0]
        self.assertTrue(jump_row['works'])
        self.assertIn('2 locators', jump_row['problem'])

    def test_rename_to_stop_keeps_songs_separate(self):
        rig = Rig([('A', 0), ('>>> B', 16), ('B', 64)])
        rig.song.cue('>>> B').name = 'STOP'
        rig.play_from(0)
        rig.run(rig.seconds(20))
        self.assertEqual(rig.song.kinds('jump'), [])
        self.assertEqual(len(rig.song.kinds('stop')), 1)


class SwitchingOffTests(unittest.TestCase):
    CUES = [('Intro', 0), ('Vamp +LOOP', 8), ('Verse', 16)]

    def test_closing_the_page_mid_loop_carries_on(self):
        for close_at in (10.0, 14.0):  # before / after the jump back is asked for
            rig = Rig(self.CUES)
            rig.song.loop_start, rig.song.loop_length = 0.0, 64.0
            rig.play_from(8)
            rig.run_until_beat(close_at)
            rig.show.deactivate(rig.song, 'browser page closed')
            rig.run(rig.seconds(8))
            self.assertTrue(rig.song.is_playing, close_at)
            self.assertEqual(rig.song.jumps_to(8.0), [], close_at)
            self.assertGreater(rig.song.engine_time, 18.0, close_at)
            self.assertEqual((rig.song.loop, rig.song.loop_start, rig.song.loop_length),
                             (False, 0.0, 64.0), close_at)

    def test_closing_the_page_just_after_a_jump_back_keeps_it(self):
        for after_jump in (0.0, 0.003, 0.008, 0.015, 0.05):
            rig = Rig(self.CUES)
            rig.play_from(8)
            assert rig.run(20, lambda: rig.song.kinds('jump'))
            rig.run(after_jump)
            rig.show.deactivate(rig.song, 'browser page closed')
            rig.run(rig.seconds(10))
            self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 8.0)], after_jump)

    def test_closing_the_page_just_after_a_jump_keeps_it(self):
        for after_jump in (0.0, 0.003, 0.008, 0.015, 0.05):
            rig = Rig([('A', 0), ('>>> B', 16), ('B', 64)])
            rig.play_from(0)
            assert rig.run(20, lambda: rig.song.kinds('jump'))
            rig.run(after_jump)
            rig.show.deactivate(rig.song, 'browser page closed')
            rig.run(rig.seconds(8))
            self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 64.0)], after_jump)

    def test_closing_the_page_with_a_jump_pending_cancels_it(self):
        rig = Rig([('A', 0), ('>>> B', 16), ('B', 64)])
        rig.play_from(0)
        rig.run_until_beat(15)  # the jump has been asked for by now
        self.assertIsNotNone(rig.song.pending)
        rig.show.deactivate(rig.song, 'browser page closed')
        rig.run(rig.seconds(8))
        self.assertTrue(all(to != 64.0 for _, _, to in rig.song.kinds('jump')))
        self.assertTrue(rig.song.is_playing)
        self.assertGreater(rig.song.engine_time, 20.0)

    def test_while_off_nothing_happens(self):
        rig = Rig([('Vamp +LOOP', 0), ('STOP', 8), ('>>> B', 12), ('B', 40)], activate=False)
        rig.play_from(0)
        rig.run(rig.seconds(20))
        self.assertEqual(rig.song.events, [('start', 0.0)])
        self.assertFalse(rig.song.loop)

    def test_switching_on_mid_section_loops_it(self):
        rig = Rig(self.CUES, activate=False)
        rig.play_from(0)
        rig.run(rig.seconds(10))
        rig.show.activate(rig.song, rig.clock)
        rig.run(rig.seconds(8))
        self.assertEqual(rig.song.kinds('jump'), [('jump', 16.0, 8.0)])


class LiveEditingTests(unittest.TestCase):

    def test_renaming_moving_and_adding_locators_while_playing(self):
        rig = Rig([('A', 0), ('Verse', 16), ('Chorus', 24)])
        rig.play_from(0)
        rig.run(rig.seconds(4))
        rig.song.cue('Verse').name = 'Verse +LOOP'      # rename
        rig.run(rig.seconds(22))                        # to beat 26: one jump back
        self.assertEqual(rig.song.kinds('jump'), [('jump', 24.0, 16.0)])
        rig.song.cue('Chorus').time = 20.0              # move the loop end while looping
        rig.run(rig.seconds(5))                         # 18 -> 20, back, -> 19
        self.assertEqual(rig.song.kinds('jump')[-1], ('jump', 20.0, 16.0))
        rig.press_leave()
        rig.song.add_cue('STOP', 28)                    # add
        rig.run(rig.seconds(10))
        (stop,) = rig.song.kinds('stop')
        self.assertGreaterEqual(stop[1], 28.0)
        self.assertEqual(len(rig.song.jumps_to(16.0)), 2)

    def test_renaming_the_loop_locator_while_looping_lets_go(self):
        rig = Rig([('Vamp +LOOP', 0), ('Verse', 8)])
        rig.play_from(0)
        rig.run_until_beat(6)  # the jump back is asked for by now
        rig.song.cue('Vamp +LOOP').name = 'Vamp'
        rig.run(rig.seconds(8))
        self.assertEqual(rig.song.jumps_to(0.0), [])
        self.assertIsNone(rig.show.snapshot(rig.song)['loop'])
        self.assertGreater(rig.song.engine_time, 10.0)

    def test_clicking_elsewhere_while_looping_lets_go(self):
        rig = Rig([('Vamp +LOOP', 8), ('Verse', 16), ('Other', 40)])
        rig.play_from(8)
        rig.run(rig.seconds(2))
        rig.song.click(40)
        rig.run(rig.seconds(1))
        self.assertIsNone(rig.show.snapshot(rig.song)['loop'])
        self.assertEqual(rig.song.jumps_to(8.0), [])

    def test_loading_another_set_starts_afresh(self):
        rig = Rig([('Vamp +LOOP', 0), ('Verse', 8)])
        rig.play_from(0)
        rig.run(rig.seconds(2))
        other = FakeSong([('Song', 0)])
        other.start_playing()
        rig.show.tick(other, rig.clock + 0.01)
        self.assertIsNone(rig.show.snapshot(other)['loop'])
        self.assertIsNone(other.pending)


class PageStateTests(unittest.TestCase):

    def test_snapshot_lists_only_what_stage_helper_reacts_to(self):
        rig = Rig([('Song A', 0), ('Chorus +LOOP', 64), ('Bridge', 96),
                   ('>>> Song B', 128), ('Song A >>> Nowhere', 132), ('STOP', 136), ('Song B', 140)])
        state = rig.show.snapshot(rig.song)
        rows = [(l['kind'], l['name'], l['bar'], l['works']) for l in state['locators']]
        self.assertEqual(rows, [('LOOP', 'Chorus +LOOP', '17', True),
                                ('JUMP', '>>> Song B', '33', True),
                                ('JUMP', 'Song A >>> Nowhere', '34', False),
                                ('STOP', 'STOP', '35', True)])
        self.assertEqual(state['locators'][0]['does'], 'Loops to bar 25')
        self.assertEqual(state['locators'][1]['does'], 'Jumps to "Song B" (bar 36)')
        self.assertTrue(state['quantizationOk'])
        self.assertIsNone(state['loop'])


if __name__ == '__main__':
    unittest.main()
