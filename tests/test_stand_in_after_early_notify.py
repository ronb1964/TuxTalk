"""With no tray, the dictation service answers on D-Bus itself (the stand-in
service), even when a notification came first.

dbus-python shares one session-bus connection. A notification sent before
GTK starts (large-v3 set without the CUDA files) made it without a main
loop, the stand-in couldn't export itself on it, and the GNOME extension
and the KWin script got no answer for the whole run.

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

DRIVER = textwrap.dedent("""
    import subprocess, sys, threading
    from gi.repository import GLib
    from tuxtalk import app, notify
    from tuxtalk.config import load_config
    from tuxtalk.dbus_service import DBUS_NAME, DBUS_PATH

    class Stop(Exception):
        pass

    def settings_with_an_early_notification():
        notify.notify("Large-v3 needs CUDA", "Using small instead")
        raise Stop
    app._load_settings = settings_with_an_early_notification
    try:
        app.main()
    except Stop:
        pass

    if app._start_dbus_stand_in(load_config()) is None:
        print("no stand-in", flush=True)
        sys.exit()
    ASK = ("import dbus, sys; dbus.SessionBus().call_blocking(sys.argv[1], sys.argv[2], "
           "'org.freedesktop.DBus.Introspectable', 'Introspect', '', (), timeout=5); "
           "print('answered')")
    loop = GLib.MainLoop()

    def ask():
        out = subprocess.run([sys.executable, "-c", ASK, DBUS_NAME, DBUS_PATH],
                             capture_output=True, text=True)
        print(out.stdout.strip() or "no answer", flush=True)
        GLib.idle_add(loop.quit)
    threading.Thread(target=ask).start()
    loop.run()
""")

CONFIG = os.path.join(os.path.dirname(__file__), "dbus-session.conf")


def test_the_stand_in_answers_after_an_early_notification(tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    out = subprocess.run(
        ["dbus-run-session", f"--config-file={CONFIG}", "--", sys.executable, str(driver)],
        capture_output=True, text=True, timeout=60, env=os.environ.copy())
    assert out.stdout.splitlines() == ["answered"], out.stdout + out.stderr
