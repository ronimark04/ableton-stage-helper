"""Try the Stage Helper page without Live: runs the real add-on against a
simulated Live Set that plays in real time.

    python tests/simulated_live.py            then open http://localhost:8517
    python tests/simulated_live.py --auto     also presses Leave loop by itself

The simulated Set (120 BPM, 4/4) loops a chorus until you press Leave loop,
jumps from Song A to Song C, stops at STOP, waits 4 seconds, then plays on.
"""

import sys
import time

from fake_live import FakeCInstance, FakeSong
import Live
import StageHelper
from StageHelper import surface

SET = [
    ('Song A', 0),
    ('Chorus +LOOP', 16),
    ('Outro', 32),
    ('>>> Song C', 48),
    ('Song B', 64),
    ('>>> Encore', 92),
    ('Song C', 128),
    ('STOP', 160),
    ('Song D', 164),
]


def main():
    auto = '--auto' in sys.argv
    if '--port' in sys.argv:
        surface.PORT = int(sys.argv[sys.argv.index('--port') + 1])
    song = FakeSong(SET)
    script = StageHelper.create_instance(FakeCInstance(song))
    print('Simulated Live is running. Open http://localhost:%d  (Ctrl+C to quit)'
          % script._server.port)
    song.start_playing()
    stopped_at = None
    last = time.perf_counter()
    try:
        while True:
            time.sleep(0.005)
            now = time.perf_counter()
            song.advance(now - last)
            last = now
            Live.Base.Timer.fire_all()
            loop = script._show._loop
            if auto and loop and len(song.jumps_to(loop.start)) >= 2:
                script._show.leave_loop(song)
            if not song.is_playing:
                stopped_at = stopped_at or now
                if now - stopped_at > 4.0:  # "press Play" again
                    song.events = []
                    stopped_at = None
                    song.start_playing()
    except KeyboardInterrupt:
        pass
    finally:
        script.disconnect()


if __name__ == '__main__':
    main()
