class Timer(object):
    """Live.Base.Timer calls callback every `interval` ms on Live's main thread.

    Here nothing runs by itself: tests call Timer.fire_all().
    """

    instances = []

    def __init__(self, callback, interval, repeat):
        self.callback = callback
        self.interval = interval
        self.repeat = repeat
        self.running = False
        Timer.instances.append(self)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    @classmethod
    def fire_all(cls):
        for timer in list(cls.instances):
            if timer.running:
                timer.callback()
