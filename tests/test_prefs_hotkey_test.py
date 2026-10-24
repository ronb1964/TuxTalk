"""Preferences' Test Hotkeys window: a key the service hears lights its row,
and hotkey test mode never outlasts the window. The window itself is never
opened here: its test mode reaches the real dictation service."""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import gtk_text  # noqa: E402
from tuxtalk import prefs_hotkey_test, service_launcher  # noqa: E402


def test_a_key_the_service_hears_lights_its_row():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    dialog, mark_tested = prefs_hotkey_test._test_window(None, "F6", "F9")
    try:
        mark_tested("toggle")
        shown = gtk_text.tree_text(dialog)
        assert "F9 (Tap-to-toggle): ✓ Working!" in shown
        assert "F6 (Hold-to-talk): Not tested" in shown
    finally:
        dialog.destroy()


def test_test_mode_ends_with_the_window_even_after_an_error(tmp_path, monkeypatch):
    """A flag left behind would keep dictation off until it went stale."""
    flag = tmp_path / "hotkey-test"
    monkeypatch.setattr(service_launcher, "hotkey_test_flag_path", lambda: str(flag))
    with pytest.raises(RuntimeError):
        with prefs_hotkey_test._test_mode():
            assert flag.exists()
            raise RuntimeError("the window failed")
    assert not flag.exists()
