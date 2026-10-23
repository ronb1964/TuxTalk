"""Test-suite wide setup.

Keep the suite's logging out of the user's real TuxTalk log. Modules create
their file handler at import time, so this has to happen before any test
module imports tuxtalk, which is why it runs at conftest import rather than
in a fixture. Subprocesses started by tests inherit it through the
environment.
"""
import atexit
import os
import shutil
import tempfile

import gi
import pytest

# Every window a test builds must open on xvfb-run's hidden display, never on
# the user's desktop, where it would take the focus from what they're typing
# in. GTK prefers Wayland, and libwayland connects to wayland-0 in the runtime
# folder even with WAYLAND_DISPLAY unset, so the display is opened here, on
# X11, before any test module imports Gtk. GDK_BACKEND is set only while it
# opens: the programs tests start must not inherit it. pytest_configure
# checks that this display is xvfb-run's.
os.environ.pop("WAYLAND_DISPLAY", None)
os.environ["GDK_BACKEND"] = "x11"
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402,F401  (Gtk opens the display)
del os.environ["GDK_BACKEND"]

_LOG_DIR = tempfile.mkdtemp(prefix="talktype-test-logs-")
os.environ["TUXTALK_LOG_DIR"] = _LOG_DIR
# Never move the real ~/.config/talktype or ~/.local/share/TalkType while the
# suite imports tuxtalk (the rename migration runs on package import).
os.environ["TUXTALK_NO_MIGRATE"] = "1"
atexit.register(shutil.rmtree, _LOG_DIR, ignore_errors=True)


def _not_private(address):
    """Why the session bus at *address* isn't a private one, as
    dbus-run-session starts, or None. A private bus has nobody else on it; the
    user's own has their desktop (and the same id, whatever the address)."""
    from gi.repository import Gio, GLib

    def connect(where):
        return Gio.DBusConnection.new_for_address_sync(
            where, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)

    def ask(bus, method):
        return bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                             "org.freedesktop.DBus", method, None, None,
                             Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]

    if not address:
        return "there is no session bus"
    try:
        bus = connect(address)
        names = [n for n in ask(bus, "ListNames")
                 if not n.startswith(":") and n != "org.freedesktop.DBus"]
        bus_id = ask(bus, "GetId")
        bus.close_sync(None)
    except GLib.Error as e:
        return f"the session bus can't be reached ({e.message})"
    if names:
        return f"other programs are on it ({', '.join(sorted(names)[:3])})"
    own = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "bus")
    if os.path.exists(own):
        try:
            users = connect(f"unix:path={own}")
            same = ask(users, "GetId") == bus_id
            users.close_sync(None)
        except GLib.Error:
            same = False
        if same:
            return "it is the user's own session bus"
    return None


def pytest_configure(config):
    display = Gdk.Display.get_default()
    problem = None
    # xvfb-run points XAUTHORITY at its own temporary folder.
    if "xvfb-run" not in os.environ.get("XAUTHORITY", ""):
        problem = "the display isn't xvfb-run's"
    elif display is None or type(display).__gtype__.name != "GdkX11Display" \
            or display.get_name() != os.environ.get("DISPLAY"):
        problem = "GTK didn't open xvfb-run's X display"
    else:
        problem = _not_private(os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""))
    if problem:
        raise pytest.UsageError(
            f"Run the tests under xvfb-run and dbus-run-session (DEV_SETUP.md): {problem}. "
            "They build real windows and use the session bus.")


def _hold_back_notifications():
    """Desktop notifications from tests must never reach the user's desktop.

    Patched here, at conftest import, so it covers module-level code too;
    tests that check a message stub app._notify themselves.
    """
    from tuxtalk import notify

    def _held_back(title, body):
        return None

    notify._send = _held_back


_hold_back_notifications()


@pytest.fixture(autouse=True)
def _no_remembered_dictation_window(monkeypatch):
    """Start each test with no remembered dictation window. Typing records the
    focused window (state.last_inserted_window), and undo and voice navigation
    refuse when another window has the focus. A window left by an earlier test
    compared against the user's real desktop, so tests passed or failed with
    whatever the user was doing."""
    from tuxtalk import app
    monkeypatch.setattr(app.state, "last_inserted_window", None)
    monkeypatch.setattr(app.state, "nav_context", None)


@pytest.fixture(autouse=True)
def _no_real_speech_model(monkeypatch):
    """Start each test with a stand-in speech model. Starting a recording
    loads the real one when none is in memory (Power Saving, _wake_model):
    seconds on a computer that has it, a 670 MB download in CI, where the
    suite ran into its 20-minute limit (2026-10-18). Tests of the loading
    itself set app.model to None."""
    from tuxtalk import app
    monkeypatch.setattr(app, "model", object())


@pytest.fixture(autouse=True)
def _no_followed_default_mic(monkeypatch):
    """Start each test without a tracked System Default mic. A test that runs
    the real _pick_input_device("") records the user's PipeWire default, and
    start_recording would then record from that instead of the device the
    next test passes in."""
    from tuxtalk import app
    monkeypatch.setattr(app, "_system_default_mic", None)


@pytest.fixture(autouse=True)
def _no_real_send_countdown(monkeypatch, request):
    """No floating label on the user's screen and no Enter left counting down
    between tests. Sends happen at once unless a test sets a wait itself.
    test_window_messages tests the label itself, with its own fakes."""
    from tuxtalk import app, screen_message
    if request.module.__name__.rpartition(".")[2] != "test_window_messages":
        monkeypatch.setattr(screen_message, "show_over_active_window", lambda *a, **k: None)
        monkeypatch.setattr(screen_message, "hide", lambda *a, **k: None)
        monkeypatch.setattr(screen_message, "relabel", lambda *a, **k: None)
    monkeypatch.setattr(app, "_press_enter_delay", 0.0)
    monkeypatch.setattr(app.state, "pending_send", None)
    monkeypatch.setattr(app.state, "held_send", False)
    monkeypatch.setattr(app.state, "owed_space", None)
    monkeypatch.setattr(app.state, "sent", None)


@pytest.fixture(autouse=True)
def _no_real_password_check(monkeypatch, request):
    """Typing first asks the accessibility bus whether the cursor is in a
    password field, and whether a polkit password prompt is waiting. On the
    user's desktop the answers depend on what they have open;
    test_password_fields fakes them itself."""
    from tuxtalk import app, atspi_helper
    monkeypatch.setattr(app, "_polkit_prompt_waiting", lambda: False)
    if request.module.__name__.rpartition(".")[2] != "test_password_fields":
        monkeypatch.setattr(atspi_helper, "focused_is_password", lambda: False)
        # It asks which window has the focus, which tests script answer by answer.
        monkeypatch.setattr(app, "_password_prompt_has_focus", lambda: False)


@pytest.fixture(autouse=True)
def _no_real_explanation_windows(monkeypatch, tmp_path):
    """Never open an explanation window on the user's desktop, and never read
    or write their "don't explain again" choices. Tests that want the window
    record it through explain.open_window themselves."""
    from tuxtalk import explain
    monkeypatch.setattr(explain, "open_window", lambda kind: False)
    monkeypatch.setattr(explain, "_never_path", lambda: str(tmp_path / "explained.json"))


@pytest.fixture(autouse=True)
def _no_real_kwin(monkeypatch):
    """Never let a test register or release hotkeys in the user's live KWin.

    service_launcher.stop_dictation_service() releases them, and on a KDE
    machine running the suite would hand the dev service's F8 back to whatever
    app has focus. Tests of kwin_hotkeys pass their own fake bus.
    """
    from tuxtalk import kwin_hotkeys

    def _refuse():
        raise RuntimeError("tests must not talk to the real KWin")

    monkeypatch.setattr(kwin_hotkeys, "session_bus", _refuse)


@pytest.fixture(autouse=True)
def _no_real_x11_grabs(request, monkeypatch):
    """Never let a test grab keys on the user's X display: in an X11 session
    it would take F8 away from them. test_x11_hotkeys runs its own Xvfb."""
    from tuxtalk import x11_hotkeys
    if request.module.__name__.rpartition(".")[2] != "test_x11_hotkeys":
        monkeypatch.setattr(x11_hotkeys, "claim", lambda cfg: False)


@pytest.fixture(autouse=True)
def _no_real_tuxtalk(monkeypatch):
    """Never let a test send commands to the user's running TuxTalk.

    A second tray asks the running one to show its menu window, so a test of
    the single-instance lock opened the menu on Ron's desktop (2026-10-16);
    --toggle could even start a recording. Tests of control pass a fake bus.
    """
    from tuxtalk import control

    def _refuse():
        raise RuntimeError("tests must not talk to the running TuxTalk")

    monkeypatch.setattr(control, "_session_bus", _refuse)


@pytest.fixture(autouse=True)
def _no_real_media_players(monkeypatch):
    """Never let a test pause the user's music.

    Tests that run the service's settings reload turn pause_media on, and later
    tests call the real start_recording. Tests of media_pause pass a fake bus.
    """
    from tuxtalk import media_pause

    def _refuse(self):
        raise RuntimeError("tests must not talk to real media players")

    monkeypatch.setattr(media_pause.SessionBus, "_bus", _refuse)


_HOTKEY_TEST_FLAG = os.path.join(_LOG_DIR, ".hotkey_test_mode")


@pytest.fixture(autouse=True)
def _no_real_hotkey_test_flag(monkeypatch):
    """Keep tests off the real hotkey test flag in the user's data folder.

    Test Hotkeys open in the real Preferences would otherwise turn every key
    press a test sends into a test-mode report. Tests of the flag write here.
    """
    from tuxtalk import app, service_launcher

    monkeypatch.setattr(service_launcher, "hotkey_test_flag_path", lambda: _HOTKEY_TEST_FLAG)
    monkeypatch.setattr(app, "_hotkey_test_flag", None)
    yield
    try:
        os.remove(_HOTKEY_TEST_FLAG)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _no_real_snippets_or_words(monkeypatch, tmp_path_factory):
    """Keep tests off the user's own snippets and Your Words.

    custom_words.txt sits beside custom_commands.toml, so one path covers
    both. In 0.15.0 testing, "TuxTalk" saved in the real Your Words reached a
    Whisper test as a prompt it didn't expect, and building Preferences moves
    word fixes into Your Words the first time (vocabulary.adopt_fixes): run
    against the user's real files, a test would do that for real. Tests that
    need these files point the path at their own folder.
    """
    from tuxtalk import app, config
    folder = tmp_path_factory.mktemp("snippets")
    monkeypatch.setattr(config, "CUSTOM_COMMANDS_PATH", str(folder / "custom_commands.toml"))
    monkeypatch.setattr(app, "_custom_words", [])


@pytest.fixture(autouse=True)
def _no_real_hotkey_helper_or_pkexec(monkeypatch, tmp_path):
    """No test may open a password prompt or reach a real hotkey helper.

    A test that patched only the "input" group installer once ran the real
    pkexec through the hotkey helper's installer instead, and a password
    dialog sat on the desktop until it timed out. So: the helper's socket is
    a path nobody listens on, this machine counts as unable to run the
    helper unless a test says otherwise, and running anything as root fails
    the test outright."""
    monkeypatch.setenv("TUXTALK_HOTKEYS_SOCKET", str(tmp_path / "no-helper.sock"))
    from tuxtalk import secure_input
    monkeypatch.setattr(secure_input, "can_install", lambda: (False, "tests"))

    def refuse(*_a, **_k):
        pytest.fail("a test tried to run something as root through pkexec")
    monkeypatch.setattr(secure_input, "_run_as_root", refuse)

    # And pkexec itself, however it's reached: subprocess.run, or Popen as
    # the package updater uses. A test that mocks either on purpose replaces
    # this guard with its own mock.
    import subprocess
    real_run = subprocess.run

    def refuse_pkexec(cmd):
        first = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd).split(" ")[0]
        if os.path.basename(str(first)) == "pkexec":
            pytest.fail(f"a test tried to run pkexec: {str(cmd)[:60]}")

    def guarded_run(cmd, *args, **kwargs):
        refuse_pkexec(cmd)
        return real_run(cmd, *args, **kwargs)

    class GuardedPopen(subprocess.Popen):
        def __init__(self, cmd, *args, **kwargs):
            refuse_pkexec(cmd)
            super().__init__(cmd, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)


# What a running TuxTalk's processes have in their command line.
_TUXTALK_PROCESSES = (b"tuxtalk.app", b"tuxtalk.tray", b"tuxtalk.prefs", b"TuxTalk",
                      b"tuxtalk-hotkeys", b"whisper-server", b"llama-server",
                      b"parakeet_gpu_worker")


def _started_by_this_test_run(pid):
    """Whether *pid* descends from this pytest process."""
    while pid > 1:
        if pid == os.getpid():
            return True
        try:
            with open(f"/proc/{pid}/stat") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return False
    return False


@pytest.fixture(autouse=True)
def _no_signals_to_a_real_tuxtalk(monkeypatch):
    """No test may stop or kill a TuxTalk that is really running: a test that
    reached stop_dictation_service once killed Ron's dev service mid-dictation.
    Processes the tests started themselves can still be signalled."""
    real_kill = os.kill

    def guarded_kill(pid, sig):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read()
        except OSError:
            return real_kill(pid, sig)
        if (pid != os.getpid() and not _started_by_this_test_run(pid)
                and any(name in cmdline for name in _TUXTALK_PROCESSES)):
            pytest.fail(f"a test tried to signal a real TuxTalk process: {cmdline[:80]!r}")
        return real_kill(pid, sig)
    monkeypatch.setattr(os, "kill", guarded_kill)


@pytest.fixture(autouse=True)
def _no_real_history_or_stats(monkeypatch, tmp_path_factory):
    """Recent Dictations and the usage stats live in a throwaway folder: every
    full run used to put seven test sentences into the user's Recent
    Dictations (it keeps 20) and add to their word count."""
    from tuxtalk import history, stats
    folder = tmp_path_factory.mktemp("history")
    monkeypatch.setattr(history, "_history_path", lambda: str(folder / "history.json"))
    monkeypatch.setattr(stats, "stats_path", lambda: str(folder / "stats.json"))


class FakeKeyboard:
    """Stands in for TuxTalk's virtual keyboard: keeps the key events instead
    of typing them. *fail_after* makes writes fail after that many events, as
    a keyboard that breaks partway would."""

    # python-evdev leaves it None when the user can't read /dev/input, as
    # with the hotkey helper installed (the service died over it, 2026-10-20).
    device = None

    def __init__(self):
        self.events = []        # [(code, value)], in order
        self.fail_after = None
        self.closed = False

    def write(self, _type, code, value):
        if self.fail_after is not None and len(self.events) >= self.fail_after:
            raise OSError(19, "No such device")
        self.events.append((code, value))

    def syn(self):
        pass

    def close(self):
        self.closed = True

    def typed_keys(self):
        """The codes pressed down, in order."""
        return [code for code, value in self.events if value == 1]


class FakeClock:
    """virtual_keyboard's time: waits pass at once, and their total shows how
    long the typing would have taken."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)


@pytest.fixture(autouse=True)
def keyboard(monkeypatch):
    """A recording keyboard for every test. A real one would type into
    whatever window has the focus on the computer running the tests."""
    from tuxtalk import virtual_keyboard
    fake = FakeKeyboard()
    clock = FakeClock()
    fake.clock = clock
    monkeypatch.setattr(virtual_keyboard, "time", clock)
    monkeypatch.setattr(virtual_keyboard, "_device", None)
    monkeypatch.setattr(virtual_keyboard, "_make_device", lambda: fake)
    return fake


# --- TuxTalk's windows, built for real (under xvfb-run) ---------------------------
# For tests of what a window shows or does. Reading the source for a label
# would break when the code moves and pass when the label no longer shows.

@pytest.fixture
def prefs_window(monkeypatch):
    """Build Preferences from the default settings, changed by any keyword
    arguments: prefs_window(model="small"). Destroyed after the test."""
    from dataclasses import asdict
    from tuxtalk import config, prefs
    built = []

    def build(**settings):
        values = asdict(config.Settings())
        values.update(settings)
        monkeypatch.setattr(prefs.PreferencesWindow, "load_config", lambda self: dict(values))
        monkeypatch.setattr(prefs, "load_custom_commands", lambda: {})
        monkeypatch.setattr(prefs.PreferencesWindow, "_watch_custom_commands_file",
                            lambda self: None)
        window = prefs.PreferencesWindow()
        built.append(window)
        return window

    yield build
    for window in built:
        window.indicator_grid.destroy()     # not in the window until Customize… opens it
        window.window.destroy()


@pytest.fixture
def help_pages(monkeypatch):
    """Help as the user reads it, {tab title: its text}, from default settings."""
    from gi.repository import Gtk
    from gtk_text import find, shown_text
    from tuxtalk import config, help_dialog
    monkeypatch.setattr(config, "load_config", config.Settings)
    dialog = help_dialog.show_help_dialog()
    try:
        notebook = find(dialog, Gtk.Notebook)
        return {notebook.get_tab_label_text(page): "\n".join(shown_text(page))
                for page in notebook.get_children()}
    finally:
        dialog.destroy()


@pytest.fixture
def tray_menu(monkeypatch):
    """The tray's menu as built from default settings, without starting a tray."""
    from tuxtalk import config, tray
    monkeypatch.setattr(config, "load_config", config.Settings)
    menu = tray.DictationTray.build_menu(tray.DictationTray.__new__(tray.DictationTray))
    yield menu
    menu.destroy()


# --- each program's start, run with nothing real behind it ------------------------
# Each returns run(*argv) -> the list of what main() did, in order, so a test
# can check that one step comes before another. Steps are named for what the
# user would notice; the fakes stand in for the parts that would reach the
# desktop, the bus or the disk.

@pytest.fixture
def prefs_main(monkeypatch):
    """Preferences' main(): its window and the tab asked for on the command line."""
    import sys
    import dbus.mainloop.glib
    from tuxtalk import desktop_identity, prefs, ui_style
    done = []

    class Window:
        def __init__(self):
            done.append("opens the window")

        def select_tab(self, tab):
            done.append(f"shows the {tab} tab")

    monkeypatch.setattr(desktop_identity, "apply", lambda: done.append("names itself"))
    monkeypatch.setattr(ui_style, "prepare_windows", lambda: done.append("prepares windows"))
    monkeypatch.setattr(dbus.mainloop.glib, "DBusGMainLoop",
                        lambda **k: done.append("sets the D-Bus main loop"))
    monkeypatch.setattr(prefs.GLib, "unix_signal_add",
                        lambda *a: done.append("handles bring-to-front"))
    monkeypatch.setattr(prefs, "_acquire_prefs_singleton",
                        lambda tab: done.append("announces its pid"))
    monkeypatch.setattr(prefs, "PreferencesWindow", Window)
    monkeypatch.setattr(prefs.Gtk, "main", lambda: done.append("runs"))

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["tuxtalk-prefs", *argv])
        prefs.main()
        return done
    return run


class _Steps(list):
    """What a main() did, in order, and the timers it set: run_timers() runs
    them as the main loop would, one after another."""

    def __init__(self):
        super().__init__()
        self.timers = []

    def run_timers(self):
        while self.timers:
            _ms, fn, args = self.timers.pop(0)
            fn(*args)


@pytest.fixture
def tray_main(monkeypatch):
    """The tray's main(), on a start that isn't the first unless
    run(first_run=True); *welcome* stands in for the welcome screens."""
    import sys
    import dbus.mainloop.glib
    from tuxtalk import (autostart, control, cuda_helper, desktop_identity,
                         gnome_keybindings, rename_migration, service_launcher,
                         settings_backup, tray, ui_style, uinput_helper, update_checker,
                         welcome_dialog)
    done = _Steps()

    def step(name, result=None):
        return lambda *a, **k: done.append(name) or result

    class Tray:
        def __init__(self):
            self.onboarding_in_progress = False
            done.append("makes the tray")

        _auto_start_service = step("starts the service")
        quit_app = step("quits")
        refresh_menu = step("refreshes the menu")
        show_menu_window_if_no_tray = step("shows the menu window")
        auto_check_for_updates = step("checks for updates")

    monkeypatch.setattr(desktop_identity, "apply", step("names itself"))
    monkeypatch.setattr(ui_style, "prepare_windows", step("prepares windows"))
    monkeypatch.setattr(dbus.mainloop.glib, "DBusGMainLoop", step("sets the D-Bus main loop"))
    monkeypatch.setattr(tray, "_acquire_tray_singleton", step("takes the tray lock"))
    monkeypatch.setattr(tray, "_handle_old_talktype_flatpak", lambda: None)
    monkeypatch.setattr(tray, "_refresh_kde_menu_cache", lambda: None)
    monkeypatch.setattr(rename_migration, "adopt_old_install", step("adopts the old install", []))
    monkeypatch.setattr(rename_migration, "repin_launchers", step("repins launchers", []))
    monkeypatch.setattr(update_checker, "create_hidden_desktop_entry",
                        step("writes the hidden menu entry"))
    monkeypatch.setattr(update_checker, "add_launcher_actions", step("adds launcher actions"))
    monkeypatch.setattr(update_checker, "check_just_updated", lambda: None)
    monkeypatch.setattr(settings_backup, "snapshot_if_changed", step("snapshots the settings"))
    monkeypatch.setattr(uinput_helper, "retire_ydotoold", step("retires ydotoold"))
    monkeypatch.setattr(gnome_keybindings, "restore_left_over",
                        step("restores GNOME keybindings"))
    monkeypatch.setattr(autostart, "repair_stale_appimage_autostart", lambda: False)
    monkeypatch.setattr(autostart, "refresh_autostart_comment", lambda: False)
    monkeypatch.setattr(service_launcher, "stop_dictation_service",
                        step("stops a running service"))
    monkeypatch.setattr(cuda_helper, "mark_first_run_complete", step("marks setup done"))
    monkeypatch.setattr(control, "report", lambda status: None)
    monkeypatch.setattr(tray, "DictationTray", Tray)
    monkeypatch.setattr(tray.time, "sleep", lambda s: None)
    monkeypatch.setattr(tray.GLib, "timeout_add",
                        lambda ms, fn, *args: done.timers.append((ms, fn, args)))
    monkeypatch.setattr(tray.Gtk, "main", step("runs"))
    monkeypatch.delenv("FLATPAK_ID", raising=False)

    def run(*argv, first_run=False, welcome=lambda: {"continue": True}):
        monkeypatch.setattr(sys, "argv", ["tuxtalk", *argv])
        monkeypatch.setattr(cuda_helper, "is_first_run", lambda: first_run)
        monkeypatch.setattr(welcome_dialog, "show_welcome_and_install", welcome)
        tray.main()
        return done
    return run


@pytest.fixture
def service_main(monkeypatch):
    """The dictation service's main(): *cached* says whether the speech model
    is already downloaded. The service's own globals are put back afterwards."""
    import types
    from tuxtalk import (app, config, custom_words, desktop_identity, input_backends,
                         model_helper, ui_style, virtual_keyboard)
    import tuxtalk.dbus_service
    from tuxtalk import audio_duck
    done = _Steps()

    def step(name, result=None):
        return lambda *a, **k: done.append(name) or result

    for name in ("model", "_gtk_loop_running", "dbus_service", "recording_indicator",
                 "_spectrum_processor", "_key_delay_ms", "_log_transcripts", "_usage_stats",
                 "_restore_clipboard", "_pause_media", "_duck_audio", "_paste_last_hotkey",
                 "_custom_commands", "_custom_words"):
        monkeypatch.setattr(app, name, getattr(app, name))
    monkeypatch.setattr(app, "model", None)

    class Bus:
        def __init__(self, instance, primary=True):
            self.instance = instance
            done.append("offers its D-Bus service")
            instance_holder.append(instance)

        emit_service_state = emit_model_changed = lambda self, *a: None

    instance_holder = []

    class Indicator:
        def __init__(self, **options):
            done.append(("shows the indicator", options))

    class Thread:
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            done.append("starts the GTK loop")

    def ready():
        done.append(("says it is ready",
                     app._cmd_start_recording.is_set() or app._cmd_stop_recording.is_set()
                     or app._cmd_start_from_toggle_key.is_set()))

    monkeypatch.setattr(desktop_identity, "apply", step("names itself"))
    monkeypatch.setattr(ui_style, "prepare_windows", step("prepares windows"))
    monkeypatch.setattr(app, "_acquire_single_instance", step("takes the service lock"))
    monkeypatch.setattr(app, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(app, "_guard_large_v3_without_cuda", lambda cfg: None)
    monkeypatch.setattr(app, "_apply_cleanup_settings", lambda cfg: None)
    monkeypatch.setattr(app, "_pick_input_device", lambda mic: None)
    monkeypatch.setattr(app, "load_custom_commands", lambda: {})
    monkeypatch.setattr(custom_words, "load", lambda: [])
    monkeypatch.setattr(virtual_keyboard, "start", lambda: None)
    monkeypatch.setattr(audio_duck, "restore_left_over", lambda: None)
    monkeypatch.setattr(tuxtalk.dbus_service, "TuxTalkDBusService", Bus)
    monkeypatch.setattr(app, "RecordingIndicator", Indicator)
    monkeypatch.setattr(app, "threading", types.SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(app, "_build_model_or_exit", step("loads the speech model", "model"))
    monkeypatch.setattr(app, "_notify_tray_running_engine", step("tells the tray what runs"))
    monkeypatch.setattr(app, "_mark_service_ready", ready)
    monkeypatch.setattr(input_backends, "get_input_backend",
                        lambda **k: types.SimpleNamespace(start=step("listens for hotkeys")))
    monkeypatch.delenv("FLATPAK_ID", raising=False)

    def run(cached=True, **settings):
        monkeypatch.setattr(app, "load_config", lambda: config.Settings(**settings))
        monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda m: cached)
        app.main()
        done.app_instance = instance_holder[0] if instance_holder else None
        return done
    return run
