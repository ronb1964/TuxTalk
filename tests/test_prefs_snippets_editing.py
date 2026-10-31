"""Editing the Snippets list must not save rows the user never filled in,
lose a row to a duplicate phrase, or let a write error escape OK/Apply."""
from types import SimpleNamespace

import pytest

from tuxtalk import prefs_words as W
from tuxtalk.prefs import PreferencesWindow


class FakeStore(list):
    """A ListStore stand-in: rows are lists, paths are strings like "1"."""

    def __getitem__(self, path):
        return list.__getitem__(self, int(str(path)))

    def append(self, row):
        list.append(self, list(row))
        return len(self) - 1          # the "iter"

    def get_path(self, it):
        return str(it)


@pytest.fixture(autouse=True)
def shown(monkeypatch):
    """The titles of the message boxes Preferences showed."""
    titles = []
    monkeypatch.setattr(W, "show_message", lambda parent, kind, title, text="", buttons=None: titles.append(title))
    return titles


def _window(rows):
    """A Words tab with stand-in lists instead of the built page."""
    d = W.WordsTab(SimpleNamespace(window=None))
    d.commands_store = FakeStore([list(r) for r in rows])
    d.words_store = FakeStore()
    d._commands_loaded = {}
    d.commands_tree = SimpleNamespace(set_cursor=lambda *a: None, get_column=lambda i: i)
    return d


def test_an_untouched_new_row_is_not_saved():
    """The hint text in a new row is not a snippet; saving it would make
    saying "new phrase" type "replacement text"."""
    d = _window([["talk type", "TalkType"]])

    d._on_add_command(None)

    assert d._commands_from_store() == {"talk type": "TalkType"}


def test_a_duplicate_phrase_is_refused_and_explained(monkeypatch, shown):
    """Two rows with one phrase would save as one, silently dropping a row."""
    monkeypatch.setattr(W.GLib, "idle_add", lambda fn, *a: fn(*a))
    d = _window([["talk type", "TalkType"], ["", ""]])

    d._on_phrase_edited(None, "1", "  Talk Type ")
    d._on_phrase_edited(None, "1", "Talk Type")   # focus-out commits it again

    assert d.commands_store[1][0] == "", "the duplicate replaced the row's phrase"
    assert "That phrase is already a snippet" in shown


def test_the_duplicate_warning_shows_once_per_commit(monkeypatch, shown):
    pending = []
    monkeypatch.setattr(W.GLib, "idle_add", lambda fn, *a: pending.append(fn))
    d = _window([["talk type", "TalkType"], ["", ""]])

    d._on_phrase_edited(None, "1", "talk type")   # "edited"
    d._on_phrase_edited(None, "1", "talk type")   # and focus-out
    for fn in pending:
        fn()

    assert shown == ["That phrase is already a snippet"]


def test_a_row_may_keep_its_own_phrase(shown):
    d = _window([["talk type", "TalkType"]])

    d._on_phrase_edited(None, "0", "Talk Type")

    assert d.commands_store[0][0] == "talk type"
    assert shown == []


def test_a_write_error_is_reported_not_raised(tmp_path, monkeypatch, shown):
    """An OSError (full disk, read-only folder) must not escape the OK
    handler, where neither the snippets nor the settings get saved and
    nothing is said."""
    from tuxtalk import config as C

    monkeypatch.setattr(C, "CUSTOM_COMMANDS_PATH", str(tmp_path / "custom_commands.toml"))
    monkeypatch.setattr(C, "_commands_read_failed", False, raising=False)

    def full_disk(commands):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(W, "save_custom_commands", full_disk)

    d = _window([["talk type", "TalkType"]])
    assert d.save() is False
    assert shown == ["Your snippets weren't saved"]


@pytest.mark.parametrize("changed", [{"beeps"}, set()], ids=["settings changed", "none"])
def test_ok_stays_open_when_the_snippets_could_not_be_written(changed):
    """Closing right after saying the snippets weren't saved would throw the
    unsaved edits away. Settings changed are saved and applied all the
    same, as Apply does."""
    steps = []
    d = SimpleNamespace(words_tab=SimpleNamespace(save=lambda: False),
                        _changed_since_open=lambda: changed,
                        _apply_settings=lambda: steps.append("settings applied"),
                        _release=lambda: steps.append("released"),
                        window=SimpleNamespace(destroy=lambda: steps.append("destroy")))

    PreferencesWindow.on_ok(d, None)

    assert steps == (["settings applied"] if changed else [])


# --- a snippets file that can't be read ---------------------------------------

@pytest.fixture
def broken_file(tmp_path, monkeypatch):
    """custom_commands.toml broken by a hand edit, read once as the tab does
    when it opens."""
    from tuxtalk import config as C
    path = tmp_path / "custom_commands.toml"
    path.write_text('[commands]\n"my email" = "me@example.com\n')     # quote left open
    monkeypatch.setattr(C, "CUSTOM_COMMANDS_PATH", str(path))
    monkeypatch.setattr(C, "_commands_read_failed", False)
    assert C.load_custom_commands() == {}
    return path


def _tab_on(rows):
    """A Words tab showing *rows*, loaded from a file that couldn't be read."""
    from gi.repository import Gtk
    d = _window(rows)
    d.read_problem = Gtk.Label()
    return d


def test_the_tab_says_when_the_snippets_file_cant_be_read(broken_file):
    """The list just showed empty, as if every snippet were gone."""
    d = _tab_on([])
    d._show_read_problem()
    assert d.read_problem.get_visible()
    assert "can't be read" in d.read_problem.get_text()
    assert str(broken_file) in d.read_problem.get_text()


def test_the_words_tab_opens_with_the_warning(broken_file):
    """What the user sees first: the tab built as Preferences builds it."""
    d = W.WordsTab(SimpleNamespace(window=None))
    page = d.build()     # kept: a page let go of takes its label with it
    assert d.read_problem.get_visible() and d.read_problem.get_parent() is page
    assert "can't be read" in d.read_problem.get_text()


def test_the_warning_goes_once_the_file_is_fixed(broken_file):
    from tuxtalk import config as C
    d = _tab_on([])
    d._show_read_problem()
    broken_file.write_text('[commands]\n"my email" = "me@example.com"\n')
    C.load_custom_commands()
    d._show_read_problem()
    assert not d.read_problem.get_visible()


def test_an_edit_that_cant_be_saved_is_said_and_keeps_the_window_open(broken_file, shown):
    """Saving was refused without a word, and OK closed: the new snippet
    was gone."""
    damaged = broken_file.read_text()
    d = _tab_on([["sig", "Ron"]])

    assert d.save() is False
    assert shown == ["Your snippets weren't saved"]
    assert broken_file.read_text() == damaged, "the snippets in the file were deleted"
    assert d.changed(), "the edit was thrown away"


def test_with_nothing_edited_an_unreadable_file_doesnt_hold_ok_back(broken_file, shown):
    """The other settings save and the window closes: nothing of the
    user's is lost."""
    damaged = broken_file.read_text()
    d = _tab_on([])

    assert d.save() is True
    assert shown == []
    assert broken_file.read_text() == damaged


def test_the_word_already_fixed_warning_shows_once_per_commit(monkeypatch, shown):
    """Focus-out, Tab and "edited" each commit the same phrase, and the
    warning stacked up two or three times."""
    pending = []
    monkeypatch.setattr(W.GLib, "idle_add", lambda fn, *a: pending.append(fn))
    d = _window([["", ""]])
    d._word_rows = [("TuxTalk", ["tux talk"])]

    for _commit in range(3):
        d._on_phrase_edited(None, "0", "tux talk")
    for fn in pending:
        fn()

    assert shown == ["That phrase already fixes a word"]
    assert d.commands_store[0][0] == ""


def test_the_dialogs_fields_are_named_by_their_labels():
    """A screen reader read the Snippet and Your Word windows' boxes with no
    name: the label above each wasn't tied to it."""
    from gi.repository import Atk, Gtk
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    entry = Gtk.Entry()
    label = W._field(box, "You say", entry)
    relation = entry.get_accessible().ref_relation_set().get_relation_by_type(
        Atk.RelationType.LABELLED_BY)
    assert relation is not None
    assert relation.get_target()[0] == label.get_accessible()


# --- the Edit... window ------------------------------------------------------

def test_editing_a_snippet_changes_its_row():
    d = _window([["my email", "me@example.com"], ["sig", "Ron"]])
    assert d._apply_snippet_edit("1", " My Signature ", "Ron Brand") is True
    assert d.commands_store[1] == ["my signature", "Ron Brand"]
    assert d.commands_store[0] == ["my email", "me@example.com"]


def test_editing_a_snippet_onto_another_phrase_is_refused(monkeypatch, shown):
    monkeypatch.setattr(W.GLib, "idle_add", lambda fn, *a: fn(*a))
    d = _window([["my email", "me@example.com"], ["sig", "Ron"]])
    assert d._apply_snippet_edit("1", "my email", "Ron Brand") is False
    assert d.commands_store[1] == ["sig", "Ron"], "nothing changes when it's refused"
    assert "That phrase is already a snippet" in shown


def test_a_snippet_needs_a_phrase():
    d = _window([["sig", "Ron"]])
    assert d._apply_snippet_edit("0", "   ", "Ron Brand") is False
    assert d.commands_store[0] == ["sig", "Ron"]
