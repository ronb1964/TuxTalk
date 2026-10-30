""""Model Downloaded Successfully!" only after the download is verified.

The success message comes after the cache check, so a download that reports
success but leaves files missing doesn't show "Model Downloaded Successfully!"
and then "Model download failed!".
"""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")

from tuxtalk import download_progress_dialog, model_helper, prefs_speech  # noqa: E402


class FinishedDownloads:
    """The download window, with each download run to its end at once."""

    def __init__(self, **_kwargs):
        self.tasks = []

    def add_task(self, task):
        self.tasks.append(task)

    def run(self):
        for task in self.tasks:
            task.success = task.download_func(lambda *a: None, task.cancel_event)
            task.completed = True
        return {t.name: {"success": t.success, "cancelled": False} for t in self.tasks}


@pytest.fixture
def shown(monkeypatch):
    titles = []
    monkeypatch.setattr(download_progress_dialog, "UnifiedDownloadDialog", FinishedDownloads)
    monkeypatch.setattr(model_helper, "make_model_download_func",
                        lambda name: lambda progress, cancel: True)
    monkeypatch.setattr(prefs_speech, "show_message",
                        lambda parent, kind, title, text="", buttons=None: titles.append(title))
    return titles


def test_an_incomplete_download_never_claims_success(shown, monkeypatch):
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda name: False)

    assert prefs_speech.download_model(None, "small") == (False, False)
    assert "Model Downloaded Successfully!" not in shown


def test_a_verified_download_says_so(shown, monkeypatch):
    checks = iter([False, True])            # not cached before, complete after
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda name: next(checks))

    assert prefs_speech.download_model(None, "parakeet-v3") == (True, False)
    assert shown == ["Model Downloaded Successfully!"]


def test_a_model_already_here_downloads_nothing(shown, monkeypatch):
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda name: True)
    monkeypatch.setattr(download_progress_dialog, "UnifiedDownloadDialog",
                        lambda **k: pytest.fail("opened the download window"))

    assert prefs_speech.download_model(None, "small") == (True, False)
    assert shown == []
