"""Stand-ins several test files share: a label or button, Preferences' tabs,
the microphone's stream, the update's progress window and threading.Timer.
None of them opens a window on the screen or touches a device."""
import threading
import types

from gi.repository import Gtk


class ShownWidget:
    """A label or button with no window, keeping what it was last told to
    show: its label, markup, text, whether it can be clicked and whether it
    is visible (None until told)."""

    def __init__(self):
        self.label = self.markup = self.text = self.sensitive = self.visible = None

    def set_label(self, label):
        self.label = label

    def get_label(self):
        return self.label

    def set_markup(self, markup):
        self.markup = markup

    def set_text(self, text):
        self.text = text

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive

    def set_visible(self, visible):
        self.visible = visible


class Microphone:
    """A stream that records nothing by itself: the test calls its callback.
    Its stop() can be held until *release* is set."""

    def __init__(self, opened, release, **settings):
        self.callback = settings["callback"]
        self.release = release
        self.closed = threading.Event()
        opened.append(self)

    def start(self):
        pass

    def stop(self, ignore_errors=True):
        self.release.wait(5)

    def close(self, ignore_errors=True):
        self.closed.set()


def progress_in_place(monkeypatch):
    """progress_window.run_with_progress with no window, its work run at
    once on this thread."""
    from tuxtalk import progress_window
    monkeypatch.setattr(progress_window, "_progress_window", lambda *a: (
        types.SimpleNamespace(destroy=lambda: None),
        types.SimpleNamespace(set_text=lambda t: None),
        types.SimpleNamespace(set_fraction=lambda f: None, set_text=lambda t: None)))
    monkeypatch.setattr(progress_window.GLib, "idle_add", lambda fn, *a: fn(*a))
    monkeypatch.setattr(progress_window, "threading", types.SimpleNamespace(
        Thread=lambda target, daemon=None, name=None: types.SimpleNamespace(start=target)))


class HeldTimer:
    """threading.Timer that never runs by itself: the test fires it."""

    def __init__(self, delay, fn, args=()):
        self.delay, self.fn, self.args = delay, fn, args
        self.cancelled = self.daemon = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        """What the real one does when its time comes: run, unless cancelled."""
        if not self.cancelled:
            self.fn(*self.args)


def held_timers(monkeypatch):
    """threading.Timer is HeldTimer for the rest of the test; returns the
    timers made, in order."""
    made = []

    def make(delay, fn, args=()):
        made.append(HeldTimer(delay, fn, args))
        return made[-1]
    monkeypatch.setattr(threading, "Timer", make)
    return made


def tabbed_window(*names):
    """Preferences with only its tabs, named *names* in order: enough for
    PreferencesWindow.select_tab."""
    notebook = Gtk.Notebook()
    for name in names:
        page = Gtk.Box()
        page.show()          # GtkNotebook won't switch to a hidden page
        notebook.append_page(page, Gtk.Label(label=name))
    return types.SimpleNamespace(notebook=notebook)
