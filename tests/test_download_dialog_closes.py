"""The service's model download window closes on every path, including the
fallback, and says what it's doing.

When Hugging Face's file list can't be fetched, a Whisper model's files are
fetched by name instead. The window must still close then, rather than sit
until the user presses Cancel, which throws the finished model away.
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


class _Text(_Anything):
    """A label or progress bar that remembers the texts it was given."""

    shown = []

    def set_text(self, text):
        self.shown.append(text)


def _quiet_window(monkeypatch, dialog=_FakeDialog):
    monkeypatch.setattr(mh.Gtk, "Dialog", dialog)
    for name in ("Label", "ProgressBar"):
        monkeypatch.setattr(mh.Gtk, name, _Text)
    monkeypatch.setattr(mh.Gtk, "Button", _Anything)
    # Run GLib callbacks straight away, from whichever thread asks.
    monkeypatch.setattr(mh.GLib, "idle_add", lambda fn, *a: fn(*a))
    _Text.shown = []


def test_fallback_download_closes_the_window(monkeypatch):
    import huggingface_hub
    loaded, fetched = object(), []

    def no_list(repo):
        raise OSError("rate limited")

    monkeypatch.setattr(mh, "is_model_cached_fast", lambda name: False)
    monkeypatch.setattr(mh, "_files_to_download", no_list)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download",
                        lambda repo_id, filename, **k: fetched.append(filename))
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: loaded)
    _quiet_window(monkeypatch)
    monkeypatch.setattr(mh.GLib, "timeout_add", lambda ms, fn, *a: fn(*a))

    result = mh.download_model_with_progress("small", show_confirmation=False)

    assert result is loaded
    assert "model.bin" in fetched


def test_the_window_says_it_loads_the_model_once_the_files_are_in(monkeypatch):
    """The download ends at 95 %, the percent "Loading model..." was given,
    so the window kept naming the last file while the model loaded."""
    monkeypatch.setattr(mh, "is_model_cached_fast", lambda name: False)
    monkeypatch.setattr(mh, "make_model_download_func", lambda *a, **k: (
        lambda report, cancel: report("Downloading model.bin...", 100) or True))
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: "model")
    _quiet_window(monkeypatch)
    monkeypatch.setattr(mh.GLib, "timeout_add", lambda ms, fn, *a: fn(*a))

    assert mh.download_model_with_progress("small", show_confirmation=False) == "model"
    assert _Text.shown[-3:] == ["95%", "Loading model...", "100%"]


def test_a_failed_download_says_why(monkeypatch):
    errors = []
    monkeypatch.setattr(mh, "is_model_cached_fast", lambda name: False)
    monkeypatch.setattr(mh, "make_model_download_func", lambda *a, **k: (
        lambda report, cancel: report("Download failed: Name or service not known", 0)))
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: pytest.fail("loaded"))
    monkeypatch.setattr(mh, "_show_download_failed",
                        lambda parent, name, error: errors.append(str(error)))
    _quiet_window(monkeypatch)
    monkeypatch.setattr(mh.GLib, "timeout_add", lambda ms, fn, *a: fn(*a))

    assert mh.download_model_with_progress("small", show_confirmation=False) is None
    assert errors == ["Download failed: Name or service not known"]


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
    _quiet_window(monkeypatch, Window)
    monkeypatch.setattr(mh.GLib, "timeout_add",
                        lambda ms, fn, *a: timers.append(fn) or closing.set())

    assert mh.download_model_with_progress("small", show_confirmation=False) is loaded
    for timer in timers:                    # the closing timer fires after all
        assert not timer()
    assert late == [] and windows[0].destroyed
