"""The service's own download of a model missing at start: it runs in the
download window every other download uses, loads the model in the same
window, and says why when it fails.

When Hugging Face's file list can't be fetched, a Whisper model's files are
fetched by name instead; the model must still arrive then. (The window
closing on every path, and a finished download kept when the window closes,
are the download window's own, in test_download_dialog_cancel.)
"""
import pytest

pytest.importorskip("gi")
from tuxtalk import download_progress_dialog, model_helper as mh  # noqa: E402


class _Window:
    """UnifiedDownloadDialog without a window: run() runs each download at
    once on this thread, noting what it reports; *cancel_at* cancels a
    download as it reports that text."""
    reports, cancel_at = [], None

    def __init__(self, **kwargs):
        self.tasks = []

    def add_task(self, task):
        self.tasks.append(task)

    def run(self):
        for task in self.tasks:
            def report(text, percent, task=task):
                _Window.reports.append((text, percent))
                task.last_report = (text, percent)
                if text == _Window.cancel_at:
                    task.cancelled = True
                    task.cancel_event.set()
            task.success = bool(task.download_func(report, task.cancel_event))
        return {t.name: {"success": t.success and not t.cancelled, "cancelled": t.cancelled}
                for t in self.tasks}


@pytest.fixture(autouse=True)
def window(monkeypatch):
    _Window.reports, _Window.cancel_at = [], None
    monkeypatch.setattr(download_progress_dialog, "UnifiedDownloadDialog", _Window)
    monkeypatch.setattr(mh, "is_model_cached_fast", lambda name: False)
    return _Window


def test_without_a_file_list_the_files_are_fetched_by_name(monkeypatch):
    import huggingface_hub
    loaded, fetched = object(), []

    def no_list(repo):
        raise OSError("rate limited")

    monkeypatch.setattr(mh, "_files_to_download", no_list)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download",
                        lambda repo_id, filename, **k: fetched.append(filename))
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: loaded)
    assert mh.download_model_with_progress("small", show_confirmation=False) is loaded
    assert "model.bin" in fetched


def test_the_window_says_it_loads_the_model_once_the_files_are_in(monkeypatch, window):
    """The download fills the bar to 95 %, the load the rest."""
    monkeypatch.setattr(mh, "make_model_download_func", lambda *a, **k: (
        lambda report, cancel: report("Downloading model.bin...", 100) or True))
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: "model")
    assert mh.download_model_with_progress("small", show_confirmation=False) == "model"
    assert window.reports == [("Downloading model.bin...", 95), ("Loading model...", 95)]


def test_a_cancel_while_the_model_loads_gives_no_model(monkeypatch, window):
    monkeypatch.setattr(mh, "make_model_download_func", lambda *a, **k: (
        lambda report, cancel: True))
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: "model")
    window.cancel_at = "Loading model..."
    assert mh.download_model_with_progress("small", show_confirmation=False) is None


def _failed_startup_download(monkeypatch, reason):
    """The start-up download of Whisper Small failing with *reason* (its
    last report, at 0 %): what its error window says."""
    errors = []
    monkeypatch.setattr(mh, "make_model_download_func", lambda *a, **k: (
        lambda report, cancel: report(reason, 0)))
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: pytest.fail("loaded"))
    monkeypatch.setattr(mh, "_show_download_failed", lambda parent, name, error: errors.append(
        mh._download_failed_text(name, error)))
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


def test_a_model_that_wont_load_says_why(monkeypatch):
    errors = []
    monkeypatch.setattr(mh, "make_model_download_func", lambda *a, **k: (
        lambda report, cancel: True))

    def broken(*a, **k):
        raise RuntimeError("unsupported model file")
    monkeypatch.setattr(mh, "load_model", broken)
    monkeypatch.setattr(mh, "_show_download_failed",
                        lambda parent, name, error: errors.append(str(error)))
    assert mh.download_model_with_progress("small", show_confirmation=False) is None
    assert errors == ["unsupported model file"]


def test_parakeet_not_yet_downloaded_is_fetched_then_loaded(monkeypatch, window):
    """The start-up download of Parakeet goes through Parakeet Ultra's
    download, then loads the model as for Whisper."""
    from tuxtalk import parakeet_ultra
    from tuxtalk.parakeet_engine import PARAKEET_MODEL
    asked = []
    monkeypatch.setattr(parakeet_ultra, "make_download_func", lambda device: asked.append(
        device) or (lambda report, cancel: report("Done", 100) or True))
    monkeypatch.setattr(mh, "load_model", lambda name, **k: f"loaded {name}")
    result = mh.download_model_with_progress(PARAKEET_MODEL, show_confirmation=False)
    assert result == f"loaded {PARAKEET_MODEL}" and asked == ["cpu"]
    assert ("Loading model...", 95) in window.reports


def test_the_window_is_the_one_every_download_uses(monkeypatch):
    """Not a window of its own: it looks and cancels like the others."""
    seen = []

    class Window(_Window):
        def __init__(self, parent=None, title=None, description=None):
            super().__init__()
            seen.append(title)
    monkeypatch.setattr(download_progress_dialog, "UnifiedDownloadDialog", Window)
    monkeypatch.setattr(mh, "make_model_download_func", lambda *a, **k: (
        lambda report, cancel: True))
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: "model")
    assert mh.download_model_with_progress("small", show_confirmation=False) == "model"
    assert seen == ["Downloading Model: Whisper Small"]
