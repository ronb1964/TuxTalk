"""The Preferences window: its tabs, and saving and applying what they change.

It runs as its own process (python -m tuxtalk.prefs), started from the tray's
or the GNOME extension's menu, and restarts the dictation service when a
change needs it.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib
from .ui_style import Fold, message_dialog, show_message
from .logger import setup_logger
from .prefs_audio import AudioTab
from .prefs_advanced import AdvancedTab
from .prefs_general import GeneralTab
from .prefs_stats import StatsTab
from .prefs_updates import UpdatesTab
from .prefs_words import WordsTab
import os
import sys
import time
import atexit
import dbus.mainloop.glib
from dataclasses import asdict
from .config import (CONFIG_PATH, Settings, apply_toml, changed_keys, merge_changed_keys,
                     repaired, write_text_atomic, _toml_value)

# Set on the Preferences window so prefs_style.css — which must be installed
# screen-wide to reach child widgets — matches only this window's widgets.
# Every selector in that file is prefixed with it; keep the two in step.
PREFS_STYLE_CLASS = "talktype-prefs"

logger = setup_logger(__name__)

# Use tomllib (Python 3.11+) or fallback to toml
try:
    import tomllib
    def load_toml(path):
        with open(path, "rb") as f:
            return tomllib.load(f)
except ImportError:
    # Python < 3.11. No hand-rolled parser beyond this: a string-only one
    # returns '5' instead of 5, and the spinboxes crash on it.
    import toml
    def load_toml(path):
        with open(path, "r") as f:
            return toml.load(f)


def _coerce_config_types(config: dict) -> dict:
    """*config* read as the service reads it (config.apply_toml, then
    config.repaired): a hand-edited config can hold '5' for 5, and
    Gtk.Adjustment(value='5') crashes the window. An unusable value ("oops"
    or nan for a number) shows its default, which is what the service uses;
    left as it was, Preferences didn't open. Unknown keys are kept."""
    settings = Settings()
    apply_toml(settings, config)
    return {**config, **asdict(repaired(settings))}


# Single instance management (see prefs_ipc.py)
from . import prefs_ipc
_PREFS_PIDFILE = prefs_ipc.pidfile_path()

def _acquire_prefs_singleton(tab=None):
    try:
        if os.path.exists(_PREFS_PIDFILE):
            try:
                with open(_PREFS_PIDFILE, "r") as f:
                    old = int(f.read().strip() or "0")
            except Exception:
                old = 0
            if old != os.getpid() and prefs_ipc.is_prefs_pid(old):
                # Bring the open one forward rather than silently doing
                # nothing, which looked like Preferences failed to open.
                prefs_ipc.bring_to_front(old, tab)
                logger.info("Preferences is already open; brought it to the front")
                sys.exit(0)
        with open(_PREFS_PIDFILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logger.warning(f"Could not write prefs pidfile: {e}")
    def _cleanup():
        try:
            with open(_PREFS_PIDFILE, "r") as f:
                cur = int(f.read().strip() or "0")
            if cur == os.getpid():
                os.remove(_PREFS_PIDFILE)
        except Exception:
            pass
    atexit.register(_cleanup)


# What can need a download, and so is rolled back when it never finishes:
# the model, and the device (the processor or Vulkan copy of a model).
_DOWNLOAD_KEYS = ("model", "device")


class PreferencesWindow:
    """The Preferences window: the tabs (prefs_*.py modules), and saving,
    applying and closing for all of them."""

    def __init__(self):
        # Set GTK theme to prefer dark mode
        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", True)

        # Create window FIRST
        self.window = Gtk.Window(title="TuxTalk Preferences")
        self.window.set_name("preferences_window")  # Give window a name for CSS targeting
        # Size is set in create_ui (_fit_to_screen), once the content can be measured.
        self.window.set_position(Gtk.WindowPosition.CENTER)

        # Load custom CSS styling (after window is created)
        self._load_css()

        # Set window type hint to NORMAL to ensure proper window stacking
        # This prevents the "Tried to map a popup with a non-top most parent" error
        self.window.set_type_hint(Gdk.WindowTypeHint.NORMAL)

        # Load current config. Keep a snapshot of the window-open state so
        # save_config() can write only the keys the user actually changed
        # (see merge_changed_keys — protects tray changes made meanwhile).
        self.config = self.load_config()
        self._config_at_open = dict(self.config)

        # Create UI
        self.create_ui()
        
        # Connect window close event to cleanup
        self.window.connect("delete-event", self.on_window_close)
        self.window.connect("notify::is-active", self._on_window_active)
        
        self._show_window()
        # Don't auto-start level monitoring - only when user clicks record button

    def _show_window(self):
        """Show the window with the rows that depend on a setting already hidden."""
        self.window.show_all()
        # show_all() makes every child visible, so rows hidden while the tabs
        # were built come back. Hide them again here, before the main loop
        # draws anything, so they never flash on screen.
        self.general_tab.update_timeout_ui_state()
        self.ok_btn.grab_default()  # Now safe to grab default after window is shown
        self.window.present()  # Bring window to front
        # Note: Don't use set_keep_above - it interferes with combo box popups

    def _load_css(self):
        """Load custom CSS stylesheet for preferences window ONLY (not globally)."""
        # Dropdown list mode must be installed screen-wide: a provider on this
        # window's context does not reach its child combos, so it cannot ride
        # along with prefs_style.css below.
        from .ui_style import apply_dropdown_list_style
        apply_dropdown_list_style()

        try:
            css_provider = Gtk.CssProvider()
            css_file = os.path.join(os.path.dirname(__file__), 'prefs_style.css')

            if os.path.exists(css_file):
                css_provider.load_from_path(css_file)

                # This provider must go on the screen: one on the window's own
                # style context does not reach the window's children. Each
                # selector in prefs_style.css is scoped to the class set below,
                # so the stylesheet styles Preferences and no other window.
                self.window.get_style_context().add_class(PREFS_STYLE_CLASS)
                Gtk.StyleContext.add_provider_for_screen(
                    Gdk.Screen.get_default(),
                    css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
            else:
                logger.warning(f"CSS file not found: {css_file}")
        except Exception as e:
            logger.warning(f"Failed to load CSS: {e}")

    def _block_combo_scroll(self, combo):
        """
        Stop the scroll wheel from changing a ComboBox value, while still letting
        the page scroll when the pointer is over the dropdown (see
        ui_style.forward_combo_scroll for why swallowing the event stalled the
        window).
        """
        from .ui_style import forward_combo_scroll
        forward_combo_scroll(combo)

    def load_config(self):
        """Load config from TOML file.

        Defaults come from config.py's Settings dataclass, shared with the
        service and tray, so a missing key shows what the service really uses.
        """
        defaults = asdict(Settings())

        if os.path.exists(CONFIG_PATH):
            try:
                config = load_toml(CONFIG_PATH)
                defaults.update(config)
            except Exception as e:
                logger.warning(f"Could not read the config: {e}")

        return _coerce_config_types(defaults)

    def _check_cuda_availability(self):
        """Whether CUDA libraries (downloaded or system-wide) are present."""
        try:
            from . import cuda_helper
            return cuda_helper.has_cuda_libraries()
        except Exception as e:
            logger.warning(f"Could not check for CUDA's files: {e}")
            return False

    def _nvidia_gpu(self):
        """The NVIDIA card's name (or True in a Flatpak), False if there is none.

        Asked once per window: nvidia-smi runs on the GTK thread and can take
        hundreds of milliseconds while the card wakes up.
        """
        if not hasattr(self, "_nvidia_found"):
            try:
                from .cuda_helper import detect_nvidia_gpu
                self._nvidia_found = detect_nvidia_gpu() or False
            except Exception as e:
                logger.warning(f"Could not check for an NVIDIA card: {e}")
                self._nvidia_found = False
        return self._nvidia_found

    def _has_nvidia(self):
        """Whether this computer has an NVIDIA card."""
        return bool(self._nvidia_gpu())

    def _vulkan_offered(self):
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
                    on_disk = load_toml(CONFIG_PATH)
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
            # Only a change made in this window may touch the autostart entry:
            # rewriting it on every save would undo the user turning TuxTalk
            # off in their desktop's own Autostart settings.
            autostart_changed = "launch_at_login" in changed_keys(self._config_at_open, self.config)
            merged = merge_changed_keys(self._config_at_open, self.config, base)

            # Same serialiser and atomic write as config.save_config(): one
            # escaping rule, and a crash mid-write can't leave an empty file.
            lines = ["# TuxTalk config"]
            for key, value in merged.items():
                lines.append(f"{key} = {_toml_value(value)}")
            write_text_atomic(CONFIG_PATH, "\n".join(lines) + "\n")

            # Apply settings that live outside the config file only now that
            # the save has happened, so Cancel never has anything to undo.
            if autostart_changed:
                try:
                    self._handle_autostart(bool(merged.get("launch_at_login", False)))
                except Exception as e:
                    # A read-only home must not cost the user the settings they
                    # just saved — the file is already written at this point.
                    logger.warning(f"Could not update autostart entry: {e}")

            # Future saves in this window diff against what we just wrote
            self.config = dict(merged)
            self._config_at_open = dict(merged)
            return True
        except Exception as e:
            logger.error(f"Could not save the config: {e}")
            self._save_error = str(e)
            return False
    
    def create_ui(self):
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Main content column. Each notebook tab is individually wrapped in a
        # ScrolledWindow (see _scrollable_tab) so a tab taller than the window scrolls
        # instead of running off the bottom. NOTE: a notebook inside ONE outer
        # ScrolledWindow does not scroll — the notebook clips its page rather than
        # overflowing — which is why the scroll must be per-tab.
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)

        title = Gtk.Label()
        title.set_markup("<big><b>TuxTalk Preferences</b></big>")
        vbox.pack_start(title, False, False, 0)

        notebook = self.notebook = Gtk.Notebook()

        self.general_tab = GeneralTab(self)
        notebook.append_page(self._scrollable_tab(self.general_tab.build()),
                             Gtk.Label(label="General"))

        self.audio_tab = AudioTab(self)
        notebook.append_page(self._scrollable_tab(self.audio_tab.build()),
                             Gtk.Label(label="Audio"))

        self.advanced_tab = AdvancedTab(self)
        advanced_tab = self.advanced_tab.build()
        notebook.append_page(self._scrollable_tab(advanced_tab), Gtk.Label(label="Advanced"))

        # Words tab (Your Words and the snippets). Its lists have scrolls of
        # their own, which the wheel must still reach, so the page scroll leaves
        # the wheel alone; the tab has no dropdowns or sliders to protect.
        # Its whole content sets the window's opening height (see below).
        self.words_tab = WordsTab(self)
        commands_tab = self.words_tab.build()
        commands_page = self._scrollable_tab(commands_tab, capture_wheel=False)
        commands_page.set_propagate_natural_height(True)
        notebook.append_page(commands_page, Gtk.Label(label="Words"))

        self.stats_tab = StatsTab(self)
        notebook.append_page(self._scrollable_tab(self.stats_tab.build()),
                             Gtk.Label(label="Stats"))

        self.updates_tab = UpdatesTab(self)
        updates_tab = self.updates_tab.build()
        notebook.append_page(self._scrollable_tab(updates_tab), Gtk.Label(label="Updates & Backup"))
        # Connected once every tab is built: appending the first page shows it.
        notebook.connect("switch-page", self.on_tab_switched)

        vbox.pack_start(notebook, True, True, 0)

        from . import __version__
        version_label = Gtk.Label()
        version_label.set_markup(f'<span size="small" color="#888888">TuxTalk v{__version__}</span>')
        version_label.set_halign(Gtk.Align.END)
        version_label.set_margin_top(5)
        version_label.set_margin_end(5)
        vbox.pack_start(version_label, False, False, 0)

        main_vbox.pack_start(vbox, True, True, 0)

        # Buttons (outside scrolled area so always visible)
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

        # Add spacing to push other buttons to the right
        button_box.pack_start(Gtk.Label(), True, True, 0)

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

        main_vbox.pack_start(button_box, False, False, 0)
        self.window.add(main_vbox)
        self._fit_to_screen(main_vbox)
        # Wheel scrolling over combos/sliders is handled by the per-tab capture-phase
        # scroll controller installed in _scrollable_tab (compositor-independent).

    def _fit_to_screen(self, content):
        """Open at the height the content asks for, capped to the monitor.

        Every tab scrolls, so the window's minimum height is small and it can
        always fit the screen with the button row showing. The opening height
        is the content's natural height: the whole Snippets tab, the tallest
        page that is not expected to scroll.
        """
        from .ui_style import fit_dialog_to_screen
        try:
            # Hidden widgets measure as zero; window.show_all() follows anyway.
            content.show_all()
            desired = content.get_preferred_height()[1]
        except Exception:
            desired = 0
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
        # Keep a reference so the controller is not garbage-collected.
        if not hasattr(self, "_scroll_controllers"):
            self._scroll_controllers = []
        self._scroll_controllers.append(ctrl)

    def _on_capture_scroll(self, controller, dx, dy, scrolled):
        vadj = scrolled.get_vadjustment()
        if vadj is not None:
            step = vadj.get_step_increment() or 40
            hi = vadj.get_upper() - vadj.get_page_size()
            vadj.set_value(max(vadj.get_lower(), min(vadj.get_value() + dy * step, hi)))
        return True  # consume so child widgets never act on the scroll

    def _fold(self, grid, row, show_label, hide_label):
        """A closed Fold across *grid* at *row*, for settings most people
        never need; fill its .content."""
        def toggled(opening):
            if opening:
                # After the slide-down, so its full height is known.
                self._scroll_into_view(
                    fold.revealer, fold.revealer.get_transition_duration() + 60)
        fold = Fold(show_label, hide_label, on_toggle=toggled)
        fold.set_margin_top(6)
        grid.attach(fold, 0, row, 2, 1)
        return fold

    def _section_header(self, grid, row, title):
        """A divider line and a bold section title across the grid; returns the
        next free row."""
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(20)
        sep.set_margin_bottom(15)
        grid.attach(sep, 0, row, 2, 1)
        header = Gtk.Label()
        header.set_markup(f"<b>{title}</b>")
        header.set_xalign(0)
        header.set_margin_bottom(10)
        grid.attach(header, 0, row + 1, 2, 1)
        return row + 2

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

    def _on_combo_button_press(self, widget, event):
        """Handle button press events on combo boxes to ensure they open reliably."""
        # Ensure the widget has focus before processing the click
        if not widget.has_focus():
            widget.grab_focus()
        # Let GTK handle the normal click event
        return False

    def update_config(self, key, value):
        """Update config value."""
        # GTK combos emit 'changed' with get_active_id()=None while being
        # rebuilt (remove_all in SpeechSection.refresh_device_options). Writing that None
        # would produce 'device = None' in the TOML file — invalid TOML that
        # wipes ALL settings on the next load. Never a real user action.
        if value is None:
            return

        # (CUDA picked before its files are downloaded never gets here: the
        # Device dropdown offers the download instead.)
        self.config[key] = value
        # launch_at_login's autostart file is written by save_config(), not
        # here, so it changes only when the user saves and never on Cancel.

    def _handle_autostart(self, enable):
        """Create or remove the autostart desktop file. Delegates to
        tuxtalk.autostart so onboarding and Preferences stay in lock-step."""
        from . import autostart
        autostart.set_autostart(enable)

    def get_selected_device_idx(self):
        """Get the device index for the currently selected microphone.

        Uses config.find_input_device() for smart PipeWire-aware detection
        when no mic name is configured, avoiding the broken ALSA default
        virtual device that returns garbage audio.
        """
        from .config import find_input_device
        current_mic = self.config.get("mic", "")
        return find_input_device(current_mic)
    
    def _scroll_into_view(self, widget, delay_ms=60):
        """Scroll the tab so *widget*, just shown, is in view, once GTK has
        laid it out (after *delay_ms*, longer for a sliding reveal). Ticking
        a box whose settings appear below the window's edge looked like
        nothing had happened."""
        def scroll():
            sw = widget.get_parent()
            while sw is not None and not isinstance(sw, Gtk.ScrolledWindow):
                sw = sw.get_parent()
            if sw is None or not widget.get_mapped():
                return False
            coords = widget.translate_coordinates(sw, 0, 0)
            if coords is None:
                return False
            top = coords[1]
            bottom = top + widget.get_allocated_height()
            adj = sw.get_vadjustment()
            page = adj.get_page_size()
            if bottom > page:
                # Down until its bottom clears the "more below" fade (70 px,
                # prefs_style.css), but never past its top. At a page's end
                # there's no fade: the page just scrolls to the bottom.
                adj.set_value(adj.get_value() + min(bottom - page + 76, max(0, top - 12)))
            elif top < 0:
                adj.set_value(adj.get_value() + top - 12)
            return False
        GLib.timeout_add(delay_ms, scroll)

    def _stop_mic_test(self):
        """The window closes: stop the microphone tests and free the mic."""
        self.audio_tab.mic_test.close()
        self._stop_pause_test(wait=True)

    def _on_window_active(self, window, _param):
        """Going off to another window is going off to dictate: a microphone
        test still holding a direct hw: mic, or about to open it, would make
        that dictation fail with only a beep, so both tests stop."""
        if not window.is_active():
            self.audio_tab.mic_test.stop()
            self._stop_pause_test()

    def _stop_pause_test(self, wait=False):
        """Stop Type at pause's live test, where its Labs entry is built (not
        on the Flatpak). *wait*: TypeAtPause.stop_test's."""
        if self.advanced_tab.type_at_pause is not None:
            self.advanced_tab.type_at_pause.stop_test(wait)

    def _release_pidfile(self):
        """Remove the prefs singleton pidfile if it belongs to this process."""
        try:
            with open(_PREFS_PIDFILE, "r") as f:
                cur = int(f.read().strip() or "0")
            if cur == os.getpid():
                os.remove(_PREFS_PIDFILE)
        except Exception:
            pass

    def _show_save_error(self):
        """Error dialog for a failed config save (disk full, permissions…)."""
        dialog = message_dialog(
            transient_for=self.window,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Failed to save settings!"
        )
        # Why it failed (disk full, read-only folder…) is what the user needs
        # to fix it.
        reason = getattr(self, "_save_error", "")
        if reason:
            dialog.format_secondary_text(reason)
        dialog.run()
        dialog.destroy()

    def on_window_close(self, widget, event):
        """Handle window close event: stop the mic test, clean up PID file."""
        if getattr(self, "_waiting_for_kde", False):
            return True     # Apply/OK is still using the window
        # Closing with the X used to drop unapplied changes without a word.
        # Ask first, the way KDE's System Settings does. (Cancel still closes
        # without asking: clicking it already says "throw them away".)
        if self._has_unapplied_changes():
            answer = self._ask_apply_before_closing()
            if answer == "apply":
                self.on_ok(None)    # saves and closes; stays open if saving fails
                return True
            if answer != "discard":
                return True         # Keep Editing, or the question was closed
        self._stop_mic_test()
        self._release_pidfile()
        Gtk.main_quit()
        return False
    
    def _has_unapplied_changes(self):
        """True when closing now would lose something: a setting changed since
        the last Apply, or an edited Voice Commands list."""
        if self._changed_since_open():
            return True
        try:
            if self.words_tab.changed():
                return True
        except Exception:
            pass
        return False

    def _ask_apply_before_closing(self):
        """"apply", "discard", or "keep" (also when the question is closed)."""
        dialog = message_dialog(transient_for=self.window, modal=True,
                                message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE,
                                text="Apply your changes before closing?")
        dialog.format_secondary_text(
            "You changed some settings but haven't applied them. "
            "If you close without applying, they're lost.")
        dialog.add_button("Keep Editing", Gtk.ResponseType.CANCEL)
        dialog.add_button("Discard", Gtk.ResponseType.REJECT)
        dialog.add_button("Apply", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        return {Gtk.ResponseType.OK: "apply",
                Gtk.ResponseType.REJECT: "discard"}.get(response, "keep")

    def _changed_since_open(self):
        """Keys the user changed in this window since it opened.

        Deliberately measures what THIS window changed, not what differs from
        the file on disk: if the tray switched models while Preferences was
        open, that change is already live in the running service and must not
        drag a restart along with an unrelated Apply.

        Must be called before save_config(), which resets the baseline.
        """
        return changed_keys(self._config_at_open, self.config)

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

        return self.restart_service()

    def restart_service(self):
        """Restart the dictation service."""
        try:

            # Kill existing dictation-service processes
            from .service_launcher import stop_dictation_service
            stop_dictation_service()
            # Wait a moment for processes to terminate
            time.sleep(1)

            # Start it the same way the tray does. Preferences runs on native
            # Wayland, and a service spawned with this process's environment
            # would inherit it: gtk_window_move() would be ignored and the
            # recording indicator always centred.
            from .service_launcher import launch_dictation_service

            launch_dictation_service()
            return True
        except Exception as e:
            logger.warning(f"Failed to restart service: {e}")
            return False
    
    def on_tab_switched(self, notebook, page, page_num):
        """Scroll the newly shown tab to the top, refresh Stats, which the
        service updates while this window is open, and stop the microphone
        test of the tab left: it holds the mic."""
        page.get_vadjustment().set_value(0)     # each page is a _scrollable_tab
        tab = notebook.get_tab_label_text(page)
        if tab == "Stats":
            self.stats_tab.refresh()
        if tab != "Audio":
            self.audio_tab.mic_test.stop()
        if tab != "Advanced":
            self._stop_pause_test()

    def on_apply(self, button):
        """Apply changes, restarting the service only if something needs it."""
        # Save custom commands first
        self.words_tab.save()

        # MUST be computed before save_config(), which resets _config_at_open
        # to the merged result — after that there is nothing left to diff.
        changed = self._changed_since_open()
        # Same reason: after the save this no longer names the live setup.
        previous = {k: self._config_at_open.get(k) for k in _DOWNLOAD_KEYS}

        saved_at = time.time()      # for KDE's verdict on a changed hotkey
        if self.save_config():
            # Check if model needs downloading
            speech = self.general_tab.speech
            if not speech.download_selected_model():
                speech.rollback_download(previous)
                return

            # Restart only when the change actually requires it. The service
            # picks up everything else from the config file within a second,
            # which avoids a ~10s rebuild of the Whisper model during which
            # the hotkey does nothing at all.
            applied = self._apply_or_restart(changed)
            if applied and self.general_tab.after_hotkey_change(changed, saved_at):
                pass    # its question stands in for "Settings applied"
            elif applied:
                # Show confirmation
                dialog = message_dialog(
                    transient_for=self.window,
                    flags=0,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text="Settings applied successfully!"
                )

                # Say whether the service really restarted, not just that it applied.
                from .config import needs_service_restart
                dialog.format_secondary_text(
                    "Dictation service has been restarted with new settings."
                    if needs_service_restart(changed)
                    else "Your changes are already in effect."
                )
                dialog.run()
                dialog.destroy()
            else:
                # Show service restart error
                show_message(self.window,
                             Gtk.MessageType.WARNING,
                             "Settings saved, but service restart failed!",
                             "You may need to manually restart the service.")
        else:
            self._show_save_error()
    
    def on_cancel(self, button):
        """Cancel and close without saving."""
        # Cancel/OK close via destroy(), which does NOT emit delete-event,
        # so the on_window_close cleanup must be invoked explicitly here.
        self._stop_mic_test()
        self._release_pidfile()
        self.window.destroy()
        Gtk.main_quit()
    
    def on_ok(self, button):
        """Save, apply (restarting only if needed), and close."""
        # Save custom commands first. If writing them failed, closing would
        # throw away the edits the user was just told weren't saved.
        if self.words_tab.save() is None:
            return

        # MUST be computed before save_config(), which resets _config_at_open.
        changed = self._changed_since_open()
        # Same reason: after the save this no longer names the live setup.
        previous = {k: self._config_at_open.get(k) for k in _DOWNLOAD_KEYS}

        saved_at = time.time()      # for KDE's verdict on a changed hotkey
        if self.save_config():
            # Check if model needs downloading
            speech = self.general_tab.speech
            if not speech.download_selected_model():
                speech.rollback_download(previous)
                return

            # OK closes even after a download; Apply is the one that stays open.
            service_restarted = self._apply_or_restart(changed)
            if service_restarted:
                self.general_tab.after_hotkey_change(changed, saved_at)

            # Stop the mic test (if running) and clean up PID file before closing
            self._stop_mic_test()
            self._release_pidfile()

            # Show final status if service restart failed
            if not service_restarted:
                show_message(self.window,
                             Gtk.MessageType.WARNING,
                             "Settings saved, but service restart failed!",
                             "You may need to manually restart the service.")

            self.window.destroy()
            Gtk.main_quit()
        else:
            # Same failure dialog as Apply, so OK never looks dead on a save error.
            self._show_save_error()

    def on_help(self, button):
        """Show help dialog with TuxTalk features and instructions."""
        from .help_dialog import show_help_dialog
        show_help_dialog()



def main():
    from . import desktop_identity, ui_style
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
    parser.add_argument("--tab", choices=["general", "audio", "advanced", "words", "snippets", "commands",
                                          "stats", "updates", "backup"],
                        help="Tab to open initially")
    args = parser.parse_args()

    # Handlers go in BEFORE the pidfile is written (_acquire_prefs_singleton):
    # anyone who finds our pid there may signal us, and an unhandled SIGUSR1
    # would terminate this process. Until the window exists they do nothing.
    window_holder = []

    def _on_front_request(*_args):
        tab = prefs_ipc.take_tab_request()        # e.g. "stats" from the tray
        if window_holder:
            window_holder[0].bring_to_front(tab)
        return True  # keep the handler installed

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, prefs_ipc.FRONT, _on_front_request, None)

    _acquire_prefs_singleton(args.tab)
    app = PreferencesWindow()
    window_holder.append(app)

    # Switch to specified tab if requested
    if args.tab:
        app.select_tab(args.tab)

    Gtk.main()

if __name__ == "__main__":
    main()
