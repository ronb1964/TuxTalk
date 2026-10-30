"""Escape during the "Checking Speed" spinner must not end the check early.

GtkDialog sends Escape and Alt+F4 as a delete-event even with the title-bar X
hidden. If run() returned then, while the worker is still timing,
run_speed_check() would raise KeyError on the missing result.
"""
import threading
import types

import pytest

pytest.importorskip("gi")
from conftest import AnyWidget  # noqa: E402
from tuxtalk import vulkan_setup_dialogs as vsd  # noqa: E402


def test_escape_is_refused_while_the_check_runs(monkeypatch):
    release = threading.Event()
    seen = {}

    class FakeDialog(AnyWidget):
        def __init__(self, *args, **kwargs):
            self.handlers = {}

        def connect(self, signal, handler):
            self.handlers[signal] = handler

        def run(self):
            # Escape while the worker is busy, then again once it has finished.
            handler = self.handlers["delete-event"]
            seen["while_running"] = handler(self, None)
            release.set()
            seen["thread"].join(2)
            seen["after"] = handler(self, None)

    real_thread = threading.Thread

    def make_thread(target, daemon):
        seen["thread"] = real_thread(target=target, daemon=daemon)
        return seen["thread"]

    monkeypatch.setattr(vsd.Gtk, "Dialog", FakeDialog)
    for name in ("Box", "Spinner", "Label"):
        monkeypatch.setattr(vsd.Gtk, name, AnyWidget)
    monkeypatch.setattr(vsd.GLib, "timeout_add", lambda *a, **k: 0)
    # Only this module's threads: replacing threading.Thread itself broke
    # every other thread started meanwhile (the beeps' among them).
    monkeypatch.setattr(vsd, "threading", types.SimpleNamespace(Thread=make_thread))

    vsd._wait_dialog(None, lambda: release.wait(2))

    assert seen["while_running"] is True     # stays open: the check goes on
    assert seen["after"] is False            # closes normally once it is done


def test_an_interrupted_check_keeps_the_processor(monkeypatch):
    shown = []
    monkeypatch.setattr(vsd, "_wait_dialog", lambda parent, work: None)
    monkeypatch.setattr(vsd, "message",
                        lambda parent, kind, title, text, **k: shown.append(title))
    assert vsd.run_speed_check(None, "parakeet-v3") is False
    assert shown == ["Keeping the processor"]


def test_a_long_check_can_be_stopped(monkeypatch):
    """A hung graphics driver could keep the modal spinner up for many minutes
    with no way to close it, so once it runs long, Stop appears and Escape works."""
    release = threading.Event()
    timers = {}
    seen = {"buttons": []}

    class FakeDialog(AnyWidget):
        def __init__(self, *args, **kwargs):
            self.handlers = {}

        def connect(self, signal, handler):
            self.handlers[signal] = handler

        def add_button(self, text, response):
            seen["buttons"].append(text)
            return AnyWidget()

        def run(self):
            handler = self.handlers["delete-event"]
            seen["before"] = handler(self, None)
            timers[vsd._STOP_OFFER_MS]()          # the check has run long
            seen["after_offer"] = handler(self, None)
            release.set()

    monkeypatch.setattr(vsd.Gtk, "Dialog", FakeDialog)
    for name in ("Box", "Spinner", "Label"):
        monkeypatch.setattr(vsd.Gtk, name, AnyWidget)
    monkeypatch.setattr(vsd.GLib, "timeout_add",
                        lambda ms, fn, *a: timers.__setitem__(ms, fn) or 0)

    vsd._wait_dialog(None, lambda: release.wait(2))

    assert seen["before"] is True            # a stray Escape early on is refused
    assert seen["after_offer"] is False      # now it closes
    assert seen["buttons"] == ["Stop"]
    assert timers[200]() is False            # the poll stops once the window is gone


def test_stop_ends_the_check_still_running_behind_it(monkeypatch):
    """Stop closes the window while the check runs on: it's told to end, so
    a hung driver doesn't keep the graphics chip busy for minutes."""
    ran = {}

    def speed_check(model, device, cancel):
        ran["stopped"] = cancel.wait(2)
        raise RuntimeError("speed check stopped")

    def stopped_at_once(parent, work):
        ran["thread"] = threading.Thread(target=work)
        ran["thread"].start()           # Stop: the window closes, the work goes on

    monkeypatch.setattr(vsd.wv, "find_device", lambda: 0)
    monkeypatch.setattr(vsd.wv, "speed_check", speed_check)
    monkeypatch.setattr(vsd, "_wait_dialog", stopped_at_once)
    monkeypatch.setattr(vsd, "message", lambda *a, **k: None)

    assert vsd.run_speed_check(None, "small") is False
    ran["thread"].join(3)
    assert ran["stopped"] is True
