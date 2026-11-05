"""Window voice commands on GNOME: "close this", "minimize" and "maximize"
ask the extension to act on a window (window_actions in TuxTalk). The
extension runs under Node with stand-ins for GNOME Shell (gnome_extension_js);
what it did to the windows is read back from the stand-in's record."""
import re

from gnome_extension_js import run

ENABLED = """
const {extension} = await t.enable();
const served = t.shell.exported.at(-1);
"""

# Firefox with two windows (8 the most recent) and a splash screen that is
# not in the taskbar; window 5 has the focus.
FIREFOX = """
    const splash = new t.Window('firefox', 7);
    splash.skip_taskbar = true;
    t.shell.apps.set('firefox.desktop',
                     [splash, new t.Window('firefox', 8), new t.Window('firefox', 9)]);
    t.focus(new t.Window('org.kde.kwrite', 5));
"""


def test_the_extension_serves_window_action_where_tuxtalk_asks():
    """The method's name and argument types are what TuxTalk's call packs;
    a different shape and every window command fails silently."""
    (_path, xml), _logs = run(ENABLED + "t.report([served.path, served.xml]);")
    method = re.search(r'<method name="WindowAction">(.*?)</method>', xml, re.S).group(1)
    assert re.findall(r'type="(\w)" direction="(\w+)"', method) == [
        ("s", "in"), ("s", "in"), ("b", "in"), ("b", "out")]


def test_close_asks_the_apps_most_recent_window_to_close():
    """The splash screen is not the window the person means."""
    (answer, closed), _logs = run(ENABLED + FIREFOX + """
        t.report([served.object.WindowAction('close', 'firefox.desktop', false),
                  t.shell.closed]);
    """)
    assert answer is True
    assert closed == [[8, 1234]]


def test_close_all_asks_every_window_of_the_app():
    """"Close all Firefox windows": every window in the taskbar, not the splash."""
    (answer, closed), _logs = run(ENABLED + FIREFOX + """
        t.report([served.object.WindowAction('close', 'firefox.desktop', true),
                  t.shell.closed]);
    """)
    assert answer is True
    assert closed == [[8, 1234], [9, 1234]]


def test_an_empty_app_id_acts_on_the_focused_window():
    """"Close this": whatever has the focus, with no app named."""
    (answer, closed), _logs = run(ENABLED + FIREFOX + """
        t.report([served.object.WindowAction('close', '', false), t.shell.closed]);
    """)
    assert answer is True
    assert closed == [[5, 1234]]


def test_minimize_and_maximize_the_focused_window():
    """Each is recorded on the window with the focus and nothing else."""
    answers, _logs = run(ENABLED + FIREFOX + """
        t.report([served.object.WindowAction('minimize', '', false),
                  served.object.WindowAction('maximize', '', false),
                  served.object.WindowAction('unmaximize', '', false),
                  t.shell.minimized, t.shell.maximized, t.shell.unmaximized, t.shell.closed]);
    """)
    assert answers == [True, True, True, [5], [[5, 3]], [[5, 3]], []]


def test_maximize_works_on_a_shell_that_dropped_the_flags():
    """GNOME 49 removed Meta.MaximizeFlags; the call is made without them."""
    answer, _logs = run(ENABLED + FIREFOX + """
        delete t.Meta.MaximizeFlags;
        t.report([served.object.WindowAction('maximize', '', false), t.shell.maximized]);
    """)
    assert answer == [True, [[5, None]]]  # JSON turns undefined into null


def test_an_app_without_a_window_says_so():
    """Not running, or only a splash: false, and nothing is touched."""
    answers, _logs = run(ENABLED + FIREFOX + """
        t.shell.apps.set('org.gnome.Calculator.desktop', []);
        t.focus(null);
        t.report([served.object.WindowAction('close', 'org.gnome.Calculator.desktop', false),
                  served.object.WindowAction('close', 'gone.desktop', true),
                  served.object.WindowAction('close', '', false),
                  t.shell.closed]);
    """)
    assert answers == [False, False, False, []]


def test_names_every_object_has_are_not_actions():
    """'constructor' and '__proto__' exist on any JavaScript object; they must
    be refused like 'kill', not run or thrown on."""
    answers, logs = run(ENABLED + FIREFOX + """
        t.report([served.object.WindowAction('constructor', 'firefox.desktop', false),
                  served.object.WindowAction('__proto__', 'firefox.desktop', false),
                  served.object.WindowAction('hasOwnProperty', '', false),
                  t.shell.closed, t.shell.minimized, t.shell.maximized]);
    """)
    assert answers == [False, False, False, [], [], []]
    assert logs == []


def test_an_unknown_action_does_nothing():
    """Only the polite actions exist: asking to kill a window is refused."""
    answer, _logs = run(ENABLED + FIREFOX + """
        t.report([served.object.WindowAction('kill', 'firefox.desktop', true),
                  t.shell.closed]);
    """)
    assert answer == [False, []]
