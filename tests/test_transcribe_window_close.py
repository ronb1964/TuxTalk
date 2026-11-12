"""Transcribe a File: closing the window or opening another file never loses
a transcript without asking."""
import os

import pytest
from gi.repository import Gdk, Gtk

from tuxtalk import file_transcribe as ft
from tuxtalk import transcript_history as history


@pytest.fixture
def win(monkeypatch, tmp_path):
    from tuxtalk import transcribe_window as tw
    # Transcripts are kept here, never in the real data folder.
    monkeypatch.setattr(history, "recent_path", lambda: str(tmp_path / "recent-transcripts.json"))
    monkeypatch.setattr(history, "_reported", set())     # each test sees its own warnings
    monkeypatch.setattr(Gtk, "main_quit", lambda: None)
    # Never on the desktop: the windows are built but not shown.
    monkeypatch.setattr(Gtk.Window, "show_all", lambda self: None)
    w = tw.TranscribeWindow()
    w.asked = []
    w.answer = Gtk.ResponseType.CANCEL

    def ask(title, text, buttons):
        w.asked.append((title, text, [label for label, _ in buttons]))
        return w.answer

    w._ask = ask
    yield w
    w.window.destroy()


def done(w, text="Hello there.", kept_for_next_time=False):
    """A file finished. Keeping it for next time is off unless asked for, so
    the close question can be tested; it's on in real use."""
    if not kept_for_next_time:
        kept_remember, history.remember = history.remember, lambda *a: None
    try:
        w._on_done([ft.Piece(0.0, 5.0, text)], 5.0, 1.0, False)
    finally:
        if not kept_for_next_time:
            history.remember = kept_remember


def test_nothing_to_lose_closes_without_asking(win):
    assert win._on_close() is False
    assert win.asked == []


def test_unsaved_text_asks_and_cancel_keeps_the_window(win):
    done(win)
    assert win._on_close() is True                 # stays open
    title, text, buttons = win.asked[0]
    assert title == "Save the text first?" and "Closing the window loses it." in text
    assert buttons == ["Cancel", "Don't Save", "Save as Text…"]


def test_dont_save_closes(win):
    done(win)
    win.answer = Gtk.ResponseType.REJECT
    assert win._on_close() is False


def test_save_then_close(win, monkeypatch):
    done(win)
    win.answer = Gtk.ResponseType.ACCEPT
    monkeypatch.setattr(win, "_save", lambda kind: setattr(win, "kept", True))
    assert win._on_close() is False


def test_a_cancelled_save_keeps_the_window(win, monkeypatch):
    done(win)
    win.answer = Gtk.ResponseType.ACCEPT
    monkeypatch.setattr(win, "_save", lambda kind: None)
    assert win._on_close() is True


def test_copied_text_still_asks(win):
    """The next copy replaces the clipboard, so a copy isn't kept."""
    done(win)
    win._on_copy(None)
    assert win._on_close() is True
    assert "You copied it, but it isn't saved to a file." in win.asked[0][1]


def test_saved_text_closes_without_asking(win):
    done(win)
    win.kept = True                    # what a successful _save sets
    assert win._on_close() is False and win.asked == []


def test_a_new_transcript_is_unsaved_again(win):
    done(win)
    win.kept = True
    done(win, "Another one.")
    win._on_close()
    assert win.asked


def test_another_file_asks_first(win):
    done(win)
    assert win._may_discard("new") is False
    assert "Opening another file replaces it." in win.asked[0][1]


def test_closing_while_transcribing_asks_to_stop(win):
    win._set_busy(True)
    assert win._on_close() is True
    assert win.asked[0][0] == "Stop transcribing?"
    win.answer = Gtk.ResponseType.ACCEPT
    assert win._on_close() is False
    assert win._cancel.is_set()


# --- earlier transcripts, kept for next time ---------------------------------

def test_a_kept_transcript_still_asks_on_close(win):
    """The question comes on every close, as insurance; it says the text
    is kept, so it isn't a scare. Don't Save then closes."""
    done(win, kept_for_next_time=True)
    assert win.remembered
    assert "Earlier Transcripts" in win.status.get_text()
    assert win._on_close() is True
    assert "It's kept under Earlier Transcripts" in win.asked[0][1]
    win.answer = Gtk.ResponseType.REJECT
    assert win._on_close() is False


def test_another_file_loses_nothing_when_kept(win):
    done(win, kept_for_next_time=True)
    assert win._may_discard("new") is True and win.asked == []


def test_reopening_shows_the_newest_transcript(win):
    from tuxtalk import transcribe_window as tw
    win.path = "/videos/Cleaver Maintenance.mov"
    done(win, "The blade gets dull.", kept_for_next_time=True)
    again = tw.TranscribeWindow()
    try:
        again.recall()
        assert [p.text for p in again.pieces] == ["The blade gets dull."]
        assert again.path == "/videos/Cleaver Maintenance.mov"
        assert "Cleaver Maintenance.mov" in again.file_label.get_text()
        assert again.srt_btn.get_sensitive() and again.txt_btn.get_sensitive()
        assert again.status.get_text().startswith("Transcribed ")
    finally:
        again.window.destroy()


def test_earlier_transcripts_lists_them_newest_first(win):
    for n in range(3):
        win.path = f"/videos/part{n}.mov"
        done(win, f"Part {n}.", kept_for_next_time=True)
    labels = [i.get_label() for i in win.earlier_menu.get_children()
              if isinstance(i, Gtk.MenuItem) and not isinstance(i, Gtk.SeparatorMenuItem)]
    assert labels[0].startswith("part2.mov") and labels[2].startswith("part0.mov")
    assert labels[-1] == "Clear This List…"
    assert win.earlier_btn.get_sensitive()


def test_choosing_an_earlier_one_shows_it(win):
    win.path = "/videos/first.mov"
    done(win, "First.", kept_for_next_time=True)
    win.path = "/videos/second.mov"
    done(win, "Second.", kept_for_next_time=True)
    first = [e for e in history.recent() if e.path == "/videos/first.mov"][0]
    win.show_kept(first)
    assert [p.text for p in win.pieces] == ["First."] and win.path == "/videos/first.mov"



def test_an_earlier_one_asks_first_when_the_text_shown_wasnt_kept(win):
    """The list couldn't be saved (a full disk), so the text shown is the
    only copy: choosing an earlier transcript replaced it without a word."""
    win.path = "/videos/first.mov"
    done(win, "First.", kept_for_next_time=True)
    first = history.recent()[0]
    win.path = "/videos/second.mov"
    done(win, "Second, only here.")                 # not kept
    item = next(i for i in win.earlier_menu.get_children()
                if i.get_label().startswith("first.mov"))
    item.activate()
    assert win.asked and win.asked[0][0] == "Save the text first?"
    assert [p.text for p in win.pieces] == ["Second, only here."]      # Cancel kept it
    win.answer = Gtk.ResponseType.REJECT
    item.activate()
    assert [p.text for p in win.pieces] == ["First."] and first.path == win.path

def test_only_ten_are_kept_and_a_file_done_again_replaces_its_copy(win):
    for n in range(12):
        history.remember(f"/videos/{n}.mov", [ft.Piece(0, 1, str(n))], 1.0)
    history.remember("/videos/5.mov", [ft.Piece(0, 1, "again")], 1.0)
    kept = history.recent()
    assert len(kept) == history.MAX_RECENT
    assert kept[0].path == "/videos/5.mov" and kept[0].pieces[0].text == "again"
    assert sum(e.path == "/videos/5.mov" for e in kept) == 1


def test_clearing_the_list(win):
    done(win, kept_for_next_time=True)
    win.answer = Gtk.ResponseType.ACCEPT
    win._on_clear_earlier()
    assert history.recent() == [] and not win.remembered
    assert not win.earlier_btn.get_sensitive()
    win.answer = Gtk.ResponseType.CANCEL
    assert win._on_close() is True              # now closing would lose it
    assert "Closing the window loses it." in win.asked[-1][1]


def test_nothing_kept_opens_empty(win):
    win.recall()
    assert win.pieces == [] and not win.srt_btn.get_sensitive()
    assert not win.earlier_btn.get_sensitive()


def test_the_kept_transcripts_are_private(win, tmp_path):
    import stat
    done(win, kept_for_next_time=True)
    mode = (tmp_path / "recent-transcripts.json").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600


def test_a_broken_list_is_ignored(win, tmp_path):
    (tmp_path / "recent-transcripts.json").write_text("{not json")
    win.recall()
    assert win.pieces == []


def _kept_list(path):
    import json
    return json.dumps([{"path": path, "total": 5, "when": 1.0, "pieces": [[0, 5, "Kept."]]}])


def _aside(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir()
                  if p.name.startswith("recent-transcripts.json.corrupt-"))


def test_a_damaged_list_survives_the_next_transcript(win, tmp_path, caplog):
    """Whatever was in it, the bytes are still there after the next
    transcript is kept, set aside under the name a damaged config gets."""
    damaged = '[{"path": "/videos/hour.mov", "pieces": [[0, 3600, "An hour of'
    (tmp_path / "recent-transcripts.json").write_text(damaged)
    assert history.remember("/videos/new.mov", [ft.Piece(0, 1, "New.")], 1.0) is not None
    [aside] = _aside(tmp_path)
    assert (tmp_path / aside).read_text() == damaged
    assert [e.path for e in history.recent()] == ["/videos/new.mov"]
    assert "set aside" in caplog.text


def test_a_list_with_one_damaged_transcript_keeps_it_aside(win, tmp_path):
    """The JSON reads, but one entry doesn't: it isn't dropped for good."""
    import json
    good = json.loads(_kept_list("/videos/good.mov"))
    original = json.dumps(good + [{"path": "/videos/hour.mov", "total": "damaged",
                                   "pieces": [[0, 3600, "An hour of talk."]]}])
    (tmp_path / "recent-transcripts.json").write_text(original)
    history.remember("/videos/new.mov", [ft.Piece(0, 1, "New.")], 1.0)
    [aside] = _aside(tmp_path)
    assert (tmp_path / aside).read_text() == original
    assert [e.path for e in history.recent()] == ["/videos/new.mov", "/videos/good.mov"]


def test_a_second_damaged_list_in_the_same_second_keeps_the_first(win, tmp_path, monkeypatch):
    import time
    import types
    monkeypatch.setattr(history, "time", types.SimpleNamespace(
        **{**vars(time), "strftime": lambda fmt: "20261111-093000"}))
    for damaged in ("{first", "{second"):
        (tmp_path / "recent-transcripts.json").write_text(damaged)
        history.remember("/videos/new.mov", [ft.Piece(0, 1, "New.")], 1.0)
    assert [(tmp_path / a).read_text() for a in _aside(tmp_path)] == ["{first", "{second"]


def test_a_damaged_list_that_cant_be_moved_is_left_alone(win, tmp_path, monkeypatch):
    """The new transcript isn't kept then, so closing asks before losing it."""
    import os
    import types

    def cant_move(*args):
        raise OSError("read-only")
    monkeypatch.setattr(history, "os", types.SimpleNamespace(**{**vars(os), "rename": cant_move}))
    (tmp_path / "recent-transcripts.json").write_text("{not json")
    done(win, kept_for_next_time=True)
    assert not win.remembered
    assert (tmp_path / "recent-transcripts.json").read_text() == "{not json"


def test_a_list_that_cant_be_read_just_now_is_neither_moved_nor_written(win, tmp_path,
                                                                         monkeypatch):
    """Too many files open, say: the list is fine, so it isn't set aside."""
    (tmp_path / "recent-transcripts.json").write_text(_kept_list("/videos/good.mov"))

    def busy(target):
        raise OSError(24, "Too many open files")
    monkeypatch.setattr(history, "_read_list", busy)
    assert history.remember("/videos/new.mov", [ft.Piece(0, 1, "New.")], 1.0) is None
    assert _aside(tmp_path) == []
    assert (tmp_path / "recent-transcripts.json").read_text() == _kept_list("/videos/good.mov")


def test_clearing_the_list_clears_every_copy_kept(win, tmp_path):
    """The damaged ones set aside, and an older TuxTalk's one transcript,
    which would otherwise come back as the list."""
    (tmp_path / "recent-transcripts.json").write_text("{not json")
    history.remember("/videos/new.mov", [ft.Piece(0, 1, "New.")], 1.0)
    (tmp_path / "last-transcript.json").write_text(_kept_list("/videos/old.mov"))
    assert history.forget_all()
    assert history.recent() == [] and sorted(p.name for p in tmp_path.iterdir()) == []


def test_clearing_the_list_clears_a_half_written_one_too(win, tmp_path):
    """A write killed midway leaves the transcripts in full in the .tmp
    beside the list: Clear This List left them there."""
    history.remember("/videos/new.mov", [ft.Piece(0, 1, "New.")], 1.0)
    (tmp_path / "recent-transcripts.json.tmp").write_text(_kept_list("/videos/private.mov"))
    assert history.forget_all()
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_a_clear_that_cant_see_the_copies_says_so_and_keeps_the_list(win, tmp_path,
                                                                     monkeypatch):
    """Copies it can't list may still be there: not cleared, and the list
    stays, so the window's transcript is still kept."""
    import os
    import types
    (tmp_path / "recent-transcripts.json").write_text(_kept_list("/videos/good.mov"))

    def unreadable(folder):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(history, "os", types.SimpleNamespace(**{**vars(os), "scandir": unreadable}))
    assert not history.forget_all()
    assert [e.path for e in history.recent()] == ["/videos/good.mov"]


def test_a_damaged_transcript_an_older_tuxtalk_kept_isnt_deleted(win, tmp_path):
    damaged = ('{"path": "/videos/hour.mov", "total": "damaged", '
               '"pieces": [[0, 3600, "An hour of talk."]]}')
    (tmp_path / "last-transcript.json").write_text(damaged)
    assert history.recent() == []
    history.remember("/videos/new.mov", [ft.Piece(0, 1, "New.")], 1.0)
    assert (tmp_path / "last-transcript.json").read_text() == damaged


@pytest.mark.parametrize("damage", ['"path": 123', '"path": 0', '"path": false', '"path": []',
                                    '"when": "nan"', '"when": 1e300', '"total": "inf"',
                                    '"pieces": [[0, 1e400, "Kept."]]',
                                    '"pieces": [[0, 1' + '0' * 400 + ', "Kept."]]',
                                    '"pieces": [[1e308, 1e308, "Kept."]]',
                                    '"pieces": [[-5, 0, "Kept."]]'])
def test_a_transcript_with_a_field_it_cant_use_counts_as_damaged(win, tmp_path, damage):
    """Showing it under Earlier Transcripts broke the window, or reading the
    list itself raised."""
    # A key given twice keeps its last value, so the damaged field wins.
    original = '[{"total": 5, "when": 1.0, "pieces": [[0, 5, "Kept."]], ' + damage + '}]'
    (tmp_path / "recent-transcripts.json").write_text(original)
    assert history.recent() == []
    history.remember("/videos/new.mov", [ft.Piece(0, 1, "New.")], 1.0)
    [aside] = _aside(tmp_path)
    assert (tmp_path / aside).read_text() == original


def test_the_one_kept_before_the_list_comes_along(win, tmp_path):
    import json
    old = tmp_path / "last-transcript.json"
    old.write_text(json.dumps({"path": "/videos/old.mov", "total": 5, "when": 1.0,
                               "pieces": [[0, 5, "Old one."]]}))
    kept = history.recent()
    assert [e.path for e in kept] == ["/videos/old.mov"]
    assert not old.exists() and (tmp_path / "recent-transcripts.json").exists()


# --- Remove ums and repeats ---------------------------------------------------

def test_the_tick_box_cleans_what_is_shown_and_saved(win, monkeypatch, tmp_path):
    win.path = str(tmp_path / "clip.mov")
    done(win, "This is this is uh a blade.")
    buf = win.view.get_buffer()
    text = lambda: buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    assert "uh" in text()
    assert win.clean_check.get_sensitive()
    win.clean_check.set_active(True)
    assert text() == "This is a blade."
    saved = {}

    class Chooser:
        def __init__(self, *a): pass
        def set_current_name(self, n): saved["name"] = n
        def set_current_folder(self, f): pass
        def set_do_overwrite_confirmation(self, v): pass
        def run(self): return Gtk.ResponseType.ACCEPT
        def get_filename(self): return str(tmp_path / saved["name"])
        def destroy(self): pass

    monkeypatch.setattr(Gtk.FileChooserNative, "new", lambda *a: Chooser())
    win._save("srt")
    assert "This is a blade." in (tmp_path / "clip.srt").read_text()
    assert "uh" not in (tmp_path / "clip.srt").read_text()
    win.clean_check.set_active(False)
    assert "uh" in text()                       # as heard again


def test_a_save_that_fails_leaves_the_file_it_would_replace(win, monkeypatch, tmp_path):
    """A full disk: the transcript saved there before is still whole."""
    win.path = str(tmp_path / "clip.mov")
    done(win, "Meet me at noon.")
    earlier = tmp_path / "clip.txt"
    earlier.write_text("the transcript from yesterday\n")

    class Chooser:
        def __init__(self, *a): pass
        def set_current_name(self, n): pass
        def set_current_folder(self, f): pass
        def set_do_overwrite_confirmation(self, v): pass
        def run(self): return Gtk.ResponseType.ACCEPT
        def get_filename(self): return str(earlier)
        def destroy(self): pass

    monkeypatch.setattr(Gtk.FileChooserNative, "new", lambda *a: Chooser())

    def disk_full(fd):
        raise OSError(28, "No space left on device")
    with monkeypatch.context() as full:
        full.setattr(os, "fsync", disk_full)
        win._save("txt")
    assert earlier.read_text() == "the transcript from yesterday\n"
    assert not win.kept
    earlier.chmod(0o600)                                  # kept private by its owner
    win._save("txt")
    assert "Meet me at noon." in earlier.read_text()
    assert earlier.stat().st_mode & 0o777 == 0o600


def test_a_new_transcript_gets_the_users_usual_permissions(tmp_path):
    from tuxtalk import transcribe_window as tw
    old = os.umask(0o027)
    try:
        assert tw._saved_file_mode(str(tmp_path / "new.srt")) == 0o640
    finally:
        os.umask(old)


# --- dropping a file anywhere, with a hint where ---------------------------------

def test_the_text_area_takes_dropped_files(win):
    targets = win.view.drag_dest_get_target_list()
    assert targets is not None and targets.find(Gdk.Atom.intern("text/uri-list", False))[0]


def test_a_drop_on_the_text_starts_the_file(win, monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(win, "start", started.append)
    monkeypatch.setattr(win.view, "stop_emission_by_name", lambda name: None)
    clip = tmp_path / "talk.mp3"
    clip.write_bytes(b"")

    class Data:
        def get_uris(self):
            return [clip.as_uri()]

    win._on_view_drop(win.view, None, 0, 0, Data(), 0, 0)
    assert started == [str(clip)]


def test_the_drop_hint_shows_only_while_empty(win):
    assert win.drop_hint.get_visible()
    done(win)
    assert not win.drop_hint.get_visible()
    win._set_busy(True)
    assert not win.drop_hint.get_visible()


def test_a_screen_reader_names_the_transcript(win):
    assert win.view.get_accessible().get_name() == "Transcript"


def test_a_rerun_stopped_early_keeps_the_whole_transcript_from_before(win):
    """An hour transcribed and kept, the same file chosen again and stopped
    five minutes in: the hour stays under Earlier Transcripts."""
    hour = [ft.Piece(n * 10.0, n * 10.0 + 10.0, f"part {n}.") for n in range(360)]
    history.remember("/m/interview.mp3", hour, 3600.0)
    win.path = "/m/interview.mp3"
    win._on_done(hour[:30], 3600.0, 60.0, True)
    kept = history.recent()
    assert len(kept) == 1 and len(kept[0].pieces) == 360
    assert "The longer transcript from before is still under" in win.status.get_text()
    assert win._on_close() is True          # the part shown isn't kept: closing asks


def test_a_rerun_stopped_further_on_replaces_the_shorter_copy(win):
    first = [ft.Piece(0.0, 10.0, "one.")]
    history.remember("/m/talk.mp3", first, 600.0)
    win.path = "/m/talk.mp3"
    win._on_done(first + [ft.Piece(10.0, 20.0, "two.")], 600.0, 5.0, True)
    assert [p.text for p in history.recent()[0].pieces] == ["one.", "two."]


def test_a_web_link_dropped_says_only_files_can_be_transcribed(win, monkeypatch):
    started = []
    monkeypatch.setattr(win, "start", started.append)

    class Data:
        def get_uris(self):
            return ["https://example.com/a.mp3"]

    win._on_drop(win.view, None, 0, 0, Data(), 0, 0)
    assert started == [] and win.asked == []
    assert win.status.get_text() == "Only a file on this computer can be transcribed."


def test_copy_all_outlives_the_window(win, monkeypatch):
    """clipboard.copy_text hands the text to wl-copy or the clipboard
    manager; GTK's own copy went when this window's process ended."""
    from tuxtalk import clipboard
    copied = []
    monkeypatch.setattr(clipboard, "copy_text", lambda text: copied.append(text) or True)
    done(win, "Keep me.")
    win._on_copy(None)
    assert copied == ["Keep me."] and win.copied


def _escape(w):
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = Gdk.KEY_Escape
    return w.window.emit("key-press-event", event)


def test_escape_asks_about_unsaved_text_like_the_close_button(win, monkeypatch):
    """Escape did nothing here, where every other TuxTalk window closes."""
    quit_calls = []
    monkeypatch.setattr(Gtk, "main_quit", lambda: quit_calls.append(True))
    done(win)
    assert _escape(win) is True
    assert win.asked[0][0] == "Save the text first?"
    assert quit_calls == []                         # Cancel keeps it open


def test_escape_with_nothing_to_lose_closes(win, monkeypatch):
    quit_calls = []
    monkeypatch.setattr(Gtk, "main_quit", lambda: quit_calls.append(True))
    _escape(win)
    assert win.asked == [] and quit_calls == [True]


def test_a_failed_file_never_writes_what_was_heard_to_the_log(win, monkeypatch, caplog):
    """An engine's error can quote the words it heard: the log keeps the
    error's type, and only the window shows the whole of it."""
    from gi.repository import GLib
    shown = []

    heard = "my secret words"

    def fails(path):
        raise ValueError(f"couldn't handle {heard!r}")      # not this line: tracebacks show it
    monkeypatch.setattr(ft, "decode", fails)
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: shown.append(args))
    win._work("/recording.mp3")
    assert "File transcription failed: ValueError" in caplog.text
    assert "secret" not in caplog.text
    assert any("my secret words" in str(args) for args in shown)


def test_a_new_card_pick_reloads_the_open_windows_model(win, monkeypatch):
    """The window keeps its model between files: one picked on another card
    since the last file must load there, not stay on the old one."""
    import numpy as np
    from gi.repository import GLib
    from tuxtalk import config
    loaded = []

    class Engine:
        describe = "Small on the graphics card (CUDA)"

        def __init__(self, cfg):
            self.card, self.closed = cfg.graphics_card, False
            loaded.append(self)

        def reload_words(self):
            pass

        def close(self):
            self.closed = True

    cards = iter(["", "NVIDIA GeForce RTX 5090"])
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(
        model="small", device="cuda", graphics_card=next(cards)))
    monkeypatch.setattr(ft, "decode", lambda path: np.zeros(ft.SAMPLE_RATE, dtype=np.float32))
    monkeypatch.setattr(ft, "load_engine", Engine)
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: None)
    win._work("/first.mp3")
    win._work("/second.mp3")
    assert [(e.card, e.closed) for e in loaded] == [("", True), ("NVIDIA GeForce RTX 5090", False)]
    assert win.engine is loaded[1]
