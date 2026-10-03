"""A tiny web server that runs inside Live, for Stage Helper's browser page.

Live calls Stage Helper every few milliseconds on its main thread, and nothing
may hold that thread up. So this server never waits: every socket is
non-blocking, and poll() does whatever socket work is ready, then returns.

It listens on 127.0.0.1 only, so nothing outside this computer can reach it.

    GET  /               the page (page.html, read fresh on every load)
    GET  /events         a Server-Sent Events stream. While one is open,
                         Stage Helper is on.
    POST /api/<command>  a button on the page, e.g. /api/leave-loop
"""

import errno
import json
import socket

MAX_REQUEST_BYTES = 16 * 1024
MAX_UNSENT_BYTES = 1024 * 1024
MAX_CONNECTIONS = 32
REQUEST_TIMEOUT = 10.0   # seconds a browser gets to send its request
RETRY_LISTEN_EVERY = 3.0

_WOULD_BLOCK = (errno.EAGAIN, errno.EWOULDBLOCK, 10035)  # 10035: WSAEWOULDBLOCK
_REASONS = {200: 'OK', 204: 'No Content', 400: 'Bad Request', 403: 'Forbidden',
            404: 'Not Found', 500: 'Internal Server Error'}
_STREAM_HEAD = (b'HTTP/1.1 200 OK\r\n'
                b'Content-Type: text/event-stream; charset=utf-8\r\n'
                b'Cache-Control: no-store\r\n'
                b'Connection: keep-alive\r\n'
                b'\r\n'
                b'retry: 1000\n\n')


def _would_block(error):
    return isinstance(error, BlockingIOError) or getattr(error, 'errno', None) in _WOULD_BLOCK


class _Connection(object):
    def __init__(self, sock, now):
        self.sock = sock
        self.opened = now
        self.received = b''
        self.unsent = b''
        self.is_stream = False
        self.answered = False
        self.closed = False


class WebServer(object):

    def __init__(self, port, page_path, log, commands=()):
        self.port = port
        self._page_path = page_path
        self._log = log
        self._commands = frozenset(commands)
        self._listener = None
        self._next_listen = 0.0
        self._last_listen_error = None
        self._connections = []
        self._hosts = ()

    # Called by Stage Helper ---------------------------------------------------

    def poll(self, now):
        """Do the socket work that is ready. Returns (commands, new_streams)."""
        commands = []
        new_streams = []
        if self._listener is None and now >= self._next_listen:
            self._listen(now)
        if self._listener is not None:
            self._accept(now)
        for connection in self._connections:
            self._service(connection, now, commands, new_streams)
        self._connections = [c for c in self._connections if not c.closed]
        return commands, new_streams

    def stream_count(self):
        return sum(1 for c in self._connections if c.is_stream and not c.closed)

    def send(self, message, streams=None):
        """Send a JSON message to the given event streams (default: all of them)."""
        data = ('data: %s\n\n' % json.dumps(message, separators=(',', ':'))).encode('utf-8')
        for connection in self._connections if streams is None else streams:
            if connection.is_stream and not connection.closed:
                connection.unsent += data
                self._flush(connection)

    def close(self):
        for connection in self._connections:
            self._drop(connection)
        self._connections = []
        if self._listener is not None:
            self._listener.close()
            self._listener = None

    # Sockets ------------------------------------------------------------------

    def _listen(self, now):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(('127.0.0.1', self.port))
            listener.listen(16)
            listener.setblocking(False)
        except OSError as error:
            listener.close()
            self._next_listen = now + RETRY_LISTEN_EVERY
            text = str(error)
            if text != self._last_listen_error:
                self._last_listen_error = text
                self._log('Cannot use port %d (%s). Is another copy of Stage Helper running? '
                          'Trying again every few seconds.' % (self.port, text))
            return
        self._listener = listener
        self.port = listener.getsockname()[1]
        self._hosts = ('localhost:%d' % self.port, '127.0.0.1:%d' % self.port)
        self._last_listen_error = None
        self._log('Page ready at http://localhost:%d' % self.port)

    def _accept(self, now):
        for _ in range(8):
            try:
                sock, _address = self._listener.accept()
            except OSError as error:
                if not _would_block(error):
                    self._log('Could not accept a connection: %s' % error)
                return
            sock.setblocking(False)
            if len(self._connections) >= MAX_CONNECTIONS:
                sock.close()
                continue
            self._connections.append(_Connection(sock, now))

    def _service(self, connection, now, commands, new_streams):
        for _ in range(4):
            try:
                data = connection.sock.recv(4096)
            except OSError as error:
                if not _would_block(error):
                    self._drop(connection)
                    return
                break
            if not data:
                self._drop(connection)  # the browser closed it (page closed)
                return
            if connection.is_stream or connection.answered:
                continue  # nothing more is expected from the browser
            connection.received += data
            if len(connection.received) > MAX_REQUEST_BYTES:
                self._drop(connection)
                return

        if not connection.is_stream and not connection.answered:
            request = _parse_request(connection.received)
            if request is not None:
                self._handle(connection, request, commands, new_streams)
            elif now - connection.opened > REQUEST_TIMEOUT:
                self._drop(connection)
                return
        self._flush(connection)

    def _flush(self, connection):
        if connection.closed:
            return
        while connection.unsent:
            try:
                sent = connection.sock.send(connection.unsent[:65536])
            except OSError as error:
                if not _would_block(error):
                    self._drop(connection)
                    return
                break
            connection.unsent = connection.unsent[sent:]
        if len(connection.unsent) > MAX_UNSENT_BYTES:
            self._drop(connection)  # a stuck browser: don't let it eat memory
        elif connection.answered and not connection.unsent:
            self._drop(connection)

    def _drop(self, connection):
        if not connection.closed:
            connection.closed = True
            try:
                connection.sock.close()
            except OSError:
                pass

    # HTTP ---------------------------------------------------------------------

    def _handle(self, connection, request, commands, new_streams):
        method, path, headers = request
        if method is None:
            return self._reply(connection, 400, 'text/plain', b'Bad request')
        # Only our own page may talk to us: no other web sites, and no
        # tricks with other host names that point at this computer.
        origin = headers.get('origin')
        if headers.get('host', '').lower() not in self._hosts or \
                (origin is not None and origin.lower() not in ['http://' + h for h in self._hosts]):
            return self._reply(connection, 403, 'text/plain', b'Forbidden')

        if method == 'GET' and path in ('/', '/index.html'):
            try:
                with open(self._page_path, 'rb') as page:
                    body = page.read()
            except OSError:
                return self._reply(connection, 500, 'text/plain', b'page.html is missing')
            return self._reply(connection, 200, 'text/html; charset=utf-8', body)
        if method == 'GET' and path == '/events':
            connection.is_stream = True
            connection.received = b''
            connection.unsent += _STREAM_HEAD
            new_streams.append(connection)
            return
        if method == 'POST' and path.startswith('/api/') and path[5:] in self._commands:
            commands.append(path[5:])
            return self._reply(connection, 200, 'application/json', b'{"ok":true}')
        if path == '/favicon.ico':
            return self._reply(connection, 204, 'text/plain', b'')
        return self._reply(connection, 404, 'text/plain', b'Not found')

    def _reply(self, connection, status, content_type, body):
        head = ('HTTP/1.1 %d %s\r\n'
                'Content-Type: %s\r\n'
                'Content-Length: %d\r\n'
                'Cache-Control: no-store\r\n'
                'Connection: close\r\n'
                '\r\n' % (status, _REASONS[status], content_type, len(body)))
        connection.unsent += head.encode('latin-1') + body
        connection.answered = True


def _parse_request(data):
    """(method, path, headers) once a whole request has arrived, else None.

    A malformed request gives (None, None, None).
    """
    end = data.find(b'\r\n\r\n')
    if end < 0:
        return None
    lines = data[:end].decode('latin-1').split('\r\n')
    parts = lines[0].split(' ')
    if len(parts) != 3:
        return None, None, None
    headers = {}
    for line in lines[1:]:
        name, colon, value = line.partition(':')
        if colon:
            headers[name.strip().lower()] = value.strip()
    try:
        length = int(headers.get('content-length') or 0)
    except ValueError:
        return None, None, None
    if len(data) < end + 4 + length:
        return None  # the body is still on its way
    method, target, _version = parts
    return method, target.split('?', 1)[0], headers
