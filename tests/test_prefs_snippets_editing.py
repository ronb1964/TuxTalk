"""Editing the Snippets list must not save rows the user never filled in,
lose a row to a duplicate phrase, or let a write error escape OK/Apply."""
import types
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
    d = SimpleNamespace(commands_store=FakeStore([list(r) for r in rows]),
                        _commands_loaded={}, window=None)
    d.commands_tree = SimpleNamespace(set_cursor=lambda *a: None, get_column=lambda i: i)
    for name in ("_commands_from_store", "_fill_commands_store", "save",
                 "_on_add_command", "_on_phrase_edited", "_warn_duplicate_phrase",
                 "_split_commands", "_fill_words_store", "_apply_snippet_edit"):
        setattr(d, name, types.MethodType(getattr(W.WordsTab, name), d))
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
    assert d.save() is None
    assert shown == ["Your snippets weren't saved"]


def test_ok_stays_open_when_the_snippets_could_not_be_written():
    """Closing right after saying the snippets weren't saved would throw the
    unsaved edits away."""
    steps = []
    d = SimpleNamespace(words_tab=SimpleNamespace(save=lambda: None),
                        _changed_since_open=lambda: steps.append("settings") or {},
                        save_config=lambda: steps.append("save") or True,
                        window=SimpleNamespace(destroy=lambda: steps.append("destroy")))

    PreferencesWindow.on_ok(d, None)

    assert steps == []


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
