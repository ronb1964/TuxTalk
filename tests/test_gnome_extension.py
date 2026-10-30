"""The GNOME extension's panel button and menu, run under Node with stand-ins
for GNOME Shell (gnome_extension_js): what a GNOME user, or a screen reader,
gets from it as TuxTalk starts, changes and quits."""
import json

from gnome_extension_js import run

STARTED = """
const {extension, button} = await t.enable();
const tux = t.tuxtalkStarts();
"""


def test_the_menu_matches_the_trays_item_for_item(tray_menu):
    """CLAUDE.md: the two menus keep the same items in the same order. Only
    the on/off switch and the model line read differently, being widgets of
    their own kind."""
    tray = [item.get_label() or "---" for item in tray_menu.get_children()]
    gnome, _logs = run(STARTED + "t.report(t.labels(button.menu));")
    assert gnome[0] == "Dictation on/off" and tray[0] == "Turn Dictation On"
    assert gnome[5] == "Loading the speech model..."
    assert gnome[:5] + gnome[6:] == ["Dictation on/off"] + tray[1:5] + tray[6:]


def test_the_button_tells_a_screen_reader_whether_dictation_is_on():
    """The icon changed with dictation, but a screen reader only ever heard
    "TuxTalk": the panel button's name never said whether it was on."""
    said, _logs = run(STARTED + """
        const said = [button.accessible_name];
        t.status(tux, true);
        said.push(button.accessible_name);
        tux.signal('RecordingStateChanged', true);
        said.push(button.accessible_name);
        tux.signal('ServiceStateChanged', false);
        said.push(button.accessible_name);
        t.report(said);
    """)
    assert said == ["TuxTalk, dictation off", "TuxTalk, dictation on",
                    "TuxTalk, recording", "TuxTalk, dictation off"]


def test_a_service_stopped_mid_recording_shows_off_not_recording():
    shown, _logs = run(STARTED + """
        t.status(tux, true);
        tux.signal('RecordingStateChanged', true);
        tux.signal('ServiceStateChanged', false);
        const icon = button.children[0];
        t.report([icon.icon_name, icon.style_class]);
    """)
    assert shown == ["microphone-sensitivity-muted-symbolic", "system-status-icon"]


def test_the_button_appears_only_while_tuxtalk_runs():
    shown, _logs = run("""
        const {button} = await t.enable();
        const shown = [button.visible];
        t.tuxtalkStarts();
        shown.push(button.visible);
        t.tuxtalkQuits();
        shown.push(button.visible);
        t.report(shown);
    """)
    assert shown == [False, True, False]


def test_a_status_update_never_turns_dictation_on_or_off():
    """From GNOME 47, setToggleState emits 'toggled' as a click does: without
    the guard, every status update started or stopped the service."""
    calls, _logs = run(STARTED + """
        t.status(tux, true);
        tux.signal('ServiceStateChanged', false);
        const fromStatus = tux.calls.map(c => c[0]).filter(m => m.endsWith('Service'));
        t.item(button.menu, 'Dictation on/off').toggle();
        t.report([fromStatus, tux.calls.at(-1)[0]]);
    """)
    assert calls == [[], "StartService"]


def test_every_menu_item_calls_a_method_its_interface_declares():
    """Each item asks for a method by a name built at run time: one the
    interface doesn't declare would throw on the click (GJS's proxy has no
    such call). That TuxTalk has each one: test_the_interface_is_tuxtalks."""
    asked, logs = run(STARTED + """
        for (const label of ['Fix a Word...', 'Transcribe a File...', 'Preferences...',
                'Voice Commands...', 'Your Stats...', 'Help...', 'About TuxTalk...',
                'Restart Service', 'Quit TuxTalk'])
            t.item(button.menu, label).activate();
        t.report(tux.calls.map(c => c[0]).slice(2));
    """)
    assert asked == ["FixWordInDictation", "TranscribeFile", "OpenPreferences",
                     "ShowVoiceCommands", "OpenPreferencesStats", "ShowHelp", "ShowAbout",
                     "RestartService", "Quit"]
    assert logs == []


def test_only_preferences_has_an_icon():
    """The gear, as in the tray; the other items are plain."""
    with_icons, _logs = run(STARTED + """
        t.report(button.menu.items.filter(i => 'icon' in i).map(i => [i.label.text, i.icon]));
    """)
    assert with_icons == [["Preferences...", "emblem-system-symbolic"]]


def test_a_click_before_tuxtalk_answers_does_nothing():
    """Before the proxy is ready a click must not throw."""
    done, _logs = run("""
        const {button} = await t.enable();
        for (const label of ['Fix a Word...', 'Quit TuxTalk', 'Check for Updates...'])
            t.item(button.menu, label).activate();
        t.item(button.menu, 'Dictation on/off').toggle();
        t.report(t.shell.notifications);
    """)
    assert done == []


def test_the_extension_never_starts_tuxtalk_or_waits_on_it():
    """The proxy is made with a callback, so GNOME Shell isn't blocked on
    D-Bus at every login and unlock, without starting TuxTalk, and without
    asking for properties TuxTalk doesn't have (one more round trip)."""
    asked, _logs = run("""
        await t.enable();
        const setup = t.shell.proxySetups[0];
        const {DO_NOT_AUTO_START, DO_NOT_LOAD_PROPERTIES} = t.Gio.DBusProxyFlags;
        t.report([typeof setup.callback, Boolean(setup.flags & DO_NOT_AUTO_START),
                  Boolean(setup.flags & DO_NOT_LOAD_PROPERTIES)]);
    """)
    assert asked == ["function", True, True]


def test_a_proxy_that_failed_is_made_again_when_tuxtalk_next_starts():
    """Otherwise the menu stayed dead until the next login."""
    shown, logs = run("""
        const {button} = await t.enable();
        t.shell.proxySetups[0].callback(null, t.dbusError('NoReply'));
        t.tuxtalkQuits();
        const tux = t.tuxtalkStarts();
        t.status(tux, true);
        t.report([t.shell.proxySetups.length, button.accessible_name]);
    """)
    assert shown == [2, "TuxTalk, dictation on"]
    assert logs == [["error", "TuxTalk: Failed to connect to D-Bus: GDBus.Error: NoReply"]]


def test_it_listens_to_every_signal_it_declares():
    """A declared signal nothing listens to is left over from a removed feature."""
    signals, _logs = run(STARTED + """
        const listened = [...tux.subscriptions.values()].map(([name]) => name);
        t.report([t.shell.proxySetups[0].signals.sort(), listened.sort()]);
    """)
    declared, listened = signals
    assert declared == listened


def test_turned_off_while_tuxtalk_starts_it_does_nothing_with_the_proxy():
    """The screen lock turns the extension off: a proxy finished afterwards
    must not subscribe the destroyed button to TuxTalk's signals."""
    subscribed, _logs = run("""
        const {extension} = await t.enable();
        const setup = t.shell.proxySetups[0];
        extension.disable();
        const tux = t.tuxtalk(setup.methods);
        setup.callback(tux, null);
        t.report([setup.cancellable.is_cancelled(), tux.subscriptions.size]);
    """)
    assert subscribed == [True, 0]


def test_no_reply_touches_the_extension_after_it_is_turned_off():
    """A GetStatus reply arriving after the screen lock grabbed F8 again,
    with nothing listening, and the others drew on the destroyed menu."""
    after, logs = run(STARTED + """
        t.openMenu(button);                    // asks for Recent Dictations and presets
        extension.disable();
        t.status(tux, true, {hotkeys: ['F8']});
        tux.answer('GetRecentDictations', ['hello']);
        tux.answer('GetPresets', [['light', 'Lightest', 'small']]);
        t.report([t.shell.grabs.size, button.accessible_name,
                  button.menu.items[1].menu.items.length,
                  button.menu.items[6].menu.items.length]);
    """)
    assert after == [0, "TuxTalk, dictation off", 0, 0]
    assert logs == []


def test_recent_dictations_lists_each_entry_once_however_often_the_menu_opens():
    """Reopened before the first reply came, the menu listed every entry twice."""
    shown, _logs = run(STARTED + """
        t.openMenu(button);
        t.openMenu(button);
        tux.answer('GetRecentDictations', ['first', 'second']);
        tux.answer('GetRecentDictations', ['first', 'second']);
        t.report(t.labels(button.menu.items[1].menu));
    """)
    assert shown == ["Click one to copy it, then press Ctrl+V", "first", "second",
                     "---", "Clear History"]


def test_a_late_reply_never_replaces_the_newer_list():
    """Opened, then opened again after a dictation: the first reply, arriving
    last, put back the list without the newest dictation."""
    shown, _logs = run(STARTED + """
        t.openMenu(button);
        t.openMenu(button);
        tux.waiting.reverse();                 // the second reply comes first
        tux.answer('GetRecentDictations', ['newest', 'older']);
        tux.answer('GetRecentDictations', ['older']);
        t.report(t.labels(button.menu.items[1].menu).slice(1, -2));
    """)
    assert shown == ["newest", "older"]


def test_the_paste_key_reply_after_the_extension_is_turned_off_does_nothing():
    """The screen lock between the two replies: the paste key's line went
    into the destroyed menu ("already disposed" in the journal)."""
    shown, logs = run(STARTED + """
        t.openMenu(button);
        tux.answer('GetRecentDictations', ['see you at noon']);
        const history = button.menu.items[1].menu;
        const before = t.labels(history);
        extension.disable();
        tux.answer('GetPasteLastHotkey', 'Ctrl+Alt+P');
        t.report([before, t.labels(history)]);
    """)
    before, after = shown
    assert after == before
    assert logs == []


def test_a_late_paste_key_reply_never_lands_in_the_newer_list():
    """The menu opened again before the paste key's reply came: the old
    reply added its line to the new list, which then had it twice."""
    shown, _logs = run(STARTED + """
        t.openMenu(button);
        tux.answer('GetRecentDictations', ['older']);
        t.openMenu(button);
        tux.answer('GetRecentDictations', ['newest', 'older']);
        tux.answer('GetPasteLastHotkey', 'Ctrl+Alt+P');     // the first menu's
        tux.answer('GetPasteLastHotkey', 'Ctrl+Alt+P');
        t.report(t.labels(button.menu.items[1].menu));
    """)
    assert shown.count("Ctrl+Alt+P types the newest one again") == 1


def test_a_recent_dictation_is_copied_and_the_paste_key_named():
    shown, _logs = run(STARTED + """
        t.openMenu(button);
        tux.answer('GetRecentDictations', ['see you at noon']);
        tux.answer('GetPasteLastHotkey', 'Ctrl+Alt+P');
        const history = button.menu.items[1].menu;
        t.item(history, 'see you at noon').activate();
        t.item(history, 'Clear History').activate();
        t.report([t.labels(history)[1], t.shell.clipboard, tux.calls.at(-1)[0]]);
    """)
    assert shown == ["Ctrl+Alt+P types the newest one again", "see you at noon",
                     "ClearRecentDictations"]


def test_performance_is_built_from_the_trays_presets_with_custom_last():
    """The tray owns the presets, so the two menus can't drift; Custom opens
    Preferences, where a custom setup is made, and the dot follows what took
    effect."""
    shown, _logs = run(STARTED + """
        t.status(tux, true, {preset: 'light'});
        t.openMenu(button);
        tux.answer('GetPresets', [['recommended', 'Recommended', 'Parakeet'],
                                  ['light', 'Lightest', 'small']]);
        const presets = button.menu.items[6].menu;
        t.item(presets, 'Lightest (small)').activate();
        t.item(presets, 'Custom (opens Preferences)').activate();
        t.report([t.labels(presets), presets.items.map(i => i.ornament),
                  tux.calls.slice(-2)]);
    """)
    labels, dots, calls = shown
    assert labels == ["Recommended (Parakeet)", "Lightest (small)", "---",
                      "Custom (opens Preferences)"]
    assert dots == [0, 1, 0, 0]
    assert calls == [["ApplyPerformancePreset", ["light"]], ["OpenPreferences", []]]


# --- the hotkeys held back from apps ------------------------------------------------

def test_the_hotkeys_are_held_back_only_while_dictation_is_on():
    """With dictation off, F8 must be an ordinary key again."""
    held, _logs = run(STARTED + """
        const held = [];
        const now = () => held.push([...t.shell.grabs.values()]);
        t.status(tux, true, {hotkeys: ['F8', 'F9']});
        now();
        tux.signal('HotkeysChanged', ['F6']);
        now();
        tux.signal('ServiceStateChanged', false);
        now();
        tux.signal('ServiceStateChanged', true);
        now();
        t.tuxtalkQuits();
        now();
        t.report(held);
    """)
    assert held == [["F8", "F9"], ["F6"], [], ["F6"], []]


def test_a_held_hotkey_works_in_the_overview_too():
    modes, _logs = run(STARTED + """
        t.status(tux, true, {hotkeys: ['F8']});
        t.report([...t.shell.keybindings.values()]);
    """)
    assert modes == [0xffff]


def test_a_hotkey_another_shortcut_has_is_left_to_it_and_logged():
    held, logs = run(STARTED + """
        t.shell.taken.add('F8');
        t.status(tux, true, {hotkeys: ['F8', 'F9']});
        t.report([...t.shell.grabs.values()]);
    """)
    assert held == ["F9"]
    assert logs == [["warn", "TuxTalk: could not hold F8 back from apps (already in use?)"]]


def test_turning_the_extension_off_gives_the_hotkeys_back():
    held, _logs = run(STARTED + """
        t.status(tux, true, {hotkeys: ['F8', 'F9']});
        extension.disable();
        t.report([t.shell.grabs.size, [...t.shell.keybindings.values()]]);
    """)
    assert held == [0, [0, 0]]


# --- the focused window, for the paste keys and the floating messages ------------------------

def test_the_focused_window_is_reported_in_one_call():
    """Sent apart, a look between the calls paired one window's class with
    the next one's id."""
    calls, _logs = run(STARTED + """
        t.focus(new t.Window('org.kde.kwrite', 7));
        t.report(tux.calls.filter(c => c[0].startsWith('SetFocused')));
    """)
    assert calls[-1] == ["SetFocusedWindow", ["org.kde.kwrite", "7", "10,20,800,600||0,0,1920,1080"]]


# Focused before TuxTalk starts, so the window's report is the only one waiting.
KWRITE_FOCUSED = """
const {extension, button} = await t.enable();
t.focus(new t.Window('org.kde.kwrite', 7));
const tux = t.tuxtalkStarts();
"""


def test_a_tuxtalk_without_the_one_call_gets_the_class_and_frame_the_old_way():
    """The extension from the latest release can meet an older TuxTalk."""
    calls, _logs = run(KWRITE_FOCUSED + """
        tux.fail('SetFocusedWindow', t.dbusError(t.Gio.DBusError.UNKNOWN_METHOD));
        t.report(tux.calls.slice(-2));
    """)
    assert calls == [["SetFocusedWindowClass", ["org.kde.kwrite"]],
                     ["SetFocusedWindowGeometry", ["10,20,800,600||0,0,1920,1080"]]]


def test_a_busy_tuxtalk_never_gets_the_old_calls_late():
    """Sent after its time-out, they undid a newer window's report."""
    calls, _logs = run(KWRITE_FOCUSED + """
        tux.fail('SetFocusedWindow', t.dbusError('Timeout'));
        t.report(tux.calls.map(c => c[0]));
    """)
    assert "SetFocusedWindowClass" not in calls


def test_the_old_calls_never_report_a_window_no_longer_focused():
    calls, _logs = run(KWRITE_FOCUSED + """
        t.focus(new t.Window('firefox', 8));
        tux.fail('SetFocusedWindow', t.dbusError(t.Gio.DBusError.UNKNOWN_METHOD));
        t.report(tux.calls.map(c => c[0]));
    """)
    assert "SetFocusedWindowClass" not in calls


def test_a_window_without_a_frame_is_still_reported():
    """A monitor being plugged in or out: the frame threw, and neither the
    class nor the id went out, so paste kept the last window's class."""
    calls, logs = run(STARTED + """
        const window = new t.Window('firefox', 8);
        window.get_frame_rect = () => { throw new Error('no monitor'); };
        t.focus(window);
        t.report(tux.calls.at(-1));
    """)
    assert calls == ["SetFocusedWindow", ["firefox", "8", ""]]
    assert logs == [["error", "TuxTalk: no frame for the focused window: Error: no monitor"]]


def test_a_window_moved_is_reported_again_at_most_every_150_ms():
    calls, _logs = run(STARTED + """
        const window = new t.Window('firefox', 8);
        t.focus(window);
        window.emit('position-changed');
        window.emit('size-changed');
        const waiting = t.shell.timeouts.length;
        t.shell.timeouts.shift().callback();
        t.report([waiting, tux.calls.at(-1)[0]]);
    """)
    assert calls == [1, "SetFocusedWindowGeometry"]


def test_turned_off_it_clears_the_focus_and_stops_following_windows():
    """Disabled or uninstalled, the extension stops reporting; the window it
    reported last must not stay TuxTalk's paste target."""
    after, logs = run(STARTED + """
        const window = new t.Window('firefox', 8);
        t.focus(window);
        window.emit('position-changed');
        extension.disable();
        window.emit('size-changed');
        t.report([tux.calls.slice(-2), window._handlers.size, t.shell.timeouts.length,
                  tux.subscriptions.size]);
    """)
    cleared, handlers, timeouts, subscriptions = after
    assert cleared == [["SetFocusedWindowClass", [""]], ["SetFocusedWindowGeometry", [""]]]
    assert (handlers, timeouts, subscriptions) == (0, 0, 0)
    assert logs == []


def test_teardown_errors_are_logged_not_dropped():
    """Each failure while turning off is told in the journal, and the rest of
    the teardown still runs."""
    _value, logs = run(STARTED + """
        const window = new t.Window('firefox', 8);
        t.focus(window);
        window.disconnect = () => { throw new Error('already freed'); };
        tux.disconnectSignal = () => { throw new Error('closing'); };
        tux.SetFocusedWindowClassRemote = () => { throw new Error('quitting'); };
        extension.disable();
        t.report(null);
    """)
    assert logs == [
        ["warn", "TuxTalk: could not stop following a window: Error: already freed"],
        ["warn", "TuxTalk: could not stop following a window: Error: already freed"],
        ["warn", "TuxTalk: could not clear the focused window: Error: quitting"],
    ] + [["warn", "TuxTalk: could not disconnect a D-Bus signal: Error: closing"]] * 5


# --- Check for Updates ----------------------------------------------------------------

def test_check_for_updates_says_so_and_comes_back_if_no_answer_comes():
    shown, _logs = run(STARTED + """
        const updates = t.item(button.menu, 'Check for Updates...');
        updates.activate();
        const during = [updates.label.text, updates.sensitive];
        t.shell.timeouts.shift().callback();
        t.report([during, [updates.label.text, updates.sensitive], t.shell.notifications]);
    """)
    assert shown == [["Checking...", False], ["Check for Updates...", True],
                     [["Checking for TuxTalk updates", "The result shows in a moment."]]]


def test_a_failed_update_check_says_what_failed():
    """The title says what happened, never just "TuxTalk" (gnome.mjs refuses one)."""
    shown, _logs = run(STARTED + """
        tux.signal('UpdateCheckComplete', false, '', '', false, -1, -1, false, '', 'No internet');
        t.report(t.shell.notifications);
    """)
    assert shown == [["TuxTalk update check failed", "No internet"]]


def test_an_extension_update_opens_the_updates_tab():
    shown, _logs = run(STARTED + """
        t.item(button.menu, 'Check for Updates...').activate();
        tux.signal('UpdateCheckComplete', true, '0.19.0', '0.19.0', false, 29, 30, true, '', '');
        t.report([t.shell.timeouts.length, tux.calls.at(-1)[0], t.shell.notifications.at(-1)]);
    """)
    assert shown == [0, "OpenPreferencesUpdates",
                     ["TuxTalk extension update available",
                      "Opening Updates & Backup to install it."]]


# --- the stand-ins hold the extension to what GNOME Shell and TuxTalk accept ---------

def test_an_import_of_a_module_without_a_stand_in_fails(tmp_path):
    """A wrong path, and GNOME Shell doesn't load the extension at all: it
    fails here too, as any module the stand-ins don't have does."""
    from gnome_extension_js import STAND_INS
    source = (STAND_INS.parent.parent / "gnome-extension" / "talktype@ronb1964.github.io"
              / "extension.js").read_text()
    broken = tmp_path / "extension.js"
    broken.write_text(source.replace("ui/panelMenu.js", "ui/panelMenus.js"))
    loaded, _logs = run(f"""
        try {{
            await t.enable({json.dumps(broken.as_uri())});
            t.report('loaded');
        }} catch (e) {{
            t.report(String(e));
        }}
    """)
    assert "No such module" in loaded and "panelMenus.js" in loaded


def test_a_call_with_the_wrong_arguments_is_refused():
    """GJS packs the arguments by the interface's types: one too few, or a
    number for a string, throws in GNOME Shell, so it throws here too."""
    refused, _logs = run(STARTED + """
        const refused = [];
        for (const call of [() => tux.SetFocusedWindowRemote('firefox', () => {}),
                            () => tux.ApplyPerformancePresetRemote(3)]) {
            try {
                call();
            } catch (e) {
                refused.push(e.message);
            }
        }
        t.report(refused);
    """)
    assert refused == ["SetFocusedWindowRemote takes 3 argument(s) (sss), got 1",
                       "ApplyPerformancePresetRemote: argument 1 must be a s, got 3"]


def _interface(xml):
    """{name: signature} for the methods (in-args, then '->' and out-args)
    and signals of a D-Bus interface's XML."""
    import xml.etree.ElementTree as ET
    found = {}
    for element in ET.fromstring(xml).iter():
        args = element.findall("arg")
        if element.tag == "method":
            # A method's arg without a direction is an in-arg, as D-Bus says.
            found[element.get("name")] = (
                "".join(a.get("type") for a in args if a.get("direction", "in") == "in")
                + "->"
                + "".join(a.get("type") for a in args if a.get("direction") == "out"))
        elif element.tag == "signal":
            found[element.get("name")] = "".join(a.get("type") for a in args)
    return found


def test_the_interface_is_tuxtalks():
    """Every method and signal the extension declares, TuxTalk has, with the
    same arguments: one TuxTalk dropped (task 8 dropped four) would fail on
    GNOME and pass every test that runs the extension."""
    import re
    from gnome_extension_js import STAND_INS
    from tuxtalk.dbus_service import TuxTalkDBusService
    source = (STAND_INS.parent.parent / "gnome-extension" / "talktype@ronb1964.github.io"
              / "extension.js").read_text()
    declared = _interface(re.search(r"const TuxTalkIface = `(.*?)`;", source, re.S)[1])
    tuxtalk = {}
    for name in dir(TuxTalkDBusService):
        member = getattr(TuxTalkDBusService, name)
        if getattr(member, "_dbus_is_method", False):
            tuxtalk[name] = ((member._dbus_in_signature or "") + "->"
                             + (member._dbus_out_signature or ""))
        elif getattr(member, "_dbus_is_signal", False):
            tuxtalk[name] = member._dbus_signature or ""
    assert {name: tuxtalk.get(name) for name in declared} == declared


INTERFACE_IN_ANY_ORDER = """<node><interface name="io.example.Test">
  <method name="Mixed">
    <arg direction="in" type="s" name="first"/>
    <arg type="u" name="no_direction"/>
    <arg name="answer" type="b" direction="out"/>
  </method>
</interface></node>"""


def test_args_are_read_in_any_attribute_order_and_default_to_in():
    """D-Bus allows the attributes in any order, and a method's arg without
    a direction is an in-arg: both readers of the interface agree."""
    assert _interface(INTERFACE_IN_ANY_ORDER) == {"Mixed": "su->b"}
    types, _logs = run(f"""
        const Wrapper = t.Gio.DBusProxy.makeProxyWrapper({json.dumps(INTERFACE_IN_ANY_ORDER)});
        new Wrapper(null, '', '', () => {{}}, null, 0);
        t.report(t.shell.proxySetups.at(-1).methods.Mixed);
    """)
    assert types == ["s", "u"]
