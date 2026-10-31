"""OK in Preferences closes the window, even right after a model download.

Apply is the button that keeps the window open, so OK closes it after a
download too instead of needing a second click. On Vulkan the download is
the Vulkan setup's (the engine and the model's whisper.cpp file), and its
success counts as one.
"""
from types import SimpleNamespace


def test_a_vulkan_model_download_reports_success(monkeypatch):
    from tuxtalk import vulkan_setup_dialogs
    from tuxtalk.prefs_speech import SpeechSection
    downloads = []
    monkeypatch.setattr(vulkan_setup_dialogs, "ensure_files",
                        lambda parent, m, confirm: downloads.append(m) or True)
    stub = SimpleNamespace(config={"model": "parakeet-v3", "device": "vulkan"}, window=None)
    assert SpeechSection.download_selected_model(stub) is True
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
        _saved_before={"model": "parakeet-v3"},     # what the save replaced
        save_config=lambda: True,
        _changed_since_open=lambda: {"model"},
        _apply_or_restart=lambda changed: True,
        general_tab=SimpleNamespace(
            speech=SimpleNamespace(download_selected_model=lambda: True),  # downloaded fine
            show_saved=lambda: None,
            after_hotkey_change=lambda changed, since: False),
        _release=lambda: None,
        words_tab=SimpleNamespace(save=lambda: True),
        window=SimpleNamespace(destroy=lambda: closed.append(True)),
    )

    stub._save_and_download = lambda: prefs_cls._save_and_download(stub)
    prefs_cls.on_ok(stub, None)

    assert closed == [True]
    assert quit_calls == [True]
