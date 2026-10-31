"""The dictation service's calls reach the tray running now.

A service outlives a tray that crashed, and the tray opened again adopts it.
Its link to the tray must follow the D-Bus name to the new tray: bound to
the first one, the GNOME panel never showed recording again, the hotkey test
heard no key and tuxtalk --start waited for a ready report that never came.

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

# A stand-in tray: holds the name, and prints each hotkey report it gets.
TRAY = textwrap.dedent("""
    import sys
    import dbus, dbus.service, dbus.mainloop.glib
    from gi.repository import GLib
    from tuxtalk.dbus_service import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    tag = sys.argv[1]

    class Tray(dbus.service.Object):
        @dbus.service.method(DBUS_INTERFACE, in_signature="s")
        def NotifyHotkeyPressed(self, key):
            print(f"{tag} heard {key}", flush=True)

    name = dbus.service.BusName(DBUS_NAME, bus=dbus.SessionBus(), replace_existing=True)
    Tray(name, DBUS_PATH)
    print("ready", flush=True)
    GLib.MainLoop().run()
""")

DRIVER = textwrap.dedent("""
    import select, subprocess, sys
    from tuxtalk import tray_link
    TRAY = sys.argv[1]

    def heard(p):
        if not select.select([p.stdout], [], [], 5)[0]:
            return "nothing heard"
        return p.stdout.readline().strip()

    def start(tag):
        p = subprocess.Popen([sys.executable, "-c", TRAY, tag],
                             stdout=subprocess.PIPE, text=True)
        assert p.stdout.readline().strip() == "ready"
        return p

    first = start("first")
    tray_link.notify_hotkey_pressed("hold")
    print(heard(first), flush=True)
    first.kill(); first.wait()              # the tray crashed

    second = start("second")                # TuxTalk opened again
    tray_link.notify_hotkey_pressed("toggle")
    print(heard(second), flush=True)
    second.kill(); second.wait()
""")

CONFIG = os.path.join(os.path.dirname(__file__), "dbus-session.conf")


def test_the_service_reaches_a_tray_opened_again_after_a_crash(tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    out = subprocess.run(
        ["dbus-run-session", f"--config-file={CONFIG}", "--",
         sys.executable, str(driver), TRAY],
        capture_output=True, text=True, timeout=60, env=os.environ.copy())
    assert out.stdout.splitlines() == ["first heard hold", "second heard toggle"], \
        out.stdout + out.stderr
