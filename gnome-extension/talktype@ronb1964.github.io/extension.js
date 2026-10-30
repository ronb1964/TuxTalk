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
    <!-- Properties/Status -->
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

            // Track focused window class so TuxTalk can pick the right
            // paste keystroke (Ctrl+V for normal apps, Ctrl+Shift+V for terminals).
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
        // The same items, in the same order, as the tray's menu (tray.py).
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

        // The app shows its own result window for the app itself. Only an
        // extension update, which that window can't offer, needs the Updates tab.
        if (!updateAvailable && extUpdate) {
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
    }

    disable() {
        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }
}
