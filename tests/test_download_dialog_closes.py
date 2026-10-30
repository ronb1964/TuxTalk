"""The service's model download window closes on every path, including the
fallback, and says what it's doing.

When Hugging Face's file list can't be fetched, a Whisper model's files are
fetched by name instead. The window must still close then, rather than sit
until the user presses Cancel, which throws the finished model away.
"""
import threading

import pytest

pytest.importorskip("gi")
from conftest import AnyWidget  # noqa: E402
from tuxtalk import model_helper as mh  # noqa: E402


class _FakeDialog(AnyWidget):
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


class _Text(AnyWidget):
    """A label or progress bar that remembers the texts it was given."""

    shown = []

    def set_text(self, text):
        self.shown.append(text)


def _quiet_window(monkeypatch, dialog=_FakeDialog):
    monkeypatch.setattr(mh.Gtk, "Dialog", dialog)
    for name in ("Label", "ProgressBar"):
        monkeypatch.setattr(mh.Gtk, name, _Text)
    monkeypatch.setattr(mh.Gtk, "Button", AnyWidget)
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


def _failed_startup_download(monkeypatch, reason):
    """The start-up download of Whisper Small failing with *reason* (its
    last report, at 0 %): what its error window says."""
    errors = []
    monkeypatch.setattr(mh, "is_model_cached_fast", lambda name: False)
    monkeypatch.setattr(mh, "make_model_download_func", lambda *a, **k: (
        lambda report, cancel: report(reason, 0)))
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: pytest.fail("loaded"))
    monkeypatch.setattr(mh, "_show_download_failed", lambda parent, name, error: errors.append(
        mh._download_failed_text(name, error)))
    _quiet_window(monkeypatch)
    monkeypatch.setattr(mh.GLib, "timeout_add", lambda ms, fn, *a: fn(*a))
    assert mh.download_model_with_progress("small", show_confirmation=False) is None
    return errors


def test_a_failed_download_says_why(monkeypatch):
    [text] = _failed_startup_download(monkeypatch, "Download failed: Name or service not known")
    assert "Error: Download failed: Name or service not known" in text
    assert text.endswith("Check your internet connection and try again.")


def test_a_disk_space_refusal_asks_for_room_not_a_connection(monkeypatch):
    """It said to check the internet connection for a full disk too."""
    [text] = _failed_startup_download(
        monkeypatch, "Not enough disk space: model needs ~0.5 GB free, only 0.1 GB available")
    assert "Not enough disk space" in text
    assert text.endswith("Free up some disk space and try again.")


def test_parakeet_not_yet_downloaded_is_fetched_then_loaded(monkeypatch):
    """The start-up download of Parakeet goes through Parakeet Ultra's
    download, then loads the model as for Whisper."""
    from tuxtalk import parakeet_ultra
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    asked = []
    monkeypatch.setattr(mh, "is_model_cached_fast", lambda name: False)
    monkeypatch.setattr(parakeet_ultra, "make_download_func", lambda device: asked.append(
        device) or (lambda report, cancel: report("Done", 100) or True))
    monkeypatch.setattr(mh, "load_model", lambda name, **k: f"loaded {name}")
    _quiet_window(monkeypatch)
    monkeypatch.setattr(mh.GLib, "timeout_add", lambda ms, fn, *a: fn(*a))
    result = mh.download_model_with_progress(PARAKEET_MODEL, show_confirmation=False)
    assert result == f"loaded {PARAKEET_MODEL}" and asked == ["cpu"]
    assert "Loading model..." in _Text.shown


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
