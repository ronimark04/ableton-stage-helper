"""Tests for the tiny web server, using real sockets on this computer."""

import json
import os
import socket
import time
import unittest

import fake_live  # noqa: F401  (sets up the import path)
from StageHelper import web

PAGE = os.path.join(fake_live.ROOT, 'StageHelper', 'page.html')


class Harness(object):
    """Drives the server from the test, the way Live's timer would."""

    def __init__(self, port=0):
        self.log = []
        self.server = web.WebServer(port, PAGE, self.log.append, commands=('leave-loop',))
        self.commands = []
        self.new_streams = []
        self.clock = 0.0
        self.pump()
        assert self.server.port, 'server did not start'

    def pump(self, times=3, advance=0.0):
        for _ in range(times):
            self.clock += advance
            commands, streams = self.server.poll(self.clock)
            self.commands += commands
            self.new_streams += streams
            time.sleep(0.002)

    def connect(self):
        client = socket.create_connection(('127.0.0.1', self.server.port))
        client.setblocking(False)
        self.pump()
        return client

    def request(self, client, text):
        client.sendall(text.replace('\n', '\r\n').encode('latin-1'))

    def read(self, client, until, limit=2.0):
        """Pump the server and collect what the client receives until until(data)."""
        data = b''
        deadline = time.time() + limit
        while time.time() < deadline:
            self.pump(1)
            try:
                chunk = client.recv(65536)
                if not chunk:
                    return data, True  # closed by the server
                data += chunk
            except BlockingIOError:
                pass
            if until(data):
                return data, False
        return data, False

    def host(self):
        return 'localhost:%d' % self.server.port


class WebServerTests(unittest.TestCase):

    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.server.close()

    def get(self, path, host=None, extra=''):
        client = self.h.connect()
        self.h.request(client, 'GET %s HTTP/1.1\nHost: %s\n%s\n' % (path, host or self.h.host(), extra))
        data, closed = self.h.read(client, lambda d: False, limit=0.5)
        client.close()
        return data

    def test_serves_the_page(self):
        data = self.get('/')
        self.assertTrue(data.startswith(b'HTTP/1.1 200 OK'))
        self.assertIn(b'<title>Stage Helper</title>', data)

    def test_event_stream_switches_on_and_off_with_the_page(self):
        client = self.h.connect()
        self.h.request(client, 'GET /events HTTP/1.1\nHost: %s\nAccept: text/event-stream\n\n'
                       % self.h.host())
        data, _ = self.h.read(client, lambda d: b'retry: 1000' in d)
        self.assertIn(b'text/event-stream', data)
        self.assertEqual(self.h.server.stream_count(), 1)
        self.assertEqual(len(self.h.new_streams), 1)

        self.h.server.send({'type': 'state', 'name': 'Café "B"'})
        data, _ = self.h.read(client, lambda d: d.endswith(b'\n\n') and b'data:' in d)
        line = [l for l in data.split(b'\n') if l.startswith(b'data: ')][0]
        self.assertEqual(json.loads(line[6:].decode('utf-8')), {'type': 'state', 'name': 'Café "B"'})

        client.close()  # closing the browser page
        self.h.pump(5)
        self.assertEqual(self.h.server.stream_count(), 0)

    def test_leave_loop_button(self):
        client = self.h.connect()
        self.h.request(client, 'POST /api/leave-loop HTTP/1.1\nHost: %s\nOrigin: http://%s\n'
                       'Content-Length: 0\n\n' % (self.h.host(), self.h.host()))
        data, closed = self.h.read(client, lambda d: b'"ok":true' in d)
        self.assertTrue(data.startswith(b'HTTP/1.1 200 OK'))
        self.assertEqual(self.h.commands, ['leave-loop'])
        client.close()

    def test_request_arriving_in_pieces(self):
        client = self.h.connect()
        self.h.request(client, 'POST /api/leave-loop HTTP/1.1\nHo')
        self.h.pump(3)
        self.h.request(client, 'st: %s\nContent-Length: 2\n\n{' % self.h.host())
        self.h.pump(3)
        self.assertEqual(self.h.commands, [])  # body not complete yet
        self.h.request(client, '}')
        data, _ = self.h.read(client, lambda d: b'"ok":true' in d)
        self.assertEqual(self.h.commands, ['leave-loop'])
        client.close()

    def test_unknown_paths_and_commands(self):
        self.assertTrue(self.get('/nope').startswith(b'HTTP/1.1 404'))
        client = self.h.connect()
        self.h.request(client, 'POST /api/stop-everything HTTP/1.1\nHost: %s\n\n' % self.h.host())
        data, _ = self.h.read(client, lambda d: b'404' in d)
        self.assertTrue(data.startswith(b'HTTP/1.1 404'))
        self.assertEqual(self.h.commands, [])

    def test_other_web_sites_are_refused(self):
        self.assertTrue(self.get('/', host='evil.example:%d' % self.h.server.port)
                        .startswith(b'HTTP/1.1 403'))
        client = self.h.connect()
        self.h.request(client, 'POST /api/leave-loop HTTP/1.1\nHost: %s\nOrigin: https://evil.example\n'
                       'Content-Length: 0\n\n' % self.h.host())
        data, _ = self.h.read(client, lambda d: b'403' in d)
        self.assertTrue(data.startswith(b'HTTP/1.1 403'))
        self.assertEqual(self.h.commands, [])

    def test_garbage_is_dropped(self):
        client = self.h.connect()
        client.setblocking(True)
        try:
            client.sendall(b'x' * (web.MAX_REQUEST_BYTES + 10))
        except OSError:
            pass
        client.setblocking(False)
        data, closed = self.h.read(client, lambda d: False, limit=0.5)
        self.assertTrue(closed or data == b'')
        self.assertEqual(self.h.server._connections, [])

    def test_idle_connections_are_dropped(self):
        client = self.h.connect()
        self.h.pump(2, advance=web.REQUEST_TIMEOUT + 1)
        self.assertEqual(self.h.server._connections, [])
        client.close()

    def test_busy_port_is_retried(self):
        blocker = socket.socket()
        blocker.bind(('127.0.0.1', 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        log = []
        server = web.WebServer(port, PAGE, log.append)
        server.poll(0.0)
        self.assertIsNone(server._listener)
        self.assertEqual(len(log), 1)
        server.poll(1.0)  # retries only every few seconds, and logs once
        self.assertEqual(len(log), 1)
        blocker.close()
        server.poll(web.RETRY_LISTEN_EVERY + 0.1)
        self.assertIsNotNone(server._listener)
        server.close()

    def test_port_is_free_again_after_close(self):
        client = self.h.connect()
        self.h.request(client, 'GET /events HTTP/1.1\nHost: %s\n\n' % self.h.host())
        self.h.read(client, lambda d: b'retry' in d)
        port = self.h.server.port
        self.h.server.close()
        again = web.WebServer(port, PAGE, lambda text: None)
        again.poll(0.0)
        self.assertIsNotNone(again._listener)  # e.g. Live reloading the script
        again.close()
        client.close()


if __name__ == '__main__':
    unittest.main()
