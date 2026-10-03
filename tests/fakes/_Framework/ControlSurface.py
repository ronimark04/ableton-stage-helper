class ControlSurface(object):
    """The few parts of Live's _Framework.ControlSurface that Stage Helper uses."""

    def __init__(self, c_instance, *a, **k):
        self._c_instance = c_instance

    def song(self):
        return self._c_instance.song()

    def log_message(self, *message):
        self._c_instance.log.append(' '.join(map(str, message)))

    def show_message(self, message):
        self._c_instance.status = message

    def update_display(self):
        pass

    def disconnect(self):
        self._c_instance.disconnected = True
