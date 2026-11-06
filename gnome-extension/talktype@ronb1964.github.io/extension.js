/* extension.js
 *
 * TuxTalk GNOME Shell Extension
 * Provides native GNOME integration for TuxTalk speech recognition
 */

import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Gio from 'gi://Gio';
import Pango from 'gi://Pango';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

// What the extension calls of TuxTalk's D-Bus interface (the tray has more).
const TuxTalkIface = `
<node>
  <interface name="io.github.ronb1964.TalkType">
    <!-- Status -->
    <method name="GetStatus">
      <arg type="a{sv}" direction="out" name="status"/>
    </method>
    <method name="GetPresets">
      <arg type="a(sss)" direction="out" name="presets"/>
    </method>

    <!-- Actions -->
    <method name="StartService"/>
    <method name="StopService"/>
    <method name="RestartService"/>
    <method name="SetFocusedWindow">
      <arg type="s" direction="in" name="wm_class"/>
      <arg type="s" direction="in" name="window_id"/>
      <arg type="s" direction="in" name="geometry"/>
    </method>
    <method name="SetFocusedWindowClass">
      <arg type="s" direction="in" name="wm_class"/>
    </method>
    <method name="SetFocusedWindowGeometry">
      <arg type="s" direction="in" name="geometry"/>
    </method>
    <method name="ApplyPerformancePreset">
      <arg type="s" direction="in" name="preset"/>
    </method>
    <method name="GetRecentDictations">
      <arg type="as" direction="out" name="entries"/>
    </method>
    <method name="GetPasteLastHotkey">
      <arg type="s" direction="out" name="hotkey"/>
    </method>
    <method name="ClearRecentDictations"/>
    <method name="FixWordInDictation"/>
    <method name="TranscribeFile"/>
    <method name="OpenPreferences"/>
    <method name="OpenPreferencesUpdates"/>
    <method name="OpenPreferencesStats"/>
    <method name="ShowHelp"/>
    <method name="ShowVoiceCommands"/>
    <method name="ShowAbout"/>
    <method name="CheckForUpdates"/>
    <method name="Quit"/>

    <!-- Signals -->
    <signal name="RecordingStateChanged">
      <arg type="b" name="is_recording"/>
    </signal>
    <signal name="ServiceStateChanged">
      <arg type="b" name="is_running"/>
    </signal>
    <signal name="HotkeysChanged">
      <arg type="as" name="accelerators"/>
    </signal>
    <signal name="ModelChanged">
      <arg type="s" name="model_name"/>
    </signal>
    <signal name="UpdateCheckComplete">
      <arg type="b" name="success"/>
      <arg type="s" name="current_version"/>
      <arg type="s" name="latest_version"/>
      <arg type="b" name="update_available"/>
      <arg type="i" name="extension_current"/>
      <arg type="i" name="extension_latest"/>
      <arg type="b" name="extension_update"/>
      <arg type="s" name="release_url"/>
      <arg type="s" name="error"/>
    </signal>
  </interface>
</node>`;

const TuxTalkProxy = Gio.DBusProxy.makeProxyWrapper(TuxTalkIface);

// What TuxTalk's dictation service asks of the extension (app_focus.py):
// "open Firefox" with Firefox open brings its window forward. Served on
// GNOME Shell's own bus connection, so TuxTalk reaches it at org.gnome.Shell,
// with or without its tray running.
const AppFocusIface = `
<node>
  <interface name="io.github.ronb1964.TuxTalk.Extension">
    <method name="ActivateApp">
      <arg type="s" direction="in" name="app_id"/>
      <arg type="b" direction="out" name="had_window"/>
    </method>
    <method name="WindowAction">
      <arg type="s" direction="in" name="action"/>
      <arg type="s" direction="in" name="app_id"/>
      <arg type="b" direction="in" name="every"/>
      <arg type="b" direction="out" name="acted"/>
    </method>
    <method name="DesktopCommand">
      <arg type="s" direction="in" name="action"/>
      <arg type="i" direction="in" name="number"/>
      <arg type="s" direction="out" name="answer"/>
    </method>
  </interface>
</node>`;
const APP_FOCUS_PATH = '/io/github/ronb1964/TuxTalk/Extension';

// What each WindowAction does to one window.
const WINDOW_ACTIONS = {
    close: w => w.delete(global.get_current_time()),
    // Nothing done: only whether the app has a window (a window command said
    // with a word of the app's name acts only on an open app).
    find: () => {},
    minimize: w => w.minimize(),
    // GNOME 49 dropped the flags argument; older Shells need it.
    maximize: w => (Meta.MaximizeFlags ? w.maximize(Meta.MaximizeFlags.BOTH) : w.maximize()),
    unmaximize: w => (Meta.MaximizeFlags ? w.unmaximize(Meta.MaximizeFlags.BOTH) : w.unmaximize()),
    // A named app's window only: brings back a minimized or maximized one.
    restore: w => {
        if (w.minimized)
            w.unminimize();
        WINDOW_ACTIONS.unmaximize(w);
        Main.activateWindow(w);
    },
};

// Whether the focused window *w* is one "close window" may act on: an app's
// window or its dialog, never the desktop's icons (DING's desktop window) or
// a panel. By its type, not skip_taskbar: a dialog or a Preferences window
// with a parent is left out of the taskbar, but is the window the user is in.
function isAppWindow(w) {
    return [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG, Meta.WindowType.MODAL_DIALOG]
        .includes(w.get_window_type());
}

// Where "snap <place>" puts a window: which part of the work area's width and
// then of its height (the first half, the second half, or all of it).
const SNAP_PLACES = {
    'snap left': ['first', 'all'],
    'snap right': ['second', 'all'],
    'snap top': ['all', 'first'],
    'snap bottom': ['all', 'second'],
    'snap top left': ['first', 'first'],
    'snap top right': ['second', 'first'],
    'snap bottom left': ['first', 'second'],
    'snap bottom right': ['second', 'second'],
};

// The part *part* of the span starting at *start* that is *size* long; the
// second half takes the odd pixel so two halves leave no gap.
function spanPart(start, size, part) {
    const half = Math.floor(size / 2);
    if (part === 'first')
        return [start, half];
    return part === 'second' ? [start + half, size - half] : [start, size];
}

// Put the front window *w* in the part of its monitor's work area (the screen
// without the top bar) that *place* names. Unmaximized first: a maximized
// window ignores a new size.
function snapWindow(w, place) {
    WINDOW_ACTIONS.unmaximize(w);
    const area = w.get_work_area_current_monitor();
    const [x, width] = spanPart(area.x, area.width, place[0]);
    const [y, height] = spanPart(area.y, area.height, place[1]);
    w.move_resize_frame(true, x, y, width, height);
    return 'done';
}

function centerWindow(w) {
    const area = w.get_work_area_current_monitor();
    const frame = w.get_frame_rect();
    w.move_frame(true,
        area.x + Math.floor((area.width - frame.width) / 2),
        area.y + Math.floor((area.height - frame.height) / 2));
    return 'done';
}

// The workspace one step RIGHT or LEFT of the current one, or what to answer
// when there is none: only one workspace, or already at that end.
function neighbourWorkspace(direction) {
    const manager = global.workspace_manager;
    if (manager.n_workspaces === 1)
        return 'one_desktop';
    const current = manager.get_active_workspace();
    const neighbour = current.get_neighbor(direction);
    return neighbour.index() === current.index() ? 'edge' : neighbour;
}

// The workspace TuxTalk's number n (1 is the first) names, or 'no_desktop'.
function numberedWorkspace(n) {
    const manager = global.workspace_manager;
    return n >= 1 && n <= manager.n_workspaces ? manager.get_workspace_by_index(n - 1) : 'no_desktop';
}

// Switch to *target* (a workspace, or the answer neighbourWorkspace gave).
function switchTo(target) {
    if (typeof target === 'string')
        return target;
    target.activate(global.get_current_time());
    return 'done';
}

// Send the front window *w* to *target* (as switchTo).
function sendTo(w, target) {
    if (typeof target === 'string')
        return target;
    w.change_workspace_by_index(target.index(), false);
    return 'done';
}

// The windows "show desktop" minimized on each workspace, to bring back with
// the next "show desktop" there, and the one of them that had the focus.
// GNOME has no show-desktop action of its own, so it is done by minimizing
// the normal windows and remembering them: windows the person minimized
// earlier must stay minimized when they come back.
const hiddenByShowDesktop = new Map();

// Whether GNOME Shell still manages *w*: a window closed while the desktop was
// shown keeps its object, which must not be touched again.
function stillOpen(w) {
    return w.get_compositor_private() !== null;
}

// Bring *w* back. One that refuses is left, so the others still come back.
function unminimize(w) {
    try {
        w.unminimize();
    } catch (e) {
        console.warn(`TuxTalk: a window didn't come back after "show desktop": ${e}`);
    }
}

// Unminimized, the windows come back with none focused, and what the person
// types next goes nowhere: the one that had the focus gets it back, as on KDE
// and Cinnamon.
function showDesktop() {
    const workspace = global.workspace_manager.get_active_workspace();
    const {windows = [], focused = null} = hiddenByShowDesktop.get(workspace) ?? {};
    const hidden = windows.filter(w => stillOpen(w) && w.minimized);
    hiddenByShowDesktop.delete(workspace);
    if (hidden.length > 0) {
        hidden.forEach(unminimize);
        if (hidden.includes(focused) && !focused.minimized)
            Main.activateWindow(focused);
        return 'done';
    }
    const shown = workspace.list_windows()
        .filter(w => isAppWindow(w) && !w.minimized && !w.skip_taskbar);
    const front = global.display.focus_window;
    shown.forEach(w => w.minimize());
    hiddenByShowDesktop.set(workspace,
        {windows: shown, focused: shown.includes(front) ? front : null});
    return 'done';
}

// What each DesktopCommand does: (number) for the ones that need no window,
// and the front window first for the others.
const DESKTOP_COMMANDS = {
    'show desktop': showDesktop,
    overview: () => {
        Main.overview.toggle();
        return 'done';
    },
    desktop: n => switchTo(numberedWorkspace(n)),
    'next desktop': () => switchTo(neighbourWorkspace(Meta.MotionDirection.RIGHT)),
    'previous desktop': () => switchTo(neighbourWorkspace(Meta.MotionDirection.LEFT)),
};
const FRONT_WINDOW_COMMANDS = {
    ...Object.fromEntries(Object.entries(SNAP_PLACES).map(
        ([action, place]) => [action, w => snapWindow(w, place)])),
    center: centerWindow,
    'to desktop': (w, n) => sendTo(w, numberedWorkspace(n)),
    'to next desktop': w => sendTo(w, neighbourWorkspace(Meta.MotionDirection.RIGHT)),
    'to previous desktop': w => sendTo(w, neighbourWorkspace(Meta.MotionDirection.LEFT)),
    'to next screen': w => {
        const monitors = global.display.get_n_monitors();
        if (monitors < 2)
            return 'one_screen';
        w.move_to_monitor((w.get_monitor() + 1) % monitors);
        return 'done';
    },
};

const AppFocus = {
    // Bring forward the window of the menu entry *appId* ("firefox.desktop")
    // used last, as clicking it in the dash does. False when it has none
    // (not running, or only in the background): TuxTalk then starts it.
    ActivateApp(appId) {
        const app = Shell.AppSystem.get_default().lookup_app(appId);
        // Most recently used first; a splash screen or tooltip isn't one to show.
        const windows = app ? app.get_windows().filter(w => !w.skip_taskbar) : [];
        if (windows.length === 0)
            return false;
        Main.activateWindow(windows[0]);
        return true;
    },

    // Voice commands "close this", "minimize", "maximize": *action* is close,
    // minimize, maximize, unmaximize or restore (a named app only), or find,
    // which only looks: whether
    // the app has a window, touching none. *appId* "" means the focused
    // window; else the app's most recently used window, or with *every* all
    // of them. False when there is no such window or action.
    //
    // Like ActivateApp, any process on the session bus can call this: the
    // same trust as launching or raising an app. close is delete(), the same
    // as clicking the X, never kill(): an app with unsaved work asks "Save
    // changes?", and that question is the only protection. An app that
    // doesn't ask closes, and with *every* all of its windows do.
    WindowAction(action, appId, every) {
        // Own keys only: 'constructor' or '__proto__' must not count as actions.
        if (!Object.hasOwn(WINDOW_ACTIONS, action))
            return false;
        const act = WINDOW_ACTIONS[action];
        let windows;
        if (appId === '') {
            const focused = global.display.focus_window;
            windows = focused && isAppWindow(focused) ? [focused] : [];
        } else {
            const app = Shell.AppSystem.get_default().lookup_app(appId);
            windows = app ? app.get_windows().filter(w => !w.skip_taskbar) : [];
            if (!every)
                windows = windows.slice(0, 1);
        }
        windows.forEach(act);
        // Maximizing a named app's window brings it forward too: it may be
        // behind the window the person is in.
        if (action === 'maximize' && appId !== '')
            windows.forEach(w => Main.activateWindow(w));
        return windows.length > 0;
    },

    // Voice commands that snap or move the focused window, switch workspace
    // or show the desktop or overview (desktop_actions.py). *number* is the
    // workspace's for "desktop" and "to desktop", else 0. The answer is
    // 'done', or why nothing was done: no_window, no_desktop, one_desktop,
    // edge (no workspace further that way), one_screen, unknown.
    //
    // The same trust as WindowAction: any process on the session bus can ask.
    // Nothing here closes a window; show desktop only minimizes.
    DesktopCommand(action, number) {
        // Own keys only: 'constructor' or '__proto__' must not count as actions.
        if (Object.hasOwn(DESKTOP_COMMANDS, action))
            return DESKTOP_COMMANDS[action](number);
        if (!Object.hasOwn(FRONT_WINDOW_COMMANDS, action))
            return 'unknown';
        const focused = global.display.focus_window;
        if (!focused || !isAppWindow(focused))
            return 'no_window';
        return FRONT_WINDOW_COMMANDS[action](focused, number);
    },
};

// Panel indicator for TuxTalk
const TuxTalkIndicator = GObject.registerClass(
class TuxTalkIndicator extends PanelMenu.Button {
    _init() {
        super._init(0.0, 'TuxTalk');

        this._icon = new St.Icon({
            icon_name: 'audio-input-microphone-symbolic',
            style_class: 'system-status-icon',
        });
        this.add_child(this._icon);

        // State
        this._isRecording = false;
        this._isServiceRunning = false;
        // Id of the active preset, worked out by the tray (recommend.match_preset).
        this._currentPreset = 'custom';
        // What runs, in the tray's words (GetStatus 'engine').
        this._engineText = '';
        // Only watch_name sets this: true while TuxTalk owns its bus name.
        this._dbusAvailable = false;
        this._proxy = null;
        this._signalIds = [];
        this._cancellable = new Gio.Cancellable();
        // Set by destroy(): a D-Bus reply still on its way must not touch the
        // destroyed button or grab keys again (the screen lock disables us).
        this._destroyed = false;
        this._connecting = false;
        // Bumped on each Recent Dictations refresh, so a late reply from an
        // earlier one can't add its list a second time.
        this._historyRequest = 0;

        // TuxTalk reads its hotkeys below the desktop, so GNOME Shell also
        // hands F8 to the focused app and a terminal prints "~". While the
        // service runs we grab the hotkeys here, which keeps them from the
        // app; TuxTalk still sees them. The list comes from the service via
        // the tray (HotkeysChanged / GetStatus) in accelerator syntax.
        // accelerator -> action id from grab_accelerator.
        this._hotkeys = [];
        this._grabs = new Map();

        this._buildMenu();
        this._updateButton();

        // Hidden until watch_name sees TuxTalk on the bus.
        this.hide();

        // Connect to D-Bus
        this._connectDBus();

        // Monitor D-Bus service availability (this will show/hide based on service presence)
        this._watchDBusService();
    }

    _connectDBus() {
        if (this._proxy || this._connecting)
            return;
        this._connecting = true;
        const cancellable = this._cancellable;
        // Built asynchronously: a synchronous proxy blocks the whole shell on
        // D-Bus round trips at every login and unlock. No auto-start and no
        // property load: TuxTalk can't be bus-activated and has no properties.
        new TuxTalkProxy(
            Gio.DBus.session,
            'io.github.ronb1964.TalkType',
            '/io/github/ronb1964/TalkType',
            (proxy, error) => {
                if (cancellable.is_cancelled())
                    return;
                this._connecting = false;
                if (error || !proxy) {
                    // watch_name's appeared callback tries again.
                    console.error('TuxTalk: Failed to connect to D-Bus:', error);
                    return;
                }
                this._onProxyReady(proxy);
            },
            cancellable,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START | Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES
        );
    }

    _onProxyReady(proxy) {
        this._proxy = proxy;
        try {
            // Connect to signals. Store every subscription id so destroy()
            // can disconnect them — otherwise the callbacks keep firing on
            // the disposed panel button after the extension is disabled
            // (e.g. on screen lock), throwing "already disposed" errors.
            this._signalIds = [];
            this._signalIds.push(this._proxy.connectSignal('RecordingStateChanged', (proxy, sender, [isRecording]) => {
                this._isRecording = isRecording;
                this._updateButton();
            }));

            this._signalIds.push(this._proxy.connectSignal('ServiceStateChanged', (proxy, sender, [isRunning]) => {
                this._isServiceRunning = isRunning;
                // A stopped service isn't recording, and one that was killed
                // mid-recording never said so.
                if (!isRunning)
                    this._isRecording = false;
                this._updateButton();
                this._updateMenu();  // Update menu when service state changes
                this._syncHotkeyGrabs();
            }));

            this._signalIds.push(this._proxy.connectSignal('HotkeysChanged', (proxy, sender, [accelerators]) => {
                this._hotkeys = accelerators;
                this._syncHotkeyGrabs();
            }));

            this._signalIds.push(this._proxy.connectSignal('ModelChanged', () => {
                // Re-query: a preset also changes the device, the model line
                // names both, and the preset dot is matched on both.
                this._updateStatus();
            }));

            this._signalIds.push(this._proxy.connectSignal('UpdateCheckComplete', (proxy, sender, params) => {
                this._handleUpdateCheckResult(params);
            }));

            // Tell TuxTalk which window has the focus: its class picks the
            // paste keystroke (Ctrl+Shift+V in terminals), and its id and frame
            // keep a send, an undo or "edit that" in the window the words went
            // into, and place the floating messages over it.
            this._setupFocusTracking();
            // TuxTalk may already be on the bus: watch_name's appeared
            // callback can run before the proxy is ready.
            if (this._dbusAvailable)
                this._updateStatus();
        } catch (e) {
            console.error('TuxTalk: Failed to connect to D-Bus signals:', e);
        }
    }

    _setupFocusTracking() {
        if (!global.display) return;
        this._focusSignalId = global.display.connect(
            'notify::focus-window',
            () => this._pushFocusedWindow()
        );
        this._pushFocusedWindow();
    }

    _pushFocusedWindow() {
        if (!this._proxy || !this._dbusAvailable) {
            return;
        }
        try {
            const win = global.display && global.display.focus_window;
            const wmClass = (win && win.get_wm_class()) || '';
            const geometry = this._frameOf(win);
            // Its class, which window it is and where, in one call: two
            // windows of one app look the same by their class, and a look
            // between separate calls would pair one window's class with
            // another's id. A TuxTalk from before the call existed answers
            // that there's no such method, and gets the class and frame the
            // old way. Not on any other error (a busy or restarting TuxTalk):
            // sent late, the old calls would undo a newer report.
            this._proxy.SetFocusedWindowRemote(wmClass, win ? String(win.get_id()) : '', geometry,
                (result, error) => {
                    if (error && error.matches(Gio.DBusError, Gio.DBusError.UNKNOWN_METHOD)
                            && this._proxy && this._dbusAvailable
                            && global.display.focus_window === win) {
                        this._proxy.SetFocusedWindowClassRemote(wmClass, () => {});
                        this._proxy.SetFocusedWindowGeometryRemote(geometry, () => {});
                    }
                });
            this._watchFocusedWindow(win);
        } catch (e) {
            console.error('TuxTalk: focus push failed:', e);
        }
    }

    // Follow the focused window's moves and resizes, so TuxTalk's messages
    // can float over it (window_geometry.py in the app).
    _watchFocusedWindow(win) {
        if (this._geometryWindow && this._geometrySignalIds) {
            for (const id of this._geometrySignalIds) {
                try {
                    this._geometryWindow.disconnect(id);
                } catch (e) {
                    // A window already freed can refuse; the ids go anyway.
                    console.warn('TuxTalk: could not stop following a window:', e);
                }
            }
        }
        this._geometryWindow = win || null;
        this._geometrySignalIds = win ? [
            win.connect('position-changed', () => this._queueGeometryPush()),
            win.connect('size-changed', () => this._queueGeometryPush()),
        ] : [];
    }

    // A drag moves the window every frame; send its position at most every 150 ms.
    _queueGeometryPush() {
        if (this._geometryTimeoutId)
            return;
        this._geometryTimeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 150, () => {
            this._geometryTimeoutId = null;
            this._pushFocusedWindowGeometry();
            return GLib.SOURCE_REMOVE;
        });
    }

    // "x,y,w,h|output|ox,oy,ow,oh": the frame and its monitor. GNOME has no
    // connector name at hand, so the output is left empty.
    // Where *win* is, '' when that can't be told: a monitor being plugged
    // in or out has no geometry for a moment, and the class and id must
    // still go out with the frame.
    _frameOf(win) {
        if (!win)
            return '';
        try {
            const r = win.get_frame_rect();
            const m = global.display.get_monitor_geometry(win.get_monitor());
            return `${r.x},${r.y},${r.width},${r.height}||${m.x},${m.y},${m.width},${m.height}`;
        } catch (e) {
            console.error('TuxTalk: no frame for the focused window:', e);
            return '';
        }
    }

    _pushFocusedWindowGeometry() {
        if (!this._proxy || !this._dbusAvailable)
            return;
        try {
            const win = global.display && global.display.focus_window;
            this._proxy.SetFocusedWindowGeometryRemote(this._frameOf(win), () => {});
        } catch (e) {
            console.error('TuxTalk: geometry push failed:', e);
        }
    }

    _watchDBusService() {
        // Watch for D-Bus name owner changes (detects when TuxTalk quits)
        this._nameWatcherId = Gio.DBus.session.watch_name(
            'io.github.ronb1964.TalkType',
            Gio.BusNameWatcherFlags.NONE,
            () => {
                // Service appeared - show the indicator
                this._dbusAvailable = true;
                this.show();
                // A failed proxy setup is retried each time TuxTalk appears.
                if (!this._proxy)
                    this._connectDBus();
                this._updateStatus();
                // Re-push current focus so a freshly restarted Python service
                // doesn't sit at class=None until the user changes windows.
                this._pushFocusedWindow();
            },
            () => {
                // Service vanished (TuxTalk quit) - hide the indicator
                this._dbusAvailable = false;
                this._isServiceRunning = false;
                this._isRecording = false;
                this._syncHotkeyGrabs();  // TuxTalk quit: give the keys back
                this._updateButton();       // shown off, not as it was, when it comes back
                this._updateMenu();
                this.hide();
            }
        );
    }

    _buildMenu() {
        // The same items, in the same order, as the tray's menu (tray_menu.py).
        this._serviceItem = new PopupMenu.PopupSwitchMenuItem('Dictation on/off', false);
        this._updatingToggle = false;
        this._serviceItem.connect('toggled', item => {
            // Only a click: from GNOME 47, setToggleState emits 'toggled' too,
            // and a status update would start or stop dictation.
            if (this._updatingToggle)
                return;
            if (item.state)
                this._proxy?.StartServiceRemote();
            else
                this._proxy?.StopServiceRemote();
        });
        this.menu.addMenuItem(this._serviceItem);

        // Recent dictations. Refilled each time the panel menu opens, since
        // the list changes after every dictation. Mirrors the tray submenu.
        this._historySubMenu = new PopupMenu.PopupSubMenuMenuItem('Recent Dictations');
        this.menu.addMenuItem(this._historySubMenu);
        this.menu.connect('open-state-changed', (_menu, open) => {
            if (open) {
                this._refreshHistoryMenu();
                this._refreshPresets();
            }
        });
        // Fix a Word's window has its own dictation picker, on the newest.
        this._addRemoteItem('Fix a Word...', 'FixWordInDictation');
        this._addRemoteItem('Transcribe a File...', 'TranscribeFile');
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // What runs, one read-only line ("Parakeet on the graphics card (Vulkan)")
        this._engineItem = new PopupMenu.PopupMenuItem('Loading the speech model...', {reactive: false});
        this._engineItem.label.style = 'font-weight: bold;';
        this.menu.addMenuItem(this._engineItem);
        // Filled from the tray's GetPresets each time the menu opens.
        this._performanceSubMenu = new PopupMenu.PopupSubMenuMenuItem('Performance');
        this._presetItems = {};
        this.menu.addMenuItem(this._performanceSubMenu);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // A gear, easy to spot, as in front of the tray's (Adwaita's
        // preferences-system-symbolic is a wrench and screwdriver).
        this._addRemoteItem('Preferences...', 'OpenPreferences', 'emblem-system-symbolic');
        this._addRemoteItem('Voice Commands...', 'ShowVoiceCommands');
        this._addRemoteItem('Your Stats...', 'OpenPreferencesStats');
        this._addRemoteItem('Help...', 'ShowHelp');
        this._addRemoteItem('About TuxTalk...', 'ShowAbout');
        this._updatesItem = this.menu.addAction('Check for Updates...',
            () => this._checkForUpdates());
        // For when something's wrong, so it sits with Help.
        this._addRemoteItem('Restart Service', 'RestartService');
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._addRemoteItem('Quit TuxTalk', 'Quit');
    }

    // A menu item that asks TuxTalk for something: *method* of its D-Bus
    // interface. Before the proxy is ready a click does nothing. Without
    // *iconName* (undefined, never null) addAction makes a plain item.
    _addRemoteItem(label, method, iconName) {
        this.menu.addAction(label, () => this._proxy?.[`${method}Remote`](), iconName);
    }

    _checkForUpdates() {
        if (!this._proxy)
            return;     // nothing would answer
        Main.notify('Checking for TuxTalk updates', 'The result shows in a moment.');
        this._updatesItem.label.text = 'Checking...';
        this._updatesItem.setSensitive(false);

        // Call D-Bus method - results come via signal
        this._proxy?.CheckForUpdatesRemote();

        // Re-enable menu item after timeout (in case signal fails).
        // The id is kept so a result arriving first can cancel it, and so
        // destroy() can remove it — an uncancelled source fires against a
        // destroyed menu item after the extension is disabled or the screen
        // locks, which logs errors and is a standard ego.gnome.org rejection.
        this._clearUpdateTimeout();
        this._updateTimeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 30, () => {
            this._updateTimeoutId = null;
            this._restoreUpdatesItem();
            return GLib.SOURCE_REMOVE;
        });
    }

    _clearUpdateTimeout() {
        if (this._updateTimeoutId) {
            GLib.Source.remove(this._updateTimeoutId);
            this._updateTimeoutId = null;
        }
    }

    _restoreUpdatesItem() {
        if (!this._updatesItem) {
            return;
        }
        this._updatesItem.label.text = 'Check for Updates...';
        this._updatesItem.setSensitive(true);
    }

    _handleUpdateCheckResult(params) {
        // Result arrived — cancel the fallback timer before it can fire.
        this._clearUpdateTimeout();
        this._restoreUpdatesItem();

        const [success, , , updateAvailable, , , extUpdate, , error] = params;

        if (!success) {
            Main.notify('TuxTalk update check failed', error || 'Unknown error');
            return;
        }

        // The app shows its own result window for the app itself, which can't
        // offer the extension. With an app update in that window, the Updates
        // tab opening too would be a second window at once: the notice stays
        // in the message list for after the app's update.
        if (!extUpdate)
            return;
        if (updateAvailable) {
            Main.notify('TuxTalk extension update available',
                'After updating TuxTalk, install it from Preferences → Updates & Backup, '
                + 'then log out and back in.');
        } else {
            Main.notify('TuxTalk extension update available', 'Opening Updates & Backup to install it.');
            this._proxy?.OpenPreferencesUpdatesRemote();
        }
    }

    _updatePresetSelection(activePreset) {
        // Update checkmarks on preset menu items
        for (let [key, item] of Object.entries(this._presetItems)) {
            // Use ornament to show selection (like radio buttons)
            item.setOrnament(key === activePreset ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
        }
    }

    _refreshHistoryMenu() {
        const menu = this._historySubMenu.menu;
        const request = ++this._historyRequest;
        if (!this._proxy) {
            menu.removeAll();
            menu.addMenuItem(new PopupMenu.PopupMenuItem('TuxTalk Not Running', {reactive: false}));
            return;
        }
        this._proxy.GetRecentDictationsRemote((result, error) => {
            // Cleared here, not before the call: a menu reopened before the
            // first reply would otherwise get two full lists.
            if (this._destroyed || request !== this._historyRequest)
                return;
            menu.removeAll();
            // On error `result` is null, so it cannot be destructured.
            const entries = result ? result[0] : null;
            if (error || !entries || entries.length === 0) {
                menu.addMenuItem(new PopupMenu.PopupMenuItem(
                    error ? 'TuxTalk Not Running' : 'No dictations yet', {reactive: false}));
                return;
            }
            menu.addMenuItem(new PopupMenu.PopupMenuItem(
                'Click one to copy it, then press Ctrl+V', {reactive: false}));
            // The Paste Last Dictation hotkey, as in the tray's menu. Placed
            // second when the answer comes; an older tray without the method
            // just leaves it out.
            this._proxy?.GetPasteLastHotkeyRemote((res, err) => {
                const key = (!err && res) ? res[0] : '';
                if (key && !this._destroyed && request === this._historyRequest)
                    menu.addMenuItem(new PopupMenu.PopupMenuItem(
                        `${key} types the newest one again`, {reactive: false}), 1);
            });
            for (const text of entries) {
                // GNOME cannot nest a submenu inside this one (it unfolds in
                // place), so instead of the tray's hover view each entry shows
                // up to ~100 characters wrapped onto two lines.
                const flat = text.split(/\s+/).join(' ');
                const label = flat.length <= 100 ? flat : `${flat.slice(0, 99).trimEnd()}\u2026`;
                const item = new PopupMenu.PopupMenuItem(label);
                item.label.style = 'max-width: 24em;';
                item.label.clutter_text.line_wrap = true;
                item.label.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
                item.connect('activate', () => {
                    St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, text);
                });
                menu.addMenuItem(item);
            }
            menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            menu.addAction('Clear History', () => this._proxy?.ClearRecentDictationsRemote());
        });
    }
    _updateStatus() {
        if (!this._proxy)
            return;

        // Get current status
        this._proxy.GetStatusRemote((result, error) => {
            if (this._destroyed)
                return;
            if (error) {
                console.error('TuxTalk: Failed to get status:', error);
                return;
            }

            let [status] = result;
            this._isRecording = status.recording ? status.recording.deep_unpack() : false;
            this._isServiceRunning = status.service_running ? status.service_running.deep_unpack() : false;

            // The tray works out which preset is active (recommend.match_preset).
            this._currentPreset = status.preset ? status.preset.deep_unpack() : 'custom';
            this._engineText = status.engine ? status.engine.deep_unpack() : '';
            // Absent from a TuxTalk older than 0.14.1, which grabs nothing.
            this._hotkeys = status.hotkeys ? status.hotkeys.deep_unpack() : [];

            this._updateButton();
            this._updateMenu();
            this._syncHotkeyGrabs();
        });
    }

    _syncHotkeyGrabs() {
        // Grab exactly the service's hotkeys while it runs, and none otherwise:
        // with dictation off, F8 must be an ordinary key again.
        const wanted = (this._dbusAvailable && this._isServiceRunning) ? this._hotkeys : [];
        for (const [accel, action] of this._grabs) {
            if (!wanted.includes(accel)) {
                this._ungrabHotkey(action);
                this._grabs.delete(accel);
            }
        }
        for (const accel of wanted) {
            if (this._grabs.has(accel))
                continue;
            // The same two calls GNOME Shell's own GrabAccelerator makes.
            const action = global.display.grab_accelerator(accel, Meta.KeyBindingFlags.NONE);
            if (action === Meta.KeyBindingAction.NONE) {
                // Usually another shortcut already has it. Dictation still
                // works; the key just reaches the app too.
                console.warn(`TuxTalk: could not hold ${accel} back from apps (already in use?)`);
                continue;
            }
            // ALL: also while the overview or a system modal is up, or the key
            // would leak there.
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.ALL);
            this._grabs.set(accel, action);
        }
    }

    _ungrabHotkey(action) {
        Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.NONE);
        global.display.ungrab_accelerator(action);
    }

    _refreshPresets() {
        // The tray owns the presets (recommend.py): build the submenu from them
        // each time the menu opens, so the two menus can't drift. Don't mark a
        // choice on click: the tray can refuse it (a cancelled download), and
        // the dot follows what actually took effect (status.preset).
        const menu = this._performanceSubMenu.menu;
        if (!this._proxy)
            return;
        this._proxy.GetPresetsRemote((result, error) => {
            if (this._destroyed)
                return;
            menu.removeAll();
            this._presetItems = {};
            const presets = result ? result[0] : null;
            if (error || !presets || presets.length === 0) {
                menu.addMenuItem(new PopupMenu.PopupMenuItem(
                    'Update TuxTalk to use presets', {reactive: false}));
                return;
            }
            for (const [id, label, description] of presets) {
                this._presetItems[id] = menu.addAction(`${label} (${description})`,
                    () => this._proxy?.ApplyPerformancePresetRemote(id));
            }
            // Same as the GTK tray: Custom has the dot when the settings match
            // no preset (status.preset is 'custom'), and opens Preferences,
            // where a custom setup is made.
            menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            this._presetItems['custom'] = menu.addAction('Custom (opens Preferences)',
                () => this._proxy?.OpenPreferencesRemote());
            this._updatePresetSelection(this._currentPreset);
        });
    }

    // The button shows whether dictation is on, and says so to a screen
    // reader, which reads a panel button by its accessible name (set from
    // PanelMenu.Button's nameText) and can't see the icon.
    _updateButton() {
        // Dictation off: the crossed-out microphone, dimmed (St's CSS has no
        // opacity). Recording: red (stylesheet.css). With TuxTalk not running
        // the button is hidden.
        const off = !this._isServiceRunning;
        this._icon.icon_name = off ? 'microphone-sensitivity-muted-symbolic'
            : 'audio-input-microphone-symbolic';
        this._icon.opacity = off ? 128 : 255;
        this._icon.style_class = this._isRecording
            ? 'system-status-icon tuxtalk-recording' : 'system-status-icon';
        if (this._isRecording)
            this.accessible_name = 'TuxTalk, recording';
        else
            this.accessible_name = off ? 'TuxTalk, dictation off' : 'TuxTalk, dictation on';
    }

    _updateMenu() {
        this._updatingToggle = true;      // not a click (the 'toggled' handler)
        this._serviceItem.setToggleState(this._isServiceRunning);
        this._updatingToggle = false;

        // Disable menu items if D-Bus is unavailable
        this._serviceItem.setSensitive(this._dbusAvailable);
        this._performanceSubMenu.setSensitive(this._dbusAvailable);

        if (!this._dbusAvailable) {
            this._engineItem.label.text = 'TuxTalk Not Running';
        } else {
            this._engineItem.label.text = this._engineText || 'Loading the speech model...';
            this._updatePresetSelection(this._currentPreset);
        }
    }

    destroy() {
        this._destroyed = true;
        // Cancel the update-check fallback timer so it can't fire against a
        // destroyed menu item after the extension is disabled.
        this._clearUpdateTimeout();

        // Never leave a key grabbed behind a disabled extension (screen lock
        // disables it too); it would swallow F8 with nothing listening.
        for (const action of this._grabs.values())
            this._ungrabHotkey(action);
        this._grabs.clear();

        // A proxy still being set up must not call back into this button.
        this._cancellable.cancel();

        // Clean up D-Bus name watcher
        if (this._nameWatcherId) {
            Gio.DBus.session.unwatch_name(this._nameWatcherId);
            this._nameWatcherId = null;
        }

        // Disconnect focus-window listener
        if (this._focusSignalId && global.display) {
            global.display.disconnect(this._focusSignalId);
            this._focusSignalId = null;
        }
        this._watchFocusedWindow(null);
        if (this._geometryTimeoutId) {
            GLib.Source.remove(this._geometryTimeoutId);
            this._geometryTimeoutId = null;
        }

        if (this._proxy) {
            // Nothing reports focus once this is gone, so the last window
            // reported must not stay behind as the paste target. The two old
            // calls on purpose: every TuxTalk version has them.
            if (this._dbusAvailable) {
                try {
                    this._proxy.SetFocusedWindowClassRemote('', () => {});
                    this._proxy.SetFocusedWindowGeometryRemote('', () => {});
                } catch (e) {
                    // TuxTalk may be quitting too: nothing left to clear.
                    console.warn('TuxTalk: could not clear the focused window:', e);
                }
            }
            // Disconnect every D-Bus signal subscription before dropping the
            // proxy, so no callback fires on the destroyed button afterward.
            if (this._signalIds) {
                for (const id of this._signalIds) {
                    try {
                        this._proxy.disconnectSignal(id);
                    } catch (e) {
                        // The proxy may be tearing down already.
                        console.warn('TuxTalk: could not disconnect a D-Bus signal:', e);
                    }
                }
                this._signalIds = null;
            }
            this._proxy = null;
        }
        super.destroy();
    }
});

export default class TuxTalkExtension extends Extension {
    enable() {
        this._indicator = new TuxTalkIndicator();
        Main.panel.addToStatusArea('talktype', this._indicator);
        this._appFocus = Gio.DBusExportedObject.wrapJSObject(AppFocusIface, AppFocus);
        this._appFocus.export(Gio.DBus.session, APP_FOCUS_PATH);
    }

    disable() {
        hiddenByShowDesktop.clear();
        if (this._appFocus) {
            this._appFocus.unexport();
            this._appFocus = null;
        }
        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }
}
