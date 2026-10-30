"""First run, from the welcome screen to Setup Complete: the optional GNOME
extension, the hotkey test, the speech model the welcome card chose (on the
graphics card or the processor, with fallbacks when a download fails), the
AppImage put in place, and the last screen.
"""

import os

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk

from .hotkey_test_dialog import show_hotkey_test_dialog
from .logger import setup_logger
from .setup_complete_dialog import show_setup_complete_dialog
from .ui_style import message_dialog, show_message
from .welcome_dialog import show_welcome_dialog

logger = setup_logger(__name__)

# A failed first-run download is offered again at most this many times.
_FIRST_RUN_RETRIES = 2


def _vulkan_speed_check_first_run(model):
    """True if the graphics chip should run *model*: the same speed check (and
    messages) as choosing Vulkan in Preferences.

    Without it, the small graphics built into some processors could end up
    slower than the processor itself. Any error keeps the processor, as
    Preferences does.
    """
    try:
        from tuxtalk import vulkan_setup_dialogs
        return vulkan_setup_dialogs.run_speed_check(None, model)
    except Exception as e:
        logger.warning(f"First-run speed check failed, using the processor: {e}")
        return False


def _download_vulkan_model_first_run(model):
    """Download the graphics engine and *model* in whisper.cpp's format, as
    Preferences does: (both here now, cancelled). An error counts as a
    failed download, so setup goes on on the processor."""
    try:
        from tuxtalk import vulkan_setup_dialogs
        return vulkan_setup_dialogs.fetch_files(None, model)
    except Exception as e:
        logger.warning(f"Vulkan model download failed: {e}")
        return False, False


def _download_processor_model(model):
    """Download *model* for the processor, as Preferences does, without
    loading it: the dictation service loads it when it starts. (Here now,
    cancelled.)"""
    from tuxtalk.model_helper import download_model_files
    return download_model_files(None, model)


def _processor_model(model, result):
    """The model to use when setup ends up on the processor after all. Large-v3
    needs a graphics card (the card greys it out without one), so it becomes
    what the card recommends there: Whisper Small."""
    from tuxtalk import recommend
    return recommend.recommend(result.get("dictation_language") or "en",
                               recommend.Hardware(None), model=model).model


def _ask_retry_download(model):
    """After a failed first-run download: True to try *model* again, False
    to use a smaller model."""
    from .recommend import model_title
    msg = message_dialog(
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.NONE,
        text=f"Try downloading {model_title(model)} again?",
    )
    msg.format_secondary_text(
        "The download didn't finish, often just a slow moment on the network. "
        "You can try again, or use a smaller model for now and switch later.")
    msg.add_button("Use a Smaller Model", Gtk.ResponseType.NO)
    msg.add_button("Try Again", Gtk.ResponseType.YES)
    msg.set_default_response(Gtk.ResponseType.YES)
    answer = msg.run()
    msg.destroy()
    return answer == Gtk.ResponseType.YES


def _tell_using_smaller_model(chosen, used):
    """Say that setup fell back from *chosen* to *used*, and how to go back."""
    from .recommend import model_title
    show_message(None,
                 Gtk.MessageType.INFO,
                 f"Using {model_title(used)} for now",
                 f"{model_title(chosen)} couldn't be downloaded, so TuxTalk will use "
                 f"{model_title(used)}. To switch later, open Preferences, General and "
                 "click Use Recommended.")


def _use(model, device=None):
    """Save *model* (and *device*, when given) as what dictation uses. Read
    afresh each time: the steps before may have saved what they learned (the
    speed check records vulkan_slower), and saving an older copy would
    quietly undo it."""
    from tuxtalk.config import load_config, save_config
    config = load_config()
    config.model = model
    if device:
        config.device = device
    save_config(config)


def _save_choice(result):
    """Save the setup the welcome card showed. The update notice is marked
    shown, since a new install starts on the recommendation already."""
    from tuxtalk.config import load_config, save_config
    try:
        config = load_config()
        config.model = result["model"]
        config.device = result["device"]
        config.dictation_language = result.get("dictation_language", "")
        config.recommend_notice_shown = True
        save_config(config)
    except Exception as e:
        logger.warning(f"Could not save the first-run setup: {e}")


def _set_up_graphics(model, result):
    """On the graphics card, fetch the Vulkan engine and *model* in its
    format, then run the speed check. Returns (model, ready, cancelled):
    ready when *model* runs on the graphics card; otherwise setup goes on on
    the processor, with the model for it (_processor_model) saved, and
    cancelled says the user cancelled the graphics download."""
    from tuxtalk import whisper_vulkan
    from tuxtalk.config import load_config
    if load_config().device.lower() != "vulkan":
        return model, False, False
    if not whisper_vulkan.supports_model(model):
        model = _processor_model(model, result)
        _use(model, "cpu")
        return model, False, False
    # One download window: it fetches the engine too when it's missing.
    downloaded, cancelled = _download_vulkan_model_first_run(model)
    if downloaded and _vulkan_speed_check_first_run(model):
        _use(model)
        logger.info(f"{model} ready for Vulkan")
        return model, True, False
    # The graphics card didn't work out: fall back to the processor, so
    # setup still ends with a model that works.
    model = _processor_model(model, result)
    _use(model, "cpu")
    logger.warning(f"Not using Vulkan after first-run setup; {model} on the processor")
    return model, False, cancelled


def _retry(model):
    """Offer *model*'s download again, at most _FIRST_RUN_RETRIES times:
    (downloaded, cancelled). Both False when the user would rather have a
    smaller model, or every retry failed."""
    for _ in range(_FIRST_RUN_RETRIES):
        if not _ask_retry_download(model):
            break
        downloaded, cancelled = _download_processor_model(model)
        if downloaded:
            _use(model)
            logger.info(f"{model} downloaded on a retry")
            return True, False
        if cancelled:
            logger.info(f"Retry of {model} cancelled; leaving it for later")
            return False, True
    return False, False


def _after_failed_download(model):
    """The download failed, and its window said why. The user may try it
    again; otherwise use a smaller model that is already downloaded, or try
    ONE smaller download, and say so. True when a model is ready."""
    from tuxtalk.model_helper import is_model_cached_fast
    logger.warning(f"Model download failed for {model}")
    downloaded, cancelled = _retry(model)
    if downloaded or cancelled:
        return downloaded
    fallback_models = ["small", "tiny"] if model not in ("small", "tiny") else ["tiny"]
    cached = next((m for m in fallback_models if is_model_cached_fast(m)), None)
    if cached:
        logger.info(f"Using cached {cached} model as fallback")
        _use(cached)
        _tell_using_smaller_model(model, cached)
        return True
    fallback = fallback_models[0]
    logger.info(f"Attempting fallback to {fallback} model...")
    try:
        if _download_processor_model(fallback)[0]:
            logger.info(f"Fallback to {fallback} model successful")
            _use(fallback)
            _tell_using_smaller_model(model, fallback)
            return True
        logger.warning(f"Fallback to {fallback} did not download either")
    except Exception as e:
        logger.warning(f"Error downloading fallback {fallback}: {e}")
    return False


def _emergency_tiny():
    """After an unexpected error, make sure SOME model works: Tiny on the
    processor (whisper.cpp has no copy of Tiny, so the device goes too).
    True when it is there."""
    from tuxtalk.model_helper import is_model_cached_fast
    try:
        if not is_model_cached_fast("tiny"):
            logger.info("Downloading tiny model as emergency fallback...")
            if not _download_processor_model("tiny")[0]:
                return False
        _use("tiny", "cpu")
        return True
    except Exception:
        logger.warning("Emergency Tiny fallback failed", exc_info=True)
        return False


def _apply_first_run_setup(result):
    """Save the setup the welcome card showed, then download it. True when
    a model is ready to dictate with; False when none was downloaded (a
    Cancel, or no download worked), which the dictation service then offers
    again when it starts.

    *result* is WelcomeDialog.run()'s: model, device ("vulkan" or "cpu") and
    dictation_language. On the graphics card this fetches the Vulkan engine
    and the model in its format, then runs the speed check; if any of that
    doesn't work out it falls back to the processor. CUDA is never fetched
    here (it lives in Preferences).
    """
    from tuxtalk.model_helper import is_model_cached_fast
    _save_choice(result)
    model = result["model"]
    logger.info(f"Setting up {model} on {result['device']}")
    try:
        model, ready, cancelled = _set_up_graphics(model, result)
        if ready:
            return True
        if is_model_cached_fast(model):
            # Already on disk: save the choice and leave loading to the
            # dictation service.
            logger.info(f"{model} already downloaded, saving config")
            _use(model)
            return True
        if cancelled:
            # Cancel in the graphics download window: no processor download
            # straight after it. The service offers that one when it starts.
            logger.info(f"First-run download cancelled; leaving {model} for later")
            return False
        downloaded, cancelled = _download_processor_model(model)
        if downloaded:
            _use(model)
            logger.info(f"{model} model ready")
            return True
        if cancelled:
            # "Not now": no surprise downloads after a Cancel. The setting
            # stays, and the service offers the download when it starts.
            logger.info(f"First-run download cancelled; leaving {model} for later")
            return False
        return _after_failed_download(model)
    except Exception:
        logger.warning(f"First-run setup of {model} failed", exc_info=True)
        return _emergency_tiny()


def _install_extension(result):
    """Install the GNOME extension when the welcome screen's box was ticked.
    True when it went in."""
    if not result.get('install_extension', False):
        return False
    try:
        from tuxtalk.download_progress_dialog import (EXTENSION_TASK,
                                                      show_unified_download_dialog,
                                                      succeeded)
        download_results = show_unified_download_dialog(cuda=False, extension=True,
                                                        parent=None)
        logger.info(f"Download results: {download_results}")
        return succeeded(download_results, EXTENSION_TASK)
    except Exception as e:
        logger.error(f"Error during installations: {e}", exc_info=True)
        return False


def _offer_kde_notification_help():
    """On the KDE Flatpak, raise the help window for turning off the "Remote
    control session started" popup, which starts firing after every
    dictation.

    Onboarding text is gone the moment the user clicks through, and they need
    the steps ON SCREEN while they're in System Settings, so the persistent,
    always-on-top window opens now (deferred to the tray's main loop so it's
    interactive). It's reopenable from Preferences too.
    """
    try:
        from .desktop_detect import get_desktop_environment
        if not (os.environ.get("FLATPAK_ID") and get_desktop_environment() == "kde"):
            return
        from .notification_help import show_notification_help_window

        # One-shot, briefly delayed so the shortcut picker appears first
        # (it must return False to run once — returning the truthy window
        # would make the timeout re-fire and keep re-presenting the window).
        def _show_help_once():
            show_notification_help_window()
            return False
        GLib.timeout_add(3000, _show_help_once)
    except Exception as e:
        logger.warning(f"Could not show KDE notification help window: {e}")


def show_welcome_and_install():
    """
    Run first-run setup from start to end: the welcome screen, the GNOME
    extension if ticked, the hotkey test, the speech model, the AppImage put
    in place and Setup Complete. The tray's entry point for a first run.

    Returns:
        dict: WelcomeDialog.run()'s result, or None when it failed
    """
    result = show_welcome_dialog()
    if not result or not result.get('continue'):
        logger.info("User cancelled welcome dialog")
        return result

    # The GNOME extension is the welcome screen's only optional download; the
    # speech model (and the Vulkan engine) follow the hotkey test.
    extension_installed = _install_extension(result)

    # Not on the Flatpak: the hotkey test detects key presses via evdev (raw
    # /dev/input), which the sandbox blocks ("No keyboards found for evdev"),
    # and the F8/F9 it shows are the evdev defaults, not the portal shortcuts
    # the desktop actually assigns. So it can't test anything useful there.
    if os.environ.get("FLATPAK_ID"):
        logger.info("Flatpak: skipping evdev hotkey test (portal shortcuts in use)")
    else:
        logger.info("Showing hotkey test dialog")
        show_hotkey_test_dialog()

    # Then download exactly what the welcome card showed.
    model_ready = _apply_first_run_setup(result) if "model" in result else True

    from tuxtalk import appimage_copy
    appimage_installed, launcher_created = appimage_copy.put_in_place()

    # needs_restart carries the deferred restart reminder here: if the user fixed
    # device permissions during setup (uinput_fixed), typing/hotkeys won't work
    # until a reboot, so the final screen offers "Restart computer now" instead of an
    # interrupting popup earlier in the flow.
    show_setup_complete_dialog(
        appimage_installed=appimage_installed,
        launcher_created=launcher_created,
        needs_restart=bool(result.get('needs_restart', result.get('uinput_fixed'))),
        extension_installed=extension_installed,
        model_ready=model_ready,
    )
    _offer_kde_notification_help()
    logger.info("First-run setup completed")
    return result
