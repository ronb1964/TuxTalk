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


def test_a_cancel_the_window_missed_still_gives_no_model(monkeypatch):
    """Cancel pressed as the load ended: the download counted as finished,
    but the model it loaded isn't kept."""
    class Window(_Window):
        def run(self):
            [task] = self.tasks
            task.download_func(lambda text, percent: text == "Loading model..."
                               and task.cancel_event.set(), task.cancel_event)
            return {task.name: {"success": True, "cancelled": False}}
    monkeypatch.setattr(download_progress_dialog, "UnifiedDownloadDialog", Window)
    monkeypatch.setattr(mh, "make_model_download_func", lambda *a, **k: (
        lambda report, cancel: True))
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: "model")
    assert mh.download_model_with_progress("small", show_confirmation=False) is None


def test_a_cancel_while_the_model_loads_gives_no_model(monkeypatch, window):
    monkeypatch.setattr(mh, "make_model_download_func", lambda *a, **k: (
        lambda report, cancel: True))
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: "model")
    window.cancel_at = "Loading model..."
    assert mh.download_model_with_progress("small", show_confirmation=False) is None


def _failed_startup_download(monkeypatch, download):
    """The start-up download of Whisper Small failing in *download*: the
    error boxes shown after its window closed."""
    boxes = []
    monkeypatch.setattr(mh, "make_model_download_func", lambda *a, **k: download)
    monkeypatch.setattr(mh, "show_message", lambda parent, kind, title, text, **k:
                        boxes.append((title, text)))
    assert mh.download_model_with_progress("small", show_confirmation=False) is None
    return boxes


@pytest.mark.parametrize("reason", [
    "Download failed: Name or service not known",
    "Not enough disk space: model needs ~0.5 GB free, only 0.1 GB available"])
def test_a_failure_is_explained_once_in_the_window(monkeypatch, window, reason):
    """The window shows why and waits for Close; a box saying it again
    after that was the same news twice."""
    def download(report, cancel):
        report(reason, 0)
        return False
    monkeypatch.setattr(mh, "load_model", lambda *a, **k: pytest.fail("loaded"))
    assert _failed_startup_download(monkeypatch, download) == []
    assert window.reports[-1] == (reason, 0)


def test_a_model_that_wont_load_is_explained_in_the_window(monkeypatch, window):
    def broken(*a, **k):
        raise RuntimeError("unsupported model file")
    monkeypatch.setattr(mh, "load_model", broken)
    assert _failed_startup_download(monkeypatch, lambda report, cancel: True) == []
    assert window.reports[-1] == ("Couldn't load the model: unsupported model file", 0)


@pytest.mark.parametrize("percent", [40, 1])
def test_a_failure_the_window_couldnt_explain_gets_a_box(monkeypatch, window, percent):
    """At 1 %, scaled to the window's 95 %, progress turned into 0 %, which
    reads as a failure's reason: "Downloading model.bin..." was shown as one."""
    def download(report, cancel):
        report("Downloading model.bin...", percent)
        return False
    [(title, text)] = _failed_startup_download(monkeypatch, download)
    assert window.reports[-1][1] > 0
    assert title == "Model Download Failed"
    assert text == ("Could not download the Whisper Small model.\n\n"
                    "Check your internet connection and try again.")


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
