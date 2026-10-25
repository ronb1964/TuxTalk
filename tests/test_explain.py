"""Rule-type problems ("select works right after you dictate") open a small
window explaining them, once per run; then the short label alone (Ron,
2026-10-14: the label made little sense and the notification was gone
before it could be read)."""
import pytest

from tuxtalk import edit_notices, explain
from tuxtalk import notices

# The real open_window, before the suite's stand-in replaces it (conftest).
REAL_OPEN_WINDOW = explain.open_window


@pytest.fixture
def problems(monkeypatch):
    seen = {"windows": [], "labels": [], "notices": []}

    def open_window(kind):
        seen["windows"].append(kind)
        return True
    monkeypatch.setattr(explain, "open_window", open_window)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, msg: seen["notices"].append(msg))
    from tuxtalk import screen_message
    monkeypatch.setattr(screen_message, "show_over_active_window", seen["labels"].append)
    return seen


def test_the_window_explains_every_time(problems):
    """Someone who makes the same mistake twice is told twice (Ron)."""
    notices.edit_problem("nothing_said", False)
    notices.edit_problem("nothing_said", False)
    assert problems["windows"] == ["nothing_said", "nothing_said"]
    assert problems["labels"] == [] and problems["notices"] == []


def test_a_window_already_open_is_not_stacked_on(problems, monkeypatch):
    monkeypatch.setattr(explain, "window_open", lambda: True)
    notices.edit_problem("nothing_said", False)
    assert problems["windows"] == []
    assert problems["labels"] == ["Works only right after dictating"] and problems["notices"] == []


def test_dont_explain_again_is_kept(problems):
    explain.never_again("nav_terminal")
    assert not explain.should_explain("nav_terminal")
    notices.edit_problem("nav_terminal", False)
    assert problems["windows"] == [] and problems["labels"] and problems["notices"] == []


def test_a_real_error_keeps_the_notification(problems):
    notices.edit_problem("shortcut_failed", False)
    assert problems["windows"] == [] and problems["notices"]


def test_if_the_window_cannot_open_the_notification_still_says_it(problems, monkeypatch):
    monkeypatch.setattr(explain, "open_window", lambda kind: False)
    notices.edit_problem("terminal", False)
    assert problems["notices"]


def test_every_explanation_has_a_label_and_a_notice():
    for kind in explain.EXPLANATIONS:
        assert kind in edit_notices.NOTICES and kind in edit_notices.LABELS


def test_the_window_has_the_title_text_tickbox_and_got_it():
    pytest.importorskip("gi")
    dialog, check = explain.build_dialog("nothing_said")
    try:
        assert dialog.get_property("text") == "Select works right after you dictate"
        assert "right after you dictate" in dialog.get_property("secondary-text")
        assert check.get_label() == "Don't explain this again"
    finally:
        dialog.destroy()


def test_a_broken_file_means_explain_again(tmp_path, monkeypatch):
    p = tmp_path / "explained.json"
    p.write_text("{not json")
    monkeypatch.setattr(explain, "_never_path", lambda: str(p))
    assert explain.should_explain("nothing_said")


def test_show_tips_again_brings_everything_back(problems):
    explain.never_again("cant_format")
    explain.never_again("terminal")
    assert explain.anything_to_reset() and not explain.should_explain("terminal")
    explain.reset()                            # Preferences' button
    assert explain.should_explain("terminal") and explain.should_explain("cant_format")
    assert not explain.anything_to_reset()


def test_the_window_is_centred_over_the_app_and_kept_on_its_monitor():
    from tuxtalk.window_geometry import Rect
    right_monitor = Rect(2560, 0, 2560, 1440)
    # A window over an app on the right-hand monitor.
    assert explain.position_over((500, 240), (3400, 700), right_monitor) == (3150, 580)
    # An app near the monitor's edge: the window stays on that monitor.
    assert explain.position_over((500, 240), (5100, 100), right_monitor) == (4620, 0)


def test_it_opens_on_xwayland_where_it_may_be_placed(monkeypatch):
    started = []
    monkeypatch.setattr(explain, "_window", None)     # put back after the test
    monkeypatch.setattr(explain.subprocess, "Popen",
                        lambda cmd, env, **k: started.append((cmd, env)) or object())
    monkeypatch.setattr(explain, "_window", None)
    assert REAL_OPEN_WINDOW("terminal") is True
    cmd, env = started[0]
    assert cmd[-2:] == ["tuxtalk.explain", "terminal"]
    assert env["GDK_BACKEND"] == "x11,wayland"
