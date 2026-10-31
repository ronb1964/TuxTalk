"""tuxtalk --toggle, --start and --stop (control.py, GitHub Discussion #12).

People bind a desktop shortcut to the command. It must work whether TuxTalk
is running or not, and a request sent while the speech model loads must wait
for it rather than be lost: the service throws away toggles sent while it
loads, so a stray key press can't start a recording out of the blue.

Nothing here touches a real bus, a real service or the user's runtime folder.
"""

import fcntl
import os
import pathlib
import types

import pytest

from tuxtalk import control
from tuxtalk.control import WaitingStart, act_now, parse_action
from tuxtalk import recording_state as rs
from tuxtalk import tray_link
from tuxtalk import recording

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------- the command line ----------

@pytest.mark.parametrize("argv,expected", [
    (["--toggle"], "toggle"),
    (["--start"], "start"),
    (["--stop"], "stop"),
    ([], None),
    (["tray"], None),                   # the AppImage's desktop entry: "AppRun tray"
    (["tray", "--toggle"], "toggle"),
])
def test_parse_action(argv, expected):
    assert parse_action(argv) == expected


def test_only_one_action_at_a_time():
    with pytest.raises(SystemExit):
        parse_action(["--start", "--stop"])


def test_every_launcher_hands_its_arguments_to_the_tray():
    """The AppImage's AppRun and run-dev.sh pass "$@" on, so the tray's
    main() sees --toggle; so do the .deb/.rpm launchers, which run AppRun
    (test_native_packages builds them)."""
    assert '-m tuxtalk.tray "$@"' in (ROOT / "container-build.sh").read_text()
    assert '-m tuxtalk.tray "$@"' in (ROOT / "run-dev.sh").read_text()


@pytest.mark.parametrize("action,recording,expected", [
    ("toggle", False, (True, "started")),
    ("toggle", True, (True, "stopped")),
    ("start", False, (True, "started")),
    ("start", True, (False, "already")),
    ("stop", True, (True, "stopped")),
    ("stop", False, (False, "idle")),
])
def test_act_now(action, recording, expected):
    assert act_now(action, recording) == expected


# ---------- a request made while the model loads ----------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_toggle_waits_and_a_second_one_cancels_it():
    w = WaitingStart(Clock())
    assert w.apply("toggle") == "waiting" and w.pending
    assert w.apply("toggle") == "cancelled" and not w.pending


def test_start_and_stop_while_loading():
    w = WaitingStart(Clock())
    assert w.apply("stop") == "idle"
    assert w.apply("start") == "waiting"
    assert w.apply("start") == "waiting", "start is not a toggle"
    assert w.apply("stop") == "cancelled"


def test_a_waiting_start_is_taken_once():
    w = WaitingStart(Clock())
    w.apply("start")
    assert w.take() is True
    assert w.take() is False


def test_a_waiting_start_lapses():
    """A recording minutes later (a model download, a failed start) would come
    out of the blue."""
    clock = Clock()
    w = WaitingStart(clock)
    w.apply("toggle")
    clock.now += control.WAIT_LIMIT_S + 1
    assert w.take() is False


# ---------- asking the running TuxTalk ----------

class FakeDBusError(Exception):
    def __init__(self, name):
        self._name = name

    def get_dbus_name(self):
        return self._name


class FakeBus:
    def __init__(self, owned_after=0, control_error=None):
        self.checks = 0
        self.owned_after = owned_after
        self.calls = []
        self.control_error = control_error

    def name_has_owner(self, name):
        assert name == "io.github.ronb1964.TalkType"
        self.checks += 1
        return self.checks > self.owned_after

    def get_object(self, name, path, introspect=True):
        bus = self

        class Obj:
            def ControlDictation(self, action, dbus_interface):
                bus.calls.append(("ControlDictation", action))
                if bus.control_error:
                    raise FakeDBusError(bus.control_error)
                return "started"

            def ToggleRecording(self, dbus_interface):
                bus.calls.append(("ToggleRecording",))

        return Obj()


@pytest.fixture
def fake_dbus(monkeypatch):
    import dbus
    monkeypatch.setattr(dbus, "DBusException", FakeDBusError)


def test_nothing_running_means_this_process_starts_tuxtalk(fake_dbus):
    bus = FakeBus(owned_after=99)
    assert control.ask_running("toggle", bus=bus) is None
    assert bus.calls == []


def test_a_running_tuxtalk_answers(fake_dbus):
    bus = FakeBus()
    assert control.ask_running("toggle", bus=bus) == "started"
    assert bus.calls == [("ControlDictation", "toggle")]


def test_a_tray_still_starting_up_is_waited_for(fake_dbus):
    """At login the shortcut can beat the tray to its D-Bus name; starting a
    second tray then would only say "already running"."""
    bus = FakeBus(owned_after=3)
    sleeps = []
    status = control.ask_running("start", tray_starting=lambda: True, bus=bus,
                                 sleep=sleeps.append)
    assert status == "started" and len(sleeps) == 3


def test_a_tray_that_never_takes_its_name_gives_up(fake_dbus):
    clock = Clock()

    def sleep(s):
        clock.now += s

    status = control.ask_running("toggle", tray_starting=lambda: True,
                                 bus=FakeBus(owned_after=10**9), sleep=sleep, clock=clock)
    assert status == "starting"


def test_an_older_tuxtalk_gets_its_menu_toggle(fake_dbus):
    bus = FakeBus(control_error="org.freedesktop.DBus.Error.UnknownMethod")
    assert control.ask_running("toggle", bus=bus) == "old"
    assert bus.calls[-1] == ("ToggleRecording",)


def test_other_dbus_errors_are_not_hidden(fake_dbus):
    bus = FakeBus(control_error="org.freedesktop.DBus.Error.NoReply")
    with pytest.raises(FakeDBusError):
        control.ask_running("toggle", bus=bus)


def test_report_exit_codes(capsys):
    assert control.report("started") == 0
    assert control.report("waiting") == 0
    assert control.report("setup") == 1
    assert "first-run setup" in capsys.readouterr().out


# ---------- the tray ----------

@pytest.fixture
def tray_ns(monkeypatch):
    """A tray with just what control() and service_ready() use."""
    from tuxtalk import service_launcher, tray
    services = {4242}
    monkeypatch.setattr(service_launcher, "read_ready_pid", lambda: None)
    signals = []
    started = []
    app = types.SimpleNamespace(is_recording=False)
    t = types.SimpleNamespace(
        onboarding_in_progress=False,
        _quitting=False,
        _ready_pid=None,
        _waiting_start=WaitingStart(),
        dbus_service=types.SimpleNamespace(app=app),
        running=False,
        services=services,
        signals=signals,
        started=started,
    )
    t._check_pid_is_service = lambda pid: pid in services
    t._ready_service_pid = lambda: tray.DictationTray._ready_service_pid(t)
    t.is_service_running = lambda: t.running
    t.start_service = lambda _: started.append(True)
    t._signal_toggle = signals.append
    t.control = lambda action: tray.DictationTray.control(t, action)
    t.service_ready = lambda pid: tray.DictationTray.service_ready(t, pid)
    return t


def test_toggle_with_nothing_running_starts_the_service_and_waits(tray_ns):
    assert tray_ns.control("toggle") == "waiting"
    assert tray_ns.started == [True] and tray_ns.signals == []
    tray_ns.service_ready(4242)
    assert tray_ns.signals == [4242], "the recording starts once the model is loaded"
    tray_ns.service_ready(4242)
    assert tray_ns.signals == [4242], "and only once"


def test_toggle_while_the_model_loads_does_not_start_a_second_service(tray_ns):
    tray_ns.running = True
    assert tray_ns.control("toggle") == "waiting"
    assert tray_ns.started == []


def test_a_ready_service_is_toggled_at_once(tray_ns):
    tray_ns.service_ready(4242)
    assert tray_ns.control("toggle") == "started"
    tray_ns.dbus_service.app.is_recording = True
    assert tray_ns.control("toggle") == "stopped"
    assert tray_ns.control("start") == "already"
    assert tray_ns.signals == [4242, 4242]


def test_stop_with_nothing_running_starts_nothing(tray_ns):
    assert tray_ns.control("stop") == "idle"
    assert tray_ns.started == []


def test_nothing_happens_during_first_run_setup(tray_ns):
    tray_ns.onboarding_in_progress = True
    assert tray_ns.control("toggle") == "setup"
    assert tray_ns.started == [] and not tray_ns._waiting_start.pending


def test_a_ready_report_from_a_stopped_service_counts_for_nothing(tray_ns):
    """After a restart the old service's report must not make the new one,
    still loading, look ready: its toggle would be thrown away."""
    tray_ns.service_ready(1111)             # not a running service any more
    tray_ns.running = True
    assert tray_ns.control("toggle") == "waiting"
    assert tray_ns.signals == []


@pytest.mark.parametrize("pid", [12345, 0, -1])
def test_a_ready_report_for_another_process_signals_nothing(tray_ns, pid):
    """Any program on the session bus can call NotifyServiceReady. While a
    --start waits, a PID that isn't a dictation service must not get the
    SIGUSR1 (it would end that program; 0 is the tray's process group), and
    the waiting request stays for the real service."""
    assert tray_ns.control("start") == "waiting"
    tray_ns.service_ready(pid)
    assert tray_ns.signals == []
    assert tray_ns._ready_pid is None
    tray_ns.service_ready(4242)
    assert tray_ns.signals == [4242], "the real service still gets the recording"


def test_a_tray_started_after_the_service_reads_its_ready_file(tray_ns, monkeypatch):
    from tuxtalk import service_launcher
    monkeypatch.setattr(service_launcher, "read_ready_pid", lambda: 4242)
    assert tray_ns.control("toggle") == "started"
    assert tray_ns.signals == [4242]


def test_the_tray_forgets_readiness_when_the_service_stops(monkeypatch):
    from tuxtalk import tray
    t = types.SimpleNamespace(
        _last_service_state=True, dbus_service=None, _ready_pid=4242,
        is_service_running=lambda: False,
        update_icon_status=lambda is_running=None: None,
        update_menu_display=lambda is_running=None: None,
        _release_desktop_hotkeys=lambda: None,
    )
    tray.DictationTray.update_status_and_menu(t, repeat=False)
    assert t._ready_pid is None


def test_the_command_runs_before_the_tray_takes_its_lock(monkeypatch):
    """With TuxTalk running, `tuxtalk --toggle` asks it and exits; taking the
    lock first would only show "TuxTalk is already running"."""
    from tuxtalk import tray

    class Asked(Exception):
        pass

    def ask(action):
        assert action == "toggle"
        raise Asked

    from tuxtalk import desktop_identity
    monkeypatch.setattr(desktop_identity, "apply", lambda: None)
    monkeypatch.setattr(tray.sys, "argv", ["tuxtalk", "--toggle"])
    monkeypatch.setattr(tray.tray_instance, "run_command_line", ask)
    monkeypatch.setattr(tray.tray_instance, "acquire",
                        lambda: pytest.fail("took the lock before asking"))
    with pytest.raises(Asked):
        tray.main()


@pytest.mark.parametrize("status,action,code", [
    ("started", "toggle", 0),
    ("setup", "toggle", 1),
    (None, "stop", 0),                  # nothing running: nothing to stop
])
def test_run_command_line_exits_when_it_is_done(monkeypatch, status, action, code):
    from tuxtalk import tray_instance
    monkeypatch.setattr(control, "ask_running", lambda a, tray_starting: status)
    with pytest.raises(SystemExit) as e:
        tray_instance.run_command_line(action)
    assert e.value.code == code


def test_run_command_line_returns_to_start_tuxtalk(monkeypatch):
    from tuxtalk import tray_instance
    monkeypatch.setattr(control, "ask_running", lambda a, tray_starting: None)
    tray_instance.run_command_line("toggle")        # returns: main() goes on to start TuxTalk


def test_tray_lock_held(monkeypatch, tmp_path):
    """Only a lock someone holds now counts: not a file left by a tray that
    has exited."""
    from tuxtalk import single_instance, tray_instance
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path))
    assert tray_instance.tray_starting() is False          # no tray ever started here
    path = tmp_path / "talktype-tray.lock"
    path.write_text("123")
    assert tray_instance.tray_starting() is False
    with open(path) as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert tray_instance.tray_starting() is True
    assert tray_instance.tray_starting() is False
    assert path.read_text() == "123", "the running tray's PID stays in the file"


# ---------- D-Bus ----------

def make_service(app):
    from tuxtalk.dbus_service import TuxTalkDBusService
    s = TuxTalkDBusService.__new__(TuxTalkDBusService)    # no real bus
    s.app = app
    return s


def test_control_dictation_answers_with_the_status():
    s = make_service(types.SimpleNamespace(control=lambda a: f"did {a}"))
    assert s.ControlDictation("toggle") == "did toggle"


def test_control_dictation_refuses_anything_else():
    s = make_service(types.SimpleNamespace(control=lambda a: pytest.fail("called")))
    assert s.ControlDictation("quit") == "unknown"
    assert make_service(object()).ControlDictation("toggle") == "unknown"


def test_notify_service_ready_reaches_the_tray(monkeypatch):
    from tuxtalk import dbus_service
    monkeypatch.setattr(dbus_service.GLib, "idle_add", lambda f, *a: f(*a))
    got = []
    make_service(types.SimpleNamespace(service_ready=got.append)).NotifyServiceReady(4242)
    assert got == [4242]


# ---------- the service ----------

@pytest.fixture
def service(monkeypatch):
    from tuxtalk import app, service_launcher
    monkeypatch.setattr(app, "_service_ready", False)
    monkeypatch.setattr(app, "_waiting_start", None)
    monkeypatch.setattr(rs.state, "is_recording", False)
    written = []
    monkeypatch.setattr(service_launcher, "write_ready_pid", written.append)
    told = []

    class Tray:
        def NotifyServiceReady(self, pid, dbus_interface, timeout, signature=None):
            told.append((int(pid), signature))

    monkeypatch.setattr(tray_link, "get_proxy", lambda: Tray())
    recording.start_requested.clear()
    recording.stop_requested.clear()
    yield types.SimpleNamespace(app=app, written=written, told=told)
    recording.start_requested.clear()
    recording.stop_requested.clear()


def test_the_service_says_when_it_is_ready(service):
    service.app._mark_service_ready()
    assert service.told == [(os.getpid(), "u")] and service.written == [os.getpid()]
    assert not recording.start_requested.is_set()


def test_the_service_gets_ready_even_without_dbus(monkeypatch):
    """With dbus-python missing, telling the tray fails, is logged and is
    passed over: the service still marks itself ready, and main() goes on
    to start the input loop. Real tray_link, with "import dbus" failing."""
    import sys
    from tuxtalk import app, service_launcher
    monkeypatch.setitem(sys.modules, "dbus", None)    # "import dbus" raises
    monkeypatch.setattr(tray_link, "_tray_dbus_proxy", None)
    monkeypatch.setattr(app, "_service_ready", False)
    monkeypatch.setattr(app, "_waiting_start", None)
    written = []
    monkeypatch.setattr(service_launcher, "write_ready_pid", written.append)
    app._mark_service_ready()
    assert written == [os.getpid()] and app._service_ready

def test_with_no_tray_the_service_keeps_a_request_until_it_is_ready(service):
    """The service owns the D-Bus name itself when no tray runs."""
    assert service.app._control_in_service("toggle") == "waiting"
    assert not recording.start_requested.is_set()
    service.app._mark_service_ready()
    assert recording.start_requested.is_set()


def test_with_no_tray_a_ready_service_toggles_at_once(service):
    service.app._mark_service_ready()
    assert service.app._control_in_service("toggle") == "started"
    assert recording.start_requested.is_set()


def test_ready_is_said_after_stale_toggles_are_cleared(service_main):
    """Said before the clear, a toggle sent in between would be thrown away;
    after the input backend starts, it would come too late to help."""
    recording.started_from_toggle_key.set()        # pressed while the model loaded
    recording.start_requested.set()
    started = service_main()
    assert ("says it is ready", False) in started
    assert started.index(("says it is ready", False)) < started.index("listens for hotkeys")


@pytest.mark.parametrize("appimage,expected", [
    ("/home/u/AppImages/TuxTalk.AppImage", "/home/u/AppImages/TuxTalk.AppImage --toggle"),
    # Quoted, or the shortcut runs "/home/Jo" (GNOME splits it like a shell);
    # and never "~": GNOME starts a custom shortcut without a shell to expand it.
    ("/home/Jo Ann/AppImages/TuxTalk.AppImage",
     "'/home/Jo Ann/AppImages/TuxTalk.AppImage' --toggle"),
    ("/opt/tuxtalk/TuxTalk.AppImage", "tuxtalk --toggle"),      # AUR: its launcher
    ("", "tuxtalk --toggle"),                                    # .deb / .rpm
])
def test_help_names_the_command_for_this_install(appimage, expected):
    assert control.shortcut_command(appimage_env=appimage) == expected


def test_help_mentions_the_command(help_pages):
    """Help names the command for this install (shortcut_command, above)."""
    terminals = next(text for title, text in help_pages.items() if "Terminals" in title)
    assert control.shortcut_command() in terminals
    assert "tuxtalk --toggle" in (ROOT / "README.md").read_text()


def test_the_ready_file_round_trip(monkeypatch, tmp_path):
    from tuxtalk import service_launcher
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert service_launcher.read_ready_pid() is None
    service_launcher.write_ready_pid(4242)
    assert service_launcher.read_ready_pid() == 4242
    assert os.listdir(tmp_path) == ["tuxtalk-service-ready"]


def test_a_toggle_sent_right_after_ready_starts_a_recording(monkeypatch):
    """tuxtalk --toggle with nothing running: the tray sends it the moment
    the service says it's ready, while the service still connects to the
    hotkey helper. It was counted into the input loop's starting point and
    never acted on."""
    from tuxtalk import app, service_tick
    monkeypatch.setattr(recording, "toggles_asked", 5)     # sent while the model loaded
    monkeypatch.setattr(rs.state, "is_recording", False)
    recording.start_requested.clear()
    recording.toggles_asked = 0                             # app.main, before "ready"
    app._handle_sigusr1(None, None)                         # the tray's toggle
    svc = service_tick.ServiceState(types.SimpleNamespace(mic=""))
    service_tick._take_toggle(svc)
    assert recording.start_requested.is_set()
    recording.start_requested.clear()
