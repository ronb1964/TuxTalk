"""OK in Preferences closes the window, even right after a model download.

Apply is the button that keeps the window open, so OK closes it after a
download too instead of needing a second click. On Vulkan, files that are
merely present must not count as a download, or OK would never close
Preferences at all.
"""
from types import SimpleNamespace

import pytest

from tuxtalk import whisper_vulkan as wv


@pytest.fixture
def prefs_cls():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from tuxtalk.prefs import PreferencesWindow
    return PreferencesWindow


def _vulkan_stub(model, downloads):
    stub = SimpleNamespace(config={"model": model, "device": "vulkan"})
    stub._download_vulkan_files = lambda m, confirm: downloads.append(m) or True
    return stub


def test_a_vulkan_model_download_reports_success(prefs_cls, monkeypatch):
    downloads = []
    monkeypatch.setattr(wv, "is_installed", lambda m: bool(downloads))
    assert prefs_cls._download_selected_model(_vulkan_stub("parakeet-v3", downloads)) is True
    assert downloads == ["parakeet-v3"]


def test_ok_closes_after_a_model_download(prefs_cls, monkeypatch):
    """Pick Small, click OK, it downloads: the window then closes by itself."""
    from tuxtalk import prefs
    quit_calls = []
    monkeypatch.setattr(prefs.Gtk, "main_quit", lambda: quit_calls.append(True))

    closed = []
    stub = SimpleNamespace(
        _config_at_open={"model": "parakeet-v3"},
        config={"model": "small"},
        save_config=lambda: True,
        _changed_since_open=lambda: {"model"},
        _download_selected_model=lambda: True,  # downloaded fine
        _apply_or_restart=lambda changed: True,
        _after_hotkey_change=lambda changed, since: False,
        _stop_mic_test=lambda: None,
        _release_pidfile=lambda: None,
        words_tab=SimpleNamespace(save=lambda: True),
        window=SimpleNamespace(destroy=lambda: closed.append(True)),
    )

    prefs_cls.on_ok(stub, None)

    assert closed == [True]
    assert quit_calls == [True]
