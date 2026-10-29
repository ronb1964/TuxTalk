"""The model download window closes on every path, including the fallback.

When Hugging Face's file list can't be fetched, a Whisper model is loaded
through WhisperModel() instead. The window must still close then, rather than
sit at 50% until the user presses Cancel, which throws the finished model away.
"""
import sys
import threading
import types

import pytest

pytest.importorskip("gi")
from tuxtalk import model_helper as mh  # noqa: E402


class _Anything:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: _Anything()


class _FakeDialog(_Anything):
    """run() waits for response(), like Gtk.Dialog; gives up as a Cancel."""

    def __init__(self, *args, **kwargs):
        self._answer = threading.Event()
        self._response = None

    def response(self, value):
        self._response = value
        self._answer.set()

    def run(self):
        if not self._answer.wait(5):
            return mh.Gtk.ResponseType.CANCEL
        return self._response


def test_fallback_download_closes_the_window(monkeypatch):
    loaded = object()
    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = lambda *a, **k: loaded
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    def no_list(repo):
        raise OSError("rate limited")

    monkeypatch.setattr(mh, "is_model_cached_fast", lambda name: False)
    monkeypatch.setattr(mh, "_files_to_download", no_list)
    monkeypatch.setattr(mh.Gtk, "Dialog", _FakeDialog)
    for name in ("Label", "ProgressBar", "Button"):
        monkeypatch.setattr(mh.Gtk, name, _Anything)
    # Run GLib callbacks straight away, from whichever thread asks.
    monkeypatch.setattr(mh.GLib, "idle_add", lambda fn, *a: fn(*a))
    monkeypatch.setattr(mh.GLib, "timeout_add", lambda ms, fn, *a: fn(*a))

    result = mh.download_model_with_progress("small", show_confirmation=False)

    assert result is loaded


def test_a_cancel_just_after_the_download_keeps_the_model(monkeypatch):
    """The window closes by itself 200 ms after the download ends. A Cancel
    in that moment threw the finished model away, and the closing timer
    then answered the window that was already gone."""
    loaded, timers, windows, late = object(), [], [], []
    closing = threading.Event()

    class Window(_FakeDialog):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.destroyed = False
            windows.append(self)

        def run(self):
            closing.wait(5)                 # the download is done; Cancel now
            return mh.Gtk.ResponseType.CANCEL

        def destroy(self):
            self.destroyed = True

        def response(self, value):
            if self.destroyed:
                late.append(value)

    monkeypatch.setattr(mh, "is_model_cached_fast", lambda name: False)
    monkeypatch.setattr(mh, "_files_to_download", lambda repo: [("model.bin", 10)])
    monkeypatch.setattr(mh, "_fetch_with_retry", lambda fetch, filename: None)
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: loaded)
    monkeypatch.setattr(mh.Gtk, "Dialog", Window)
    for name in ("Label", "ProgressBar", "Button"):
        monkeypatch.setattr(mh.Gtk, name, _Anything)
    monkeypatch.setattr(mh.GLib, "idle_add", lambda fn, *a: fn(*a))
    monkeypatch.setattr(mh.GLib, "timeout_add",
                        lambda ms, fn, *a: timers.append(fn) or closing.set())

    info = {}
    assert mh.download_model_with_progress("small", show_confirmation=False,
                                           result_info=info) is loaded
    assert info == {}
    for timer in timers:                    # the closing timer fires after all
        assert not timer()
    assert late == [] and windows[0].destroyed
