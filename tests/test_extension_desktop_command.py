"""Desktop voice commands on GNOME: "snap left", "centre window", "show
desktop", "switch to desktop two" and the rest ask the extension to place the
focused window or change workspace (desktop_commands in TuxTalk). The extension
runs under Node with stand-ins for GNOME Shell (gnome_extension_js); what it did
to windows and workspaces is read back from the stand-in's record."""
import re

from gnome_extension_js import run

ENABLED = """
const {extension} = await t.enable();
const served = t.shell.exported.at(-1);
const ask = (action, number = 0) => served.object.DesktopCommand(action, number);
"""

# KWrite (window 5, 800x600) has the focus; Firefox has two more windows.
FOCUSED = """
    const kwrite = new t.Window('org.kde.kwrite', 5);
    t.shell.workspaceWindows = [kwrite, new t.Window('firefox', 8), new t.Window('firefox', 9)];
    t.focus(kwrite);
"""


def test_the_extension_serves_desktop_command_where_tuxtalk_asks():
    """The method's name and argument types are what TuxTalk's call packs;
    a different shape and every desktop command fails silently."""
    (_path, xml), _logs = run(ENABLED + "t.report([served.path, served.xml]);")
    method = re.search(r'<method name="DesktopCommand">(.*?)</method>', xml, re.S).group(1)
    assert re.findall(r'type="(\w)" direction="(\w+)"', method) == [
        ("s", "in"), ("i", "in"), ("s", "out")]


def test_snap_left_fills_the_left_half_of_the_work_area():
    """The work area is the screen without the top bar."""
    answer, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('snap left'), t.shell.placed]);
    """)
    assert answer == ["done", [[5, 0, 32, 960, 1048]]]


def test_snap_top_right_fills_a_quarter():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('snap top right'), t.shell.placed]);
    """)
    assert answer == ["done", [[5, 960, 32, 960, 524]]]


def test_the_right_and_bottom_parts_take_the_odd_pixel():
    """An area of odd size leaves no gap between the two halves."""
    answer, _logs = run(ENABLED + FOCUSED + """
        kwrite.get_work_area_current_monitor = () => ({x: 0, y: 0, width: 1921, height: 1049});
        ask('snap left');
        ask('snap bottom right');
        t.report(t.shell.placed);
    """)
    assert answer == [[5, 0, 0, 960, 1049], [5, 960, 524, 961, 525]]


def test_a_maximized_window_is_unmaximized_before_snapping():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('snap right'), t.shell.unmaximized, t.shell.placed]);
    """)
    assert answer == ["done", [[5, 3]], [[5, 960, 32, 960, 1048]]]


def test_snap_works_on_a_shell_that_dropped_the_maximize_flags():
    """GNOME 49 removed Meta.MaximizeFlags; the window is still unmaximized."""
    answer, _logs = run(ENABLED + FOCUSED + """
        delete t.Meta.MaximizeFlags;
        t.report([ask('snap left'), t.shell.unmaximized, t.shell.placed]);
    """)
    assert answer == ["done", [[5, None]], [[5, 0, 32, 960, 1048]]]


def test_centre_keeps_the_size():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('center'), t.shell.placed]);
    """)
    assert answer == ["done", [[5, 560, 256]]]


def test_snap_moves_the_focused_window_and_no_other():
    answer, _logs = run(ENABLED + FOCUSED + """
        ask('snap left');
        ask('center');
        t.report(t.shell.placed.map(p => p[0]));
    """)
    assert answer == [5, 5]


def test_no_focused_app_window_says_so():
    """Nothing in focus, or the desktop's own window: nothing is placed."""
    answers, _logs = run(ENABLED + FOCUSED + """
        t.focus(null);
        const first = ask('snap left');
        const desktop = new t.Window('ding', 3);
        desktop.type = t.Meta.WindowType.DESKTOP;
        t.focus(desktop);
        t.report([first, ask('center'), ask('to desktop', 2), ask('to next screen'),
                  t.shell.placed, t.shell.movedToWorkspace, t.shell.movedToMonitor]);
    """)
    assert answers == ["no_window"] * 4 + [[], [], []]


def test_show_desktop_twice_brings_back_only_what_it_minimized():
    """A window the person minimized themselves stays minimized."""
    answer, _logs = run(ENABLED + FOCUSED + """
        t.shell.workspaceWindows[2].minimized = true;
        const first = ask('show desktop');
        const minimizedFirst = [...t.shell.minimized];
        const second = ask('show desktop');
        t.report([first, minimizedFirst, second, t.shell.unminimized,
                  t.shell.workspaceWindows.map(w => w.minimized)]);
    """)
    assert answer == ["done", [5, 8], "done", [5, 8], [False, False, True]]


def test_show_desktop_leaves_splash_screens_and_panels_alone():
    answer, _logs = run(ENABLED + FOCUSED + """
        const splash = new t.Window('firefox', 7);
        splash.skip_taskbar = true;
        const dock = new t.Window('plank', 4);
        dock.type = t.Meta.WindowType.DOCK;
        t.shell.workspaceWindows.push(splash, dock);
        ask('show desktop');
        t.report(t.shell.minimized);
    """)
    assert answer == [5, 8, 9]


def test_a_third_show_desktop_hides_again():
    """After the windows came back, the next one hides them anew."""
    answer, _logs = run(ENABLED + FOCUSED + """
        ask('show desktop'); ask('show desktop'); ask('show desktop');
        t.report(t.shell.minimized);
    """)
    assert answer == [5, 8, 9, 5, 8, 9]


def test_turning_the_extension_off_forgets_what_show_desktop_hid():
    """Else a later enable would bring back windows from an earlier session."""
    answer, _logs = run(ENABLED + FOCUSED + """
        ask('show desktop');
        extension.disable();
        await t.enable();
        const again = t.shell.exported.at(-1).object.DesktopCommand('show desktop', 0);
        t.report([again, t.shell.unminimized]);
    """)
    assert answer == ["done", []]


def test_switch_to_desktop_two_activates_it():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('desktop', 2), t.shell.activatedWorkspace]);
    """)
    assert answer == ["done", [1]]


def test_a_desktop_that_isnt_there_says_so():
    answers, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('desktop', 5), ask('desktop', 0), ask('to desktop', 5),
                  t.shell.activatedWorkspace, t.shell.movedToWorkspace]);
    """)
    assert answers == ["no_desktop", "no_desktop", "no_desktop", [], []]


def test_next_desktop_goes_to_the_neighbour():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('next desktop'), t.shell.activatedWorkspace]);
    """)
    assert answer == ["done", [1]]


def test_previous_desktop_at_the_first_says_edge():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('previous desktop'), t.shell.activatedWorkspace]);
    """)
    assert answer == ["edge", []]


def test_next_desktop_at_the_last_says_edge():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.shell.activeWorkspace = 1;
        t.report([ask('next desktop'), t.shell.activatedWorkspace]);
    """)
    assert answer == ["edge", []]


def test_one_desktop_has_nowhere_to_go():
    answers, _logs = run(ENABLED + FOCUSED + """
        t.shell.workspaces = 1;
        t.report([ask('next desktop'), ask('to next desktop'), t.shell.activatedWorkspace]);
    """)
    assert answers == ["one_desktop", "one_desktop", []]


def test_move_window_to_desktop_two():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('to desktop', 2), t.shell.movedToWorkspace]);
    """)
    assert answer == ["done", [[5, 1]]]


def test_move_window_to_the_next_desktop():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('to next desktop'), ask('to previous desktop'), t.shell.movedToWorkspace]);
    """)
    assert answer == ["done", "edge", [[5, 1]]]


def test_one_monitor_moves_nothing():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('to next screen'), t.shell.movedToMonitor]);
    """)
    assert answer == ["one_screen", []]


def test_two_monitors_move_the_window():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.shell.monitors = 2;
        t.report([ask('to next screen'), t.shell.movedToMonitor]);
    """)
    assert answer == ["done", [[5, 1]]]


def test_show_all_windows_toggles_the_overview():
    answer, _logs = run(ENABLED + FOCUSED + """
        t.report([ask('overview'), t.shell.overviewToggles]);
    """)
    assert answer == ["done", 1]


def test_an_unknown_action_does_nothing():
    """'constructor' and '__proto__' exist on any object; like 'kill' they are
    refused, not run or thrown on."""
    answer, logs = run(ENABLED + FOCUSED + """
        t.report([ask('constructor'), ask('__proto__'), ask('kill', 5),
                  t.shell.placed, t.shell.minimized, t.shell.closed,
                  t.shell.movedToWorkspace, t.shell.activatedWorkspace]);
    """)
    assert answer == ["unknown"] * 3 + [[]] * 5
    assert logs == []
