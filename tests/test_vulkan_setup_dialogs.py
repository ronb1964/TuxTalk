"""The Vulkan setup questions name the model the way the menus do."""
import pytest

from tuxtalk import vulkan_setup_dialogs as vsd


def test_the_light_or_full_question_names_large_v3_as_the_menus_do(message_boxes):
    """str.title() wrote "Large-V3"; the menus say "Whisper Large-v3"."""
    assert vsd.choose_light_or_full(None, "large-v3") is None
    [box] = message_boxes.shown
    assert box.title == "Whisper Large-v3 needs your graphics card"
    assert "Large-V3" not in box.text and "Whisper Large-v3 in its format" in box.text


@pytest.mark.parametrize("last_report,boxes", [
    (("Download failed: HTTP 404 Not Found", 0), []),      # the window said why
    (("Downloading... 40%", 40), ["The download did not finish"]),
])
def test_a_failed_download_is_explained_once(monkeypatch, last_report, boxes):
    """The window showed the reason and waited for Close, then a second box
    said "The download did not finish"."""
    from tuxtalk.download_progress_dialog import DownloadTask
    task = DownloadTask("Vulkan engine", "~40 MB", lambda progress, cancel: None)
    monkeypatch.setattr(vsd, "_missing_files", lambda model: [(task, "the engine")])

    def fails(parent, tasks, model):
        task.last_report = last_report
        return False, False
    monkeypatch.setattr(vsd, "_download", fails)
    shown = []
    monkeypatch.setattr(vsd, "message", lambda parent, kind, title, text, **k: shown.append(title))
    assert vsd.ensure_files(None, "small", confirm=False) is False
    assert shown == boxes
