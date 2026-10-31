"""Transcribe a File: closing the window or opening another file never loses
a transcript without asking."""
import os

import pytest
from gi.repository import Gdk, Gtk

from tuxtalk import file_transcribe as ft


@pytest.fixture
def win(monkeypatch, tmp_path):
    from tuxtalk import transcribe_window as tw
    # Transcripts are kept here, never in the real data folder.
    monkeypatch.setattr(ft, "recent_path", lambda: str(tmp_path / "recent-transcripts.json"))
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
        ft_remember, ft.remember = ft.remember, lambda *a: None
    try:
        w._on_done([ft.Piece(0.0, 5.0, text)], 5.0, 1.0, False)
    finally:
        if not kept_for_next_time:
            ft.remember = ft_remember


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
    first = [e for e in ft.recent() if e.path == "/videos/first.mov"][0]
    win.show_kept(first)
    assert [p.text for p in win.pieces] == ["First."] and win.path == "/videos/first.mov"



def test_an_earlier_one_asks_first_when_the_text_shown_wasnt_kept(win):
    """The list couldn't be saved (a full disk), so the text shown is the
    only copy: choosing an earlier transcript replaced it without a word."""
    win.path = "/videos/first.mov"
    done(win, "First.", kept_for_next_time=True)
    first = ft.recent()[0]
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
        ft.remember(f"/videos/{n}.mov", [ft.Piece(0, 1, str(n))], 1.0)
    ft.remember("/videos/5.mov", [ft.Piece(0, 1, "again")], 1.0)
    kept = ft.recent()
    assert len(kept) == ft.MAX_RECENT
    assert kept[0].path == "/videos/5.mov" and kept[0].pieces[0].text == "again"
    assert sum(e.path == "/videos/5.mov" for e in kept) == 1


def test_clearing_the_list(win):
    done(win, kept_for_next_time=True)
    win.answer = Gtk.ResponseType.ACCEPT
    win._on_clear_earlier()
    assert ft.recent() == [] and not win.remembered
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


def test_the_one_kept_before_the_list_comes_along(win, tmp_path):
    import json
    old = tmp_path / "last-transcript.json"
    old.write_text(json.dumps({"path": "/videos/old.mov", "total": 5, "when": 1.0,
                               "pieces": [[0, 5, "Old one."]]}))
    kept = ft.recent()
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
    ft.remember("/m/interview.mp3", hour, 3600.0)
    win.path = "/m/interview.mp3"
    win._on_done(hour[:30], 3600.0, 60.0, True)
    kept = ft.recent()
    assert len(kept) == 1 and len(kept[0].pieces) == 360
    assert "The longer transcript from before is still under" in win.status.get_text()
    assert win._on_close() is True          # the part shown isn't kept: closing asks


def test_a_rerun_stopped_further_on_replaces_the_shorter_copy(win):
    first = [ft.Piece(0.0, 10.0, "one.")]
    ft.remember("/m/talk.mp3", first, 600.0)
    win.path = "/m/talk.mp3"
    win._on_done(first + [ft.Piece(10.0, 20.0, "two.")], 600.0, 5.0, True)
    assert [p.text for p in ft.recent()[0].pieces] == ["one.", "two."]


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
