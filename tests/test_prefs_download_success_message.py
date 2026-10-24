""""Model Downloaded Successfully!" only after the download is verified.

The success message comes after the cache check, so a download that reports
success but leaves files missing doesn't show "Model Downloaded Successfully!"
and then "Model download failed!".
"""
import threading
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")

from tuxtalk import model_helper  # noqa: E402
from tuxtalk import prefs as P  # noqa: E402
from tuxtalk.prefs import PreferencesWindow  # noqa: E402


class Anything:
    """Stands in for every widget the progress dialog builds."""

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: Anything()

    def run(self):
        return P.Gtk.ResponseType.OK


class SyncThread:
    def __init__(self, target=None, **k):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def fake_dialog(monkeypatch):
    fake_gtk = SimpleNamespace(Dialog=Anything, Label=Anything, ProgressBar=Anything,
                               Button=Anything, ResponseType=P.Gtk.ResponseType,
                               MessageType=P.Gtk.MessageType)
    monkeypatch.setattr(P, "Gtk", fake_gtk)
    monkeypatch.setattr(P.GLib, "idle_add", lambda fn, *a: None)
    monkeypatch.setattr(threading, "Thread", SyncThread)
    monkeypatch.setattr(model_helper, "make_model_download_func",
                        lambda name: lambda progress, cancel: True)


def _window(monkeypatch):
    shown = []
    monkeypatch.setattr(P, "show_message", lambda parent, kind, title, text="", buttons=None: shown.append(title))
    d = SimpleNamespace(window=None)
    return d, shown


def test_an_incomplete_download_never_claims_success(fake_dialog, monkeypatch):
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda name: False)
    d, shown = _window(monkeypatch)

    assert PreferencesWindow.check_and_download_model(d, "small") == (False, False, False)
    assert "Model Downloaded Successfully!" not in shown


def test_a_verified_download_says_so(fake_dialog, monkeypatch):
    checks = iter([False, True])            # not cached before, complete after
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda name: next(checks))
    d, shown = _window(monkeypatch)

    assert PreferencesWindow.check_and_download_model(d, "parakeet-v3") == (True, True, False)
    assert shown == ["Model Downloaded Successfully!"]
