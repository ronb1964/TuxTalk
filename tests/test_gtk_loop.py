"""The service's GTK code runs on GTK's own thread (gtk_loop.py): the
recording indicator and the stand-in D-Bus service were made on the main
thread and then run by Gtk.main() on another (CODE_HEALTH task 17).

A stand-in loop thread plays GTK's here: a real Gtk.main() thread in the
test process would drive GTK alongside the tests that build windows."""
import queue
import threading

import pytest

from tuxtalk import gtk_loop


@pytest.fixture
def loop(monkeypatch):
    """A thread that runs what GLib.idle_add hands it, standing in as GTK's."""
    from gi.repository import GLib
    jobs = queue.Queue()

    def serve():
        while (job := jobs.get()) is not None:
            job()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *a: jobs.put(lambda: fn(*a)))
    monkeypatch.setattr(gtk_loop, "_thread", thread)
    yield thread
    jobs.put(None)


def test_gtk_work_runs_on_the_loops_thread_and_is_waited_for(loop):
    assert gtk_loop.call(threading.current_thread) is loop
    assert not gtk_loop.here()            # the test's own thread isn't GTK's


def test_what_the_work_raises_reaches_the_caller(loop):
    """A model download that gives up ends the service with SystemExit:
    raised on GTK's thread, it has to end the service's main thread too."""
    def give_up():
        raise SystemExit(1)
    with pytest.raises(SystemExit):
        gtk_loop.call(give_up)


def test_before_the_loop_runs_gtk_may_be_used_anywhere(monkeypatch):
    monkeypatch.setattr(gtk_loop, "_thread", None)
    assert gtk_loop.here() and not gtk_loop.running()
    assert gtk_loop.call(lambda: "ran") == "ran"


def test_work_the_loop_doesnt_get_to_in_time_is_called_off(monkeypatch):
    """A stalled loop: the caller stops waiting, and the work, still queued,
    never runs when the loop catches up (a dictation copied too late)."""
    from gi.repository import GLib
    queued, ran = [], []
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *a: queued.append(fn))
    monkeypatch.setattr(gtk_loop, "_thread", threading.Thread())
    with pytest.raises(TimeoutError):
        gtk_loop.call(lambda: ran.append(1), timeout=0.05)
    queued[0]()
    assert ran == []


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_loop_that_dies_before_it_runs_doesnt_hang_the_start(monkeypatch):
    """No display, say: the service stopped at start, waiting for good."""
    from gi.repository import GLib, Gtk

    def no_display():
        raise RuntimeError("cannot open display")
    monkeypatch.setattr(Gtk, "main", no_display)
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *a: None)
    monkeypatch.setattr(gtk_loop, "_thread", None)
    raised = []

    def start():
        try:
            gtk_loop.start()
        except RuntimeError as e:
            raised.append(str(e))
    # On a thread with a limit: without the fix this would hang the test run.
    starter = threading.Thread(target=start, daemon=True)
    starter.start()
    starter.join(5)
    assert raised and "ended before it started" in raised[0]
