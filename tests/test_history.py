"""Recent Dictations: the in-memory list of the last few dictations.

It exists so text that landed in the wrong window can be copied again, so the
important properties are that every dictation is recorded before it is typed,
that the list stays short and in RAM-backed storage, and that the tray and the
GNOME extension present it the same way.
"""
import os
import stat

import numpy as np
import pytest

from tuxtalk import history
from tuxtalk import recording_state as rs
from tuxtalk import engine
from tuxtalk import dictation_text
from tuxtalk import delivery
from tuxtalk import pipeline


# Saved before conftest points every test at a throwaway file: these tests
# check where the real one goes.
_REAL_HISTORY_PATH = history._history_path


@pytest.fixture(autouse=True)
def runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_history_path", _REAL_HISTORY_PATH)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    return tmp_path


def test_newest_first_and_capped():
    for i in range(history.MAX_ENTRIES + 5):
        history.add_entry(f"dictation {i}")
    entries = history.get_entries()
    assert len(entries) == history.MAX_ENTRIES
    assert entries[0] == f"dictation {history.MAX_ENTRIES + 4}"


def test_repeating_a_dictation_moves_it_to_the_top_instead_of_duplicating():
    history.add_entry("one")
    history.add_entry("two")
    history.add_entry("one")
    assert history.get_entries() == ["one", "two"]


def test_an_entry_can_replace_the_one_it_grew_out_of():
    """Type at pause: a recording's entry grows phrase by phrase."""
    history.add_entry("earlier")
    history.add_entry("We meet")
    history.add_entry("We meet. The end.", replaces="We meet ")
    assert history.get_entries() == ["We meet. The end.", "earlier"]


def test_spoken_line_breaks_are_stored_as_real_newlines():
    history.add_entry("First line.\xa7SHIFT_ENTER\xa7Second line. ")
    assert history.get_entries() == ["First line.\nSecond line."]


def test_blank_text_is_ignored():
    history.add_entry("   ")
    history.add_entry(None)
    assert history.get_entries() == []


def test_file_lives_in_the_runtime_dir_and_is_private(runtime_dir):
    history.add_entry("secret-ish")
    path = runtime_dir / "talktype-history.json"
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_without_a_private_runtime_folder_nothing_is_kept(runtime_dir, monkeypatch):
    """A misconfigured, shared runtime folder, or none: the locks fall back to
    /tmp, which may be on disk, so dictated text would outlive the session
    there. Recent Dictations keeps nothing instead, never written anywhere."""
    from tuxtalk import single_instance
    shared = runtime_dir / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    monkeypatch.setattr(single_instance, "FALLBACK_ROOT", str(runtime_dir))
    for session in (str(shared), str(runtime_dir / "missing")):
        monkeypatch.setenv("XDG_RUNTIME_DIR", session)
        history.add_entry("secret-ish")
        assert history.get_entries() == []
        history.clear()
    written = [p for p in runtime_dir.rglob("*") if p.is_file()]
    assert written == []


def test_a_private_runtime_folder_keeps_them(runtime_dir):
    history.add_entry("first")
    history.add_entry("second")
    assert history.get_entries() == ["second", "first"]
    assert (runtime_dir / "talktype-history.json").exists()


def test_clear_and_missing_or_corrupt_file(runtime_dir):
    assert history.get_entries() == []          # no file yet
    history.add_entry("x")
    history.clear()
    assert history.get_entries() == []
    history.clear()                             # clearing twice is fine
    (runtime_dir / "talktype-history.json").write_text("{not json")
    assert history.get_entries() == []


def test_preview_is_one_short_line():
    assert history.preview("short") == "short"
    long_text = "word " * 40
    p = history.preview("line one\nline two " + long_text)
    assert "\n" not in p
    assert len(p) <= history.PREVIEW_CHARS and p.endswith("…")


def test_dictation_is_recorded_before_it_is_typed(monkeypatch):
    """Even if typing goes to the wrong place or fails, the text is recoverable."""

    order = []
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: "hello there")
    monkeypatch.setattr(dictation_text, "prepare_text", lambda raw, *a, **k: "Hello there. ")
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr(delivery, "inject_text",
                        lambda text, mode, **kw: order.append(("typed", history.get_entries())))

    # A full second: a near-silent 0.1 s would be dropped as an accidental tap
    # (silence.is_speechless_tap) before reaching the engine.
    frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    pipeline.transcribe_and_inject(frames, engine.SAMPLE_RATE, beeps_on=False,
                               smart_quotes=False, notify_on=False,
                               message=rs.state.current.message)

    assert order == [("typed", ["Hello there."])]


def test_the_tray_places_recent_dictations_first(tray_menu):
    """CLAUDE.md menu order: the on/off switch, then Recent Dictations, Fix a
    Word, Transcribe a File. Restart Service sits at the end, with Help and
    Check for Updates (0.16 simplicity review). The GNOME menu matches it
    item for item (test_gnome_extension)."""
    items = [item.get_label() or "---" for item in tray_menu.get_children()]
    assert items[:5] == ["Turn Dictation On", "Recent Dictations", "Fix a Word...",
                         "Transcribe a File...", "---"]        # off until the status comes
    assert items[-4:] == ["Check for Updates...", "Restart Service", "---", "Quit TuxTalk"]


def test_full_text_lines_wraps_keeps_breaks_and_caps():
    lines = history.full_text_lines("First paragraph here.\n\nSecond " + "word " * 30, width=20)
    assert lines[0] == "First paragraph"
    assert "" not in lines                      # blank lines dropped
    assert all(len(l) <= 20 for l in lines)

    many = history.full_text_lines("word " * 500, width=20, max_lines=10)
    assert len(many) == 10 and many[-1].endswith("…")


def test_each_tray_entry_opens_a_hover_view_with_copy(monkeypatch):
    """KDE drops tooltips from tray menus, so the full text lives in a submenu."""
    import types
    import gi
    gi.require_version("Gtk", "3.0")
    from tuxtalk import tray_menu

    copied, fixing = [], []
    monkeypatch.setattr(tray_menu, "copy_dictation", copied.append)
    menu = types.SimpleNamespace(
        tray=types.SimpleNamespace(show_fix_word_dialog=fixing.append))
    text = "One.\nTwo " + "word " * 30
    submenu = tray_menu.TrayMenu._history_entry_submenu(menu, text, 3)

    items = {c.get_label(): c for c in submenu.get_children()}
    labels = [c.get_label() for c in submenu.get_children()]
    assert labels[0] == "One." and labels[-2:] == ["Copy", "Fix a Word..."]
    items["Copy"].activate()
    assert copied == [text]   # the whole dictation, not the wrapped display
    items["Fix a Word..."].activate()
    assert fixing == [3]      # opens the Fix a Word window on this dictation


def test_dictation_is_named_for_what_a_click_does(monkeypatch):
    """"Turn Dictation Off" (checkmarked, as it's on) or "Turn Dictation On":
    "Dictation off" read as an action as easily as a state. The words carry
    the state, so a screen reader reads it on every desktop. The tray's
    status poll only changes the words; a click starts or stops dictation."""
    from tuxtalk import config, tray, tray_menu
    monkeypatch.setattr(config, "load_config", config.Settings)
    t = tray.DictationTray.__new__(tray.DictationTray)
    asked = []
    monkeypatch.setattr(t, "start_service", lambda w: asked.append("start"), raising=False)
    monkeypatch.setattr(t, "stop_service", lambda w: asked.append("stop"), raising=False)
    t.dbus_service = None
    t.menu = tray_menu.TrayMenu(t)
    toggle = t.menu.service_toggle
    try:
        for running in (True, False, True):   # the status poll, as it runs
            t.update_menu_display(is_running=running)
            assert toggle.get_label() == ("Turn Dictation Off" if running
                                          else "Turn Dictation On")
        assert asked == []
        toggle.activate()                      # the user clicks it: off
        assert toggle.get_label() == "Turn Dictation On"
        toggle.activate()                      # and on again
        assert asked == ["stop", "start"]
    finally:
        t.menu.widget.destroy()
