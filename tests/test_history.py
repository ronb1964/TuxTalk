"""Recent Dictations: the in-memory list of the last few dictations.

It exists so text that landed in the wrong window can be copied again, so the
important properties are that every dictation is recorded before it is typed,
that the list stays short and in RAM-backed storage, and that the tray and the
GNOME extension present it the same way.
"""
import os
import stat
from pathlib import Path

import numpy as np
import pytest

from tuxtalk import history
from tuxtalk import engine

ROOT = Path(__file__).resolve().parent.parent


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
    from tuxtalk import app

    order = []
    monkeypatch.setattr(engine, "transcribe", lambda audio, language: "hello there")
    monkeypatch.setattr(app, "_prepare_text", lambda raw, *a, **k: "Hello there. ")
    monkeypatch.setattr(app, "_beep", lambda *a, **k: None)
    monkeypatch.setattr(app, "_inject_text",
                        lambda text, mode, **kw: order.append(("typed", history.get_entries())))

    # A full second: a near-silent 0.1 s would be dropped as an accidental tap
    # (silence.is_speechless_tap) before reaching the engine.
    frames = [(np.ones(engine.SAMPLE_RATE, dtype=np.int16) * 1000).tobytes()]
    app._transcribe_and_inject(frames, engine.SAMPLE_RATE, beeps_on=False,
                               smart_quotes=False, notify_on=False)

    assert order == [("typed", ["Hello there."])]


def test_tray_and_gnome_extension_place_recent_dictations_first(tray_menu):
    """CLAUDE.md menu order: the on/off switch, then Recent Dictations, Fix a
    Word, Transcribe a File, in both. Restart Service sits at the end, with
    Help and Check for Updates (0.16 simplicity review)."""
    items = [item.get_label() or "---" for item in tray_menu.get_children()]
    assert items[:5] == ["Dictation on/off", "Recent Dictations", "Fix a Word...",
                         "Transcribe a File...", "---"]
    assert items[-4:] == ["Check for Updates...", "Restart Service", "---", "Quit TuxTalk"]

    js = (ROOT / "gnome-extension/talktype@ronb1964.github.io/extension.js").read_text()
    switch = js.index("new PopupMenu.PopupSwitchMenuItem('Dictation on/off'")
    recent = js.index("new PopupMenu.PopupSubMenuMenuItem('Recent Dictations')")
    fix_word = js.index("new PopupMenu.PopupMenuItem('Fix a Word...')")
    transcribe = js.index("new PopupMenu.PopupMenuItem('Transcribe a File...')")
    separator = js.index("new PopupMenu.PopupSeparatorMenuItem()", recent)
    updates = js.index("new PopupMenu.PopupMenuItem('Check for Updates...')")
    restart = js.index("new PopupMenu.PopupMenuItem('Restart Service')")
    quit_item = js.index("new PopupMenu.PopupMenuItem('Quit TuxTalk')")
    assert switch < recent < fix_word < transcribe < separator < updates < restart < quit_item


def test_full_text_lines_wraps_keeps_breaks_and_caps():
    lines = history.full_text_lines("First paragraph here.\n\nSecond " + "word " * 30, width=20)
    assert lines[0] == "First paragraph"
    assert "" not in lines                      # blank lines dropped
    assert all(len(l) <= 20 for l in lines)

    many = history.full_text_lines("word " * 500, width=20, max_lines=10)
    assert len(many) == 10 and many[-1].endswith("…")


def test_each_tray_entry_opens_a_hover_view_with_copy():
    """KDE drops tooltips from tray menus, so the full text lives in a submenu."""
    import types
    import gi
    gi.require_version("Gtk", "3.0")
    from tuxtalk.tray import DictationTray

    tray = types.SimpleNamespace()
    tray._copy_history_entry = lambda text: copied.append(text)
    tray.show_fix_word_dialog = lambda index: fixing.append(index)
    copied, fixing = [], []
    text = "One.\nTwo " + "word " * 30
    submenu = DictationTray._build_history_entry_submenu(tray, text, 3)

    items = {c.get_label(): c for c in submenu.get_children()}
    labels = [c.get_label() for c in submenu.get_children()]
    assert labels[0] == "One." and labels[-2:] == ["Copy", "Fix a Word..."]
    items["Copy"].activate()
    assert copied == [text]   # the whole dictation, not the wrapped display
    items["Fix a Word..."].activate()
    assert fixing == [3]      # opens the Fix a Word window on this dictation
