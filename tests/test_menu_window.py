"""TuxTalk's menu without a tray (2026-10-16).

Plain GNOME (Fedora, Silverblue, Debian...) shows no tray icons. A user who
skipped the GNOME extension there had no way to reach Preferences, Transcribe
a File or Quit, and opening TuxTalk again said to look for a tray icon that
doesn't exist. Now:
  * the setup screen ticks the extension when there's no tray, and says why;
  * opening TuxTalk again shows its menu as a window (menu_window.py);
  * the launcher has right-click shortcuts (tuxtalk --open NAME / --quit).
"""
import re
import types
from pathlib import Path

import pytest

from tuxtalk import control, desktop_detect, update_checker

ROOT = Path(__file__).resolve().parent.parent


# ----- tuxtalk --open / --quit -----

@pytest.mark.parametrize("argv, expected", [
    (["--open", "preferences"], "preferences"),
    (["--open", "menu"], "menu"),
    (["--open", "fix-word"], "fix-word"),
    (["--quit"], "quit"),
    (["tray"], None),           # the AppImage launcher's own argument
    ([], None),
])
def test_parse_open(argv, expected):
    assert control.parse_open(argv) == expected


def test_open_and_toggle_dont_mix():
    with pytest.raises(SystemExit):
        control.parse_open(["--toggle", "--open", "menu"])


def test_every_open_name_has_a_tray_action():
    """The tray must know every window the command line can name."""
    from tuxtalk import tray
    opened = []

    def opens(window):
        return lambda *a: opened.append(window)
    stub = types.SimpleNamespace(
        show_menu_window=opens("menu"), open_preferences=opens("preferences"),
        open_transcribe_window=opens("transcribe"), show_fix_word_dialog=opens("fix-word"),
        show_help=opens("help"), _quitting=False)
    for name in control.OPEN_METHODS:
        if name not in control._OWN_OPTION:
            opened.clear()
            tray.DictationTray.open_window(stub, name)
            assert opened == [name]


class _FakeBus:
    def __init__(self, owners, method_result=None, unknown=False):
        self.owners = list(owners)      # name_has_owner answers, last repeats
        self.calls = []
        self.closed = False
        self.method_result = method_result
        self.unknown = unknown

    def name_has_owner(self, _name):
        return self.owners.pop(0) if len(self.owners) > 1 else self.owners[0]

    def get_object(self, *_a, **_k):
        bus = self

        class Obj:
            def __getattr__(self, method):
                def call(**_kw):
                    bus.calls.append(method)
                    if bus.unknown:
                        import dbus
                        raise dbus.DBusException(
                            name="org.freedesktop.DBus.Error.UnknownMethod")
                    return bus.method_result
                return call
        return Obj()


def test_open_in_running_none_when_nothing_runs():
    bus = _FakeBus([False])
    assert control.open_in_running("menu", bus=bus) is None
    assert bus.calls == []


def test_open_in_running_asks_for_the_window():
    bus = _FakeBus([True], method_result=True)
    assert control.open_in_running("menu", bus=bus) is True
    assert bus.calls == ["ShowMenu"]
    bus = _FakeBus([True], method_result=None)     # methods with no reply
    assert control.open_in_running("preferences", bus=bus) is True
    assert bus.calls == ["OpenPreferences"]


def test_open_in_running_false_for_an_older_tuxtalk():
    """An older TuxTalk has no ShowMenu: the caller falls back to the
    "already running" message."""
    pytest.importorskip("dbus")
    bus = _FakeBus([True], unknown=True)
    assert control.open_in_running("menu", bus=bus) is False


def test_open_in_running_false_when_the_stand_in_service_answers():
    """The dictation service standing in for a missing tray has no window."""
    bus = _FakeBus([True], method_result=False)
    assert control.open_in_running("menu", bus=bus) is False


def test_open_in_running_waits_for_a_starting_tray():
    bus = _FakeBus([False, False, True], method_result=True)
    assert control.open_in_running("menu", tray_starting=lambda: True,
                                   bus=bus, sleep=lambda _s: None) is True


# ----- is there a tray at all? -----

def test_tray_host_available():
    has = types.SimpleNamespace(name_has_owner=lambda n: n == "org.kde.StatusNotifierWatcher")
    none = types.SimpleNamespace(name_has_owner=lambda n: False)
    assert desktop_detect.tray_host_available(bus=has) is True
    assert desktop_detect.tray_host_available(bus=none) is False


def test_tray_host_unknown_counts_as_available():
    def boom(_n):
        raise RuntimeError("no session bus")
    assert desktop_detect.tray_host_available(
        bus=types.SimpleNamespace(name_has_owner=boom)) is True


# ----- the launcher's right-click shortcuts -----

def _actions(text):
    """{action id: (Name, Exec)} from a desktop file's text."""
    found = {}
    for block in re.split(r"\n(?=\[)", text):
        m = re.match(r"\[Desktop Action (\w+)\]", block)
        if m:
            name = re.search(r"^Name=(.*)$", block, re.M).group(1)
            exe = re.search(r"^Exec=(.*)$", block, re.M).group(1)
            found[m.group(1)] = (name, exe)
    return found


def _expected(program):
    return {aid: (name, f"{program} {args}")
            for aid, name, args in update_checker.DESKTOP_ACTIONS}


@pytest.mark.parametrize("path, program", [
    ("io.github.ronb1964.TuxTalk.desktop", "AppRun"),
    ("aur/tuxtalk.desktop", "tuxtalk"),
])
def test_shipped_launchers_have_the_shortcuts(path, program):
    text = (ROOT / path).read_text()
    assert _actions(text) == _expected(program)
    main = text.split("\n[", 1)[0]
    assert "Actions=Preferences;TranscribeFile;Help;Quit;" in main


@pytest.mark.parametrize("script", ["build-deb.sh", "build-rpm.sh"])
def test_package_scripts_keep_the_shortcuts(script):
    """The .deb/.rpm rewrite Exec for /usr/bin/tuxtalk. Rewriting every Exec
    line to "tuxtalk tray" would turn each shortcut into a plain start."""
    sed = re.search(r"sed '(s/\^Exec=[^']*)'", (ROOT / script).read_text()).group(1)
    _s, pattern, replacement, _flags = sed.split("/")
    text = re.sub(pattern.replace("^", "(?m)^", 1), replacement,
                  (ROOT / "io.github.ronb1964.TuxTalk.desktop").read_text())
    assert re.search(r"(?m)^Exec=tuxtalk tray$", text)
    assert _actions(text) == _expected("tuxtalk")


def _launcher(tmp_path, monkeypatch, text):
    path = tmp_path / "tuxtalk.desktop"
    path.write_text(text)
    monkeypatch.setattr(update_checker, "DESKTOP_FILE_PATH", str(path))
    monkeypatch.setattr(update_checker, "get_install_type", lambda **_k: "appimage")
    monkeypatch.setattr(update_checker.menu_cache, "refresh_menu_cache", lambda: None)
    return path


OLD_LAUNCHER = """[Desktop Entry]
Name=TuxTalk
Comment=Hold a key, talk, and your words are typed for you (v0.15.1)
Exec=/home/someone/AppImages/TuxTalk.AppImage
Icon=tuxtalk
Type=Application
StartupNotify=true
Terminal=false
"""


def test_older_launcher_gets_the_shortcuts_once(tmp_path, monkeypatch):
    path = _launcher(tmp_path, monkeypatch, OLD_LAUNCHER)
    assert update_checker.add_launcher_actions() is True
    text = path.read_text()
    assert _actions(text) == _expected("/home/someone/AppImages/TuxTalk.AppImage")
    main = text.split("\n[Desktop Action", 1)[0]
    assert "Actions=" in main and "Exec=/home/someone/AppImages/TuxTalk.AppImage" in main
    assert update_checker.add_launcher_actions() is False      # once
    assert path.read_text() == text


def test_hidden_entry_and_packages_left_alone(tmp_path, monkeypatch):
    hidden = OLD_LAUNCHER + update_checker._HIDDEN_ENTRY_MARK + "\n"
    path = _launcher(tmp_path, monkeypatch, hidden)
    assert update_checker.add_launcher_actions() is False
    assert path.read_text() == hidden
    path.write_text(OLD_LAUNCHER)
    monkeypatch.setattr(update_checker, "get_install_type", lambda **_k: "deb")
    assert update_checker.add_launcher_actions() is False


def test_no_launcher_is_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(update_checker, "DESKTOP_FILE_PATH", str(tmp_path / "none.desktop"))
    monkeypatch.setattr(update_checker, "get_install_type", lambda **_k: "appimage")
    assert update_checker.add_launcher_actions() is False


def test_new_appimage_launcher_has_the_shortcuts():
    text = update_checker.desktop_actions("/x/TuxTalk.AppImage")
    assert _actions("[Desktop Entry]\n" + text) == _expected("/x/TuxTalk.AppImage")


# ----- the menu window -----

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

# The tray menu's order (CLAUDE.md), less the on/off switch and the model line.
MENU_ORDER = ["Recent Dictations  ▸", "Fix a Word...",
              "Transcribe a File...", "Performance  ▸",
              "Preferences...", "Voice Commands...", "Your Stats...", "Help...",
              "About TuxTalk...", "Check for Updates...", "Restart Service", "Quit TuxTalk"]


def _fake_tray(running=True):
    calls = []

    def rec(name):
        return lambda *a: calls.append(name)
    tray = types.SimpleNamespace(
        calls=calls, _menu_window=None,
        is_service_running=lambda: running,
        start_service=rec("start"), stop_service=rec("stop"),
        restart_service=rec("restart"), show_fix_word_dialog=rec("fix"),
        open_transcribe_window=rec("transcribe"), open_preferences=rec("prefs"),
        show_voice_commands=rec("voice"), open_preferences_stats=rec("stats"),
        show_help=rec("help"), show_about_dialog=rec("about"),
        check_for_updates_clicked=rec("updates"), quit_app=rec("quit"),
        _launch_preferences=lambda tab=None: calls.append(f"prefs:{tab}"),
        engine_item=Gtk.MenuItem(label="Parakeet on the processor"))
    return tray


def _menu_window(**kw):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    from tuxtalk.menu_window import MenuWindow
    tray = _fake_tray(kw.pop("running", True))
    win = MenuWindow(tray, **kw)
    return tray, win


def _buttons(win):
    from gtk_text import widgets
    return [w for w in widgets(win.window)
            if isinstance(w, Gtk.Button) and not isinstance(w, Gtk.Switch)]


def _texts(win):
    from gtk_text import widgets
    return [w.get_text() for w in widgets(win.window) if isinstance(w, Gtk.Label)]


def test_menu_window_has_the_tray_menu_in_order():
    _tray, win = _menu_window()
    try:
        assert [b.get_label() for b in _buttons(win)] == MENU_ORDER
    finally:
        win.window.destroy()


def test_menu_window_buttons_run_the_tray_actions():
    tray, win = _menu_window()
    try:
        by_label = {b.get_label(): b for b in _buttons(win)}
        by_label["Preferences..."].clicked()
        by_label["Transcribe a File..."].clicked()
        by_label["Quit TuxTalk"].clicked()
        assert tray.calls == ["prefs", "transcribe", "quit"]
    finally:
        win.window.destroy()


def test_menu_window_switch_follows_and_drives_the_service():
    tray, win = _menu_window(running=False)
    try:
        assert not win.switch.get_active()
        assert "Dictation is off" in win.status_label.get_text()
        assert tray.calls == []             # showing the state starts nothing
        win.switch.set_active(True)
        assert tray.calls == ["start"]
    finally:
        win.window.destroy()


def test_menu_window_explains_itself_without_a_tray():
    tray, win = _menu_window(no_tray=True, offer_extension=True)
    try:
        assert any("no system tray" in t for t in _texts(win))
        add = [b for b in _buttons(win) if b.get_label().startswith("Add TuxTalk")]
        assert len(add) == 1
        add[0].clicked()
        assert tray.calls == ["prefs:advanced"]
    finally:
        win.window.destroy()
    _tray, win = _menu_window(no_tray=False)
    try:
        assert not any("no system tray" in t for t in _texts(win))
    finally:
        win.window.destroy()


def test_menu_window_after_installing_the_extension_says_log_out():
    _tray, win = _menu_window(no_tray=True, extension_installed=True, offer_extension=True)
    try:
        assert any("log out and back in" in t for t in _texts(win))
        assert not any(b.get_label().startswith("Add TuxTalk") for b in _buttons(win))
    finally:
        win.window.destroy()


def test_closing_the_window_lets_the_tray_open_a_new_one():
    tray, win = _menu_window()
    tray._menu_window = win
    win.window.destroy()
    assert tray._menu_window is None


# ----- the setup screen -----

@pytest.mark.parametrize("has_tray", [True, False])
def test_setup_ticks_the_extension_only_without_a_tray(monkeypatch, has_tray):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    from tuxtalk import welcome_dialog
    monkeypatch.setattr(desktop_detect, "tray_host_available", lambda bus=None: has_tray)
    monkeypatch.setattr(welcome_dialog, "_extension_already_active", lambda: False)
    dialog = welcome_dialog.WelcomeDialog(force_gnome=True)
    try:
        assert dialog.extension_check is not None
        assert dialog.extension_check.get_active() is (not has_tray)
    finally:
        dialog.dialog.destroy()


def test_menu_window_buttons_dont_keep_a_focus_box():
    """A clicked item kept a focus box after its popup closed (Breeze).
    Menu items don't take focus on click, and nothing starts focused."""
    _tray, win = _menu_window()
    try:
        assert all(not b.get_focus_on_click() for b in _buttons(win))
        assert not win.switch.get_focus_on_click()
        assert win.window.get_focus() is None
    finally:
        win.window.destroy()


# ----- the hotkey test hears keys through the hotkey helper -----

def test_hotkey_test_uses_the_helper_and_watches_its_keys():
    """With the helper the user isn't in the "input" group, so the test
    can't open the keyboards itself (Silverblue VM, 2026-10-16: it said the
    keys wouldn't work until a restart)."""
    from tuxtalk import hotkey_test_devices
    ecodes = pytest.importorskip("evdev").ecodes
    watched = []
    helper = types.SimpleNamespace(watch=watched.extend)
    assert hotkey_test_devices.hotkey_test_helper(ecodes, connect=lambda: helper) is helper
    assert str(ecodes.KEY_F8) in watched and str(ecodes.KEY_F9) in watched
    assert str(ecodes.KEY_F24) in watched and str(ecodes.KEY_ESC) in watched


def test_hotkey_test_without_the_helper():
    from tuxtalk import hotkey_test_devices
    ecodes = pytest.importorskip("evdev").ecodes
    assert hotkey_test_devices.hotkey_test_helper(ecodes, connect=lambda: None) is None

    def broken():
        raise OSError("no socket")
    assert hotkey_test_devices.hotkey_test_helper(ecodes, connect=broken) is None


def test_fix_typing_success_updates_the_status_lines(monkeypatch):
    """After Fix Typing worked, the ❌ "needed" line above the button stayed
    (Silverblue VM, 2026-10-16)."""
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    from tuxtalk import welcome_dialog
    fake_msg = types.SimpleNamespace(format_secondary_text=lambda t: None,
                                     run=lambda: None, destroy=lambda: None)
    monkeypatch.setattr("tuxtalk.ui_style.message_dialog", lambda **k: fake_msg)
    uinput = Gtk.Label()
    me = types.SimpleNamespace(
        dialog=None, uinput_fixed=False, restart_needed=False, needs_uinput_fix=True,
        typing_status_label=Gtk.Label(), _uinput_status_label=uinput)
    button = Gtk.Button()
    welcome_dialog.WelcomeDialog._report_fix_typing(me, button, {
        "results": [], "uinput_fixed": True, "needs_restart": False})
    assert uinput.get_text() == "✅ Input device permissions OK"
