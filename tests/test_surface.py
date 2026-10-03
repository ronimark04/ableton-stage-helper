"""Loads Stage Helper the way Live does (with stand-ins for Live's modules) and
checks the whole chain: browser page <-> web server <-> show logic <-> song."""

import json
import socket
import time
import unittest

from fake_live import FakeCInstance, FakeSong
import Live
import StageHelper
from StageHelper import surface


class Browser(object):
    """A pretend browser tab holding the page's event stream open."""

    def __init__(self, port):
        self.port = port
        self.sock = socket.create_connection(('127.0.0.1', port))
        self.sock.sendall(('GET /events HTTP/1.1\r\nHost: localhost:%d\r\n\r\n' % port).encode())
        self.sock.setblocking(False)
        self.buffer = b''

    def messages(self):
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                self.buffer += chunk
        except BlockingIOError:
            pass
        found = [json.loads(line[6:].decode('utf-8'))
                 for line in self.buffer.split(b'\n') if line.startswith(b'data: ')]
        return found

    def press_leave_loop(self):
        button = socket.create_connection(('127.0.0.1', self.port))
        button.sendall(('POST /api/leave-loop HTTP/1.1\r\nHost: localhost:%d\r\n'
                        'Origin: http://localhost:%d\r\nContent-Length: 0\r\n\r\n'
                        % (self.port, self.port)).encode())
        return button

    def close(self):
        self.sock.close()


class SurfaceTests(unittest.TestCase):

    def setUp(self):
        surface.PORT = 0  # any free port
        Live.Base.Timer.instances = []
        self.song = FakeSong([('Intro', 0), ('Vamp +LOOP', 8), ('Verse', 16)])
        self.c_instance = FakeCInstance(self.song)
        self.script = StageHelper.create_instance(self.c_instance)
        self.port = self.script._server.port

    def tearDown(self):
        if not self.c_instance.disconnected:
            self.script.disconnect()

    def run_live(self, seconds):
        """Live playing in real time: the engine runs, the fast timer fires."""
        last = time.perf_counter()
        end = last + seconds
        while last < end:
            time.sleep(0.005)
            now = time.perf_counter()
            self.song.advance(now - last)
            last = now
            Live.Base.Timer.fire_all()

    def test_timer_runs_and_page_is_served_from_the_start(self):
        (timer,) = Live.Base.Timer.instances
        self.assertTrue(timer.running)
        self.assertEqual(timer.interval, 10)
        self.run_live(0.05)
        self.assertNotEqual(self.port, 0)
        self.assertTrue(any('Open http://localhost' in line for line in self.c_instance.log))

    def looping(self):
        return self.script._show.snapshot(self.song)['loop'] is not None

    def test_whole_show_chain(self):
        self.song.start_marker = 6.0
        self.song.start_playing()
        self.run_live(0.5)
        self.assertFalse(self.script._show.active)  # page not open: Stage Helper is off

        browser = Browser(self.port)
        self.run_live(1.5)                # reaches the +LOOP section
        self.assertTrue(self.looping())
        kinds = [m['type'] for m in browser.messages()]
        self.assertIn('history', kinds)
        state = [m for m in browser.messages() if m['type'] == 'state'][-1]
        self.assertEqual(state['loop'], {'name': 'Vamp', 'from': '3', 'to': '5'})
        self.assertIn('Looping "Vamp"', self.c_instance.status)  # Live's status bar

        button = browser.press_leave_loop()
        self.run_live(0.1)
        self.assertFalse(self.looping())
        button.close()

        self.run_live(0.5)
        browser.close()                   # closing the page
        self.run_live(0.1)
        self.assertFalse(self.script._show.active)
        self.assertEqual('Stage Helper: Switched off (browser page closed).', self.c_instance.status)
        self.assertTrue(self.song.is_playing)

    def test_closing_the_page_mid_loop_carries_on(self):
        self.song.start_marker = 8.0
        self.song.start_playing()
        browser = Browser(self.port)
        self.run_live(1.0)
        self.assertTrue(self.looping())
        browser.close()
        self.run_live(4.0)                # past the loop end at beat 16
        self.assertEqual(self.song.jumps_to(8.0), [])
        self.assertGreater(self.song.engine_time, 16.0)
        self.assertTrue(self.song.is_playing)

    def test_errors_are_logged_not_raised(self):
        browser = Browser(self.port)
        self.run_live(0.2)

        class Broken(object):
            def __get__(self, obj, owner):
                raise RuntimeError('simulated Live API failure')

            def __set__(self, obj, value):
                pass

        FakeSong.cue_points = Broken()
        try:
            self.song.start_playing()
            self.run_live(0.5)  # would raise if errors escaped into Live
        finally:
            del FakeSong.cue_points
        errors = [line for line in self.c_instance.log if line.startswith('Error in show')]
        self.assertTrue(errors)
        self.assertLessEqual(len(errors), 3)  # not flooding the log
        browser.close()

    def test_backup_timer_when_the_fast_timer_is_missing(self):
        Live.Base.Timer.instances[0].stop()
        browser = Browser(self.port)
        time.sleep(0.3)
        self.script.update_display()
        self.assertTrue(self.script._show.active)
        browser.close()

    def test_disconnect_cleans_up(self):
        self.song.start_marker = 8.0
        self.song.start_playing()
        browser = Browser(self.port)
        self.run_live(0.5)
        self.assertTrue(self.looping())
        self.script.disconnect()
        self.assertFalse(Live.Base.Timer.instances[0].running)
        self.assertFalse(self.script._show.active)
        self.assertTrue(self.c_instance.disconnected)
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', self.port), timeout=0.5).close()
        browser.close()


if __name__ == '__main__':
    unittest.main()
