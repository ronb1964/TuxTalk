"""The Preferences window: its tabs, and saving and applying what they change.

It runs as its own process (python -m tuxtalk.prefs), started from the tray's
or the GNOME extension's menu, and restarts the dictation service when a
change needs it.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib
from . import ui_style
from .ui_style import show_message
from .logger import setup_logger
from .prefs_audio import AudioTab
from .prefs_advanced import AdvancedTab
from .prefs_general import GeneralTab
from .prefs_stats import StatsTab
from .prefs_updates import UpdatesTab
from .prefs_words import WordsTab
import os
import subprocess
import sys
import threading
import time
import atexit
from contextlib import contextmanager
import dbus.mainloop.glib
from dataclasses import asdict
from . import prefs_ipc
from . import config
from .config import (CONFIG_PATH, changed_keys, merge_changed_keys, write_text_atomic,
                     _load_toml_file, _toml_value)

logger = setup_logger(__name__)


# One Preferences window (single_instance.claim_window).
WINDOW_LOCK = "prefs-window.lock"
_window_lock = None


def _acquire_prefs_singleton(tab=None):
    """Give way to a Preferences window already open, bringing it to the
    front (on *tab*) and exiting, or claim the window for this one."""
    global _window_lock
    from . import single_instance
    go_on, lock = single_instance.claim_window(
        WINDOW_LOCK, lambda wait_s: _brought_open_window_forward(tab, wait_s), "Preferences")
    if not go_on:
        sys.exit(0)
    _window_lock = lock     # until the window closes (_let_go_of_the_window)
    try:
        prefs_ipc.claim_pidfile()
    except OSError as e:
        logger.warning(f"Could not write prefs pidfile: {e}")
    atexit.register(prefs_ipc.release_pidfile)


def _let_go_of_the_window():
    """Release the lock _acquire_prefs_singleton took, so the next
    Preferences window can open."""
    global _window_lock
    lock, _window_lock = _window_lock, None
    if lock is not None:
        lock.close()


def _other_window_pid():
    """The open Preferences window's pid, if it isn't this process."""
    pid = prefs_ipc.running_pid()
    return pid if pid != os.getpid() else None


def _brought_open_window_forward(tab, wait_s):
    """Whether an open Preferences window (from its pidfile, waited for up
    to *wait_s* while one starts) took the request to come to the front."""
    from .single_instance import wait_for_pid
    pid = wait_for_pid(_other_window_pid, wait_s)
    return pid is not None and prefs_ipc.bring_to_front(pid, tab)


# How long a restart waits for the new service to say it's ready.
_READY_WAIT_S = 180
# Set once the window is gone: then there's no one to tell how a restart
# went, and a process left waiting would look to an uninstall like an
# open Preferences window with changes in it.
_window_closed = threading.Event()

# What can need a download, and so is rolled back when it never finishes:
# the model, and the device (the processor or Vulkan copy of a model).
_DOWNLOAD_KEYS = ("model", "device")


def _restart_without_tray():
    """prefs.restart_service with no tray: end the service, start a new one
    the way the tray does (service_launcher), holding the tray's lock
    meanwhile as a tray would: an uninstall, or a tray starting, waits for
    it, and none of them can start or remove a service under the other.
    Preferences runs on native Wayland, and a service spawned with its
    environment would inherit it: gtk_window_move() would be ignored and the
    recording indicator always centred. Whether the new service got ready:
    True, False, or None when it was still loading at the wait's end
    (_came_up)."""
    from . import single_instance, tray_instance
    from .service_launcher import QUIT_WAIT_S, end_dictation_service, launch_dictation_service
    try:
        with single_instance.open_lock(tray_instance.TRAY_LOCK) as lock:
            if not single_instance.claim(lock, wait_s=QUIT_WAIT_S):
                # A failure, so the change stays owed: a tray starting keeps
                # the service already running, with the old settings.
                logger.warning("Dictation not restarted: TuxTalk is starting or uninstalling")
                return False
            if not end_dictation_service():
                logger.warning("Dictation not restarted: the old service wouldn't end")
                return False
            service = launch_dictation_service()
    except Exception as e:
        logger.warning(f"Failed to restart service: {e}")
        return False
    return _came_up(service)


def _came_up(service):
    """Whether *service*, just launched, got ready: it writes its pid to the
    ready file once its model is loaded. One that ends first didn't (a
    broken setting, a model that won't load). None for one still loading
    after _READY_WAIT_S, or when the window has closed (_window_closed):
    it may be asking to download its model, so it isn't called a failure,
    but the change stays owed."""
    from .service_launcher import read_ready_pid
    deadline = time.monotonic() + _READY_WAIT_S
    while time.monotonic() < deadline and not _window_closed.is_set():
        if read_ready_pid() == service.pid:
            return True
        try:
            service.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            continue
        logger.warning("The restarted dictation service ended before it was ready "
                       f"(exit {service.returncode})")
        return False
    logger.info("The restarted dictation service isn't ready yet; the change stays owed")
    return None


class PreferencesWindow:
    """The Preferences window: the tabs (prefs_*.py modules), and saving,
    applying and closing for all of them."""

    def __init__(self):
        """Read the settings, build the window and show it."""
        ui_style.prefer_dark_theme()

        self.window = Gtk.Window(title="TuxTalk Preferences")
        self.window.set_name("preferences_window")
        # Size is set in create_ui (_fit_to_screen), once the content can be measured.
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self._load_css()
        # A normal window, so dropdown popups have a top-level parent ("Tried
        # to map a popup with a non-top most parent" otherwise).
        self.window.set_type_hint(Gdk.WindowTypeHint.NORMAL)

        # The settings as they were at open: save_config() writes only the
        # keys changed in this window (merge_changed_keys), so a change the
        # tray made meanwhile survives.
        self.config = self.load_config()
        self._config_at_open = dict(self.config)
        self._restart_owed = set()      # keys saved whose restart failed
        self._restarts_asked = 0        # the latest restart's number (_apply_or_restart)
        # Set while busy() (Apply waiting for KDE's verdict on a new hotkey).
        self._busy = False
        self._save_error = ""
        self._scroll_controllers = []

        self.create_ui()
        self.window.connect("delete-event", self.on_window_close)
        self.window.connect("notify::is-active", self._on_window_active)
        self._show_window()

    def _show_window(self):
        """Show the window with the rows that depend on a setting already hidden."""
        self.window.show_all()
        # show_all() makes every child visible, so rows hidden while the tabs
        # were built come back. Hide them again here, before the main loop
        # draws anything, so they never flash on screen.
        self.general_tab.update_timeout_ui_state()
        self.ok_btn.grab_default()      # only once the window is shown
        # Not set_keep_above: it gets in the way of the dropdowns' popups.
        self.window.present()

    def _load_css(self):
        """TuxTalk's own look (ui_style.use_tuxtalk_style), and dropdowns that
        open below their button. List mode must be installed screen-wide: a
        provider on this window's context does not reach its child combos."""
        from .ui_style import apply_dropdown_list_style, use_tuxtalk_style
        apply_dropdown_list_style()
        use_tuxtalk_style(self.window)

    def load_config(self):
        """The settings as the service reads them (config.load_config): a
        damaged or empty file is recovered from its backup before anything
        is shown or saved, a hand-edited value ('5' for 5, "oops" for a
        number) shows what the service makes of it, and a missing key its
        default."""
        return asdict(config.load_config())

    def cuda_files_present(self):
        """Whether CUDA libraries (downloaded or system-wide) are present."""
        try:
            from . import cuda_helper
            return cuda_helper.has_cuda_libraries()
        except Exception as e:
            logger.warning(f"Could not check for CUDA's files: {e}")
            return False

    def nvidia_gpu(self):
        """The NVIDIA card's name (or True in a Flatpak), False if there is none.

        Asked once per window: nvidia-smi runs on the GTK thread and can take
        hundreds of milliseconds while the card wakes up.
        """
        if not hasattr(self, "_nvidia_found"):
            from .cuda_helper import detect_nvidia_gpu
            self._nvidia_found = detect_nvidia_gpu() or False
        return self._nvidia_found

    def has_nvidia(self):
        """Whether this computer has an NVIDIA card."""
        return bool(self.nvidia_gpu())

    def vulkan_offered(self):
        """Whether Vulkan is offered on this computer (whisper_vulkan.is_offered)."""
        try:
            from . import whisper_vulkan
            return whisper_vulkan.is_offered()
        except Exception as e:
            logger.warning(f"Could not check for Vulkan graphics: {e}")
            return False

    def save_config(self):
        """Save config to TOML file (merge-on-save).

        Only the keys the user changed in this window are overlaid onto a
        fresh read of the file, so settings changed by the tray while the
        window was open (model switch, performance preset, …) survive
        Apply/OK instead of being silently reverted.
        """
        # The service reads only `language` (empty string = auto-detect).
        if self.config.get("language_mode", "auto") == "auto":
            self.config["language"] = ""
        else:
            self.config["language"] = self.config.get("language") or "en"

        try:
            on_disk = {}
            if os.path.exists(CONFIG_PATH):
                try:
                    on_disk = _load_toml_file(CONFIG_PATH)
                except Exception as e:
                    # The file exists but cannot be read. Everything this
                    # window is showing is therefore defaults, not the user's
                    # settings — and writing them would both persist those
                    # defaults (blank hotkey included) and copy the damaged
                    # file over config.toml.bak, destroying the last good
                    # copy. Leave it alone; config.load_config()'s repair
                    # path is what knows how to recover this safely.
                    logger.warning(f"Refusing to save over an unreadable config: {e}")
                    self._save_error = f"Your settings file could not be read: {e}"
                    return False
            # Base: window-open snapshot (has every key incl. defaults),
            # updated with whatever is currently on disk.
            base = dict(self._config_at_open)
            base.update(on_disk)
            saved_before = dict(base)       # merge_changed_keys fills in base
            # Only a change made in this window may touch the autostart entry:
            # rewriting it on every save would undo the user turning TuxTalk
            # off in their desktop's own Autostart settings.
            autostart_changed = "launch_at_login" in changed_keys(self._config_at_open, self.config)
            # What this window last put in force, not what's on disk: after a
            # failed attempt the disk already holds the new value.
            autostart_before = self._config_at_open.get("launch_at_login")
            merged = merge_changed_keys(self._config_at_open, self.config, base)

            # Same serialiser and atomic write as config.save_config(): one
            # escaping rule, and a crash mid-write can't leave an empty file.
            lines = ["# TuxTalk config"]
            for key, value in merged.items():
                lines.append(f"{key} = {_toml_value(value)}")
            write_text_atomic(CONFIG_PATH, "\n".join(lines) + "\n")

            # Future saves in this window diff against what we just wrote;
            # what it replaced is what a failed download goes back to.
            self._saved_before = saved_before
            self.config = dict(merged)
            self._config_at_open = dict(merged)
            # Settings that live outside the config file only now that the
            # save has happened, so Cancel never has anything to undo.
            if autostart_changed:
                self._apply_launch_at_login(autostart_before)
            return True
        except Exception as e:
            logger.error(f"Could not save the config: {e}")
            self._save_error = str(e)
            return False

    def create_ui(self):
        """The title, the tabs, the version and the button row."""
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Each tab scrolls on its own (_scrollable_tab).
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)

        title = Gtk.Label()
        title.set_markup("<big><b>TuxTalk Preferences</b></big>")
        vbox.pack_start(title, False, False, 0)

        vbox.pack_start(self._build_tabs(), True, True, 0)

        from . import __version__
        version_label = Gtk.Label()
        version_label.set_markup(
            f'<span size="small" color="#888888">TuxTalk v{__version__}</span>')
        version_label.set_halign(Gtk.Align.END)
        version_label.set_margin_top(5)
        version_label.set_margin_end(5)
        vbox.pack_start(version_label, False, False, 0)

        main_vbox.pack_start(vbox, True, True, 0)
        main_vbox.pack_start(self._button_row(), False, False, 0)
        self.window.add(main_vbox)
        self._fit_to_screen(main_vbox)

    def _build_tabs(self):
        """The notebook with every tab, each in a scroll of its own."""
        notebook = self.notebook = Gtk.Notebook()
        self._tabs = []     # (tab, its page)

        def add(tab, label, page):
            notebook.append_page(page, Gtk.Label(label=label))
            self._tabs.append((tab, page))

        self.general_tab = GeneralTab(self)
        self.audio_tab = AudioTab(self)
        self.advanced_tab = AdvancedTab(self)
        for tab, label in ((self.general_tab, "General"), (self.audio_tab, "Audio"),
                           (self.advanced_tab, "Advanced")):
            add(tab, label, self._scrollable_tab(tab.build()))

        # Words tab (Your Words and the snippets). Its lists have scrolls of
        # their own, which the wheel must still reach, so the page scroll leaves
        # the wheel alone; the tab has no dropdowns or sliders to protect.
        # Its whole content sets the window's opening height (_fit_to_screen).
        self.words_tab = WordsTab(self)
        words_page = self._scrollable_tab(self.words_tab.build(), capture_wheel=False)
        words_page.set_propagate_natural_height(True)
        add(self.words_tab, "Words", words_page)

        self.stats_tab = StatsTab(self)
        self.updates_tab = UpdatesTab(self)
        for tab, label in ((self.stats_tab, "Stats"), (self.updates_tab, "Updates & Backup")):
            add(tab, label, self._scrollable_tab(tab.build()))
        # Connected once every tab is built: appending the first page shows it.
        notebook.connect("switch-page", self.on_tab_switched)
        return notebook

    def _button_row(self):
        """Help, Cancel, Apply and OK, below the tabs so they always show."""
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_margin_start(20)
        button_box.set_margin_end(20)
        button_box.set_margin_top(10)
        button_box.set_margin_bottom(15)

        help_btn = Gtk.Button(label="Help")
        help_btn.connect("clicked", self.on_help)
        help_btn.get_child().set_xalign(0.5)
        help_btn.set_tooltip_text("Open TuxTalk help documentation")
        button_box.pack_start(help_btn, False, False, 0)

        button_box.pack_start(Gtk.Label(), True, True, 0)     # the rest to the right

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self.on_cancel)

        apply_btn = Gtk.Button(label="Apply")
        apply_btn.connect("clicked", self.on_apply)

        self.ok_btn = Gtk.Button(label="OK")
        self.ok_btn.connect("clicked", self.on_ok)
        self.ok_btn.set_can_default(True)

        button_box.pack_start(cancel_btn, False, False, 0)
        button_box.pack_start(apply_btn, False, False, 0)
        button_box.pack_start(self.ok_btn, False, False, 0)
        return button_box

    def _fit_to_screen(self, content):
        """Open at the height the content asks for, capped to the monitor.

        Every tab scrolls, so the window's minimum height is small and it can
        always fit the screen with the button row showing. The opening height
        is the content's natural height: the whole Words tab, the tallest
        page that is not expected to scroll.
        """
        from .ui_style import fit_dialog_to_screen
        # Hidden widgets measure as zero; window.show_all() follows anyway.
        content.show_all()
        desired = content.get_preferred_height()[1]
        fit_dialog_to_screen(self.window, 500, desired if desired > 0 else 600)

    def _scrollable_tab(self, widget, capture_wheel=True):
        """Wrap a notebook tab's content in a vertical ScrolledWindow.

        A notebook inside one outer ScrolledWindow does not scroll (it clips its page
        rather than overflowing), so each tall tab needs its own scroll to stay usable
        on a screen shorter than the tab's content. capture_wheel=False leaves the
        wheel to scrollable children (see _install_capture_scroll).
        """
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # The scrollbar gets its own strip beside the page. Drawn over the
        # page (overlay scrolling) it would sit inside the 20 px right margin,
        # up against the dropdowns.
        sw.set_overlay_scrolling(False)
        sw.add(widget)
        if capture_wheel:
            self._install_capture_scroll(sw)
        return sw

    def _install_capture_scroll(self, scrolled):
        """Scroll the tab in the CAPTURE phase — intercepting the wheel *before* it
        reaches child widgets like ComboBox/Scale, which otherwise consume it (a combo
        changes selection, a slider changes value) and stop the page from scrolling.

        This works at the GTK widget level via EventControllerScroll, not via GdkWindow
        event masks, which Wayland compositors honour inconsistently (kwin does,
        mutter does not). Capture-phase interception is compositor-independent.
        """
        ctrl = Gtk.EventControllerScroll.new(
            scrolled, Gtk.EventControllerScrollFlags.VERTICAL)
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctrl.connect("scroll", self._on_capture_scroll, scrolled)
        # Kept, or the controller is garbage-collected and stops working.
        self._scroll_controllers.append(ctrl)

    def _on_capture_scroll(self, controller, dx, dy, scrolled):
        """Scroll the page by the wheel's movement, and keep it from the
        widget under the pointer."""
        vadj = scrolled.get_vadjustment()
        if vadj is not None:
            step = vadj.get_step_increment() or 40
            hi = vadj.get_upper() - vadj.get_page_size()
            vadj.set_value(max(vadj.get_lower(), min(vadj.get_value() + dy * step, hi)))
        return True  # consume so child widgets never act on the scroll

    def select_tab(self, name):
        """Show the tab whose label is *name* (case-insensitive). Looked up by
        label so adding a tab can't make --tab or bring_to_front open the wrong one."""
        # Older callers still ask for the Words tab by its former names.
        name = {"commands": "words", "snippets": "words"}.get(name.lower(), name)
        for i in range(self.notebook.get_n_pages()):
            label = self.notebook.get_tab_label_text(self.notebook.get_nth_page(i)) or ""
            # Any word of the label: "updates" (the tray's --tab) and
            # "backup" both find "Updates & Backup".
            if name.lower() in label.lower().replace("&", " ").split():
                self.notebook.set_current_page(i)
                return True
        return False

    def bring_to_front(self, tab=None):
        """Show this window in front of the others (another "open" happened)."""
        if tab:
            self.select_tab(tab)
        from .raise_window import raise_window
        raise_window(self.window)

    def update_config(self, key, value):
        """Change a setting in the window; Apply or OK saves it (and only
        then writes launch_at_login's autostart file, so Cancel has nothing
        to undo)."""
        # GTK combos emit 'changed' with get_active_id()=None while being
        # rebuilt (SpeechSection._fill_device_options). That None would be
        # saved as the text "None", a device or model nothing can use. Never
        # a real user action.
        if value is not None:
            self.config[key] = value

    def show_without_saving(self, key, value):
        """Show *value* for *key* while the saved setting stays as it is,
        unless the user picks something else: the baseline save_config()
        diffs against takes it too, so it isn't a change."""
        if self._config_at_open.get(key) == self.config.get(key):
            self._config_at_open[key] = value
        self.config[key] = value

    def save_now(self, settings):
        """Save *settings* ({key: value}) at once, in one write, not on
        Apply: settings that follow from something done already (a
        download), which Cancel can't undo. The window's other changes stay
        unsaved. True when written; if they can't be, they stay changes for
        Apply to save."""
        from .config import ConfigNotLoadedError, load_config, save_config
        self.config.update(settings)
        try:
            cfg = load_config()
            for key, value in settings.items():
                setattr(cfg, key, value)
            save_config(cfg)
        except (OSError, ConfigNotLoadedError) as e:
            logger.warning(f"Could not save {sorted(settings)}: {e}")
            return False
        self._config_at_open.update(settings)
        return True

    def save_before_restart(self):
        """Save the snippets and the settings, as something about to restart
        TuxTalk (an update, an uninstall) must; False, having said why, when
        either failed. What's saved stays owed: if that restart never comes
        (the uninstall is cancelled, the update's download fails), the next
        Apply still downloads what it needs and restarts for it."""
        if not self.words_tab.save():
            return False
        self._restart_owed |= self._changed_since_open()
        if not self.save_config():
            self._show_save_error()
            return False
        return True

    @contextmanager
    def busy(self):
        """The window can't be used, its close button included, while code
        inside Apply runs the main loop (KDE's wait): a second Apply, OK or
        Cancel would otherwise run inside the first."""
        self.window.set_sensitive(False)
        self._busy = True
        try:
            yield
        finally:
            self._busy = False
            self.window.set_sensitive(True)

    def _apply_launch_at_login(self, before):
        """Create or turn off the autostart entry, as onboarding does
        (tuxtalk.autostart). When that fails (a read-only autostart folder,
        the Flatpak's background request denied), it's said, and stays a
        change, *before* being what it was, so Apply tries again (OK stays
        open for that); the settings just saved are kept either way."""
        from . import autostart
        enable = bool(self.config.get("launch_at_login", False))
        try:
            done = autostart.set_autostart(enable)
        except Exception as e:
            logger.warning(f"Could not update the autostart entry: {e}")
            done = False
        if done:
            return
        self._config_at_open["launch_at_login"] = before
        show_message(self.window, Gtk.MessageType.WARNING,
                     "Start at login wasn't " + ("turned on" if enable else "turned off"),
                     "Your other settings are saved. Apply tries this again; "
                     "TuxTalk's log says what went wrong.")

    def _autostart_pending(self):
        """True after a save whose start-at-login change failed: it's still
        a change (_apply_launch_at_login)."""
        return "launch_at_login" in changed_keys(self._config_at_open, self.config)

    def get_selected_device_idx(self):
        """The sound device of the microphone picked in the Audio tab
        (microphones.find_input_device: with none picked, the PipeWire default
        rather than ALSA's "default", which records noise)."""
        from .microphones import find_input_device
        return find_input_device(self.config.get("mic", ""))

    def _stop_tests(self, closing=False, shown=None):
        """Stop the microphone tests of every tab but the one on page
        *shown* (Tab.stop); *closing*: the window closes, so free the mic."""
        for tab, page in self._tabs:
            if page is not shown:
                tab.stop(closing)

    def refresh_gpu_status(self):
        """Something was installed for the graphics card: the System check says so."""
        self.advanced_tab.system_check.check_gpu_status()

    def _on_window_active(self, window, _param):
        """Going off to another window is going off to dictate: a microphone
        test still holding a direct hw: mic, or about to open it, would make
        that dictation fail with only a beep, so the tests stop."""
        if not window.is_active():
            self._stop_tests()

    def _show_save_error(self):
        """Say the settings weren't saved, and why (disk full, read-only
        folder…): that's what the user needs to fix it."""
        show_message(self.window, Gtk.MessageType.ERROR, "Failed to save settings!",
                     self._save_error)

    def _say_restart_failed(self):
        """The settings are saved, but the dictation service didn't restart."""
        show_message(self.window, Gtk.MessageType.WARNING,
                     "Settings saved, but service restart failed!",
                     "You may need to manually restart the service.")

    def _release(self):
        """The window closes: stop the microphone tests and let go of the
        pidfile and the window's lock. Not at the process's exit: an Apply
        restart can keep it running after the window is gone, and a new
        window must open meanwhile."""
        self._stop_tests(closing=True)
        prefs_ipc.release_pidfile()
        _let_go_of_the_window()
        _window_closed.set()

    def on_window_close(self, widget, event):
        """The title bar's close: asks first about changes not applied."""
        if self._busy:
            return True     # Apply/OK is still using the window
        # Closing with the X used to drop unapplied changes without a word.
        # Ask first, the way KDE's System Settings does. (Cancel still closes
        # without asking: clicking it already says "throw them away".)
        if self.has_unapplied_changes():
            answer = self.ask_apply_before_closing()
            if answer == "apply":
                self.on_ok(None)    # saves and closes; stays open if saving fails
                return True
            if answer != "discard":
                return True         # Keep Editing, or the question was closed
        self._release()
        Gtk.main_quit()
        return False

    def has_unapplied_changes(self):
        """True when closing now would lose something: a setting changed since
        the last Apply, or an edited snippet. One saved whose restart failed
        isn't lost."""
        return bool(changed_keys(self._config_at_open, self.config)) or self.words_tab.changed()

    def ask_apply_before_closing(self):
        """"apply", "discard", or "keep" (also when the question is closed)."""
        response = show_message(
            self.window, Gtk.MessageType.QUESTION, "Apply your changes before closing?",
            "You changed some settings but haven't applied them. "
            "If you close without applying, they're lost.",
            [("Keep Editing", Gtk.ResponseType.CANCEL), ("Discard", Gtk.ResponseType.REJECT),
             ("Apply", Gtk.ResponseType.OK)],
            default=Gtk.ResponseType.OK)
        return {Gtk.ResponseType.OK: "apply",
                Gtk.ResponseType.REJECT: "discard"}.get(response, "keep")

    def _changed_since_open(self):
        """Keys the user changed in this window since it opened.

        Deliberately measures what THIS window changed, not what differs from
        the file on disk: if the tray switched models while Preferences was
        open, that change is already live in the running service and must not
        drag a restart along with an unrelated Apply.

        Must be called before save_config(), which resets the baseline.
        Keys whose restart failed count until one works.
        """
        return changed_keys(self._config_at_open, self.config) | self._restart_owed

    def _apply_or_restart(self, changed):
        """Restart the service only if the change requires it.

        Returns True when the settings are in force — either because the
        service restarted successfully, or because it will pick them up from
        the config file on its own within about a second.

        Restarting rebuilds the Whisper model, roughly ten seconds during which
        the hotkey silently does nothing, so a change like auto-punctuation
        must not cause one.
        """
        from .config import needs_service_restart

        if not needs_service_restart(changed):
            logger.info(f"Applied without restart: {sorted(changed) or 'no changes'}")
            return True

        self._restarts_asked += 1
        asked = self._restarts_asked

        def failed_later(failed):
            # Owed again, so the next Apply restarts; a failure is said
            # while the window is open (not a start that's only slow), and
            # only for the latest restart: an older one's, arriving late,
            # isn't news once a newer one went ahead.
            if asked != self._restarts_asked:
                return False
            self._restart_owed |= set(changed)
            if failed and self.window.get_visible():
                self._say_restart_failed()
            return False
        return self.restart_service(on_failed=failed_later)

    def put_in_force(self, changed):
        """Put the settings named in *changed*, saved already, in force:
        True when they are. Dictation restarts only when they need it; the
        service picks up everything else from the config file within a
        second, which avoids a ~10 s rebuild of the speech model during
        which the hotkey does nothing at all. A restart that failed says so
        and stays owed until one works: the save moved the baseline on, so
        later Applies would find nothing changed."""
        restarted = self._apply_or_restart(changed)
        self._restart_owed = set() if restarted else self._restart_owed | set(changed)
        if not restarted:
            self._say_restart_failed()
        return restarted

    def restart_service(self, on_failed=None):
        """Restart the dictation service; True if it was done or asked for.
        A running tray does it (Restart Service: it waits for the old service
        off its menu's thread, and starts none once Quit or Dictation off came
        after); one that doesn't answer is left to it, never worked around.
        With no tray it's done here, on a thread: the old service types what
        it holds and quits (or is killed past its time), then a new one starts.
        A start that fails there calls *on_failed*(True) on GTK's thread,
        and one still not ready at the wait's end *on_failed*(False)."""
        from . import single_instance, tray_instance, update_ui
        if single_instance.held_elsewhere(tray_instance.TRAY_LOCK):
            try:
                update_ui.tray_interface().RestartService()
                return True
            except Exception as e:
                logger.warning(f"Could not ask the tray to restart dictation: {e}")
                return False
        def restart():
            came_up = _restart_without_tray()
            if came_up is not True and on_failed is not None:
                GLib.idle_add(on_failed, came_up is False)
        # Not a daemon: closing Preferences meanwhile must not drop the start.
        threading.Thread(target=restart, name="restart-service").start()
        return True

    def on_tab_switched(self, notebook, page, page_num):
        """Scroll the newly shown tab to the top, refresh Stats, which the
        service updates while this window is open, and stop the microphone
        test of the tab left: it holds the mic."""
        page.get_vadjustment().set_value(0)     # each page is a _scrollable_tab
        if notebook.get_tab_label_text(page) == "Stats":
            self.stats_tab.refresh()
        self._stop_tests(shown=page)

    def _save_and_download(self):
        """Save the settings, then download what the chosen model needs:
        (the keys changed, when they were saved), or None when the save
        failed (it says why) or the download didn't finish (its model and
        device are rolled back)."""
        # Both before save_config(), which resets _config_at_open to what it
        # wrote: after that there is nothing left to diff, and it no longer
        # names the setup the service runs.
        changed = self._changed_since_open()
        saved_at = time.time()      # for KDE's verdict on a changed hotkey
        if not self.save_config():
            self._show_save_error()
            return None
        # Only what this window changed is undone, and to what was saved
        # until now: a model the tray switched to meanwhile stays.
        previous = {k: self._saved_before.get(k) for k in _DOWNLOAD_KEYS if k in changed}
        self.general_tab.show_saved()
        speech = self.general_tab.speech
        if not speech.download_selected_model():
            speech.rollback_download(previous)
            # The rest was saved but never put in force: the next Apply
            # restarts for it rather than say it's in effect.
            self._restart_owed |= set(changed) - set(_DOWNLOAD_KEYS)
            return None
        return changed, saved_at

    def on_apply(self, button):
        """Apply changes, restarting the service only if something needs it.
        Snippets that can't be saved (they say why) don't hold the settings back."""
        self.words_tab.save()
        self._apply_settings()

    def _apply_settings(self):
        """Save the settings, download what they need and put them in force,
        saying how that went; the window stays open."""
        saved = self._save_and_download()
        if saved is None:
            return
        changed, saved_at = saved
        if self.put_in_force(changed) and not self.general_tab.after_hotkey_change(
                changed, saved_at):
            # (A hotkey question stands in for this.) Whether the service
            # really restarted, not just that the change applied.
            from .config import needs_service_restart
            show_message(self.window, Gtk.MessageType.INFO, "Settings applied successfully!",
                         "Dictation is restarting with the new settings."
                         if needs_service_restart(changed)
                         else "Your changes are already in effect.")

    def on_cancel(self, button):
        """Close without saving."""
        # destroy() emits no delete-event, so on_window_close doesn't run.
        self._release()
        self.window.destroy()
        Gtk.main_quit()

    def on_ok(self, button):
        """Save, apply (restarting only if needed), and close; Apply is the
        one that stays open, so OK closes after a download too."""
        if not self.words_tab.save():
            # Closing would throw away the edits the user was just told
            # weren't saved: any settings changed are applied, and the
            # window stays.
            if self._changed_since_open():
                self._apply_settings()
            return
        saved = self._save_and_download()
        if saved is None:
            return
        changed, saved_at = saved
        if self._autostart_pending():
            # Its warning says Apply tries again, which a closed window
            # can't: the rest is put in force as Apply would, and it stays.
            if self.put_in_force(changed):
                self.general_tab.after_hotkey_change(changed, saved_at)
            return
        restarted = self._apply_or_restart(changed)
        if restarted:
            self.general_tab.after_hotkey_change(changed, saved_at)
        self._release()
        if not restarted:
            self._say_restart_failed()
        self.window.destroy()
        Gtk.main_quit()

    def on_help(self, button):
        """Open TuxTalk's Help window."""
        from .help_dialog import show_help_dialog
        show_help_dialog()


def main():
    """Open Preferences (python -m tuxtalk.prefs [--tab TAB]), or bring the
    one already open to the front."""
    from . import desktop_identity
    desktop_identity.apply()        # TuxTalk's icon on its windows, not KDE's generic one
    ui_style.prepare_windows()   # emoji that show, buttons clear of panels and docks
    # Before anything opens the shared D-Bus connection, as in the tray: one
    # opened without the GLib main loop can't receive signals, and Test
    # Hotkeys (which listens for HotkeyPressed) crashed on opening in 0.15.0
    # testing. Setting the loop in Test Hotkeys itself came too late for a
    # shared connection that already existed.
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    import argparse
    parser = argparse.ArgumentParser(description="TuxTalk Preferences")
    parser.add_argument("--tab", help="Tab to open initially",
                        choices=["general", "audio", "advanced", "words", "snippets",
                                 "commands", "stats", "updates", "backup"])
    args = parser.parse_args()

    # Handlers go in BEFORE the pidfile is written (_acquire_prefs_singleton):
    # anyone who finds our pid there may signal us, and an unhandled SIGUSR1
    # or SIGUSR2 would terminate this process. Until the window exists they
    # do nothing.
    window_holder = []

    def _on_front_request(*_args):
        tab = prefs_ipc.take_tab_request()        # e.g. "stats" from the tray
        if window_holder:
            window_holder[0].bring_to_front(tab)
        return True  # keep the handler installed

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, prefs_ipc.FRONT, _on_front_request, None)

    def _on_close_request(*_args):
        # An uninstall about to go ahead: closed as the title bar's close
        # does, so changes not applied are asked about, not lost.
        if window_holder:
            window_holder[0].on_window_close(None, None)
        return True
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, prefs_ipc.CLOSE, _on_close_request, None)

    _acquire_prefs_singleton(args.tab)
    app = PreferencesWindow()
    window_holder.append(app)

    if args.tab:
        app.select_tab(args.tab)
    Gtk.main()


if __name__ == "__main__":
    main()
