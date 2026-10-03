"""Connects Stage Helper to Live (as a Control Surface script) and to the page.

Live runs this on its main thread. Every few milliseconds it:
  1. lets the web server handle any browser traffic,
  2. switches the show logic on or off depending on whether a page is open,
  3. runs the show logic, and every 100 ms sends the page the current state.
"""

import collections
import os
import time
import traceback

import Live
from _Framework.ControlSurface import ControlSurface

from .show import ShowController
from .web import WebServer

PORT = 8517
TIMER_MS = 10          # how often Live's fast timer calls us
STATE_EVERY = 0.1      # seconds between state updates to the page
HEARTBEAT_EVERY = 2.0  # send the state at least this often, so the page knows we're alive
PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'page.html')


class StageHelper(ControlSurface):

    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self._history = collections.deque(maxlen=40)
        self._error_counts = {}
        self._server = WebServer(PORT, PAGE_PATH, self._log, commands=('leave-loop',))
        self._show = ShowController(self._message)
        self._last_tick = 0.0
        self._next_state = 0.0
        self._last_state = None
        self._last_state_sent = 0.0
        self._timer = None
        try:
            # Undocumented but long-standing: a repeating timer on Live's main
            # thread, much finer than the 100 ms update_display() below.
            self._timer = Live.Base.Timer(callback=self._on_timer, interval=TIMER_MS, repeat=True)
            self._timer.start()
        except Exception:
            self._timer = None
            self._report('timer')
            self._log('Fast timer unavailable: checking 10 times a second instead.')
        try:
            self._server.poll(time.perf_counter())  # start listening straight away
        except Exception:
            self._report('web')
        self._log('Loaded. Open http://localhost:%d to switch Stage Helper on.' % self._server.port)

    def disconnect(self):
        try:
            if self._timer is not None:
                self._timer.stop()
        except Exception:
            self._report('timer')
        try:
            if self._show.active:
                self._show.deactivate(self.song(), 'Live closed the script')
        except Exception:
            self._report('show')
        try:
            self._server.close()
        except Exception:
            self._report('web')
        ControlSurface.disconnect(self)

    def update_display(self):
        ControlSurface.update_display(self)
        # Live calls this every 100 ms. Normally the fast timer does the work.
        # This is the backup in case that timer isn't running.
        if time.perf_counter() - self._last_tick > 0.25:
            self._tick()

    def _on_timer(self):
        self._tick()

    def _tick(self):
        now = time.perf_counter()
        self._last_tick = now
        commands, new_streams = (), ()
        try:
            commands, new_streams = self._server.poll(now)
        except Exception:
            self._report('web')

        try:
            song = self.song()
            page_open = self._server.stream_count() > 0
            if page_open and not self._show.active:
                self._show.activate(song, now)
            elif self._show.active and not page_open:
                self._show.deactivate(song, 'browser page closed')
            if self._show.active:
                for command in commands:
                    if command == 'leave-loop':
                        self._show.leave_loop(song)
                self._show.tick(song, now)
        except Exception:
            self._report('show')

        try:
            if self._show.active:
                self._send_state(self.song(), now, new_streams)
        except Exception:
            self._report('page')

    def _send_state(self, song, now, new_streams):
        if new_streams:
            self._server.send({'type': 'history', 'messages': list(self._history)}, new_streams)
        elif now < self._next_state:
            return
        self._next_state = now + STATE_EVERY
        state = self._show.snapshot(song)
        if new_streams or state != self._last_state or \
                now - self._last_state_sent >= HEARTBEAT_EVERY:
            self._server.send(state)
            self._last_state = state
            self._last_state_sent = now

    def _message(self, text, level):
        entry = {'type': 'message', 'time': time.strftime('%H:%M:%S'),
                 'level': level, 'text': text}
        self._history.append(entry)
        self._log(text)
        try:
            self._server.send(entry)
            self.show_message('Stage Helper: ' + text)  # Live's status bar
        except Exception:
            self._report('message')

    def _log(self, text):
        self.log_message(text)  # goes to Live's Log.txt

    def _report(self, where):
        """Log an error, without flooding Log.txt if it repeats every tick."""
        details = traceback.format_exc()
        key = (where, details.strip().splitlines()[-1])
        count = self._error_counts.get(key, 0) + 1
        self._error_counts[key] = count
        if count <= 3 or count % 1000 == 0:
            self.log_message('Error in %s (seen %d times):\n%s' % (where, count, details))
