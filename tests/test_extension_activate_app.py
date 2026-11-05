"""Open apps by voice on GNOME: "open Firefox" with Firefox already open asks
the extension to bring its window forward (app_focus.py). The extension runs
under Node with stand-ins for GNOME Shell (gnome_extension_js); its answer
says whether there was a window, and with none TuxTalk starts the app."""
import re

from gnome_extension_js import run
from tuxtalk import app_focus

ENABLED = """
const {extension} = await t.enable();
const served = t.shell.exported.at(-1);
"""


def test_the_extension_serves_activate_app_where_tuxtalk_asks():
    """The dictation service calls GNOME Shell at app_focus's path and
    interface: a different spelling on either side and the window never
    comes forward."""
    (path, xml), _logs = run(ENABLED + "t.report([served.path, served.xml]);")
    assert path == app_focus.EXTENSION_PATH
    assert f'<interface name="{app_focus.EXTENSION_INTERFACE}">' in xml
    method = re.search(r'<method name="ActivateApp">(.*?)</method>', xml, re.S).group(1)
    assert re.findall(r'type="(\w)" direction="(\w+)"', method) == [("s", "in"), ("b", "out")]


def test_an_open_app_is_brought_forward_with_its_most_recent_window():
    """GNOME Shell lists an app's windows most recently used first; a window
    left out of the taskbar (a splash screen, a tooltip) is never the one."""
    (answer, activated), _logs = run(ENABLED + """
        const splash = new t.Window('firefox', 7);
        splash.skip_taskbar = true;
        t.shell.apps.set('firefox.desktop',
                         [splash, new t.Window('firefox', 8), new t.Window('firefox', 9)]);
        t.report([served.object.ActivateApp('firefox.desktop'), t.shell.activated]);
    """)
    assert answer is True
    assert activated == [8]


def test_an_app_without_a_window_says_so():
    """Running in the background (a mail app's tray icon), or not running at
    all: false, so TuxTalk starts it, which shows its window."""
    answers, _logs = run(ENABLED + """
        const splash = new t.Window('thunderbird', 3);
        splash.skip_taskbar = true;
        t.shell.apps.set('thunderbird.desktop', [splash]);
        t.shell.apps.set('org.gnome.Calculator.desktop', []);
        t.report([served.object.ActivateApp('thunderbird.desktop'),
                  served.object.ActivateApp('org.gnome.Calculator.desktop'),
                  served.object.ActivateApp('gone.desktop'),
                  t.shell.activated]);
    """)
    assert answers == [False, False, False, []]


def test_turning_the_extension_off_takes_the_method_off_the_bus():
    """The screen lock turns extensions off too; a call then must find
    nothing rather than a button already destroyed."""
    still_there, _logs = run(ENABLED + """
        extension.disable();
        t.report(served.path);
    """)
    assert still_there is None

