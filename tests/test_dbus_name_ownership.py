"""The tray, not the dictation service, owns the app's D-Bus name.

Both register the name. If they requested it the same way, the service would
queue behind the tray and inherit the name whenever the tray restarted while
the service kept running. The GNOME extension's menu would then reach the
service's stub, which has no FixWordInDictation, and the service's
recording-state reports would go to itself instead of the tray.

Runs real processes on a private bus (dbus-run-session), so it never touches
the desktop session's TuxTalk.
"""
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("dbus-run-session"),
                                reason="needs dbus-run-session")

ROLE = textwrap.dedent("""
    import sys
    from gi.repository import GLib
    from tuxtalk.dbus_service import TuxTalkDBusService
    class App: pass
    try:
        TuxTalkDBusService(App(), primary=(sys.argv[1] == "tray"))
        print("registered", flush=True)
    except Exception as e:
        print(type(e).__name__, flush=True)   # the service keeps running anyway
    GLib.MainLoop().run()
""")

DRIVER = textwrap.dedent("""
    import subprocess, sys, time, dbus
    ROLE = sys.argv[1]
    bus = dbus.SessionBus()
    NAME = "io.github.ronb1964.TalkType"

    def start(role):
        p = subprocess.Popen([sys.executable, "-c", ROLE, role],
                             stdout=subprocess.PIPE, text=True)
        p.first_line = p.stdout.readline().strip()
        return p

    def owner(killed):
        # The bus lets go of a killed process's name a moment after the kill.
        deadline = time.monotonic() + 5
        while True:
            try:
                pid = bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", (NAME,))
            except dbus.exceptions.DBusException:
                pid = None
            if pid not in {p.pid for p in killed} or time.monotonic() > deadline:
                return pid
            time.sleep(0.01)

    def who(procs, killed=()):
        o = owner(killed)
        return next((name for name, p in procs.items() if p.pid == o), o)

    # 1. Normal start: tray first, then the service it launches.
    tray = start("tray"); svc = start("service")
    print("normal", who({"tray": tray, "service": svc}), svc.first_line)

    # 2. The tray restarts while the service keeps running.
    tray.kill(); tray.wait()
    print("tray_gone", who({"service": svc}, killed=[tray]))
    tray2 = start("tray")
    print("tray_back", who({"tray": tray2, "service": svc}))
    tray2.kill(); svc.kill()
    tray2.wait(); svc.wait()
    killed = [tray2, svc]

    # 3. No tray running: the service stands in, then a tray takes over.
    svc = start("service")
    print("standalone", who({"service": svc}, killed), svc.first_line)
    tray = start("tray")
    print("tray_takes_over", who({"tray": tray, "service": svc}))
    tray.kill(); svc.kill()
    tray.wait(); svc.wait()
""")


# The tests' own bus configuration: nothing starts on it when asked for.
CONFIG = os.path.join(os.path.dirname(__file__), "dbus-session.conf")


def test_tray_owns_the_name_and_the_service_never_inherits_it(tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    out = subprocess.run(
        ["dbus-run-session", f"--config-file={CONFIG}", "--", sys.executable, str(driver), ROLE],
        capture_output=True, text=True, timeout=60, env=os.environ.copy())
    lines = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
    assert lines.get("normal") == "tray NameExistsException", out.stdout + out.stderr
    assert lines.get("tray_gone") == "None", "the service inherited the name"
    assert lines.get("tray_back") == "tray"
    assert lines.get("standalone") == "service registered"
    assert lines.get("tray_takes_over") == "tray"


def test_a_bus_that_cant_say_who_holds_the_name_is_logged(caplog):
    """Waiting in line behind another TuxTalk, the bus answers that the name
    has no owner yet; the log says so instead of nothing."""
    import dbus
    from tuxtalk.dbus_service import TuxTalkDBusService

    class Bus:
        def get_name_owner(self, name):
            raise dbus.exceptions.DBusException("no owner",
                                                name="org.freedesktop.DBus.Error.NameHasNoOwner")

        def get_unique_name(self):
            return ":1.4"
    service = object.__new__(TuxTalkDBusService)
    service.bus = Bus()
    with caplog.at_level("INFO"):
        assert service.owns_name() is False
    assert "Couldn't ask the bus who holds" in caplog.text
