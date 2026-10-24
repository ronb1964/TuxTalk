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
from .whisper_vulkan import ENGINE_SIZE_TEXT
from .hotkey_labels import dropdown_entries, hands_free_label
from .logger import setup_logger
from .prefs_audio import AudioTab
from .prefs_stats import StatsTab
from .prefs_type_at_pause import TypeAtPause
from .prefs_system_check import UPDATE_HELPER_LABEL, SystemCheck
from .prefs_tab import WARNING_AMBER
from .prefs_updates import UpdatesTab
from .prefs_words import WordsTab
import os
import sys
import threading
import time
import atexit
import dbus.mainloop.glib
from dataclasses import asdict
from .config import (CONFIG_PATH, Settings, apply_toml, changed_keys, merge_changed_keys,
                     repaired, write_text_atomic,
                     _toml_value, HOTKEY_CHOICES)

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


# The Numbers dropdown: (label, numbers_as_digits, numbers_small_as_words).
# "As written" leaves the one-to-nine choice as it was (None), for next time.
NUMBER_CHOICES = {
    "written": ("As the speech model writes them", False, None),
    "digits": ("As digits (3 cats, 25 chickens)", True, False),
    "from-ten": ("Digits from 10 up (three cats, 25 chickens)", True, True),
}


def number_choice(config):
    """The NUMBER_CHOICES entry for numbers_as_digits and numbers_small_as_words."""
    if not config.get("numbers_as_digits", False):
        return "written"
    return "from-ten" if config.get("numbers_small_as_words", False) else "digits"


def swiss_spelling_offered(config, env=None):
    """Whether Swiss German spelling belongs in Preferences: German is the
    computer's language or one the user dictates in, or it's already on."""
    from . import language_limit
    from .recommend import system_language
    if config.get("swiss_spelling", False):
        return True
    languages = {system_language(env), config.get("dictation_language", "")}
    if config.get("language_mode", "auto") == "manual":
        languages.add(config.get("language", ""))
    languages.update(language_limit.parse(config.get("auto_languages", "")))
    return "de" in languages


# The Language dropdown's entry for auto-detect held to a list of languages.
SEVERAL_LANGUAGES = "several"


def language_choice(config):
    """The Language dropdown's entry for these settings: "auto", a language
    code, or SEVERAL_LANGUAGES. One dropdown stands for three settings
    (language_mode, language, auto_languages), which keep their meaning, so
    older configs and backups read the same."""
    if config.get("language_mode", "auto") == "manual":
        return config.get("language") or "en"
    return SEVERAL_LANGUAGES if config.get("auto_languages") else "auto"


def apply_language_choice(config, choice):
    """Write a Language dropdown entry (not SEVERAL_LANGUAGES, which needs the
    list) into *config*."""
    if choice == "auto":
        config["language_mode"] = "auto"
        config["auto_languages"] = ""
    else:
        config["language_mode"] = "manual"
        config["language"] = choice


def several_languages_label(setting):
    """"Several: English, Spanish…", or "Several languages…" before any are picked."""
    from . import language_limit
    codes = language_limit.parse(setting)
    return f"Several: {language_limit.describe(codes)}\u2026" if codes else "Several languages\u2026"


def language_summary(config):
    """The language part of the setup line: "English", "English, Spanish",
    or "detects the language"."""
    from . import language_limit
    from .recommend import language_name
    choice = language_choice(config)
    if choice == "auto":
        return "detects the language"
    if choice == SEVERAL_LANGUAGES:
        return language_limit.describe(language_limit.parse(config.get("auto_languages", "")))
    return language_name(choice)


def model_setting_warning(model, device, language_mode, language, language_name="",
                          auto_languages=()):
    """What's wrong with this model/device/language combination, or "".

    Parakeet can't run on CUDA: with that device it quietly uses the processor
    while Preferences says "CUDA (GPU)". And it knows 25 languages, detects the
    one spoken and ignores the language setting, so choosing Japanese with it
    gets nothing useful.
    """
    if model != "parakeet-v3":
        return ""
    from .parakeet_engine import PARAKEET_LANGUAGES
    notes = []
    if device == "cuda":
        notes.append("Parakeet can't use CUDA, so it runs on the processor. To use your "
                     "graphics card, set the Device to Vulkan (any GPU).")
    if language_mode == "manual" and language and language not in PARAKEET_LANGUAGES:
        lang = language_name or language
        notes.append(f"Parakeet doesn't understand {lang}. It knows English and 24 European "
                     f"languages. For {lang}, choose one of the Whisper models.")
    if language_mode == "auto" and auto_languages:
        notes.append("\u201cSeveral languages\u201d does nothing with Parakeet, which always "
                     "picks the language by itself. It only works with the Whisper models.")
    return "\n".join(notes)




def _newer_helper_lines(unheard):
    """For hotkey_change_message: what to do about *unheard*, new keys the
    running hotkey helper is too old to report."""
    if not unheard:
        return []
    return [f"{' and '.join(unheard)} need{'s' if len(unheard) == 1 else ''} "
            "the hotkey helper this TuxTalk brings. "
            f"Update it first: Advanced tab, Show system check, {UPDATE_HELPER_LABEL}."]


def hotkey_change_message(cfg, changed, refused, apps=None, unheard=()):
    """(title, text, offer the test, a key clashes) for the question after OK or
    Apply, or None.

    *changed*: the settings keys changed; *refused*: keys KDE already uses
    (kwin_hotkeys.refused_since), None when unknown; *apps*: {key: app} for
    those that open an app (kwin_hotkeys.app_shortcuts_since); *unheard*: new
    keys the running hotkey helper is too old to report. A changed key is
    offered the same test first run gives it.

    A key KDE also uses comes first, in the title as well: someone who reads
    only the top of the window must still learn that the key does two things.
    """
    # KDE reports keys in Qt's spelling (Super is "Meta"): map back to ours.
    from .kwin_hotkeys import qt_key_sequence
    all_keys = ("hotkey", "toggle_hotkey", "voice_commands_hotkey", "paste_last_hotkey")
    ours = {qt_key_sequence(cfg.get(k)): cfg.get(k) for k in all_keys if cfg.get(k)}
    clashes = []        # (our name for the key, the app or None)
    if changed & set(all_keys):
        clashes = [(ours[key], (apps or {}).get(key)) for key in refused or [] if key in ours]
    lines = []
    for name, app in clashes:
        if app:
            lines.append(f"KDE opens {app} with {name}, so pressing {name} does that too. "
                         f"Pick another key, or change that shortcut in System Settings, "
                         f"Keyboard, Shortcuts.")
        else:
            lines.append(f"KDE already uses {name} for one of its own shortcuts, so "
                         f"pressing it does that too. Pick another key if that gets in the way.")

    roles = (("hotkey", "hold-to-talk key"), ("toggle_hotkey", "tap-to-start key"))
    new_keys = [f"Your {what} is now {cfg.get(key)}." for key, what in roles
                if key in changed and cfg.get(key)]
    offer_test = bool(new_keys)
    lines += new_keys + _newer_helper_lines(unheard)
    if offer_test:
        lines.append("Try it once in the test to make sure it reaches TuxTalk. On some "
                     "laptops the F-keys change the brightness or volume unless you hold Fn; "
                     "Right Ctrl, Right Alt and Scroll Lock never need it.")
        # So Not Now isn't a dead end: the Test Hotkeys button can be below the fold.
        lines.append("You can also test it later with Test Hotkeys, under the hotkeys on "
                     "the General tab.")
    if not lines:
        return None

    if len(clashes) > 1:
        title = "KDE already uses some of these keys"
    elif clashes and clashes[0][1]:
        title = f"{clashes[0][0]} also opens {clashes[0][1]}"
    elif clashes:
        title = f"KDE already uses {clashes[0][0]}"
    else:
        title = "Try your new hotkey?"
    return title, "\n\n".join(lines), offer_test, bool(clashes)


def device_label(device, model, cuda_ready):
    """What the Device dropdown calls *device* with *model* selected.

    CUDA is listed on every NVIDIA computer, so until its files are
    downloaded the label says how big they are. Parakeet can't use CUDA and
    runs on the processor with it, and the label says that too instead of
    promising the graphics card.
    """
    if device == "vulkan":
        from .whisper_vulkan import DEVICE_LABEL
        return DEVICE_LABEL
    if device != "cuda":
        return "CPU"
    from .parakeet_engine import is_parakeet
    if is_parakeet(model):
        return ("CUDA (Parakeet runs on the processor)" if cuda_ready
                else "CUDA (1.4 GB download, Parakeet runs on the processor)")
    return "CUDA (NVIDIA)" if cuda_ready else "CUDA (NVIDIA, 1.4 GB download)"


# What can need a download, and so is rolled back when it never finishes:
# the model, and the device (the processor or Vulkan copy of a model).
_DOWNLOAD_KEYS = ("model", "device")


class PreferencesWindow:
    """The Preferences window: the tabs (prefs_*.py modules), and saving,
    applying and closing for all of them."""

    # Type at pause's Labs entry: built outside the Flatpak only.
    type_at_pause = None

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

        # Initialize model tracking to prevent repeated warnings
        self._last_selected_model = self.config.get("model")

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
        self._update_timeout_ui_state()
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
        except Exception:
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
            except Exception:
                self._nvidia_found = False
        return self._nvidia_found

    def _has_nvidia(self):
        """Whether this computer has an NVIDIA card."""
        return bool(self._nvidia_gpu())

    def _refresh_device_options(self):
        """Rebuild the Device dropdown.

        CUDA is listed on every NVIDIA computer, like Vulkan, not only once
        its files are downloaded: people looking for their graphics card look
        here. Picking it before the download offers the download
        (_on_device_changed).
        """
        if not hasattr(self, 'device_combo'):
            return

        self.device_combo.remove_all()
        self.device_combo.append("cpu", "CPU")
        tooltip_text = "Processing device for AI transcription:\n• CPU: works on all computers, slower"

        self._cuda_ready = self._check_cuda_availability()
        if self._cuda_ready or self._has_nvidia():
            self.device_combo.append("cuda", device_label(
                "cuda", self.config.get("model"), self._cuda_ready))
            tooltip_text += ("\n• CUDA (NVIDIA): much faster for the Whisper models, a one-time "
                             "1.4 GB download. Parakeet can't use it")
        # Set to CUDA without its files, the service runs on the processor,
        # so the dropdown shows CPU rather than claim CUDA.
        if self.config["device"] == "cuda" and not self._cuda_ready:
            self.config["device"] = "cpu"
            # Only the display changes: a saved "cuda" stays on disk unless
            # the user picks another device, so an unrelated OK can't drop
            # the choice while CUDA's files are missing for a while.
            if self._config_at_open.get("device") == "cuda":
                self._config_at_open["device"] = "cpu"

        # Any graphics card through Vulkan (whisper_vulkan.py): the only GPU
        # option on AMD / Intel, and the light one on NVIDIA. Offered when a
        # graphics chip is present, and kept if it is already the setting.
        try:
            from . import whisper_vulkan
            if whisper_vulkan.is_offered() or self.config["device"] == "vulkan":
                self.device_combo.append("vulkan", whisper_vulkan.DEVICE_LABEL)
                tooltip_text += ("\n• Vulkan (any GPU): AMD, Intel or NVIDIA. On NVIDIA about "
                                 f"as fast as CUDA for a {ENGINE_SIZE_TEXT} download (one-time, "
                                 "with a speed check). Also runs Parakeet on the graphics chip")
        except Exception as e:
            logger.warning(f"Could not check for Vulkan graphics: {e}")
        
        # Set active selection and tooltip
        self.device_combo.set_active_id(self.config["device"])
        self.device_combo.set_tooltip_text(tooltip_text)

    def _relabel_cuda_option(self, model):
        """Retell the CUDA entry for *model*: with Parakeet it runs on the
        processor. A new label on the selected entry fires "changed", which
        brings this back here: writing only a label that differs ends that
        (it looped until Preferences crashed, Ron 2026-10-20)."""
        combo = getattr(self, "device_combo", None)
        if combo is None:
            return
        label = device_label("cuda", model, getattr(self, "_cuda_ready", False))
        # Nor may the new label read as picking CUDA: with Parakeet that asks
        # which Whisper model to use on CUDA, unasked.
        combo.handler_block_by_func(self._on_device_changed)
        try:
            for row in combo.get_model():
                if row[1] == "cuda" and row[0] != label:   # ComboBoxText rows are (text, id)
                    row[0] = label
        finally:
            combo.handler_unblock_by_func(self._on_device_changed)

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

        general_tab = self.create_general_tab()
        notebook.append_page(self._scrollable_tab(general_tab), Gtk.Label(label="General"))

        self.audio_tab = AudioTab(self)
        notebook.append_page(self._scrollable_tab(self.audio_tab.build()),
                             Gtk.Label(label="Audio"))

        advanced_tab = self.create_advanced_tab()
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

    def create_general_tab(self):
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_margin_start(20)
        grid.set_margin_end(20)
        grid.set_margin_top(20)
        grid.set_margin_bottom(20)

        row = 0

        # ===== DICTATION KEYS SECTION =====
        # First on the first tab: the keys are the one thing most people change.
        hotkey_header = Gtk.Label()
        hotkey_header.set_markup('<b>Dictation Keys</b>')
        hotkey_header.set_xalign(0)
        hotkey_header.set_margin_bottom(10)
        grid.attach(hotkey_header, 0, row, 2, 1)
        row += 1

        # Hold-to-talk hotkey
        self.hotkey_label = Gtk.Label(label="Hold Hotkey 💡:", xalign=0)
        self.hotkey_label.set_tooltip_text("Hold this key while speaking — release to stop and transcribe.\nBoth hotkeys are always active simultaneously.\nRecommended: F8 (default). Avoid: F1, F5, F11, F12.\nF-keys that need Fn held? Right Ctrl, Right Alt and Scroll Lock don't.")
        grid.attach(self.hotkey_label, 0, row, 1, 1)
        hotkeys = dropdown_entries()
        self.hotkey_combo = self._hotkey_combo(self.hotkey_label, hotkeys)
        # Selection for both combos is set once the toggle combo exists, by
        # _apply_hotkey_fallbacks — see there for why the fallback must also
        # be written back into self.config.

        self.hotkey_combo.connect("changed", lambda x: self.update_config("hotkey", x.get_active_id()))
        grid.attach(self.hotkey_combo, 1, row, 1, 1)
        row += 1
        
        # Tap-to-toggle hotkey (active alongside the hold key)
        self.toggle_label = Gtk.Label(label="Toggle Hotkey 💡:", xalign=0)
        self.toggle_label.set_tooltip_text("Press once to start recording, press again to stop.\n"
                                           "Double-tap it for hands-free (the option under this row):\n"
                                           "the recording stops by itself when you stop talking.\n"
                                           "Both hotkeys are always active simultaneously.\nRecommended: F9 (default).")
        self.toggle_combo = self._hotkey_combo(self.toggle_label, hotkeys)
        # Both combos exist now, so set their selections together.
        self._apply_hotkey_fallbacks(HOTKEY_CHOICES)

        self.toggle_combo.connect("changed", lambda x: self.update_config("toggle_hotkey", x.get_active_id()))
        grid.attach(self.toggle_label, 0, row, 1, 1)
        grid.attach(self.toggle_combo, 1, row, 1, 1)
        row += 1

        # Hands-free: double-tap the toggle key and the recording stops itself
        # when you go quiet (silence.py). A single tap stays a plain toggle.
        # Shown on the Flatpak too: its toggle shortcut double-taps the same way.
        auto_stop_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        auto_stop_check = Gtk.CheckButton(label=hands_free_label(self.config.get("toggle_hotkey")))
        # Says the key itself, and follows the dropdown above.
        self.toggle_combo.connect("changed", lambda c: auto_stop_check.set_label(
            hands_free_label(c.get_active_id())))
        auto_stop_check.set_active(bool(self.config.get("auto_stop_silence", False)))
        auto_stop_adj = Gtk.Adjustment(value=float(self.config.get("auto_stop_seconds", 2.0)),
                                       lower=0.5, upper=10.0, step_increment=0.5)
        auto_stop_spin = Gtk.SpinButton(adjustment=auto_stop_adj, digits=1)
        auto_stop_spin.connect("scroll-event", lambda *a: True)  # no accidental scroll changes
        auto_stop_spin.set_sensitive(auto_stop_check.get_active())
        auto_stop_check.connect("toggled", lambda x: (
            self.update_config("auto_stop_silence", x.get_active()),
            auto_stop_spin.set_sensitive(x.get_active())))
        auto_stop_spin.connect("value-changed",
                               lambda x: self.update_config("auto_stop_seconds", round(x.get_value(), 1)))
        auto_stop_box.pack_start(auto_stop_check, False, False, 0)
        auto_stop_box.pack_start(auto_stop_spin, False, False, 0)
        auto_stop_box.pack_start(Gtk.Label(label="seconds of silence"), False, False, 0)
        auto_stop_box.set_tooltip_text(
            "Double-tap your toggle key (two quick taps), talk, and when you stop\n"
            "talking the recording ends and your text is typed by itself. A short\n"
            "high beep confirms the double-tap.\n\n"
            "A single tap is still a plain on/off toggle that runs until you tap\n"
            "again, however long you pause. Hands-free waits until you have actually\n"
            "started talking, and a pause shorter than this setting does not stop it.\n"
            "Hold-to-talk is not affected.")
        # Indented under the toggle key's row, which it belongs to.
        auto_stop_box.set_margin_start(28)
        grid.attach(auto_stop_box, 0, row, 2, 1)
        row += 1

        # Test Hotkeys button
        test_hotkeys_btn = Gtk.Button(label="Test Hotkeys")
        test_hotkeys_btn.connect("clicked", self._on_test_hotkeys)
        test_hotkeys_btn.get_child().set_xalign(0.5)
        test_hotkeys_btn.set_tooltip_text("Test your configured hotkeys to make sure they work")
        test_hotkeys_btn.set_halign(Gtk.Align.START)
        test_hotkeys_btn.set_margin_top(10)
        grid.attach(test_hotkeys_btn, 0, row, 2, 1)
        row += 1

        # On the Flatpak the dictation keys are owned by the desktop's
        # global-shortcuts portal, not this config: the combos above show the
        # (irrelevant) config keys, editing them does nothing, and Test Hotkeys
        # uses evdev, which the sandbox blocks. Hide the editable controls and
        # point the user to where the keys actually live. self.hotkey_combo /
        # self.toggle_combo still exist (hidden) so the save/apply paths that
        # read them keep working — their values simply go unused there.
        if os.environ.get("FLATPAK_ID"):
            from .desktop_detect import flatpak_hotkey_hint
            for w in (self.hotkey_label, self.hotkey_combo,
                      self.toggle_label, self.toggle_combo, test_hotkeys_btn):
                w.set_no_show_all(True)
                w.hide()
            flatpak_note = Gtk.Label()
            flatpak_note.set_markup(
                "🔒 <b>Dictation keys are set by your desktop, not here.</b>\n"
                + flatpak_hotkey_hint()
            )
            flatpak_note.set_xalign(0)
            flatpak_note.set_line_wrap(True)
            flatpak_note.set_margin_top(4)
            grid.attach(flatpak_note, 0, row, 2, 1)
            row += 1

            # On KDE, typing via the remote-control portal makes a "Remote control
            # session started" popup fire after every dictation. Give the user a
            # button that reopens the persistent, always-on-top steps for turning
            # it off — the same window shown once after onboarding, available here
            # whenever they decide to deal with it.
            from .desktop_detect import get_desktop_environment
            if get_desktop_environment() == "kde":
                from .notification_help import show_notification_help_window
                notif_btn = Gtk.Button(
                    label="🔔 Turn off the “remote control” notification…")
                notif_btn.get_child().set_xalign(0.0)
                notif_btn.set_halign(Gtk.Align.START)
                notif_btn.set_margin_top(8)
                notif_btn.set_tooltip_text(
                    "KDE shows a popup after every dictation. Opens step-by-step "
                    "instructions in a window that stays on top while you do it.")
                notif_btn.connect(
                    "clicked", lambda _b: show_notification_help_window())
                grid.attach(notif_btn, 0, row, 2, 1)
                row += 1

        # The two extra shortcuts work out of the box; folded so the section
        # shows only the keys people dictate with.
        self.shortcuts_fold = self._fold(grid, row, "Show more shortcuts",
                                         "Hide more shortcuts")
        shortcuts = self.shortcuts_fold.content
        row += 1

        # Voice Commands hotkey (optional combo shortcut to open quick reference)
        vc_label = Gtk.Label(label="Commands cheat sheet:", xalign=0)
        vc_label.set_tooltip_text(
            "Press this key combo to pop up a quick reference of all voice commands.\n"
            "Leave set to 'None' to disable the shortcut.\n"
            "The reference is also available from the tray menu."
        )
        shortcuts.attach(vc_label, 0, 0, 1, 1)

        self.vc_hotkey_combo = Gtk.ComboBoxText()
        self.vc_hotkey_combo.set_can_focus(True)
        self.vc_hotkey_combo.connect("button-press-event", self._on_combo_button_press)
        self._block_combo_scroll(self.vc_hotkey_combo)

        # Pre-defined combos that avoid common system/app conflicts
        # Ctrl+Shift combos conflict with browsers; Super+H is GNOME minimize
        vc_combos = [
            ("", "None (disabled)"),
            ("Ctrl+Alt+V", "Ctrl+Alt+V (recommended)"),
            ("Ctrl+Alt+H", "Ctrl+Alt+H"),
            ("Ctrl+Super+V", "Ctrl+Super+V"),
            ("Ctrl+Super+H", "Ctrl+Super+H"),
        ]
        for combo_id, combo_label in vc_combos:
            self.vc_hotkey_combo.append(combo_id, combo_label)

        current_vc = self.config.get("voice_commands_hotkey", "")
        # If the saved value matches a known combo, select it; otherwise default to None
        known_ids = [c[0] for c in vc_combos]
        if current_vc in known_ids:
            self.vc_hotkey_combo.set_active_id(current_vc)
        else:
            self.vc_hotkey_combo.set_active_id("")  # None

        self.vc_hotkey_combo.connect("changed", lambda x: (self.update_config(
            "voice_commands_hotkey", x.get_active_id() or ""
        ), self._update_shortcuts_summary()))

        shortcuts.attach(self.vc_hotkey_combo, 1, 0, 1, 1)

        # Paste Last Dictation hotkey: types the last dictation again, for when
        # it went to the wrong window (app._paste_last_dictation).
        pl_label = Gtk.Label(label="Paste Last Dictation:", xalign=0)
        pl_label.set_tooltip_text(
            "Press this key combo to type your last dictation again where the cursor is,\n"
            "for when it went to the wrong window. It types once you let go of the keys.\n"
            "Leave set to 'None' to turn it off.")
        shortcuts.attach(pl_label, 0, 1, 1, 1)

        self.pl_hotkey_combo = Gtk.ComboBoxText()
        self.pl_hotkey_combo.set_can_focus(True)
        self.pl_hotkey_combo.connect("button-press-event", self._on_combo_button_press)
        self._block_combo_scroll(self.pl_hotkey_combo)
        pl_combos = [
            ("", "None (disabled)"),
            ("Ctrl+Alt+P", "Ctrl+Alt+P (recommended)"),
            ("Ctrl+Super+P", "Ctrl+Super+P"),
        ]
        for combo_id, combo_label in pl_combos:
            self.pl_hotkey_combo.append(combo_id, combo_label)
        current_pl = self.config.get("paste_last_hotkey", "")
        self.pl_hotkey_combo.set_active_id(
            current_pl if current_pl in [c[0] for c in pl_combos] else "")
        self.pl_hotkey_combo.connect("changed", lambda x: (self.update_config(
            "paste_last_hotkey", x.get_active_id() or ""
        ), self._update_shortcuts_summary()))
        shortcuts.attach(self.pl_hotkey_combo, 1, 1, 1, 1)
        self._update_shortcuts_summary()

        # ===== SPEECH RECOGNITION SECTION =====
        # One line for the setup first run picked; the dropdowns behind it are
        # for the few who want something else.
        row = self._section_header(grid, row, "Speech Recognition")

        self.setup_summary = Gtk.Label(xalign=0)
        self.setup_summary.set_line_wrap(True)
        grid.attach(self.setup_summary, 0, row, 2, 1)
        row += 1

        # The setup first run recommends, one click away (Tray → Performance →
        # Recommended does the same), so trying other settings has an easy way back.
        rec_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.recommended_label = Gtk.Label(xalign=0)
        self.recommended_label.set_line_wrap(True)
        rec_row.pack_start(self.recommended_label, True, True, 0)
        self.use_recommended_btn = Gtk.Button(label="Use Recommended")
        self.use_recommended_btn.connect("clicked", self._on_use_recommended_clicked)
        # Shown or hidden by _update_recommended_row; the window's show_all()
        # would otherwise bring it back next to the tick.
        self.use_recommended_btn.set_no_show_all(True)
        rec_row.pack_end(self.use_recommended_btn, False, False, 0)
        grid.attach(rec_row, 0, row, 2, 1)
        row += 1

        # Parakeet Ultra for someone still on the original Parakeet: who said
        # Not Now to the tray's one-time offer, or came back to Parakeet later.
        # Hidden otherwise (_update_parakeet_row).
        update_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        update_label = Gtk.Label(label="A more accurate Parakeet is available.", xalign=0)
        update_label.show()
        update_row.pack_start(update_label, True, True, 0)
        self.parakeet_update_btn = Gtk.Button()
        self.parakeet_update_btn.connect("clicked", self._on_update_parakeet_clicked)
        self.parakeet_update_btn.show()
        update_row.pack_end(self.parakeet_update_btn, False, False, 0)
        update_row.set_no_show_all(True)
        self.parakeet_update_row = update_row
        grid.attach(update_row, 0, row, 2, 1)
        row += 1

        # A warning for model/device/language combinations that don't do what
        # they say: Parakeet with CUDA runs on the processor, and Parakeet
        # ignores a language it doesn't know. Outside the fold, so a closed
        # fold can't hide it. Hidden while there's nothing to say.
        self.model_combo_hint = Gtk.Label(xalign=0)
        self.model_combo_hint.set_line_wrap(True)
        self.model_combo_hint.set_max_width_chars(60)
        self.model_combo_hint.set_no_show_all(True)
        grid.attach(self.model_combo_hint, 0, row, 2, 1)
        row += 1

        # No summary: the bold line above already says how it's set.
        setup = self._fold(grid, row, "Change model, device or language",
                           "Hide model, device and language").content
        row += 1
        srow = 0

        # Which models run on which device: too many combinations to remember.
        compat_btn = Gtk.Button(label="What works with what?")
        compat_btn.set_halign(Gtk.Align.START)
        compat_btn.connect("clicked", self._show_compatibility)
        setup.attach(compat_btn, 0, srow, 2, 1)
        srow += 1

        # Model selection
        model_label = Gtk.Label(label="Model 💡:", xalign=0)
        model_label.set_tooltip_text(
            "Speech model - choose based on your needs:\n\n"
            "• Parakeet (670 MB): recommended. The most accurate for English\n"
            "  and 24 European languages, and fast even on the processor.\n"
            "  On a graphics card, set Device to Vulkan.\n\n"
            "Whisper models, for the other 75 or so languages:\n"
            "• tiny (78 MB): Fastest, basic accuracy - quick notes\n"
            "• base (148 MB): Fast, good accuracy - casual use\n"
            "• small (486 MB): Balanced - a good start on the processor\n"
            "• medium (1.5 GB): Slower, very accurate - professional use\n"
            "• large-v3 (3.1 GB): Whisper's best - needs a graphics card\n"
            "  (Sizes are for the processor; the Vulkan copies are about half.)\n"
            "  ⚠️ Takes 30-60 seconds to load initially\n\n"
            "Larger Whisper models provide:\n"
            "• Better word recognition (technical terms, proper nouns)\n"
            "• Improved punctuation and context awareness\n"
            "• Better handling of accents and background noise"
        )
        setup.attach(model_label, 0, srow, 1, 1)
        # CUDA/GPU availability decides what the large-v3 row says
        try:
            from . import cuda_helper as _ch
            _has_nvidia = self._has_nvidia()
            # has_cuda_libraries() also accepts a system-wide CUDA install —
            # same check the Device dropdown uses. (has_talktype_cuda_libraries
            # is only for download-status display, per its own docstring.)
            _has_cuda   = _ch.has_cuda_libraries()
        except Exception:
            _has_nvidia = False
            _has_cuda   = False

        # Model store columns: [model_id, display_text, is_sensitive]
        _MODEL_DISPLAY = {
            "tiny":     "tiny — fastest, lowest accuracy",
            "base":     "base — fast, basic accuracy",
            "small":    "small — good balance, 99 languages",
            "medium":   "medium — better accuracy",
            "large-v3": "large-v3 — best accuracy",
            "parakeet-v3": "Parakeet — recommended: most accurate, English + 24 European languages",
        }
        from .model_helper import OFFERED_MODELS

        self.model_store = Gtk.ListStore(str, str, bool)
        for _mid in OFFERED_MODELS:
            if _mid == "large-v3":
                if _has_cuda:
                    self.model_store.append([_mid, _MODEL_DISPLAY[_mid], True])
                elif _has_nvidia:
                    # Selectable — clicking it will offer CUDA download
                    self.model_store.append([_mid,
                        "large-v3 — click to download required CUDA libraries", True])
                else:
                    # Selectable. On AMD/Intel, clicking it offers Vulkan.
                    try:
                        from . import whisper_vulkan as _wv
                        _vk = _wv.is_offered()
                    except Exception:
                        _vk = False
                    self.model_store.append([_mid,
                        "large-v3 — Whisper's best, on your graphics card (Vulkan)" if _vk
                        else "large-v3 — needs a graphics card (none found)", True])
            else:
                self.model_store.append([_mid, _MODEL_DISPLAY[_mid], True])

        model_combo = Gtk.ComboBox.new_with_model(self.model_store)
        # Column 0 holds the model id — without this, set_active_id() is a
        # silent no-op (plain ComboBox has no id column by default).
        model_combo.set_id_column(0)
        model_combo.set_can_focus(True)
        self._block_combo_scroll(model_combo)
        _renderer = Gtk.CellRendererText()
        model_combo.pack_start(_renderer, True)
        model_combo.add_attribute(_renderer, "text", 1)
        model_combo.add_attribute(_renderer, "sensitive", 2)

        # Set active row to match current config model
        _current = self.config.get("model", Settings.model)
        for _i, _row in enumerate(self.model_store):
            if _row[0] == _current:
                model_combo.set_active(_i)
                break
        else:
            model_combo.set_active(OFFERED_MODELS.index(Settings.model))  # the default

        self.model_combo = model_combo  # Store reference for later use
        model_combo.connect("changed", self._on_model_changed)
        setup.attach(model_combo, 1, srow, 1, 1)
        srow += 1
        
        # Device selection
        setup.attach(Gtk.Label(label="Device:", xalign=0), 0, srow, 1, 1)
        device_combo = Gtk.ComboBoxText()
        device_combo.set_can_focus(True)
        device_combo.connect("button-press-event", self._on_combo_button_press)
        self._block_combo_scroll(device_combo)

        # Store device combo for later refresh
        self.device_combo = device_combo
        
        # Populate device options
        self._refresh_device_options()
        
        self._device_before = self.config.get("device", "cpu")
        device_combo.connect("changed", self._on_device_changed)
        setup.attach(device_combo, 1, srow, 1, 1)
        srow += 1

        # Language: one dropdown for what used to be Language Mode, Language
        # and Only these languages (language_choice maps the three settings).
        lang_label = Gtk.Label(label="Language 💡:", xalign=0)
        lang_label.set_tooltip_text(
            "The language you speak.\n"
            "Detect automatically works for most people. Picking your language\n"
            "makes it a little faster, and it never takes you for another one.\n"
            "Several languages: it picks only from the ones you tick (Whisper models).")
        setup.attach(lang_label, 0, srow, 1, 1)
        self.lang_combo = Gtk.ComboBoxText()
        self.lang_combo.set_can_focus(True)
        self.lang_combo.connect("button-press-event", self._on_combo_button_press)
        self._block_combo_scroll(self.lang_combo)
        self._lang_choice = language_choice(self.config)
        self._fill_language_choices(_current)
        self.lang_combo.connect("changed", self._on_language_choice_changed)
        # The tooltip is on the label: on the combo it gets in the way of the popup
        # Picking the entry already selected does nothing, so the list of
        # several languages gets its own button, shown only with that entry.
        self.several_button = Gtk.Button(label="Change list\u2026")
        self.several_button.set_no_show_all(True)
        self.several_button.connect("clicked", lambda _b: self._pick_several_languages())
        lang_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lang_row.pack_start(self.lang_combo, True, True, 0)
        lang_row.pack_start(self.several_button, False, False, 0)
        setup.attach(lang_row, 1, srow, 1, 1)

        for _combo in (self.model_combo, self.device_combo, self.lang_combo):
            _combo.connect("changed", lambda *_: self._update_model_combo_hint())
        self._update_model_combo_hint()

        # ===== STARTUP OPTIONS SECTION =====
        row = self._section_header(grid, row, "Startup Options")

        # Launch at login checkbox
        self.launch_at_login_check = Gtk.CheckButton(label="Launch TuxTalk at login")
        self.launch_at_login_check.set_active(self.config.get("launch_at_login", False))
        self.launch_at_login_check.connect("toggled", lambda x: self.update_config("launch_at_login", x.get_active()))
        self.launch_at_login_check.set_tooltip_text("Automatically start TuxTalk when you log in to your desktop.\nUseful for having dictation always available.")
        grid.attach(self.launch_at_login_check, 0, row, 2, 1)
        row += 1

        # ===== POWER SAVING SECTION =====
        power_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        power_sep.set_margin_top(20)
        power_sep.set_margin_bottom(15)
        grid.attach(power_sep, 0, row, 2, 1)
        row += 1

        power_header = Gtk.Label()
        power_header.set_markup('<b>Power Saving</b>')
        power_header.set_xalign(0)
        power_header.set_margin_bottom(10)
        grid.attach(power_header, 0, row, 2, 1)
        row += 1

        self.auto_timeout_check = Gtk.CheckButton(
            label="Save power when dictation isn't used for a while")
        self.auto_timeout_check.set_active(self.config.get("auto_timeout_enabled", False))
        self.auto_timeout_check.connect("toggled", lambda x: self.update_config("auto_timeout_enabled", x.get_active()))
        self.auto_timeout_check.connect("toggled", self._on_auto_timeout_toggled)
        self.auto_timeout_check.set_tooltip_text(
            "Saves battery on a laptop: after the minutes below without dictating, the\n"
            "speech model lets go of the processor and the graphics card. Your keys keep\n"
            "working, and the next dictation loads it again.\n\n"
            "On by default on laptops, off on desktop computers.")
        grid.attach(self.auto_timeout_check, 0, row, 2, 1)
        row += 1

        # The minutes, and what happens then, indented under the tickbox and
        # hidden with it off (_update_timeout_ui_state).
        self.timeout_label = Gtk.Label(label="After", xalign=0)
        self.timeout_spin = Gtk.SpinButton()
        self.timeout_spin.set_range(1, 60)  # 1 to 60 minutes
        self.timeout_spin.set_increments(1, 5)
        self.timeout_spin.set_value(self.config.get("auto_timeout_minutes", 5))
        self.timeout_spin.connect("value-changed", lambda x: self.update_config("auto_timeout_minutes", int(x.get_value())))
        self.timeout_spin.connect("scroll-event", lambda *a: True)  # Disable scroll wheel to prevent accidental changes
        self.timeout_spin.set_tooltip_text("Minutes without dictating before the speech model rests.")
        timeout_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        timeout_row.set_margin_start(28)
        timeout_row.pack_start(self.timeout_label, False, False, 0)
        timeout_row.pack_start(self.timeout_spin, False, False, 0)
        self.timeout_minutes_label = Gtk.Label(label="minutes without dictating", xalign=0)
        timeout_row.pack_start(self.timeout_minutes_label, False, False, 0)
        grid.attach(timeout_row, 0, row, 2, 1)
        row += 1

        self.timeout_note = Gtk.Label(xalign=0)
        self.timeout_note.set_text(
            "Your keys keep working. The first dictation after a rest takes a few "
            "seconds longer while the speech model loads again.")
        self.timeout_note.set_line_wrap(True)
        self.timeout_note.set_max_width_chars(70)
        self.timeout_note.set_opacity(0.7)
        self.timeout_note.set_margin_start(28)
        grid.attach(self.timeout_note, 0, row, 2, 1)
        row += 1

        return grid
    
    def _swiss_spelling_check(self):
        """The "Swiss German spelling" checkbox, bound to swiss_spelling."""
        check = Gtk.CheckButton(label="Swiss German spelling (ß → ss)")
        check.set_active(bool(self.config.get("swiss_spelling", False)))
        check.connect("toggled", lambda x: self.update_config("swiss_spelling", x.get_active()))
        check.set_tooltip_text("Write ss wherever the speech model writes ß, as Swiss Standard German does\n"
                               "(Strasse, gross). On by default when the system is set to Swiss German.")
        return check

    def _numbers_option(self):
        """The Numbers dropdown: as the speech model writes them, as digits,
        or digits from 10 up. Three entries over the two settings
        numbers_as_digits and numbers_small_as_words (NUMBER_CHOICES)."""
        combo = Gtk.ComboBoxText()
        for nid, (nlabel, _digits, _small) in NUMBER_CHOICES.items():
            combo.append(nid, nlabel)
        combo.set_active_id(number_choice(self.config))
        combo.connect("scroll-event", lambda *a: True)  # no accidental scroll changes

        def changed(c):
            choice = c.get_active_id()
            if choice is None:
                return
            _label, digits, small = NUMBER_CHOICES[choice]
            self.update_config("numbers_as_digits", digits)
            if small is not None:
                self.update_config("numbers_small_as_words", small)
        combo.connect("changed", changed)

        label = Gtk.Label(label="Numbers 💡:", xalign=0)
        label.set_tooltip_text("Digits: \"twenty-five chickens\" becomes \"25 chickens\", \"october fifteenth\"\n"
                               "becomes \"october 15th\" and \"seven thirty\" after at becomes \"7:30\".\n"
                               "\"One\" and \"two\" on their own stay words (\"one of them\"), and so do fractions.\n"
                               "Many writing styles spell out one to nine and use digits from 10 up.\n"
                               "Works in English, German, Spanish, French, Italian, Portuguese, Dutch and Danish.")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.pack_start(label, False, False, 0)
        box.pack_start(combo, False, False, 0)
        return box

    def _send_automatically_option(self):
        """The "Send each dictation automatically" tickbox with its wait
        beneath it, greyed while the tickbox is off (send_countdown).
        Saying "press enter" at the end sends one dictation at once."""
        check = Gtk.CheckButton(label="Send each dictation automatically (press Enter)")
        check.set_active(bool(self.config.get("press_enter_after", False)))
        key = self.config.get("hotkey") or "the hotkey"
        check.set_tooltip_text(
            "After each dictation TuxTalk presses Enter for you: a chat message is sent,\n"
            "a terminal command runs. Not in text editors and word processors, where\n"
            "Enter would only start a new line.\n\n"
            f"During the wait, tap {key} to hold the send: the text stays in the box, so you\n"
            f"can fix it or add a screenshot, then press Enter yourself. Hold {key} instead\n"
            "to keep talking; that message is sent when you're done. A sent dictation\n"
            f"can't be changed with \u201cedit that\u201d, so tap {key} first if you want to.\n\n"
            "If what you said ends on a word like \u201cand\u201d or \u201cthe\u201d, it was probably\n"
            "cut off, so it isn't sent. Where TuxTalk can't tell which window has the focus\n"
            "(COSMIC, GNOME without TuxTalk's extension, Plasma 5.27 on Wayland), nothing\n"
            "is sent by itself.\n\n"
            "With this off, end any dictation with \u201cpress enter\u201d to send just that one.")

        waits = Gtk.ComboBoxText()
        for seconds in range(5):
            label = f"After {seconds} second{'s' * (seconds > 1)}" if seconds else "Right away"
            waits.append(str(seconds), label)
        current = int(round(float(self.config.get("press_enter_delay", 2.0))))
        waits.set_active_id(str(min(4, max(0, current))))
        waits.connect("changed", lambda c: self.update_config(
            "press_enter_delay", float(c.get_active_id())) if c.get_active_id() else None)
        waits.connect("scroll-event", lambda *a: True)  # no accidental scroll changes

        choice = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        choice.set_margin_start(28)   # under the tickbox's label, not its box
        send_label = Gtk.Label(label="Send:", xalign=0)
        send_label.set_mnemonic_widget(waits)     # a screen reader names the list by it
        choice.pack_start(send_label, False, False, 0)
        choice.pack_start(waits, False, False, 0)
        choice.set_sensitive(check.get_active())

        def toggled(button):
            self.update_config("press_enter_after", button.get_active())
            choice.set_sensitive(button.get_active())

        check.connect("toggled", toggled)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(check, False, False, 0)
        box.pack_start(choice, False, False, 0)
        return box

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

    def _update_shortcuts_summary(self):
        """The two keys beside the More shortcuts button, as they are now."""
        def key(combo):
            return combo.get_active_id() or "off"
        self.shortcuts_fold.set_summary(GLib.markup_escape_text(
            f"cheat sheet {key(self.vc_hotkey_combo)}"
            f" \u00b7 paste last {key(self.pl_hotkey_combo)}"))

    def _subheading(self, text):
        """A small heading inside a section or fold."""
        label = Gtk.Label(xalign=0)
        label.set_markup(f'<span weight="600">{GLib.markup_escape_text(text)}</span>')
        label.set_margin_top(6)
        return label

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

    def create_advanced_tab(self):
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_margin_start(20)
        grid.set_margin_end(20)
        grid.set_margin_top(20)
        grid.set_margin_bottom(20)

        row = 0

        # ===== TEXT SECTION =====
        # The text options people turn on. The ones that already do the right
        # thing by themselves are folded into Fine-tuning below.
        text_header = Gtk.Label()
        text_header.set_markup('<b>Text</b>')
        text_header.set_xalign(0)
        text_header.set_margin_bottom(10)
        grid.attach(text_header, 0, row, 2, 1)
        row += 1

        grid.attach(self._send_automatically_option(), 0, row, 2, 1)
        row += 1

        grid.attach(self._numbers_option(), 0, row, 2, 1)
        row += 1

        fillers_check = Gtk.CheckButton(label="Remove \u201cum\u201d, \u201cuh\u201d and accidentally repeated words")
        fillers_check.set_active(bool(self.config.get("remove_fillers", False)))
        fillers_check.connect("toggled", lambda x: self.update_config("remove_fillers", x.get_active()))
        fillers_check.set_tooltip_text(
            "\u201cUm, so the the van is ready\u201d becomes \u201cSo the van is ready\u201d.\n\n"
            "Instant, and nothing to download. It only ever removes um/uh and words\n"
            "repeated right next to each other; doubles people mean, like\n"
            "\u201cthat that\u201d or \u201chad had\u201d, are left alone.")
        grid.attach(fillers_check, 0, row, 2, 1)
        row += 1

        self.ai_corrections_check = Gtk.CheckButton(
            label="Fix self-corrections with AI (\u201cat 3, no wait, 4\u201d \u2192 \u201cat 4\u201d)")
        # Shown ticked only if its download is actually present. If the files
        # went missing, an unticked box is the truth, and ticking it downloads
        # them again (see _on_ai_corrections_toggled).
        from tuxtalk import ai_cleanup
        self.ai_corrections_check.set_active(
            bool(self.config.get("ai_corrections", False)) and ai_cleanup.is_installed())
        self.ai_corrections_check.set_tooltip_text(
            "When you correct yourself mid-sentence (\u201cno wait\u201d, \u201cI mean\u201d, \u201cscratch that\u201d),\n"
            "a small AI model keeps only what you meant.\n\n"
            "Runs entirely on this computer, using your graphics card if you have one.\n"
            "Only sentences with a correction phrase are sent to it, and its answer is\n"
            "checked: if it changed anything besides the correction, your words are\n"
            "typed exactly as spoken instead.\n\n"
            "Needs a one-time download of about 1.1 GB and roughly 1.5 GB of memory while on.")
        self.ai_corrections_check.connect("toggled", self._on_ai_corrections_toggled)
        grid.attach(self.ai_corrections_check, 0, row, 2, 1)
        row += 1

        ai_note = Gtk.Label(xalign=0)
        ai_note.set_markup('<span>    Runs on your computer, nothing is sent online. '
                           'One-time download of about 1.1 GB.</span>')
        ai_note.set_opacity(0.7)
        grid.attach(ai_note, 0, row, 2, 1)
        row += 1

        # ===== FINE-TUNING =====
        # Each of these already does the sensible thing; they're here for the
        # day something looks off.
        fine_fold = self._fold(grid, row, "Show fine-tuning", "Hide fine-tuning")
        fine_fold.set_summary("formatting, inserting text, privacy")
        fine = fine_fold.content
        row += 1
        frow = 0

        fine.attach(self._subheading("Formatting"), 0, frow, 2, 1)
        frow += 1
        # Smart quotes
        quotes_check = Gtk.CheckButton(label="Use smart quotes (\u201c \u201d)")
        quotes_check.set_active(self.config["smart_quotes"])
        quotes_check.connect("toggled", lambda x: self.update_config("smart_quotes", x.get_active()))
        quotes_check.set_tooltip_text("Convert spoken quotes to curved 'smart quotes' instead of straight \"quotes\".\nSay 'open quote' and 'close quote' when dictating.")
        fine.attach(quotes_check, 0, frow, 2, 1)
        frow += 1
        
        if swiss_spelling_offered(self.config):
            fine.attach(self._swiss_spelling_check(), 0, frow, 2, 1)
            frow += 1

        # Auto space
        space_check = Gtk.CheckButton(label="Auto-space between utterances")
        space_check.set_active(self.config["auto_space"])
        space_check.connect("toggled", lambda x: self.update_config("auto_space", x.get_active()))
        space_check.set_tooltip_text("Automatically add a space before each new dictation.\nTurn off if you want to control spacing manually.")
        fine.attach(space_check, 0, frow, 2, 1)
        frow += 1
        
        # Auto period
        period_check = Gtk.CheckButton(label="Ensure period at end of sentences")
        period_check.set_active(bool(self.config.get("auto_period", True)))  # Ensure boolean, default True
        period_check.connect("toggled", lambda x: self.update_config("auto_period", x.get_active()))
        period_check.set_tooltip_text("Ensure every sentence ends with punctuation.\n\nThe speech model usually adds periods by itself, but sometimes misses one.\nThis option adds the period when it does, so punctuation stays consistent.\n\nUnchecked: no period is added at the end; spoken commands and capitals still apply\nChecked: every sentence ends with punctuation")
        fine.attach(period_check, 0, frow, 2, 1)
        frow += 1

        # No closing period in terminals and code editors, no starting capital
        # in terminals (window_classes.app_kind, normalize.plain_for_app).
        plain_check = Gtk.CheckButton(label="Plain text in terminals and code editors")
        plain_check.set_active(bool(self.config.get("adapt_to_app", True)))
        plain_check.connect("toggled", lambda x: self.update_config("adapt_to_app", x.get_active()))
        plain_check.set_tooltip_text(
            "In a terminal, a dictation starts without a capital and ends without a period,\n"
            "so \u201cgit status\u201d is typed as git status, not Git status.\n"
            "In code editors (VS Code, Zed, JetBrains IDEs...) no period is added at the end.\n\n"
            "Everywhere else, including chat apps, punctuation is as usual.")
        fine.attach(plain_check, 0, frow, 2, 1)
        frow += 1

        # Injection mode
        inject_label = Gtk.Label(label="Text Injection 💡:", xalign=0)
        inject_label.set_tooltip_text("How to insert transcribed text:\n• Auto (Smart Detection): chooses paste or typing based on the focused app\n• Keystroke Typing: simulates typing character-by-character (most reliable)\n• Clipboard Paste: uses clipboard + Ctrl+V / Shift+Ctrl+V (much faster for long text)\n\nAuto mode uses paste for terminals and normal text fields, and falls back to\ntyping where paste is unreliable.\n\nTuxTalk doesn't type into password fields it can recognise: most GTK apps,\nKDE apps and Firefox with accessibility on, and the desktop's own password prompt.\n\nClipboard Paste works in most applications:\n✓ Text editors, word processors, terminals\n✓ Web browsers, email clients\n✓ Most standard text input fields\n\nUse Keystroke Typing if paste doesn't work in specialized applications.")
        inject_combo = Gtk.ComboBoxText()
        inject_combo.set_can_focus(True)
        inject_combo.connect("button-press-event", self._on_combo_button_press)
        self._block_combo_scroll(inject_combo)

        inject_combo.append("auto", "Auto (Smart Detection)")
        inject_combo.append("type", "Keystroke Typing")
        inject_combo.append("paste", "Clipboard Paste")
        inject_combo.set_active_id(self.config["injection_mode"])
        inject_combo.connect("changed", lambda x: self.update_config("injection_mode", x.get_active_id()))
        # The tooltip is on the label: on the combo it gets in the way of the popup
        # Host only: the Flatpak always types through the libei portal, so
        # Auto/Type/Paste don't apply — don't show the selector there.
        if not os.environ.get("FLATPAK_ID"):
            fine.attach(self._subheading("Inserting text"), 0, frow, 2, 1)
            frow += 1
            # The dropdown sits right beside its label at its own width, so
            # the two read as one setting.
            inject_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            inject_row.pack_start(inject_label, False, False, 0)
            inject_row.pack_start(inject_combo, False, False, 0)
            fine.attach(inject_row, 0, frow, 2, 1)
            frow += 1

            # Paste puts the dictation on the clipboard, replacing whatever was
            # copied (issue #7). Off by default: a dictation left there can be
            # re-pasted if it went into the wrong window.
            restore_check = Gtk.CheckButton(label="Put back what I'd copied after pasting a dictation")
            restore_check.set_active(bool(self.config.get("restore_clipboard", False)))
            restore_check.connect("toggled", lambda x: self.update_config("restore_clipboard", x.get_active()))
            restore_check.set_tooltip_text(
                "Pasting a dictation puts it on the clipboard, replacing whatever you'd copied.\n"
                "With this on, TuxTalk puts your copied item back half a second after pasting.\n\n"
                "Leave it off if you like to re-paste a dictation that went into the wrong window.\n"
                "Recent Dictations in the tray menu keeps your last 20 either way.\n\n"
                "Dictations are always kept out of clipboard history (like KDE's Klipper).")
            fine.attach(restore_check, 0, frow, 2, 1)
            frow += 1


        fine.attach(self._subheading("Privacy"), 0, frow, 2, 1)
        frow += 1
        # Transcript logging is OFF by default so nothing you dictate is written
        # to disk. Enabling it is gated by a warning (see _on_log_transcripts_toggled).
        self.log_transcripts_check = Gtk.CheckButton(label="Log transcribed text (for troubleshooting)")
        self.log_transcripts_check.set_active(bool(self.config.get("log_transcripts", False)))
        self.log_transcripts_check.set_tooltip_text(
            "Off by default for your privacy — TuxTalk does not write what you dictate to its log.\n"
            "Turn this on only when troubleshooting a dictation problem: it saves the full text of\n"
            "everything you dictate to TuxTalk's log file on disk, in plain text, until you turn it off."
        )
        self.log_transcripts_check.connect("toggled", self._on_log_transcripts_toggled)
        fine.attach(self.log_transcripts_check, 0, frow, 2, 1)
        frow += 1

        # Clear whatever is already on disk — the log holds past transcriptions
        # until it rolls over, so give the user a one-click wipe.
        clear_log_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        clear_log_btn = Gtk.Button(label="Clear log now")
        clear_log_btn.set_halign(Gtk.Align.START)
        clear_log_btn.set_tooltip_text("Erase the current TuxTalk log file (and its backup) from disk.")
        clear_log_btn.connect("clicked", self._on_clear_log_clicked)
        clear_log_box.pack_start(clear_log_btn, False, False, 0)
        fine.attach(clear_log_box, 0, frow, 2, 1)
        frow += 1


        self.system_check = SystemCheck(self)
        row = self.system_check.build(grid, row)

        # ===== TUXTALK LABS =====
        # Experimental features: they work but are still being
        # refined, so they live here with a note saying so, the Help page about
        # them, and a feedback link. Hidden in the Flatpak: its portal input
        # can't see Shift or send Ctrl+C, so none of them work there yet.
        if not os.environ.get("FLATPAK_ID"):
            labs_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            labs_sep.set_margin_top(20)
            labs_sep.set_margin_bottom(15)
            grid.attach(labs_sep, 0, row, 2, 1)
            row += 1

            labs_header = Gtk.Label(xalign=0)
            labs_header.set_markup('<b>🧪 TuxTalk Labs</b>')
            labs_header.set_margin_bottom(4)
            grid.attach(labs_header, 0, row, 2, 1)
            row += 1

            labs_note = Gtk.Label(xalign=0)
            labs_note.set_markup(
                '<span>Experimental features we’re still shaping, so they may change '
                'and won’t always get it right. If you try one, we’d love to hear how it '
                'went or what you’d change: use Send feedback below. Your ideas help make '
                'TuxTalk better.</span>')
            labs_note.set_line_wrap(True)
            labs_note.set_opacity(0.7)
            labs_note.set_margin_bottom(8)
            grid.attach(labs_note, 0, row, 2, 1)
            row += 1

            # Each Labs feature: a small heading, its options indented under it.
            def labs_heading(text):
                label = Gtk.Label(xalign=0)
                label.set_markup(f'<span weight="600">{text}</span>')
                label.set_margin_top(6)
                return label

            LABS_INDENT = 20
            grid.attach(labs_heading("Editing by voice"), 0, row, 2, 1)
            row += 1

            # Voice editing (app._transcribe_and_edit): the AI that fixes
            # self-corrections rewrites the selected text the way the user says.
            self.voice_editing_check = Gtk.CheckButton(
                label="Voice editing: select text, hold Shift with your dictation key, "
                      "say how to change it")
            self.voice_editing_check.set_active(
                bool(self.config.get("voice_editing", False)) and ai_cleanup.is_installed())
            self.voice_editing_check.set_tooltip_text(
                "Select some text in any app, hold Shift+F8 (Shift with your hold key, or tap\n"
                "Shift with your toggle key), and say what you want: “make this more formal”,\n"
                "“shorten this”, “fix the grammar”, “turn this into a bullet list”.\n"
                "The selection is replaced with the rewrite. Ctrl+Z puts it back.\n"
                "Or, right after dictating, say “edit that, make it shorter”: no keys needed,\n"
                "handy with hands-free and Type at pause.\n\n"
                "Uses the same small AI as self-corrections (one shared 1.1 GB download),\n"
                "on this computer. Simple, clear changes work best.")
            self.voice_editing_check.connect("toggled", self._on_voice_editing_toggled)
            self.voice_editing_check.set_margin_start(LABS_INDENT)
            grid.attach(self.voice_editing_check, 0, row, 2, 1)
            row += 1

            # Select and move by voice (voice_navigation.py): a dictation that
            # is only "select the last sentence" or "go to the end" is done as
            # a command. Works with or without voice editing.
            nav_check = Gtk.CheckButton(
                label="Select and move: say \u201cselect the last sentence\u201d, "
                      "\u201cgo to the end\u201d")
            nav_check.set_active(bool(self.config.get("voice_navigation", False)))
            nav_check.set_tooltip_text(
                "When a dictation is only one of these commands, TuxTalk does it instead of\n"
                "typing it. Inside a longer sentence the words are typed as usual.\n\n"
                "• Right after you dictate, on what you just said: “select that”,\n"
                "  “select the last sentence”, “select the last 3 words”\n"
                "• Around the cursor: “select all”, “select the line”,\n"
                "  “select the next word”, “select to the end of the line”\n"
                "• Moving: “go to the end”, “go to the start of the line”,\n"
                "  “go back 3 words”, “go up 2 lines”\n"
                "• “unselect”\n\n"
                "With voice editing on, the Shift key understands them too: Shift+F8\n"
                "“select the last sentence”, then Shift+F8 “make it more formal”.\n"
                "Not in terminals. The full list is in Help, on the TuxTalk Labs page.")
            nav_check.connect("toggled", lambda c: self.update_config(
                "voice_navigation", c.get_active()))
            nav_check.set_margin_start(LABS_INDENT)
            grid.attach(nav_check, 0, row, 2, 1)
            row += 1

            # Brings back the "how it works" windows of these two features that
            # were ticked "Don't explain this again" (explain.py).
            from . import explain
            tips_again = Gtk.Button(label="Show tips again")
            tips_again.set_tooltip_text(
                "Bring back the small windows that explain how voice editing and select\n"
                "and move work, for any you ticked \u201cDon't explain this again\u201d.")

            def _tips_again(button):
                explain.reset()
                button.set_label("\u2713 Tips will show again")
                button.set_sensitive(False)
            tips_again.connect("clicked", _tips_again)
            tips_again.set_halign(Gtk.Align.START)
            tips_again.set_margin_start(LABS_INDENT + 4)
            grid.attach(tips_again, 0, row, 2, 1)
            row += 1

            self.type_at_pause = TypeAtPause(self)
            row = self.type_at_pause.build(grid, row, LABS_INDENT, labs_heading)

            # Follows the window being dictated into (recording_indicator.
            # _follow_target), placed as the floating messages are.
            grid.attach(labs_heading("Recording indicator"), 0, row, 2, 1)
            row += 1
            follow_check = Gtk.CheckButton(
                label="Follows the window you\u2019re dictating into")
            follow_check.set_active(bool(self.config.get("indicator_follow_window", False)))
            follow_check.set_tooltip_text(
                "Shows the recording indicator in the window you're dictating into, wherever it\n"
                "is on the screen. The position set on the Audio tab (bottom center, top right...)\n"
                "applies inside that window; the offset doesn't. When TuxTalk can't tell where\n"
                "the window is, the usual position and offset are used.")
            follow_check.connect("toggled", lambda c: self.update_config(
                "indicator_follow_window", c.get_active()))
            follow_check.set_margin_start(LABS_INDENT)
            grid.attach(follow_check, 0, row, 2, 1)
            row += 1

            # How these work (Help, on its Labs tab) and Send feedback.
            from .help_dialog import FEEDBACK_URL, show_help_dialog
            labs_links = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            labs_links.set_margin_top(12)
            labs_help = Gtk.Button(label="How these work")
            labs_help.set_tooltip_text("Open Help at Writing, where Labs starts")
            labs_help.connect("clicked", lambda _b: show_help_dialog(tab="writing"))
            labs_links.pack_start(labs_help, False, False, 0)
            labs_feedback = Gtk.LinkButton.new_with_label(FEEDBACK_URL, "Send feedback")
            labs_feedback.set_tooltip_text("Tell us how the Labs features work for you "
                                           "(TuxTalk Discussions on GitHub)")
            labs_links.pack_start(labs_feedback, False, False, 0)
            grid.attach(labs_links, 0, row, 2, 1)
            row += 1

        return grid

    # --- Backup (settings_backup.py) ------------------------------------------

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

    def _hotkey_combo(self, label, hotkeys):
        """A hotkey dropdown of *hotkeys* [(key, its label)], which a screen
        reader names by *label*. The tooltip stays on the label: on the
        combo it gets in the way of the popup."""
        combo = Gtk.ComboBoxText()
        combo.set_can_focus(True)
        combo.connect("button-press-event", self._on_combo_button_press)
        self._block_combo_scroll(combo)
        for key, text in hotkeys:
            combo.append(key, text)
        label.set_mnemonic_widget(combo)
        return combo

    def _apply_hotkey_fallbacks(self, hotkeys):
        """Select the configured hotkeys, falling back to F8/F9 — and record
        that fallback in self.config, so what the dropdown shows is what OK saves.

        A blank hotkey (onboarding closed early) would otherwise show F8 but
        save the blank, and re-picking F8 emits no 'changed' to correct it.
        """
        for key, combo, default in (
            ("hotkey", self.hotkey_combo, "F8"),
            ("toggle_hotkey", self.toggle_combo, "F9"),
        ):
            current = self.config.get(key, default)
            chosen = current if current in hotkeys else default
            combo.set_active_id(chosen)
            self.config[key] = chosen

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
        # rebuilt (remove_all in _refresh_device_options). Writing that None
        # would produce 'device = None' in the TOML file — invalid TOML that
        # wipes ALL settings on the next load. Never a real user action.
        if value is None:
            return

        # (CUDA picked before its files are downloaded never gets here: the
        # Device dropdown offers the download instead, _on_device_changed.)
        self.config[key] = value
        # launch_at_login's autostart file is written by save_config(), not
        # here, so it changes only when the user saves and never on Cancel.

    def _update_model_combo_hint(self):
        """Show or hide the warning under the model settings."""
        label = getattr(self, "model_combo_hint", None)
        # Refilling the Language dropdown fires its "changed", which lands
        # here again before the refill is done: the call that started it
        # finishes the job. Without this, switching between Parakeet and
        # Whisper looped until Preferences crashed (Ron, 2026-10-20).
        if label is None or getattr(self, "_filling_languages", False):
            return
        it = self.model_combo.get_active_iter()
        model = self.model_store.get_value(it, 0) if it is not None else ""
        self._relabel_cuda_option(model)
        if (model == "parakeet-v3") != (self._languages_for_model == "parakeet-v3"):
            self._fill_language_choices(model)
        self.several_button.set_visible(self._lang_choice == SEVERAL_LANGUAGES)
        self._update_recommended_row(model)
        from .recommend import language_name
        from . import language_limit
        code = self.config.get("language", "")
        mode = self.config.get("language_mode", "auto")
        message = model_setting_warning(
            model, self.device_combo.get_active_id() or "", mode,
            code, language_name(code),
            language_limit.parse(self.config.get("auto_languages", "")))
        if message:
            # Amber and bold, so it can't be missed.
            label.set_markup(f'<span color="{WARNING_AMBER}" weight="bold">⚠️ '
                             f'{GLib.markup_escape_text(message)}</span>')
            label.show()
        else:
            label.hide()

    # --- Recommended setup and "What works with what?" (recommend.py) ---------

    def _recommendation(self):
        """(hardware, the setup recommend.py picks) for the language chosen in
        this window right now, not only the saved one."""
        from . import recommend
        shown = self._shown_language()
        hw = recommend.detect_hardware(shown)
        return hw, recommend.recommend(recommend.effective_language(shown), hw)

    def _update_recommended_row(self, model):
        """'Recommended for this computer: …  [Use Recommended]', or a tick
        once the dropdowns already show it."""
        label = getattr(self, "recommended_label", None)
        if label is None:
            return
        from . import recommend
        try:
            hw, rec = self._recommendation()
        except Exception as e:
            logger.warning(f"Could not work out the recommended setup: {e}")
            label.get_parent().set_no_show_all(True)
            label.get_parent().hide()
            return
        self.setup_summary.set_markup("<b>{}</b> \u00b7 {}".format(
            GLib.markup_escape_text(recommend.setup_title(
                model, self.device_combo.get_active_id() or "cpu", hw)),
            GLib.markup_escape_text(language_summary(self.config))))
        self._update_parakeet_row(model)
        if model == rec.model and self.device_combo.get_active_id() == rec.device:
            label.set_markup('<span color="#6db896">✓ You\'re on the recommended setup '
                             f'({GLib.markup_escape_text(rec.title)})</span>')
            self.use_recommended_btn.hide()
        else:
            label.set_markup('<span color="#888888">Recommended for this computer:</span> '
                             f'{GLib.markup_escape_text(rec.title)}')
            self.use_recommended_btn.show()

    def _parakeet_update_device(self, model):
        """Where the setup shown here would still run the original Parakeet
        (model_helper.parakeet_update_needed), or None."""
        from types import SimpleNamespace
        from .model_helper import parakeet_update_needed
        return parakeet_update_needed(SimpleNamespace(
            model=model, device=self.device_combo.get_active_id() or "cpu"))

    def _update_parakeet_row(self, model):
        device = self._parakeet_update_device(model)
        if device is None:
            self.parakeet_update_row.hide()
            return
        from .model_helper import parakeet_update_size
        self.parakeet_update_btn.set_label(f"Update Parakeet ({parakeet_update_size(device)})")
        self.parakeet_update_row.show()

    def _on_update_parakeet_clicked(self, _button):
        """Download Parakeet Ultra, then restart dictation onto it."""
        from .model_helper import download_parakeet_update
        model = self.model_combo.get_active_id()
        device = self._parakeet_update_device(model)
        if device is None:
            return
        updated = download_parakeet_update(device, self.window)
        if updated is None:
            show_message(self.window, Gtk.MessageType.ERROR, "The download did not finish",
                         "Check your internet connection and try again.")
        self._update_parakeet_row(model)
        if updated:
            self.restart_service()

    def _on_use_recommended_clicked(self, _button):
        """Set the dropdowns to the recommended setup, as Tray → Performance →
        Recommended does: on Vulkan, its download and speed check first, and
        the processor version if the graphics chip turns out slower. OK or
        Apply puts it in effect, like any other choice."""
        from . import recommend
        from .vulkan_setup_dialogs import ensure_files, run_speed_check
        _hw, rec = self._recommendation()
        model, device = rec.model, rec.device
        if device == "vulkan":
            if not ensure_files(self.window, model, confirm=True):
                return                      # cancelled: nothing changes
            if not run_speed_check(self.window, model):
                # The speed check saved this itself; the row's recommendation
                # needs it too, or it keeps offering the graphics card.
                self._vulkan_slower_now = True
                device = "cpu"
                model = recommend.recommend(recommend.effective_language(
                    self._shown_language()), recommend.Hardware(None), model=model).model
        self._set_choice("model", model)
        self._set_choice("device", device)
        self._update_model_combo_hint()

    def _shown_language(self):
        """The language settings as this window shows them, for recommend.py."""
        from types import SimpleNamespace
        return SimpleNamespace(language_mode=self.config.get("language_mode", "auto"),
                               language=self.config.get("language", ""),
                               dictation_language=self.config.get("dictation_language", ""),
                               vulkan_slower=(self.config.get("vulkan_slower", False)
                                              or getattr(self, "_vulkan_slower_now", False)))

    def _show_compatibility(self, _button):
        """Which models run on the processor, Vulkan and CUDA, and which of
        those apply to this computer. Explains only; changes nothing."""
        from . import recommend
        hw, _rec = self._recommendation()
        slower = self._shown_language().vulkan_slower
        dialog = Gtk.Dialog(title="What works with what", transient_for=self.window, modal=True)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        box = dialog.get_content_area()
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(16)
        box.set_spacing(10)

        grid = Gtk.Grid(column_spacing=24, row_spacing=8)
        for col, head in enumerate(("", "Processor (CPU)", "Vulkan (any GPU)", "CUDA (NVIDIA)")):
            label = Gtk.Label(xalign=0)
            label.set_markup(f"<b>{head}</b>")
            grid.attach(label, col, 0, 1, 1)
        for row, (name, _models, *cells) in enumerate(recommend.COMPATIBILITY, start=1):
            grid.attach(Gtk.Label(label=name, xalign=0), 0, row, 1, 1)
            for col, (works, note) in enumerate(cells, start=1):
                mark, colour = ("✓", "#6db896") if works else ("✗", WARNING_AMBER)
                text = f"{mark} {note}".strip()
                cell = Gtk.Label(xalign=0)
                cell.set_markup(f'<span color="{colour}">{GLib.markup_escape_text(text)}</span>')
                grid.attach(cell, col, row, 1, 1)
        box.pack_start(grid, False, False, 0)

        for text in (*recommend.COMPATIBILITY_NOTES, recommend.this_computer(hw, slower)):
            note = Gtk.Label(label=text, xalign=0)
            note.set_line_wrap(True)
            note.set_max_width_chars(70)
            box.pack_start(note, False, False, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _on_model_changed(self, combo):
        """Handle model selection change with warning for large models."""
        # Prevent recursive calls when reverting model selection
        if hasattr(self, '_updating_model') and self._updating_model:
            return
        # Several choices below open dialogs; close the list first so it isn't
        # left drawn on top of them.
        combo.popdown()

        # Read model ID from ListStore (column 0) via active iter
        _it = combo.get_active_iter()
        if _it is None:
            return
        new_model = self.model_store.get_value(_it, 0)

        # Track last selected model to avoid showing dialog when clicking same model
        if not hasattr(self, '_last_selected_model'):
            self._last_selected_model = self.config.get("model")

        # On Vulkan, whisper.cpp runs every Whisper model, large-v3
        # included, without NVIDIA's CUDA files, and its own model file is
        # fetched on Apply (_download_selected_model). The CUDA gate and the
        # faster-whisper size warning below don't apply.
        if self.config.get("device") == "vulkan":
            from . import whisper_vulkan
            if whisper_vulkan.supports_model(new_model):
                self._last_selected_model = new_model
                self.update_config("model", new_model)
                return

        # If large-v3 is selected, check CUDA availability first
        if new_model == "large-v3":
            try:
                from .cuda_helper import has_cuda_libraries
                # Same runtime check the Device dropdown uses (accepts a
                # system-wide CUDA install, not just TuxTalk's own download)
                _has_cuda = has_cuda_libraries()
                _has_nvidia = self._has_nvidia()
            except Exception:
                _has_cuda = False
                _has_nvidia = False

            if not _has_cuda:
                # Revert the combo immediately before showing the dialog
                _prev = self._last_selected_model if hasattr(self, '_last_selected_model') else "small"
                self._updating_model = True
                combo.set_active_id(_prev)
                self._updating_model = False

                if _has_nvidia:
                    # Two ways to run large-v3 on the card: Vulkan (light, about
                    # the same speed measured on an RTX 4070 Super) or CUDA
                    # (TuxTalk's long-standing NVIDIA setup).
                    from .vulkan_setup_dialogs import choose_light_or_full, LIGHT, FULL
                    if self._vulkan_offered():
                        _choice = choose_light_or_full(self.window, "large-v3")
                    else:
                        # No Vulkan here: CUDA is the only route, so ask before
                        # starting a 4.4 GB download.
                        _choice = FULL if show_message(
                            self.window, Gtk.MessageType.QUESTION, "Downloads Required",
                            "The 'large-v3' model needs two downloads:\n\n"
                            "  • CUDA GPU Libraries   (~1.4GB)\n"
                            "  • Large-v3 AI Model    (~3GB)\n\n"
                            "Total: ~4.4GB, a one-time download.\n\n"
                            "Would you like to download both now?",
                            buttons=Gtk.ButtonsType.YES_NO) == Gtk.ResponseType.YES else None
                    if _choice == LIGHT:
                        self._switch_to_vulkan_for_model(combo, "large-v3")
                    _resp = Gtk.ResponseType.YES if _choice == FULL else Gtk.ResponseType.CANCEL
                    if _resp == Gtk.ResponseType.YES:
                        from .download_progress_dialog import show_unified_download_dialog
                        _results = show_unified_download_dialog(
                            cuda=True, model="large-v3",
                            parent=self.window,
                        )
                        _cuda_ok = _results.get("CUDA Libraries", {}).get("success", False)
                        _model_ok = _results.get("large-v3 AI Model", {}).get("success", False)
                        if _cuda_ok and _model_ok:
                            self._on_cuda_download_for_model()
                        elif _cuda_ok and not _model_ok:
                            # CUDA worked but model failed — refresh dropdown, don't auto-select
                            self._refresh_device_options()
                elif self._vulkan_offered():
                    # AMD / Intel graphics: large-v3 can run there through Vulkan.
                    if show_message(
                            self.window, Gtk.MessageType.QUESTION,
                            "Large-v3 needs your graphics card",
                            "Large-v3 can run on your AMD or Intel graphics through Vulkan. "
                            f"That's a one-time download of a {ENGINE_SIZE_TEXT} graphics "
                            "engine and Large-v3 in its format (1 GB), then a quick check that "
                            "your graphics chip is really faster than your processor.",
                            buttons=Gtk.ButtonsType.OK_CANCEL) == Gtk.ResponseType.OK:
                        self._switch_to_vulkan_for_model(combo, "large-v3", confirm=False)
                    return
                else:
                    show_message(
                        self.window,
                        Gtk.MessageType.WARNING,
                        "Graphics Card Required",
                        "The 'large-v3' model needs a graphics card to run at a usable speed.\n\n"
                        "TuxTalk didn't find one it can use on this computer.",
                        keep_above=True)
                return

        # Show size warning for large-v3 model ONLY if it's not already
        # downloaded (fast file check — no model loading on the main thread)
        from .model_helper import is_model_cached_fast
        if new_model == "large-v3" and not is_model_cached_fast("large-v3"):
            dialog = message_dialog(
                transient_for=self.window,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK_CANCEL,
                text="Large Model Selected"
            )

            dialog.format_secondary_text(
                "The large-v3 model is approximately 3 GB in size.\n\n"
                "⏱️  First-time load: 30-60 seconds\n"
                "⏱️  Subsequent loads: 10-20 seconds\n\n"
                "The application will not respond to your dictation hotkey "
                "until the model has fully loaded.\n\n"
                "💡 Click OK here to confirm, then click Apply or OK in Preferences to start the download.\n\n"
                "Do you want to proceed with this model?"
            )

            response = dialog.run()
            dialog.destroy()

            if response == Gtk.ResponseType.CANCEL:
                # Revert to previous model selection (block recursive call)
                self._updating_model = True
                combo.set_active_id(self._last_selected_model)
                self._updating_model = False
                return

        # Update config with new model and track it
        self._last_selected_model = new_model
        self.update_config("model", new_model)

    def _on_cuda_download_for_model(self):
        """Called after a successful CUDA download triggered from the model dropdown.
        Updates the large-v3 entry label and auto-selects it so the user doesn't
        have to pick it again manually.
        """
        # Update large-v3 label now that CUDA is available
        for row in self.model_store:
            if row[0] == "large-v3":
                row[1] = "large-v3 — best accuracy"
                break
        # Also refresh the device dropdown to show CUDA
        self._refresh_device_options()
        # Auto-select large-v3 so the user gets what they wanted
        self._updating_model = True
        if hasattr(self, 'model_combo'):
            self.model_combo.set_active_id("large-v3")
        self._updating_model = False
        self.update_config("model", "large-v3")
        self.update_config("device", "cuda")
        # Sync the device combo — _refresh_device_options() above restored
        # the OLD device selection, so without this the dropdown shows CPU
        # while the config now says cuda.
        if hasattr(self, 'device_combo'):
            self.device_combo.set_active_id("cuda")

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
    
    def _fill_language_choices(self, model):
        """(Re)build the Language dropdown and select the current choice.

        "Several languages" is for the Whisper models: Parakeet always picks
        the language itself, so with Parakeet the entry is left out, unless it
        is the current choice (its warning then says it does nothing)."""
        from . import recommend
        self._filling_languages = True
        try:
            combo = self.lang_combo
            combo.remove_all()
            combo.append("auto", "Detect automatically")
            for code, _name in recommend.LANGUAGES:
                combo.append(code, recommend.language_label(code))
            if model != recommend.PARAKEET or self._lang_choice == SEVERAL_LANGUAGES:
                combo.append(SEVERAL_LANGUAGES,
                             several_languages_label(self.config.get("auto_languages", "")))
            if not combo.set_active_id(self._lang_choice):
                # A hand-edited code the list doesn't have: show it rather than
                # pretend it's another language.
                combo.append(self._lang_choice, recommend.language_label(self._lang_choice))
                combo.set_active_id(self._lang_choice)
            self._languages_for_model = model
        finally:
            self._filling_languages = False

    def _select_language_choice(self, choice):
        """Show *choice* in the Language dropdown without acting on it."""
        self._filling_languages = True
        try:
            self.lang_combo.set_active_id(choice)
        finally:
            self._filling_languages = False
        self._lang_choice = choice

    def _on_language_choice_changed(self, combo):
        if self._filling_languages:
            return
        choice = combo.get_active_id()
        if choice is None:
            return
        if choice == SEVERAL_LANGUAGES:
            if not self._pick_several_languages():
                self._select_language_choice(self._lang_choice)   # cancelled
            return
        apply_language_choice(self.config, choice)
        self._lang_choice = choice

    def _pick_several_languages(self):
        """Pick the languages auto-detect may choose from. True once the
        choice is made (OK), False when cancelled. One language ticked is the
        same as choosing it, and none is Detect automatically, so the dropdown
        then shows that instead."""
        from . import language_limit, recommend
        chosen = set(language_limit.parse(self.config.get("auto_languages", "")))
        dialog = Gtk.Dialog(title="Several Languages", transient_for=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(16)
        box.set_spacing(10)

        # First thing in the window, in the warning amber: with Parakeet the
        # ticks change nothing, and a note at the bottom was easy to miss.
        it = self.model_combo.get_active_iter()
        model = self.model_store.get_value(it, 0) if it is not None else ""
        if model == "parakeet-v3":
            parakeet_note = Gtk.Label(xalign=0)
            parakeet_note.set_line_wrap(True)
            parakeet_note.set_max_width_chars(60)
            parakeet_note.set_markup(
                f'<span color="{WARNING_AMBER}" weight="bold">⚠️ You\u2019re using Parakeet, '
                "which always picks the language by itself. These ticks do nothing with "
                "Parakeet. They only work with the Whisper models.</span>")
            box.pack_start(parakeet_note, False, False, 0)

        intro = Gtk.Label(xalign=0)
        intro.set_line_wrap(True)
        intro.set_max_width_chars(60)
        intro.set_markup("Tick the languages you dictate in. Auto-detect then picks "
                         "only from these.\nTo let it pick any language, leave them all unticked (<b>Untick all</b>).")
        box.pack_start(intro, False, False, 0)

        # Three columns, in the pickers' order, each with its own name for itself.
        checks = {}
        table = Gtk.Grid(column_spacing=18, row_spacing=2)
        per_column = -(-len(recommend.LANGUAGES) // 3)
        for i, (code, _name) in enumerate(recommend.LANGUAGES):
            check = Gtk.CheckButton(label=recommend.language_label(code))
            check.set_active(code in chosen)
            checks[code] = check
            table.attach(check, i // per_column, i % per_column, 1, 1)
        box.pack_start(table, False, False, 0)

        clear = Gtk.Button(label="Untick all")
        clear.set_halign(Gtk.Align.START)
        clear.connect("clicked", lambda _b: [c.set_active(False) for c in checks.values()])
        box.pack_start(clear, False, False, 0)

        how = Gtk.Label(xalign=0)
        how.set_line_wrap(True)
        how.set_max_width_chars(60)
        how.set_markup(
            "<small>This is for the Whisper models. When Whisper thinks it hears a "
            "language that isn\u2019t ticked, it listens again in the likeliest one "
            "that is. Tick just one and it\u2019s the same as choosing that language "
            "by hand.</small>")
        box.pack_start(how, False, False, 0)

        dialog.show_all()
        try:
            if dialog.run() != Gtk.ResponseType.OK:
                return False
            codes = language_limit.parse(language_limit.to_setting(
                c for c, chk in checks.items() if chk.get_active()))
        finally:
            dialog.destroy()
        if len(codes) < 2:
            choice = codes[0] if codes else "auto"
            apply_language_choice(self.config, choice)
        else:
            choice = SEVERAL_LANGUAGES
            self.update_config("language_mode", "auto")
            self.update_config("auto_languages", language_limit.to_setting(codes))
            label = several_languages_label(self.config["auto_languages"])
            # A new label on the selected entry fires "changed", which would
            # open this picker again (as the CUDA entry's label looped).
            self._filling_languages = True
            try:
                for entry in self.lang_combo.get_model():   # ComboBoxText rows are (text, id)
                    if entry[1] == SEVERAL_LANGUAGES and entry[0] != label:
                        entry[0] = label
            finally:
                self._filling_languages = False
        self._select_language_choice(choice)
        self._update_model_combo_hint()
        return True

    def _on_auto_timeout_toggled(self, checkbox):
        """Handle auto-timeout checkbox toggle."""
        self._update_timeout_ui_state()
        if checkbox.get_active():
            # The last thing on the General tab: what it just showed (the
            # minutes and what happens then) would sit below the window's edge.
            self._scroll_into_view(self.timeout_note)

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

    def _on_ai_corrections_toggled(self, check):
        """Turn AI self-corrections on or off (see _toggle_ai_feature)."""
        self._toggle_ai_feature(check, "ai_corrections", self._on_ai_corrections_toggled,
                                "Download the AI for self-corrections?")

    def _on_voice_editing_toggled(self, check):
        """Turn voice editing on or off. It uses the same AI as self-corrections."""
        self._toggle_ai_feature(check, "voice_editing", self._on_voice_editing_toggled,
                                "Download the AI for voice editing?")

    def _toggle_ai_feature(self, check, key, handler, question):
        """Turn an option that needs the local AI on or off.

        Turning it on the first time downloads the engine and model, after the
        user agrees to the size. If they decline, or the download fails or is
        cancelled, the box goes back to unticked so the setting never claims to
        be on while it cannot work. Self-corrections and voice editing share
        the one download.
        """
        if not check.get_active():
            self.update_config(key, False)
            return

        from tuxtalk import ai_cleanup
        if ai_cleanup.is_installed():
            self.update_config(key, True)
            return

        confirm = message_dialog(
            transient_for=self.window, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=question,
        )
        confirm.format_secondary_text(
            f"This downloads a small AI model ({ai_cleanup.MODEL_SIZE_TEXT}) and the "
            f"llama.cpp engine that runs it ({ai_cleanup.LLAMA_SIZE_TEXT}). It is a "
            "one-time download.\n\n"
            "Everything runs on this computer. Your dictation is never sent anywhere.")
        confirm.add_button("Cancel", Gtk.ResponseType.CANCEL)
        confirm.add_button("Download", Gtk.ResponseType.OK)
        confirm.set_default_response(Gtk.ResponseType.OK)
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            self._untick_ai_feature(check, key, handler)
            return

        from tuxtalk.download_progress_dialog import DownloadTask, UnifiedDownloadDialog
        dialog = UnifiedDownloadDialog(
            parent=self.window, title="Downloading the AI",
            description="One-time download. Everything runs on this computer.")
        dialog.add_task(DownloadTask("AI engine", ai_cleanup.LLAMA_SIZE_TEXT,
                                     ai_cleanup.make_engine_download_func()))
        dialog.add_task(DownloadTask("AI model", ai_cleanup.MODEL_SIZE_TEXT,
                                     ai_cleanup.make_model_download_func()))
        results = dialog.run()

        if all(r.get("success") for r in results.values()) and ai_cleanup.is_installed():
            self.update_config(key, True)
            return

        self._untick_ai_feature(check, key, handler)
        if not any(r.get("cancelled") for r in results.values()):
            show_message(self.window,
                         Gtk.MessageType.ERROR,
                         "The AI download did not finish",
                         "Check your internet connection and try turning the option on again.")

    # --- Vulkan (any GPU, whisper_vulkan.py) ---------------------------------

    def _on_device_changed(self, combo):
        """Switching to Vulkan downloads what it needs and checks
        it is actually faster than the processor before keeping it. CUDA
        without its files offers the download. Any other device is simply
        saved."""
        device = combo.get_active_id()
        if device is None:
            return
        if device == "cuda" and not getattr(self, "_cuda_ready", False):
            combo.popdown()     # or the list stays drawn over the dialogs
            GLib.idle_add(self._finish_switch_to_cuda, combo)
            return
        from .parakeet_engine import is_parakeet
        if device == "cuda" and is_parakeet(self.config.get("model")):
            # Parakeet can't use CUDA: ask for a Whisper model, the same
            # question as after the download.
            combo.popdown()
            GLib.idle_add(self._finish_cuda_for_parakeet, combo)
            return
        if device != "vulkan":
            self.update_config("device", device)
            self._device_before = device
            return
        if self._device_before == "vulkan":
            self.update_config("device", device)
            return
        # The setup shows dialogs. Run it once the dropdown has closed, or its
        # list stays drawn on top of them.
        combo.popdown()
        GLib.idle_add(self._finish_switch_to_vulkan, combo)

    def _finish_switch_to_vulkan(self, combo):
        """Keep Vulkan if its setup succeeds, otherwise put the dropdown back."""
        if self._setup_vulkan(self.config.get("model")):
            self.update_config("device", "vulkan")
            self._device_before = "vulkan"
        else:
            combo.handler_block_by_func(self._on_device_changed)
            combo.set_active_id(self._device_before)
            combo.handler_unblock_by_func(self._on_device_changed)
        return False        # run once

    def _finish_switch_to_cuda(self, combo):
        """CUDA picked before its files are downloaded. The dropdown goes back
        to the device in use first: if the download is refused or fails, or
        Parakeet is kept, nothing changed. Once downloaded,
        _after_cuda_download() selects CUDA itself, through Apply."""
        self._set_choice("device", self._device_before)
        self._download_cuda()
        return False        # run once

    def _finish_cuda_for_parakeet(self, combo):
        """CUDA picked, already downloaded, with Parakeet selected. Choosing
        a Whisper model selects it and CUDA, applied by OK or Apply like any
        other choice (Apply downloads the model). Keep Parakeet puts the
        dropdown back: under "CUDA" it would only run on the processor."""
        self._set_choice("device", self._device_before)
        model = self._ask_whisper_model_for_cuda()
        if model is not None:
            self._set_choice("model", model)
            self._set_choice("device", "cuda")
        return False        # run once

    def _vulkan_offered(self):
        """Whether Vulkan is offered on this computer (whisper_vulkan.is_offered)."""
        try:
            from . import whisper_vulkan
            return whisper_vulkan.is_offered()
        except Exception:
            return False

    def _switch_to_vulkan_for_model(self, model_combo, model, confirm=True):
        """Set up Vulkan for *model* (download plus speed check). If it's kept,
        select both the model and the Vulkan device; otherwise change nothing."""
        if not self._setup_vulkan(model, confirm=confirm):
            return False
        self._updating_model = True
        model_combo.set_active_id(model)
        self._updating_model = False
        self._last_selected_model = model
        self.update_config("model", model)
        self.device_combo.handler_block_by_func(self._on_device_changed)
        self.device_combo.set_active_id("vulkan")
        self.device_combo.handler_unblock_by_func(self._on_device_changed)
        self.update_config("device", "vulkan")
        self._device_before = "vulkan"
        return True

    def _setup_vulkan(self, model, confirm=True):
        """Download and speed-check Vulkan for *model* (vulkan_setup_dialogs).
        True if TuxTalk should use the graphics chip."""
        from .vulkan_setup_dialogs import set_up
        return set_up(self.window, model, confirm=confirm)

    def _download_vulkan_files(self, model, confirm):
        """Download the Vulkan engine and *model*'s Vulkan copy if missing."""
        from .vulkan_setup_dialogs import ensure_files
        return ensure_files(self.window, model, confirm)

    def _untick_ai_feature(self, check, key, handler):
        """Put an AI box back to unticked without re-running its handler."""
        check.handler_block_by_func(handler)
        check.set_active(False)
        check.handler_unblock_by_func(handler)
        self.update_config(key, False)

    def _on_log_transcripts_toggled(self, check):
        """Toggle transcript logging. Turning it OFF is safe and silent; turning
        it ON writes every dictation to disk, so it must be confirmed via a
        warning dialog before the config is written. Cancelling reverts the box.
        """
        if not check.get_active():
            self.update_config("log_transcripts", False)
            return

        dialog = message_dialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Log all transcribed text?",
        )
        dialog.format_secondary_text(
            "This saves the full text of EVERYTHING you dictate to TuxTalk's log "
            "file on disk, in plain text, and keeps it there until you turn this "
            "setting back off.\n\n"
            "Only enable it if you are troubleshooting a dictation problem."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        enable_btn = dialog.add_button("Yes, log everything I dictate", Gtk.ResponseType.OK)
        enable_btn.get_style_context().add_class("destructive-action")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.OK:
            self.update_config("log_transcripts", True)
        else:
            # Revert without re-entering this handler.
            check.handler_block_by_func(self._on_log_transcripts_toggled)
            check.set_active(False)
            check.handler_unblock_by_func(self._on_log_transcripts_toggled)

    def _on_clear_log_clicked(self, _button):
        """Erase the on-disk log after confirming — it can hold past dictation."""
        dialog = message_dialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Clear the TuxTalk log?",
        )
        dialog.format_secondary_text(
            "This permanently deletes the current log file and its backup from "
            "disk, including any past transcriptions recorded there. This cannot "
            "be undone."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        clear_btn = dialog.add_button("Clear log", Gtk.ResponseType.OK)
        clear_btn.get_style_context().add_class("destructive-action")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.OK:
            from .logger import clear_log
            cleared = clear_log()
            show_message(self.window,
                         Gtk.MessageType.INFO if cleared else Gtk.MessageType.ERROR,
                         "Log cleared." if cleared else "Some of the log couldn't be erased.")

    def _update_timeout_ui_state(self):
        """Update timeout UI visibility based on checkbox state."""
        if hasattr(self, 'timeout_label'):
            is_enabled = self.config.get("auto_timeout_enabled", False)
            for name in ("timeout_label", "timeout_spin", "timeout_minutes_label", "timeout_note"):
                widget = getattr(self, name, None)
                if widget is not None:
                    widget.set_visible(is_enabled)
        return False  # Don't repeat

    def _download_cuda(self):
        """Ask, then download CUDA's 1.4 GB of files with a progress bar.

        Picking CUDA in General → Device before it's installed lands here. On
        success the dropdowns are rebuilt and _after_cuda_download() switches
        to CUDA (asking for a Whisper model first on Parakeet).
        """
        from . import cuda_helper
        from tuxtalk.config import get_data_dir
        cuda_path = os.path.join(get_data_dir(), "cuda")
        if show_message(
                self.window, Gtk.MessageType.QUESTION, "Download CUDA Libraries?",
                "CUDA runs the Whisper models on your NVIDIA graphics card. It needs "
                "about 1.4 GB of NVIDIA's files, a one-time download.\n\n"
                f"The files will be stored in {cuda_path} and may take several minutes "
                "to download.\n\nContinue with download?",
                buttons=Gtk.ButtonsType.YES_NO) != Gtk.ResponseType.YES:
            return

        def on_success():
            """Refresh the window now that CUDA is installed, then switch."""
            self.system_check._check_gpu_status()
            self._refresh_device_options()

            # large-v3 no longer needs its download note now that CUDA is available
            if hasattr(self, 'model_store'):
                for row in self.model_store:
                    if row[0] == "large-v3":
                        row[1] = "large-v3 — best accuracy"
                        break

            self._after_cuda_download()

        cuda_helper.show_cuda_download_dialog(
            parent=self.window,
            on_success_callback=on_success
        )

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

    # --- TuxTalk Labs: Type at pause (chunking.py) -----------------------------

    def _stop_pause_test(self, wait=False):
        """Stop Type at pause's live test, where its Labs entry is built (not
        on the Flatpak). *wait*: TypeAtPause.stop_test's."""
        if self.type_at_pause is not None:
            self.type_at_pause.stop_test(wait)

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
    
    def check_and_download_model(self, model_name):
        """Check if model exists, download with progress if needed.

        Returns:
            tuple: (success: bool, was_downloaded: bool, cancelled: bool)
        """
        from .model_helper import is_model_cached_fast, make_model_download_func
        from .recommend import model_title

        # Completeness check on the cached files (no model loading — this
        # runs on every Apply/OK click). A partial cache left by a cancelled
        # or interrupted download reports "not cached".
        if is_model_cached_fast(model_name):
            logger.info(f"Model {model_name} already cached")
            return (True, False, False)  # Success, but not downloaded

        # Model needs downloading - show progress dialog
        logger.info(f"Model {model_name} not found, downloading...")

        dialog = Gtk.Dialog(title="Downloading Model")
        dialog.set_transient_for(self.window)
        dialog.set_modal(True)
        dialog.set_default_size(450, 180)
        dialog.set_resizable(False)

        content = dialog.get_content_area()
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_spacing(15)

        from .model_helper import MODEL_DISPLAY_SIZES
        size_str = MODEL_DISPLAY_SIZES.get(model_name, "unknown size")

        # Status label
        status_label = Gtk.Label()
        status_label.set_markup(f'<b>Downloading the speech model: {GLib.markup_escape_text(model_title(model_name))}</b>\n<span size="small">({size_str})</span>\n\nThis may take a few minutes depending on your connection...')
        status_label.set_line_wrap(True)
        content.pack_start(status_label, False, False, 0)

        # Progress bar with percentage
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_show_text(True)
        progress_bar.set_text("0%")
        content.pack_start(progress_bar, False, False, 0)

        # Add Cancel button
        cancel_button = Gtk.Button(label="Cancel")
        cancel_button.set_can_default(True)
        cancel_button.show()
        dialog.add_action_widget(cancel_button, Gtk.ResponseType.CANCEL)

        dialog.show_all()

        # Download in background thread with real cancellation: the
        # model_helper download function checks cancel_event between blocks
        # and aborts within moments.
        cancel_event = threading.Event()
        download_complete = {"done": False, "success": False}
        dialog_closed = {"v": False}

        def on_progress(message, percent):
            # Called from the download thread — marshal to the GTK main loop
            def _update():
                if not dialog_closed["v"]:
                    progress_bar.set_fraction(min(percent, 100) / 100.0)
                    progress_bar.set_text(f"{percent}%")
                return False
            GLib.idle_add(_update)

        def download_model():
            try:
                download_func = make_model_download_func(model_name)
                download_complete["success"] = download_func(on_progress, cancel_event)
            except Exception as e:
                logger.warning(f"Model download failed: {e}")
                download_complete["success"] = False
            finally:
                download_complete["done"] = True
                def _close():
                    if not dialog_closed["v"]:
                        dialog.response(Gtk.ResponseType.OK)
                    return False
                GLib.idle_add(_close)

        thread = threading.Thread(target=download_model)
        thread.daemon = True
        thread.start()

        # Wait for download (dialog.run() blocks until response)
        response = dialog.run()

        # Any response except our own OK (Cancel button, Esc, window close)
        # aborts the download thread at its next block boundary.
        if response != Gtk.ResponseType.OK:
            cancel_event.set()
            logger.info(f"User cancelled {model_name} model download")

        dialog_closed["v"] = True
        dialog.destroy()

        cancelled = cancel_event.is_set()
        _ok = download_complete["success"] and not cancelled
        # Trust the downloader only if the cache now actually contains every
        # file, and check that before showing the success message.
        if _ok:
            _ok = is_model_cached_fast(model_name)
        if _ok:
            logger.info(f"Model {model_name} downloaded successfully")
            show_message(self.window, Gtk.MessageType.INFO, "Model Downloaded Successfully!",
                         f"The {model_title(model_name)} model has been "
                         "downloaded and is ready to use.")
        return (_ok, _ok, cancelled)  # (success, was_downloaded, cancelled)

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

    def _download_selected_model(self):
        """Download the model selected in the dropdown, if it needs it.

        Shows the shared 'Model download failed!' error dialog on failure.
        Returns True on success, including when nothing needed downloading.
        """
        # Use the model that was actually SAVED (merged config) — if the tray
        # switched models while this window was open, the combo may be stale.
        model_name = self.config.get("model")
        if not model_name:
            return True

        if self.config.get("device") == "vulkan":
            from . import whisper_vulkan
            if whisper_vulkan.supports_model(model_name):
                return self._download_vulkan_files(model_name, confirm=False)

        success, _was_downloaded, cancelled = self.check_and_download_model(model_name)
        if cancelled:
            # User cancelled — no scary "check your internet connection"
            # dialog; just report failure so Apply/OK stops cleanly.
            return False
        if not success:
            dialog = message_dialog(
                transient_for=self.window,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Model download failed!"
            )

            dialog.format_secondary_text("Failed to download the speech model. Please check your internet connection.")
            dialog.run()
            dialog.destroy()
        return success

    def _after_cuda_download(self):
        """Switch to CUDA now that it's downloaded, through Apply.

        Never by saving directly: that moves the baseline Apply diffs against,
        so the switch would be saved without restarting the service. Parakeet
        can't use CUDA, and switching its device would move it from the
        graphics card to the processor, so its user is asked first.
        """
        from .parakeet_engine import is_parakeet
        if is_parakeet(self.config.get("model")):
            model = self._ask_whisper_model_for_cuda()
            if model is None:
                return
            self._set_choice("model", model)
        self._set_choice("device", "cuda")
        self.on_apply(None)

    def _ask_whisper_model_for_cuda(self):
        """Which Whisper model to use CUDA with, or None to keep Parakeet.

        Every Whisper model, with its download, so nobody downloads Small
        first only to want another one. Large-v3 is preselected: getting
        CUDA for an NVIDIA card is usually about the best accuracy.
        """
        from .model_helper import MODEL_DISPLAY_SIZES
        names = [("tiny", "Tiny", "fastest, least accurate"),
                 ("base", "Base", "fast"),
                 ("small", "Small", "a good balance"),
                 ("medium", "Medium", "more accurate"),
                 ("large-v3", "Large-v3", "most accurate")]
        box = message_dialog(transient_for=self.window, modal=True,
                             message_type=Gtk.MessageType.QUESTION,
                             buttons=Gtk.ButtonsType.NONE, text="CUDA is ready")
        box.format_secondary_text(
            "CUDA speeds up the Whisper models. Parakeet can't use it, and it "
            "stays as fast as it is now.\n\nSwitch to a Whisper model on CUDA?")
        radios, group = {}, None
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        column.set_margin_start(24)
        for model, name, what in names:
            radio = Gtk.RadioButton.new_with_label_from_widget(
                group, f"{name}, {what} ({MODEL_DISPLAY_SIZES[model]})")
            group = group or radio
            radios[model] = radio
            column.pack_start(radio, False, False, 0)
        radios["large-v3"].set_active(True)
        box.get_content_area().pack_start(column, False, False, 6)
        box.add_button("Keep Parakeet", Gtk.ResponseType.NO)
        box.add_button("Switch", Gtk.ResponseType.YES)
        box.set_default_response(Gtk.ResponseType.YES)
        box.show_all()
        answer = box.run()
        chosen = next(m for m, r in radios.items() if r.get_active())
        box.destroy()
        return chosen if answer == Gtk.ResponseType.YES else None

    def _set_choice(self, key, value):
        """Set the model or device and its dropdown without the dropdown's
        own reactions (model-picked dialogs, Vulkan's setup download)."""
        self.config[key] = value
        if key == "model" and getattr(self, "model_combo", None) is not None:
            self._updating_model = True
            try:
                self.model_combo.set_active_id(value)
            finally:
                self._updating_model = False
            self._last_selected_model = value
        elif key == "device":
            # What _on_device_changed falls back to when a setup is refused.
            self._device_before = value
            if getattr(self, "device_combo", None) is None:
                return
            self.device_combo.handler_block_by_func(self._on_device_changed)
            try:
                self.device_combo.set_active_id(value)
            finally:
                self.device_combo.handler_unblock_by_func(self._on_device_changed)

    def _rollback_download(self, previous):
        """Undo a model or device change whose download never completed.

        The config is saved *before* the download because the downloader reads
        the model and device out of the merged config. Left alone, a cancelled
        or failed download leaves config.toml naming a setup the service was
        never given, and these keys are excluded from the live-settings
        reload, so nothing corrects it.

        The second write also repairs the baseline. save_config() resets
        _config_at_open to whatever it just wrote, so without this a retry
        finds no change and skips the restart that would load the new setup.

        Only these keys are undone; the user's other edits saved fine and
        stay. The dropdowns go back too, quietly: no model-picked dialogs, and
        no device setup (Vulkan's starts a download).
        """
        undo = {k: v for k, v in previous.items()
                if v is not None and self.config.get(k) != v}
        if not undo:
            return
        for key, value in undo.items():
            self._set_choice(key, value)
        self.save_config()

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
            if not self._download_selected_model():
                self._rollback_download(previous)
                return

            # Restart only when the change actually requires it. The service
            # picks up everything else from the config file within a second,
            # which avoids a ~10s rebuild of the Whisper model during which
            # the hotkey does nothing at all.
            applied = self._apply_or_restart(changed)
            if applied and self._after_hotkey_change(changed, saved_at):
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
    
    def _kde_refused(self, since):
        """Keys KDE already uses, once the service has registered the new
        ones (kwin_hotkeys saves its verdict), or None: not KDE, dictation
        off, or no answer within a few seconds."""
        from . import kwin_hotkeys
        try:
            from .service_launcher import find_service_pids
            if not kwin_hotkeys.should_claim() or not find_service_pids():
                return None
        except Exception:
            return None
        deadline = time.monotonic() + 6
        # The wait keeps the window drawn by running the main loop, which
        # would also let a second Apply, OK or Cancel run inside this one.
        # The title bar's close button is the window manager's, so
        # on_window_close refuses it separately while the flag is set.
        self.window.set_sensitive(False)
        self._waiting_for_kde = True
        try:
            while time.monotonic() < deadline:
                found = kwin_hotkeys.refused_since(since)
                if found is not None:
                    return found
                while Gtk.events_pending():
                    Gtk.main_iteration()
                time.sleep(0.1)
            return None
        finally:
            self._waiting_for_kde = False
            self.window.set_sensitive(True)

    def _after_hotkey_change(self, changed, since):
        """After OK or Apply with a changed hotkey: offer the hotkey test,
        and say so if KDE already uses the key. True when it asked, so Apply
        doesn't also say "Settings applied"."""
        from .config import HOTKEY_KEYS
        if not set(changed) & {*HOTKEY_KEYS, "voice_commands_hotkey", "paste_last_hotkey"}:
            return False
        from .kwin_hotkeys import app_shortcuts_since
        refused = self._kde_refused(since)
        from .secure_input import unheard_keys
        unheard = unheard_keys([self.config.get(k) for k in HOTKEY_KEYS if k in changed])
        message = hotkey_change_message(self.config, set(changed), refused,
                                        app_shortcuts_since(since) if refused else None,
                                        unheard)
        if message is None:
            return False
        title, text, offer_test, clash = message
        dialog = message_dialog(transient_for=self.window, modal=True,
                                message_type=Gtk.MessageType.QUESTION
                                if offer_test and not clash else Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.NONE, text=title)
        if clash:
            # A key that does two things must not read like an ordinary
            # heading: the title and the clash paragraph are amber, the
            # colour of TuxTalk's other warnings.
            esc = GLib.markup_escape_text
            dialog.set_markup(f'<span color="{WARNING_AMBER}" weight="bold" size="large">'
                              f'⚠️ {esc(title)}</span>')
            first, _, rest = text.partition("\n\n")
            dialog.format_secondary_markup(
                f'<span color="{WARNING_AMBER}" weight="bold">{esc(first)}</span>'
                + (f"\n\n{esc(rest)}" if rest else ""))
        else:
            dialog.format_secondary_text(text)
        if offer_test:
            dialog.add_button("Not Now", Gtk.ResponseType.CANCEL)
            dialog.add_button("Test Now", Gtk.ResponseType.OK)
        else:
            dialog.add_button("OK", Gtk.ResponseType.CANCEL)
        dialog.set_default_response(Gtk.ResponseType.OK if offer_test else Gtk.ResponseType.CANCEL)
        answer = dialog.run()
        dialog.destroy()
        if answer == Gtk.ResponseType.OK:
            self._on_test_hotkeys(None)
        return True

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
            if not self._download_selected_model():
                self._rollback_download(previous)
                return

            # OK closes even after a download; Apply is the one that stays open.
            service_restarted = self._apply_or_restart(changed)
            if service_restarted:
                self._after_hotkey_change(changed, saved_at)

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

    def _on_test_hotkeys(self, button):
        """Show hotkey test dialog.

        Instead of stopping the dictation service (which releases the evdev grab and
        causes XWayland phantom key-repeat floods), we put it into "hotkey test
        mode" with a flag file that this dialog keeps fresh while it is open. In
        this mode the evdev grab stays active (no phantom floods) and hotkey
        presses are reported back via D-Bus HotkeyPressed signals instead of
        starting/stopping recording. The flag goes stale by itself if this
        process dies, so dictation can't be left switched off.
        """
        # The service tests against the SAVED hotkeys, but this dialog labels its
        # rows from the pending dropdown selections. If the user changed a hotkey
        # without applying, those disagree and the wrong row lights up. Make them
        # apply first rather than testing against a config the service isn't using.
        from .config import unapplied_hotkeys, load_config
        pending = unapplied_hotkeys(self.config, load_config().__dict__)
        if pending:
            show_message(self.window,
                         Gtk.MessageType.INFO,
                         "Apply your hotkey change first",
                         "You changed a hotkey but haven't applied it yet. Click Apply, "
                         "then test — otherwise the test checks your previous hotkeys and "
                         "the wrong row will light up.")
            return

        dialog = Gtk.Dialog(title="Test Hotkeys", transient_for=self.window)
        dialog.set_default_size(450, 280)
        dialog.set_modal(True)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.set_keep_above(True)

        content = dialog.get_content_area()
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(25)
        content.set_margin_end(25)
        content.set_spacing(15)

        # Instructions
        instructions = Gtk.Label()
        instructions.set_markup('<span size="large"><b>Test Your Hotkeys</b></span>\n\nPress each hotkey to verify it works:')
        instructions.set_xalign(0)
        content.pack_start(instructions, False, False, 0)

        # Get current hotkeys
        hold_key = self.config.get("hotkey", "F8")
        toggle_key = self.config.get("toggle_hotkey", "F9")

        # Test status labels
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        status_box.set_margin_top(10)

        hold_label = Gtk.Label()
        hold_label.set_markup(f'<b>{hold_key}</b> (Hold-to-talk): <span color="#999999">Not tested</span>')
        hold_label.set_xalign(0)
        status_box.pack_start(hold_label, False, False, 0)

        toggle_label = Gtk.Label()
        toggle_label.set_markup(f'<b>{toggle_key}</b> (Tap-to-toggle): <span color="#999999">Not tested</span>')
        toggle_label.set_xalign(0)
        status_box.pack_start(toggle_label, False, False, 0)

        content.pack_start(status_box, False, False, 0)

        # Track tested keys
        tested_keys = {"hold": False, "toggle": False}

        # Listen for HotkeyPressed D-Bus signals from app.py.
        # The app's evdev loop detects hotkey presses and sends them to the
        # tray's D-Bus service, which emits the HotkeyPressed signal.
        from .dbus_service import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH
        bus = dbus.SessionBus()

        def _on_hotkey_signal(key_name):
            """Called when app.py reports a hotkey press via D-Bus signal."""
            logger.debug(f"Hotkey test: TuxTalk heard {key_name}")
            if key_name == "hold" and not tested_keys["hold"]:
                tested_keys["hold"] = True
                hold_label.set_markup(f'<b>{hold_key}</b> (Hold-to-talk): <span color="#4CAF50">✓ Working!</span>')
            elif key_name == "toggle" and not tested_keys["toggle"]:
                tested_keys["toggle"] = True
                toggle_label.set_markup(f'<b>{toggle_key}</b> (Tap-to-toggle): <span color="#4CAF50">✓ Working!</span>')

        # Connect to the HotkeyPressed signal on the D-Bus
        signal_match = bus.add_signal_receiver(
            _on_hotkey_signal,
            signal_name="HotkeyPressed",
            dbus_interface=DBUS_INTERFACE,
            bus_name=DBUS_NAME,
            path=DBUS_PATH
        )

        # Info label
        info_label = Gtk.Label()
        info_label.set_markup('<i>Dictation is paused while testing.\nClose this dialog to resume.</i>')
        info_label.set_line_wrap(True)
        info_label.set_xalign(0)
        info_label.set_margin_top(10)
        content.pack_start(info_label, False, False, 0)

        # Close button
        close_button = Gtk.Button(label="Close")
        close_button.connect("clicked", lambda w: dialog.destroy())
        dialog.add_action_widget(close_button, Gtk.ResponseType.CLOSE)

        # Hotkey test mode lasts while the flag is fresh: start it now and keep
        # it fresh while the dialog is open (see service_launcher).
        from .service_launcher import (HOTKEY_TEST_REFRESH_S,
                                       hotkey_test_flag_path)
        flag = hotkey_test_flag_path()

        def _refresh_test_flag():
            try:
                os.makedirs(os.path.dirname(flag), exist_ok=True)
                with open(flag, "a"):
                    pass
                os.utime(flag)
            except OSError as e:
                logger.warning(f"Hotkey test: could not refresh the test flag: {e}")
            return True  # keep the timer running

        _refresh_test_flag()
        refresh_timer = GLib.timeout_add_seconds(HOTKEY_TEST_REFRESH_S, _refresh_test_flag)
        try:
            dialog.show_all()
            dialog.run()
            dialog.destroy()
        finally:
            # Resume normal hotkey processing, whatever happened in the dialog.
            GLib.source_remove(refresh_timer)
            try:
                os.remove(flag)
            except OSError:
                pass
            # Disconnect D-Bus signal listener
            try:
                signal_match.remove()
            except Exception:
                pass

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
