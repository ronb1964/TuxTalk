"""Preferences' dropdowns with the keyboard alone.

GTK picks the next entry the moment an arrow key is pressed on a closed
dropdown. Some entries start a setup when picked (large-v3 and CUDA offer a
download, Several languages opens its list) and put the old choice back, so
with the arrow keys the entries past them could never be reached. The arrow,
Page and Home/End keys open the list instead, where they move as usual.
"""
import dataclasses
import time
from types import SimpleNamespace

import pytest
from gi.repository import Gdk, Gtk

from tuxtalk import prefs_tab
from tuxtalk.config import Settings


def _settle():
    """Let GTK handle what's pending: the window mapping, the list opening."""
    end = time.monotonic() + 0.3
    while time.monotonic() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.02)


def _press(widget, keyval, state=0):
    """Press *keyval* (with the modifiers in *state*) in *widget*'s window,
    as a keyboard would: the window hands it to the focused widget."""
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.window = widget.get_toplevel().get_window()
    event.keyval = keyval
    event.state = Gdk.ModifierType(state)
    # GTK's key bindings look the key up by its hardware code.
    _found, keys = Gdk.Keymap.get_for_display(Gdk.Display.get_default()) \
        .get_entries_for_keyval(keyval)
    event.hardware_keycode, event.group = keys[0].keycode, keys[0].group
    event.send_event = True
    event.set_device(Gdk.Display.get_default().get_default_seat().get_keyboard())
    Gtk.main_do_event(event)
    _settle()


@pytest.fixture
def shown():
    """Show a widget in a window of its own; the windows go afterwards."""
    windows = []

    def show(widget):
        window = Gtk.Window()
        window.add(widget)
        window.show_all()
        windows.append(window)
        _settle()
        return widget
    yield show
    for window in windows:
        window.destroy()


def _focused(combo):
    combo.grab_focus()
    _settle()
    return combo


@pytest.mark.parametrize("key", ["Up", "Down", "Page_Down", "Home", "End"])
def test_a_key_on_a_closed_dropdown_opens_its_list_and_picks_nothing(shown, key):
    combo = prefs_tab.dropdown(Gtk.ComboBoxText())
    for i in range(4):
        combo.append(str(i), f"entry {i}")
    combo.set_active_id("1")
    picked = []
    combo.connect("changed", lambda c: picked.append(c.get_active_id()))
    _focused(shown(combo))

    _press(combo, getattr(Gdk, f"KEY_{key}"))

    assert combo.get_property("popup-shown"), "the list didn't open"
    assert picked == [] and combo.get_active_id() == "1"
    combo.popdown()


@pytest.mark.parametrize("key", ["Page_Down", "Page_Up"])
def test_ctrl_page_keys_on_a_dropdown_still_change_the_tab(shown, key):
    """Ctrl+Page Down opened the dropdown instead of moving to the next tab."""
    notebook = Gtk.Notebook()
    combo = prefs_tab.dropdown(Gtk.ComboBoxText())
    combo.append("a", "entry a")
    combo.set_active_id("a")
    notebook.append_page(combo, Gtk.Label(label="first"))
    notebook.append_page(Gtk.Label(label="second page"), Gtk.Label(label="second"))
    shown(notebook)
    _focused(combo)

    _press(combo, getattr(Gdk, f"KEY_{key}"), Gdk.ModifierType.CONTROL_MASK)

    assert notebook.get_current_page() == 1     # Ctrl+Page Up wraps round
    assert not combo.get_property("popup-shown")


def _speech(model, device):
    """The real Speech Recognition section of the General tab, for an NVIDIA
    card without CUDA's files and no Vulkan, its fold open."""
    from tuxtalk.prefs_speech import SpeechSection
    config = dict(dataclasses.asdict(Settings()), model=model, device=device)
    win = SimpleNamespace(config=config, window=None, cuda_files_present=lambda: False,
                          has_nvidia=lambda: True, vulkan_offered=lambda: False)
    win.update_config = config.__setitem__
    win.show_without_saving = config.__setitem__
    speech = SpeechSection(win)
    grid = Gtk.Grid()
    speech.build(grid, 0)
    return speech, grid


@pytest.fixture
def no_setups(monkeypatch):
    """What the dropdowns would start when picked, recorded instead of run."""
    from tuxtalk.prefs_language import LanguageRow
    from tuxtalk.prefs_speech import SpeechSection
    started = []
    monkeypatch.setattr(SpeechSection, "_update_recommended_row", lambda self, model: None)
    monkeypatch.setattr(SpeechSection, "_set_up_large_v3",
                        lambda self: started.append("large-v3 setup"))
    monkeypatch.setattr(SpeechSection, "_on_device_changed",
                        lambda self, combo: started.append("device setup"))
    monkeypatch.setattr(LanguageRow, "pick_several",
                        lambda self: started.append("Several languages") or False)
    return started


def test_the_model_device_and_language_dropdowns_open_from_the_keyboard(shown, no_setups):
    """From Parakeet, Up picked large-v3 and opened its CUDA setup; from CPU,
    Down picked CUDA; End on Language opened Several languages."""
    speech, grid = _speech("parakeet-v3", "cpu")
    shown(grid)
    speech.model_combo.get_ancestor(Gtk.Revealer).set_reveal_child(True)    # the fold opened
    _settle()
    for combo, key in ((speech.model_combo, Gdk.KEY_Up),
                       (speech.device_combo, Gdk.KEY_Down),
                       (speech.language.combo, Gdk.KEY_End)):
        before = combo.get_active_id()
        _press(_focused(combo), key)
        assert combo.get_property("popup-shown"), f"{before}: the list didn't open"
        assert combo.get_active_id() == before
        combo.popdown()
        _settle()
    assert no_setups == []
    assert (speech.config["model"], speech.config["device"]) == ("parakeet-v3", "cpu")
