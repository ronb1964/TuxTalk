"""Re-opening Fix a Word while it is open must pick the dictation asked for.

The tray passes an index into the history as it is now, so the open window
must refresh its list. Otherwise, after one new dictation, Fix a Word on the
newest entry (index 0) shows the previous one, and the new dictations are
missing from the dropdown.
"""
from types import SimpleNamespace

from tuxtalk import fix_word_dialog as F
from tuxtalk.fix_word_dialog import _FixWordDialog


class FakeCombo:
    """ComboBoxText stand-in that fires "changed" like the real one."""

    def __init__(self, on_changed):
        self.items, self.active, self.on_changed = [], -1, on_changed

    def remove_all(self):
        self.items = []
        self._set(-1)

    def append_text(self, text):
        self.items.append(text)

    def get_active(self):
        return self.active

    def set_active(self, index):
        self._set(index)

    def _set(self, index):
        if index != self.active:
            self.active = index
            self.on_changed(index)


def _dialog(entries):
    d = SimpleNamespace(entries=entries, text="", first=None, last=None,
                        view=SimpleNamespace(get_buffer=lambda: SimpleNamespace(
                            set_text=lambda t: None)),
                        combo_row=SimpleNamespace(visible=None),
                        _update=lambda: None)
    d.combo_row.set_visible = lambda v: setattr(d.combo_row, "visible", v)
    d._load = lambda i: _FixWordDialog._load(d, i)
    d.combo = FakeCombo(d._load)
    for text in entries:
        d.combo.append_text(text)
    d.select_entry = lambda i: _FixWordDialog.select_entry(d, i)
    d.set_entries = lambda e: _FixWordDialog.set_entries(d, e)
    return d


def test_the_newest_dictation_is_shown_after_new_ones_arrive():
    d = _dialog(["older", "oldest"])
    d.select_entry(0)
    assert d.text == "older"

    d.set_entries(["newest", "older", "oldest"])
    d.select_entry(0)

    assert d.text == "newest"
    assert len(d.combo.items) == 3
    assert d.combo_row.visible is True


def test_reopening_passes_the_current_history_to_the_open_window(monkeypatch):
    calls = []
    open_window = SimpleNamespace(window=object(),
                                  set_entries=lambda e: calls.append(("entries", e)),
                                  select_entry=lambda i: calls.append(("select", i)))
    monkeypatch.setattr(F, "_active_dialog", open_window)
    monkeypatch.setattr(F, "get_entries", lambda: ["newest", "older"])
    import tuxtalk.raise_window as R
    monkeypatch.setattr(R, "raise_window", lambda w: None)

    F.show_fix_word_dialog(0, copy_text=lambda t: None)

    assert calls == [("entries", ["newest", "older"]), ("select", 0)]
