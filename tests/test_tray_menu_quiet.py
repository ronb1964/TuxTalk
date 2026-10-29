"""The tray's status poll runs every second, and each change to a menu item
or the icon is sent to the desktop (dbusmenu), even to the same words. With
nothing changed it touches none; what did change still shows."""
import types

import pytest

from tuxtalk import config, tray, tray_items, tray_menu

Gtk = tray.Gtk


@pytest.fixture
def polled_tray(monkeypatch):
    """A tray with its real menu, dictation off, and nothing behind it."""
    monkeypatch.setattr(config, "load_config", config.Settings)
    t = tray.DictationTray.__new__(tray.DictationTray)
    t.running = False
    t.is_service_running = lambda: t.running
    t.icons = []
    t.indicator = types.SimpleNamespace(set_icon_full=lambda *a: t.icons.append(a[0]))
    t.dbus_service = None
    t._last_service_state = False
    t.menu = tray_menu.TrayMenu(t)
    t.update_menu_display(False)
    yield t
    t.menu.widget.destroy()


def _touched(monkeypatch):
    """The widget changes made from now on, by name."""
    touched = []
    for cls, name in ((Gtk.MenuItem, "set_label"), (Gtk.CheckMenuItem, "set_active"),
                      (Gtk.ImageMenuItem, "set_image"), (tray_items.DictationItem, "set_active")):
        real = getattr(cls, name)
        monkeypatch.setattr(cls, name, lambda self, *a, real=real, name=name:
                            touched.append(name) or real(self, *a))
    return touched


def test_a_second_with_nothing_changed_touches_no_widget(polled_tray, monkeypatch):
    t = polled_tray
    t.update_status_and_menu()
    touched = _touched(monkeypatch)
    t.update_status_and_menu()
    t.update_status_and_menu()
    assert touched == [] and t.icons == []


def test_what_changed_still_shows(polled_tray, monkeypatch):
    t = polled_tray
    t.update_status_and_menu()
    t.running = True                                    # dictation started
    t.update_status_and_menu()
    assert t.menu.service_toggle.get_label() == "Turn Dictation Off"
    assert t.icons == ["microphone-sensitivity-high"]

    touched = _touched(monkeypatch)
    monkeypatch.setattr(config, "load_config",
                        lambda: config.Settings(model="base", device="cpu"))
    t.update_status_and_menu()                          # Preferences chose Whisper Base
    assert "on the processor" in t.menu.engine_item.get_label()
    assert t.menu.preset_radios["lightest"].get_active()
    assert "set_label" in touched

    t.menu._moving_dot = True                           # the dot left on another preset
    t.menu.preset_radios["battery"].set_active(True)
    t.menu._moving_dot = False
    t.update_status_and_menu()
    assert t.menu.preset_radios["lightest"].get_active()


def test_a_click_that_doesnt_start_dictation_is_shown_off_again(polled_tray):
    t = polled_tray
    t.start_service = lambda _w: None                   # the service never comes up
    t.menu.service_toggle.activate()
    t.update_status_and_menu()
    assert t.menu.service_toggle.get_label() == "Turn Dictation On"
