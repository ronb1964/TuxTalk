"""The dictation service's one GTK main loop, on a thread of its own (the
hotkey loop has the service's main thread), and the way to run GTK code on it.

GTK must be used from a single thread. What the service makes with it, the
recording indicator, the stand-in D-Bus service and the windows of a model
download at startup, is made on the loop's thread (call); everything else
hands GTK work to it with GLib.idle_add.
"""
import threading

from .logger import setup_logger

logger = setup_logger(__name__)

_thread = None


def start() -> None:
    """Start the loop for the life of the process; returns once it runs."""
    global _thread
    from gi.repository import GLib, Gtk
    running = threading.Event()

    def run():
        GLib.idle_add(running.set)
        Gtk.main()

    _thread = threading.Thread(target=run, daemon=True, name="tt-gtk")
    _thread.start()
    running.wait()


def running() -> bool:
    """Whether the loop runs, so a label or a window can be shown at all."""
    return _thread is not None


def here() -> bool:
    """Whether this thread may use GTK: it is the loop's, or none runs yet."""
    return _thread is None or threading.current_thread() is _thread


def call(fn, *args):
    """fn(*args) on the loop's thread, waited for: what it returns, or what
    it raises (SystemExit too, so a download that gives up still ends the
    service)."""
    if here():
        return fn(*args)
    from gi.repository import GLib
    done, result = threading.Event(), {}

    def run():
        try:
            result["value"] = fn(*args)
        except BaseException as e:
            logger.info(f"Handing {e!r} from GTK's thread back to its caller")
            result["error"] = e
        finally:
            done.set()
        return False

    GLib.idle_add(run)
    done.wait()
    if "error" in result:
        raise result["error"]
    return result.get("value")
