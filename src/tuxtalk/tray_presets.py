"""Performance presets: Recommended, Lightest and Battery saver for this
computer, and applying one (with the Vulkan setup and downloads it needs), a
mixin of tray.DictationTray. The menu's radio items are tray_menu.py's."""
from .logger import setup_logger

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


def current_presets():
    """The Performance presets for this computer and language (recommend.py)."""
    from . import recommend
    from .config import load_config
    cfg = load_config()
    return recommend.presets(recommend.effective_language(cfg), recommend.detect_hardware(cfg))


def preset_in_use() -> str:
    """Id of the preset the settings match, or 'custom'."""
    try:
        from . import config, recommend
        return recommend.match_preset(config.load_config(), current_presets())
    except Exception as e:
        logger.warning(f"Could not tell which preset is in use: {e}")
        return "custom"


class Presets:
    """Applying a Performance preset, for the tray's menu, the menu window and
    the GNOME extension (a mixin of tray.DictationTray). It uses the tray's
    menu (to put the dot back when a preset isn't applied), _applying_preset,
    update_menu_display, _emit_model_changed and restart_if_on."""

    def set_performance_preset(self, preset_id: str):
        """Apply a Performance preset: Recommended, Lightest or Battery saver.

        Recommended on a graphics card runs the same setup as Preferences
        (confirm and download the Vulkan files, then the speed check) and
        uses the processor if the chip is slower. Cancelling the download
        changes nothing. Ids from an older GNOME extension ("balanced",
        "accurate"...) are ignored."""
        try:
            preset = next((p for p in current_presets() if p.id == preset_id), None)
        except Exception as e:
            logger.error(f"Could not work out the presets: {e}")
            preset = None
        if preset is None:
            logger.info(f"Ignoring unknown performance preset {preset_id!r}")
            self.menu.put_dot_back()
            return
        if preset_in_use() == preset_id:
            # Already in effect (the GNOME menu sends clicks on the dotted
            # item too): no download check, speed check or restart.
            logger.info(f"Performance preset {preset_id!r} is already in effect")
            return
        if self._applying_preset:
            # Its download window runs a loop of its own, and the menu still
            # takes clicks: a second one opened a second download.
            logger.info("A Performance preset is still being applied; ignoring the click")
            self.menu.put_dot_back_soon()
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
                self.menu.put_dot_back()
                return
            cfg = self._save_preset(preset, *setup)
            from .notify import notify
            logger.info(f"Applied performance preset: {preset.label} "
                        f"({cfg.model} on {cfg.device})")
            notify(f"Performance: {preset.label}",
                   preset_notice(cfg.model, cfg.device, _gpu_offered()))
            self.update_menu_display()
            self._emit_model_changed(cfg.model)
            self.restart_if_on()
        except Exception as e:
            logger.error(f"Failed to apply performance preset: {e}", exc_info=True)
            self.menu.put_dot_back_soon()     # on the setup still in use

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
            battery = next((p for p in current_presets() if p.id == "battery"), None)
            if battery and all(getattr(cfg, k, None) == v for k, v in battery.extras):
                # An instance, not the class: some defaults are worked out
                # for this computer (the timeout is on only with a battery).
                defaults = Settings()
                for key, _ in battery.extras:
                    setattr(cfg, key, getattr(defaults, key))
        save_config(cfg)
        return cfg
