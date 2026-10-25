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
import threading

import gi
import pytest
from tuxtalk import recording_state as rs
from tuxtalk import live_settings as live
from tuxtalk import keyboard_output
from tuxtalk import engine

# No program a test starts can reach the speakers, nor this one. Every
# program inherits this, so a child Python's sounddevice, a player started
# by a shell, GTK's event sounds and GStreamer find no sound server: the
# PulseAudio and PipeWire sockets named here can't exist (a path under a
# file), and ALSA's configuration defines no device at all, the sound cards
# included. Set before Gtk opens and before PortAudio first reads ALSA's
# configuration. A test that gives a child an environment of its own drops
# this, but only shell scripts are run that way.
os.environ.update({
    "PULSE_SERVER": f"unix:{os.devnull}/no-sound-server",
    "PIPEWIRE_REMOTE": f"{os.devnull}/no-sound-server",
    "ALSA_CONFIG_PATH": os.devnull,
    "JACK_NO_START_SERVER": "1",
    "JACK_DEFAULT_SERVER": "no-sound-server",
})

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
from gi.repository import Gdk, Gtk  # noqa: E402  (Gtk opens the display)
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
    # A fold slides open over 150 ms once its window is on screen. Tests read
    # a window right after the click without running the main loop (that
    # would run the checks Preferences schedules), so they see the fold as
    # the user does once it has opened. Only after the check above: reading
    # the settings starts GLib's helpers on the private bus.
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)


def _hold_back_notifications():
    """Desktop notifications from tests must never reach the user's desktop.

    Patched here, at conftest import, so it covers module-level code too;
    tests that check a message stub tuxtalk.notify.notify themselves.
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
    monkeypatch.setattr(rs.state, "last_inserted_window", None)
    monkeypatch.setattr(rs.state, "nav_context", None)


@pytest.fixture(autouse=True)
def _dictation_state_put_back():
    """Each test starts from the dictation state and cleanup settings the
    last one started from. Tests set rs.state.is_recording and the like
    directly, and run the settings reload (live_settings.apply), and put
    them back after their asserts: one failing assert left is_recording on
    for every later test, so one failure showed as many, depending on order."""
    import dis
    fields = {name: list(value) if isinstance(value, list) else value
              for name, value in vars(rs.state).items()}
    names = {i.argval for i in dis.get_instructions(live.apply)
             if i.opname == "STORE_GLOBAL"}
    settings = {name: getattr(live, name) for name in names}
    yield
    vars(rs.state).clear()
    vars(rs.state).update(fields)
    for name, value in settings.items():
        setattr(live, name, value)


@pytest.fixture(autouse=True)
def _no_real_speech_model(monkeypatch):
    """Start each test with a stand-in speech model. Starting a recording
    loads the real one when none is in memory (Power Saving, _wake_model):
    seconds on a computer that has it, a 670 MB download in CI, where the
    suite ran into its 20-minute limit (2026-10-18). Tests of the loading
    itself set engine.model to None."""
    monkeypatch.setattr(engine, "model", object())


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
    between tests. Sends happen at once, on the test's thread, unless a test
    sets a wait itself.
    test_window_messages tests the label itself, with its own fakes."""
    from tuxtalk import screen_message, send_countdown
    if request.module.__name__.rpartition(".")[2] != "test_window_messages":
        monkeypatch.setattr(screen_message, "show_over_active_window", lambda *a, **k: None)
        monkeypatch.setattr(screen_message, "hide", lambda *a, **k: None)
        monkeypatch.setattr(screen_message, "relabel", lambda *a, **k: None)
    monkeypatch.setattr(send_countdown, "_delay", 0.0)
    # "Right away" on the test's own thread, so a test sees the Enter at once.
    monkeypatch.setattr(send_countdown, "_send_right_away", send_countdown._send)
    monkeypatch.setattr(send_countdown, "_pending", None)
    monkeypatch.setattr(send_countdown, "_told_unknown_window", False)
    monkeypatch.setattr(rs.state, "held_send", False)
    monkeypatch.setattr(send_countdown, "_owed", None)
    monkeypatch.setattr(rs.state, "sent", None)
    # Where an earlier test's text "landed" must not stop this test's send,
    # nor its held-back mark. A test that fakes the typing has its words
    # in a window the desktop named, where an automatic Enter may go.
    from tuxtalk import focused_window
    monkeypatch.setattr(rs.state, "last_inserted_where", (None, None))
    monkeypatch.setattr(rs.state, "message", send_countdown.Message(
        focused_window.Destination(None, ("tray", "{test window}"))))


@pytest.fixture(autouse=True)
def _no_real_password_check(monkeypatch, request):
    """Typing first asks the accessibility bus whether the cursor is in a
    password field, and whether a polkit password prompt is waiting. On the
    user's desktop the answers depend on what they have open;
    test_password_fields fakes them itself."""
    from tuxtalk import atspi_helper, focused_window
    monkeypatch.setattr(focused_window, "_polkit_prompt_waiting", lambda: False)
    if request.module.__name__.rpartition(".")[2] != "test_password_fields":
        monkeypatch.setattr(atspi_helper, "focused_is_password", lambda: False)
        # It asks which window has the focus, which tests script answer by answer.
        monkeypatch.setattr(focused_window, "_password_prompt_has_focus", lambda: False)


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


# Programs that play sound. TuxTalk's audio ducking runs paplay.
_SOUND_PLAYERS = {"paplay", "aplay", "pw-play", "pw-cat", "canberra-gtk-play", "ffplay", "mpv"}


def _no_sound_on_the_speakers():
    """No test may play sound on the user's speakers, for the whole run.

    Installed once, at conftest import, and never undone. A fixture's patch
    is put back when its test ends, while a beep the test asked for can still
    be waiting on the sound thread; it then played on the real output. Every
    sounddevice stream that plays (OutputStream, Stream, the Raw ones, and
    play(), which opens an OutputStream) is refused where they all start, in
    _StreamBase; recording streams still open. Starting a sound player is
    refused too, by the program's name where it comes first; one started
    through a shell or env still finds no sound server (the environment set
    at the top of this file)."""
    import subprocess
    import sounddevice
    open_stream = sounddevice._StreamBase.__init__

    def refuse_output(self, kind, *args, **kwargs):
        if kind != "input":
            raise RuntimeError("tests must not play sound")
        open_stream(self, kind, *args, **kwargs)
    sounddevice._StreamBase.__init__ = refuse_output

    start_program = subprocess.Popen.__init__

    def refuse_players(self, args, *rest, **kwargs):
        first = args[0] if isinstance(args, (list, tuple)) and args else str(args).split(" ")[0]
        if os.path.basename(str(first)) in _SOUND_PLAYERS:
            raise RuntimeError(f"tests must not play sound: {first}")
        start_program(self, args, *rest, **kwargs)
    subprocess.Popen.__init__ = refuse_players


_no_sound_on_the_speakers()


def _play_to_nobody(samples, rate, device, called_off=None):
    """Stands in for beeps._play: the sound plays at once, and silently."""


def drain_sounds(submit, wait):
    """Whether every sound asked for so far played, and whoever asked for it
    was told, within 5 s each. *wait* (beeps.wait_until_played) waits until
    each is over, which leaves the queue empty, so *submit* (beeps._submit)
    then pushes none of them out of it with a job of its own. The sound
    thread gets to that job only once the callback of the last sound before
    it has returned."""
    if not wait(5):
        return False
    drained = threading.Event()
    submit(drained.set, "the end of a test", threading.Event())
    return drained.wait(5)


@pytest.fixture(autouse=True)
def _no_real_sound(request, monkeypatch):
    """Sounds go to a player that plays nothing (test_beeps brings a fake
    sound system of its own), and every sound a test asked for is over, and
    whoever asked told so, before the test's fakes are put back: a beep left
    on the sound thread would otherwise run against whatever the next test
    has. The queue drained is the real one, and waited for with the real
    wait, even where the test faked them."""
    from tuxtalk import beeps
    submit, wait = beeps._submit, beeps.wait_until_played
    if request.module.__name__.rpartition(".")[2] != "test_beeps":
        monkeypatch.setattr(beeps, "output_device", lambda samplerate: (None, samplerate))
        monkeypatch.setattr(beeps, "_play", _play_to_nobody)
    yield
    if not drain_sounds(submit, wait):
        pytest.fail("a sound the test asked for was still playing after it")


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
    from tuxtalk import config
    folder = tmp_path_factory.mktemp("snippets")
    monkeypatch.setattr(config, "CUSTOM_COMMANDS_PATH", str(folder / "custom_commands.toml"))
    monkeypatch.setattr(live, "words", [])


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


@pytest.fixture(autouse=True)
def _no_real_settings(monkeypatch, tmp_path_factory, request):
    """Settings and the data folder in a throwaway folder for every test.

    Loading the settings can set aside a damaged config.toml and write a
    repaired one, and the windows read the backups, first-run flag and
    downloads beside it. Every test gets it, so one that builds a window
    directly (Welcome, Help, the menu window) can't forget to ask.
    test_flatpak_private_paths checks the real path functions and reads no
    settings.
    """
    if request.module.__name__.rpartition(".")[2] == "test_flatpak_private_paths":
        return
    from tuxtalk import config, prefs
    folder = tmp_path_factory.mktemp("settings")
    path = str(folder / "tuxtalk" / "config.toml")
    for module in (config, prefs):          # prefs keeps its own name for the path
        monkeypatch.setattr(module, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "get_data_dir", lambda: str(folder / "TuxTalk"))
    monkeypatch.setattr(config, "_config_cache", None)
    monkeypatch.setattr(config, "_config_mtime", 0.0)
    monkeypatch.setattr(config, "_config_read_failed", False)


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
    arguments: prefs_window(model="small"). Destroyed after the test, with
    the checks it scheduled on the main loop (GPU, typing, extension)
    removed, so a later test that runs the loop never runs them."""
    from dataclasses import asdict
    from gi.repository import GLib
    from tuxtalk import config, prefs, prefs_words
    built, timers = [], []

    def scheduled(add):
        return lambda *args: timers.append(add(*args)) or timers[-1]

    def build(**settings):
        values = asdict(config.Settings())
        values.update(settings)
        monkeypatch.setattr(prefs.PreferencesWindow, "load_config", lambda self: dict(values))
        monkeypatch.setattr(prefs_words, "load_custom_commands", lambda: {})
        monkeypatch.setattr(prefs_words.WordsTab, "_watch_custom_commands_file",
                            lambda self: None)
        with monkeypatch.context() as m:
            for name in ("timeout_add", "timeout_add_seconds", "idle_add"):
                m.setattr(prefs.GLib, name, scheduled(getattr(GLib, name)))
            window = prefs.PreferencesWindow()
        built.append(window)
        return window

    yield build
    for timer in timers:
        if GLib.MainContext.default().find_source_by_id(timer):
            GLib.source_remove(timer)
    for window in built:
        indicator = window.audio_tab.indicator
        if indicator.dialog is not None:                # Customize… opened it
            indicator.dialog.destroy()
        indicator.grid.destroy()        # not in the window until Customize… opens it
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
        stack = find(dialog, Gtk.Stack)
        pages = {}
        for page in stack.get_children():
            stack.set_visible_child(page)
            pages[stack.child_get_property(page, "title")] = "\n".join(shown_text(page))
        return pages
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

def _main_loop_step(done):
    """DBusGMainLoop as a step: only set_as_default=True gives the shared
    connections opened later a main loop, so only that counts."""
    def main_loop(set_as_default=False):
        done.append("sets the D-Bus main loop" if set_as_default
                    else "makes a D-Bus main loop nothing uses")
    return main_loop


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

    monkeypatch.setattr(prefs, "_acquire_prefs_singleton",
                        lambda tab: done.append("announces its pid"))
    monkeypatch.setattr(prefs, "PreferencesWindow", Window)

    def run(*argv):
        # Shared with the other programs' main(), so patched for this run.
        monkeypatch.setattr(desktop_identity, "apply", lambda: done.append("names itself"))
        monkeypatch.setattr(ui_style, "prepare_windows", lambda: done.append("prepares windows"))
        monkeypatch.setattr(dbus.mainloop.glib, "DBusGMainLoop", _main_loop_step(done))
        monkeypatch.setattr(prefs.GLib, "unix_signal_add",
                            lambda *a: done.append("handles bring-to-front"))
        monkeypatch.setattr(prefs.Gtk, "main", lambda: done.append("runs"))
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
    import types
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
    # Its own name for the time module: time.sleep stays real for other threads.
    monkeypatch.setattr(tray, "time", types.SimpleNamespace(sleep=lambda s: None))
    monkeypatch.delenv("FLATPAK_ID", raising=False)

    def run(*argv, first_run=False, welcome=lambda: {"continue": True}):
        # Shared with the other programs' main(), so patched for this run.
        monkeypatch.setattr(desktop_identity, "apply", step("names itself"))
        monkeypatch.setattr(ui_style, "prepare_windows", step("prepares windows"))
        monkeypatch.setattr(dbus.mainloop.glib, "DBusGMainLoop", _main_loop_step(done))
        monkeypatch.setattr(tray.GLib, "timeout_add",
                            lambda ms, fn, *args: done.timers.append((ms, fn, args)))
        monkeypatch.setattr(tray.Gtk, "main", step("runs"))
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

    for name in ("dbus_service", "recording_indicator", "_spectrum_processor"):
        monkeypatch.setattr(app, name, getattr(app, name))
    monkeypatch.setattr(engine, "gtk_loop_running", engine.gtk_loop_running)
    monkeypatch.setattr(keyboard_output, "key_delay_ms", keyboard_output.key_delay_ms)
    for name in ("log_transcripts", "usage_stats", "restore_clipboard", "pause_media",
                 "duck_audio", "paste_last_hotkey", "commands", "words"):
        monkeypatch.setattr(live, name, getattr(live, name))
    monkeypatch.setattr(engine, "model", None)

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

    monkeypatch.setattr(app, "_acquire_single_instance", step("takes the service lock"))
    monkeypatch.setattr(app, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(engine, "guard_large_v3_without_cuda", lambda cfg: None)
    monkeypatch.setattr(live, "apply", lambda cfg: None)
    monkeypatch.setattr(app, "_pick_input_device", lambda mic: None)
    monkeypatch.setattr(app, "load_custom_commands", lambda: {})
    monkeypatch.setattr(custom_words, "load", lambda: [])
    monkeypatch.setattr(virtual_keyboard, "start", lambda: None)
    monkeypatch.setattr(audio_duck, "restore_left_over", lambda: None)
    monkeypatch.setattr(tuxtalk.dbus_service, "TuxTalkDBusService", Bus)
    monkeypatch.setattr(app, "RecordingIndicator", Indicator)
    monkeypatch.setattr(app, "threading", types.SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(engine, "build_or_exit", step("loads the speech model", "model"))
    monkeypatch.setattr(engine, "notify_tray", step("tells the tray what runs"))
    monkeypatch.setattr(app, "_mark_service_ready", ready)
    monkeypatch.setattr(input_backends, "get_input_backend",
                        lambda **k: types.SimpleNamespace(start=step("listens for hotkeys")))
    monkeypatch.delenv("FLATPAK_ID", raising=False)

    def run(cached=True, **settings):
        monkeypatch.setattr(desktop_identity, "apply", step("names itself"))
        monkeypatch.setattr(ui_style, "prepare_windows", step("prepares windows"))
        monkeypatch.setattr(app, "load_config", lambda: config.Settings(**settings))
        monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda m: cached)
        app.main()
        done.app_instance = instance_holder[0] if instance_holder else None
        return done
    return run


@pytest.fixture
def about_window(monkeypatch):
    """The tray's About window, built for real, without fetching release notes."""
    import types
    from gi.repository import Gtk
    from tuxtalk import tray, update_checker
    monkeypatch.setattr(update_checker, "fetch_release_by_tag", lambda tag: None)
    tray.DictationTray.show_about_dialog(types.SimpleNamespace(), None)
    window = next(w for w in Gtk.Window.list_toplevels()
                  if w.get_title() == "About TuxTalk" and w.get_visible())
    yield window
    window.destroy()


@pytest.fixture
def welcome_window(monkeypatch):
    """The first-run welcome screen, built for an English system with an
    NVIDIA card (any graphics card gets the same card and choices)."""
    from tuxtalk import recommend, welcome_dialog
    monkeypatch.setattr(recommend, "detect_hardware",
                        lambda cfg=None: recommend.Hardware("NVIDIA GeForce RTX 4070 SUPER", True))
    monkeypatch.setattr(recommend, "system_language", lambda env=None: "en")
    monkeypatch.setattr(welcome_dialog, "detect_uinput_access", lambda: (True, ""))
    monkeypatch.setattr(welcome_dialog, "detect_portaudio_status", lambda: {"needs_install": False})
    window = welcome_dialog.WelcomeDialog(force_gnome=False)
    window.dialog.show_all()                # as run() does, without waiting for an answer
    while Gtk.events_pending():
        Gtk.main_iteration()
    yield window
    window.destroy()


@pytest.fixture
def setup_complete(monkeypatch):
    """Show Setup Complete (the last first-run screen) and return what it
    showed; *click* names a button to press while it is open."""
    from gi.repository import Gtk
    from gtk_text import find, shown_text
    from tuxtalk import config, welcome_dialog
    monkeypatch.setattr(config, "load_config", config.Settings)

    def show(click=None, **options):
        seen = []

        def run(dialog):
            seen.extend(shown_text(dialog))
            if click:
                find(dialog, Gtk.Button, click).clicked()
            return Gtk.ResponseType.OK
        monkeypatch.setattr(Gtk.Dialog, "run", run)
        welcome_dialog.show_setup_complete_dialog(**options)
        return seen
    return show


@pytest.fixture
def first_run(monkeypatch):
    """First-run setup after the welcome screen (show_welcome_and_install),
    from an AppImage in Downloads, with each screen and download faked.
    Returns the steps it took, in order."""
    import types
    from tuxtalk import autostart, update_checker, welcome_dialog
    done = _Steps()

    class Thread:
        def __init__(self, target, name=None, daemon=None):
            self.daemon = daemon

        def start(self):
            done.append(("copies the AppImage", self))

    monkeypatch.setattr(welcome_dialog, "threading", types.SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(welcome_dialog, "_appimage_copy_thread", None)
    monkeypatch.setattr(welcome_dialog, "show_welcome_dialog", lambda: done.append("welcome") or
                        {"continue": True, "model": "parakeet-v3", "device": "cpu"})
    monkeypatch.setattr(welcome_dialog, "show_hotkey_test_dialog",
                        lambda: done.append("tests the hotkeys"))
    monkeypatch.setattr(welcome_dialog, "_apply_first_run_setup",
                        lambda result: done.append("downloads the speech model"))
    monkeypatch.setattr(welcome_dialog, "show_setup_complete_dialog",
                        lambda **k: done.append("setup complete"))
    monkeypatch.setattr(update_checker, "is_running_from_appimage", lambda: True)
    monkeypatch.setattr(update_checker, "ensure_appimage_in_standard_location",
                        lambda: (False, "/home/u/Downloads/TuxTalk.AppImage"))
    monkeypatch.setattr(update_checker, "desktop_launcher_exists", lambda: True)
    monkeypatch.setattr(autostart, "set_autostart",
                        lambda enable, prefer_installed_appimage=False:
                        done.append(("starts at login", prefer_installed_appimage)) and False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)

    def run():
        welcome_dialog.show_welcome_and_install()
        return done
    return run
