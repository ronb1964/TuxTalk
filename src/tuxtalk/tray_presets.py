"""The tray's Performance menu (a mixin of tray.DictationTray): Recommended,
Lightest and Battery saver for this computer, applying one (with the Vulkan
setup and downloads it needs), and Custom, which opens Preferences."""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .logger import setup_logger  # noqa: E402
from .tray_items import relabel  # noqa: E402

logger = setup_logger(__name__)


def preset_notice(model, device, gpu_offered):
    """The notification after choosing a Performance preset.

    It says where the model really runs and, if that's the processor on a
    computer with a usable graphics chip, how to move it there.
    """
    from .recommend import engine_line, engine_status
    text = f"{engine_line(model, device)}."
    if engine_status(model, device)[1].startswith("CPU") and gpu_offered:
        text += " To use your graphics card, set the Device to Vulkan in Preferences."
    return text + "\nRestarting service..."


def _gpu_offered():
    """Whether this computer is offered the graphics chip (Vulkan), for the
    preset's notice; False when that can't be told."""
    try:
        from . import whisper_vulkan
        return whisper_vulkan.is_offered()
    except Exception as e:
        logger.info(f"Couldn't tell whether the graphics chip is offered: {e}")
        return False


class PresetMenu:
    """DictationTray's Performance menu (a mixin of tray.DictationTray): the
    presets, applying one, and keeping the dot on the one in use. They use its
    state (preset_radios, preset_custom, _updating_preset, _applying_preset)
    and its open_preferences, update_menu_display, _emit_model_changed and
    restart_service."""

    def _current_presets(self):
        """The Performance presets for this computer and language (recommend.py)."""
        from . import recommend
        from .config import load_config
        cfg = load_config()
        return recommend.presets(recommend.effective_language(cfg), recommend.detect_hardware(cfg))

    def _get_current_preset(self) -> str:
        """Id of the preset the settings match, or 'custom'."""
        try:
            from . import config, recommend
            return recommend.match_preset(config.load_config(), self._current_presets())
        except Exception as e:
            logger.warning(f"Could not tell which preset is in use: {e}")
            return "custom"

    def set_performance_preset(self, preset_id: str):
        """Apply a Performance preset: Recommended, Lightest or Battery saver.

        Recommended on a graphics card runs the same setup as Preferences
        (confirm and download the Vulkan files, then the speed check) and
        uses the processor if the chip is slower. Cancelling the download
        changes nothing. Ids from an older GNOME extension ("balanced",
        "accurate"...) are ignored."""
        if getattr(self, "_updating_preset", False):
            return
        try:
            preset = next((p for p in self._current_presets() if p.id == preset_id), None)
        except Exception as e:
            logger.error(f"Could not work out the presets: {e}")
            preset = None
        if preset is None:
            logger.info(f"Ignoring unknown performance preset {preset_id!r}")
            self._revert_preset_radio()
            return
        if self._get_current_preset() == preset_id:
            # Already in effect (the GNOME menu sends clicks on the dotted
            # item too): no download check, speed check or restart.
            logger.info(f"Performance preset {preset_id!r} is already in effect")
            return
        if getattr(self, "_applying_preset", False):
            # Its download window runs a loop of its own, and the menu still
            # takes clicks: a second one opened a second download.
            logger.info("A Performance preset is still being applied; ignoring the click")
            self._put_dot_back_soon()
            return
        self._applying_preset = True
        try:
            self._apply_preset(preset)
        finally:
            self._applying_preset = False

    def _apply_preset(self, preset):
        """set_performance_preset's work for *preset*: the downloads and speed
        check it needs, then its settings saved and dictation restarted."""
        try:
            setup = self._preset_files(preset)
            if setup is None:
                self._revert_preset_radio()
                return
            cfg = self._save_preset(preset, *setup)
            from .notify import notify
            logger.info(f"Applied performance preset: {preset.label} "
                        f"({cfg.model} on {cfg.device})")
            notify(f"Performance: {preset.label}",
                   preset_notice(cfg.model, cfg.device, _gpu_offered()))
            self.update_menu_display()
            self._emit_model_changed(cfg.model)
            self.restart_service(None)
        except Exception as e:
            logger.error(f"Failed to apply performance preset: {e}", exc_info=True)
            self._put_dot_back_soon()     # on the setup still in use

    def _preset_files(self, preset):
        """(model, device) *preset* runs with once its files are here: the
        processor's when the graphics chip lost the speed check. None when the
        Vulkan setup or a download was cancelled or failed."""
        from .config import load_config
        from .model_helper import download_model_files, is_model_cached_fast
        model, device = preset.model, preset.device
        if device == "vulkan":
            from .vulkan_setup_dialogs import ensure_files, run_speed_check
            if not ensure_files(None, model, confirm=True):
                return None
            if not run_speed_check(None, model):
                # On the processor, use what Recommended means there:
                # Large-v3 needs a graphics card, so it becomes Small.
                from . import recommend
                device = "cpu"
                model = recommend.recommend(recommend.effective_language(load_config()),
                                            recommend.Hardware(None), model=model).model
        # Downloaded only: the dictation service loads the model when it
        # restarts, and loading it here would hold 0.7 to 3 GB in the tray.
        if (device == "cpu" and not is_model_cached_fast(model)
                and not download_model_files(None, model, confirm=True)[0]):
            return None
        return model, device

    def _save_preset(self, preset, model, device):
        """Save *preset*'s settings, with *model* on *device*, and return them.
        Leaving Battery saver puts back the settings it changed (the 2-minute
        auto-stop) to their defaults, unless the user has since chosen their
        own in Preferences."""
        from .config import Settings, load_config, save_config
        cfg = load_config()
        cfg.model, cfg.device = model, device
        for key, value in preset.extras:
            setattr(cfg, key, value)
        if not preset.extras:
            battery = next((p for p in self._current_presets() if p.id == "battery"), None)
            if battery and all(getattr(cfg, k, None) == v for k, v in battery.extras):
                # An instance, not the class: some defaults are worked out
                # for this computer (the timeout is on only with a battery).
                defaults = Settings()
                for key, _ in battery.extras:
                    setattr(cfg, key, getattr(defaults, key))
        save_config(cfg)
        return cfg

    def _sync_preset_radios(self):
        """Refresh the Performance labels and put the dot on the active preset.
        The busy flag (which makes set_performance_preset ignore the toggles
        this causes) is always cleared, or every later click would be ignored."""
        self._updating_preset = True
        try:
            for _p in self._current_presets():
                if _p.id in self.preset_radios:
                    relabel(self.preset_radios[_p.id], f"{_p.label} ({_p.description})")
            current_preset = self._get_current_preset()
            if current_preset in self.preset_radios:
                self.preset_radios[current_preset].set_active(True)
            elif hasattr(self, 'preset_custom'):
                self.preset_custom.set_active(True)
        finally:
            self._updating_preset = False

    def _dotted_preset(self):
        """Id of the preset the menu's dot is on, or 'custom'."""
        return next((pid for pid, radio in self.preset_radios.items() if radio.get_active()),
                    "custom")

    def _revert_preset_radio(self):
        """Put the Performance radio back on the preset that's actually in use."""
        self._updating_preset = True
        try:
            _cur = self._get_current_preset()
            if _cur in self.preset_radios:
                self.preset_radios[_cur].set_active(True)
            elif hasattr(self, 'preset_custom'):
                self.preset_custom.set_active(True)
        except Exception as _re:
            logger.error(f"Failed to revert preset radio: {_re}")
        finally:
            self._updating_preset = False

    def _build_performance_submenu(self):
        """Build the Performance preset submenu (Recommended, Lightest, Battery saver + Custom).

        Stores radio button references on self for later state updates.
        Returns the parent MenuItem that contains the submenu.
        """
        submenu = Gtk.Menu()
        self.preset_radios = {}
        preset_group = None
        for preset in self._current_presets():
            label = f"{preset.label} ({preset.description})"
            radio = (Gtk.RadioMenuItem(label=label) if preset_group is None
                     else Gtk.RadioMenuItem(label=label, group=preset_group))
            preset_group = preset_group or radio
            # GTK fires "activate" on the radio being unselected too; act on the selected one.
            radio.connect("activate", lambda w, pid=preset.id:
                          w.get_active() and self.set_performance_preset(pid))
            submenu.append(radio)
            self.preset_radios[preset.id] = radio

        # Custom has the dot when the settings match no preset; it opens Preferences.
        submenu.append(Gtk.SeparatorMenuItem())
        self.preset_custom = Gtk.RadioMenuItem(label="Custom (opens Preferences)",
                                               group=preset_group)
        self.preset_custom.connect("activate", self._on_custom_preset)
        submenu.append(self.preset_custom)

        item = Gtk.MenuItem(label="Performance")
        item.set_submenu(submenu)
        return item

    def _on_custom_preset(self, item):
        """Performance > Custom: open Preferences, then put the dot back on the
        setup in use (clicking a radio item moves it)."""
        # "activate" also comes for the radio being unselected, and for the
        # tray moving the dot itself.
        if getattr(self, "_updating_preset", False) or not item.get_active():
            return
        self.open_preferences(None)
        self._put_dot_back_soon()

    def _put_dot_back_soon(self):
        """Put the dot back on the preset in use, after a moment: KDE ticks
        the clicked item in its own copy of the menu, and a move there and
        back in the same main-loop pass reaches it as no change and leaves
        two dots."""
        def put_dot_back():
            self._revert_preset_radio()
            return False    # once
        GLib.timeout_add(150, put_dot_back)
