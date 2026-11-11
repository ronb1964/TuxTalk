"""The stand-in for the dictation worker every test runs with (conftest)."""


class InlineWorker:
    """Stands in for the dictation worker: a job runs as it's submitted, on
    the test's own thread, so a test sees the text typed when the key is let
    go. Tests of the real worker's order and timing use DictationWorker."""

    thread = None               # no thread of its own: a job never runs "on" it

    def submit(self, job):
        job()

    def submit_if_idle(self, job):
        job()
        return True

    def idle(self):
        return True

    def drain(self, timeout=None):
        return True
