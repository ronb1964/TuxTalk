"""The dictation service's tick, which both input backends run each pass:
the hotkey loop (hotkeys.run) and the Flatpak's portal loop
(input_backends). It takes the start and stop requests the tray, D-Bus or
the portal made, lets the speech model rest when Power Saving is on, and
picks up settings changed in Preferences without a restart."""
import time

from . import config as config_module
from . import (
    clipboard,
    dictation_worker,
    engine,
    hotkey_keys,
    keyboard_grab,
    keyboard_output,
    pipeline,
    recording,
)
from . import live_settings as live
from . import recording_state as rs
from .config import load_config
from .logger import setup_logger

logger = setup_logger(__name__)

# How often to check whether the config file changed on disk. Preferences
# doesn't restart the service for settings that can be applied live, so this
# poll is what makes them take effect. One stat() per second.
CONFIG_RECHECK_SECONDS = 1.0


def _reload_live_settings(cfg, indicator):
    """Refresh settings that can change without restarting the service.

    Updates *cfg* IN PLACE — the whole service holds a reference to that one
    object and reads most settings off it at the point of use, so refreshing
    its fields is what makes them live.

    Deliberately leaves model and device alone: those describe the speech model
    that is already loaded, and rewriting them would make the config disagree
    with reality. Changing them still restarts the service.

    Returns the hotkeys (hotkey_keys.Hotkeys) for the caller to rebind its loop
    locals, or None if the config could not be read — in which case the previous settings stay in
    force. A damaged config must never degrade a running dictation session.
    """
    try:
        fresh = load_config()
    except Exception as e:
        # Runs from the input loop; log once per change, and
        # never let anything escape into the loop.
        logger.warning(f"Could not reload settings, keeping current ones: {e}")
        return None

    try:
        for key in config_module.LIVE_APPLIED_KEYS:
            if hasattr(fresh, key) and hasattr(cfg, key):
                setattr(cfg, key, getattr(fresh, key))

        keyboard_output.key_delay_ms = cfg.key_delay

        live.apply(cfg)

        if indicator is not None:
            indicator.apply_settings(
                cfg.indicator_position,
                cfg.indicator_size,
                cfg.indicator_offset_x,
                cfg.indicator_offset_y,
                style=cfg.indicator_style,
                color_mode=cfg.indicator_color_mode,
                custom_color=cfg.indicator_color,
                sensitivity=cfg.indicator_sensitivity,
                follow_window=getattr(cfg, "indicator_follow_window", False),
            )

        return hotkey_keys.Hotkeys.from_settings(cfg)
    except Exception as e:
        logger.warning(f"Could not apply reloaded settings: {e}", exc_info=True)
        return None


class ServiceState:
    """Backend-agnostic per-service state (auto-timeout tracking), shared by the
    evdev loop (Backend A) and the portal loop (Backend B) via tick."""

    def __init__(self, cfg, input_device_idx=None):
        """Made once the model is ready and the input loop starts."""
        # Held, not copied: _reload_live_settings updates cfg IN PLACE, so
        # reading through it keeps the auto-timeout settings live.
        self._cfg = cfg
        self.last_activity = time.time()
        # 0.0 == "never checked", so the first idle tick reloads immediately.
        self.last_config_check = 0.0
        # The mic is configured by name but recorded from by device number, so
        # the number has to be re-resolved whenever the name changes. Owning it
        # here keeps one copy, which a reload can update.
        self.input_device_idx = input_device_idx
        self._resolved_mic = getattr(cfg, "mic", "")
        # Counted from 0 when the service said it was ready (app.main): one
        # sent while the model loaded was dropped there, one sent since counts.
        self.toggles_seen = 0

    def refresh_input_device(self) -> None:
        """Re-resolve the mic if its configured name changed.

        Called after a live reload. Resolving enumerates audio devices, so this
        is gated on the name actually changing rather than run every tick. A
        failure keeps the device currently in use — a mic that cannot be
        resolved must not take down the one that works.
        """
        mic = getattr(self._cfg, "mic", "")
        if mic == self._resolved_mic:
            return
        try:
            self.input_device_idx = recording.pick_input_device(mic)
            logger.info(f"Microphone changed to {mic!r} (device {self.input_device_idx})")
        except Exception as e:
            logger.warning(f"Could not resolve microphone {mic!r}, keeping current: {e}")
        # Either way, do not retry the same name every second.
        self._resolved_mic = mic

    @property
    def timeout_enabled(self) -> bool:
        return getattr(self._cfg, "auto_timeout_enabled", False)

    @property
    def timeout_minutes(self):
        return getattr(self._cfg, "auto_timeout_minutes", 5)

    @property
    def timeout_seconds(self):
        return self.timeout_minutes * 60


def tick(cfg, svc: "ServiceState") -> "hotkey_keys.Hotkeys | None":
    """One iteration of backend-agnostic service work: consume the thread-safe
    start/stop commands (set by the evdev loop OR the portal signal handlers),
    enforce the auto-timeout, and pick up settings changed in Preferences. Safe
    from either backend — keyboard_grab.release_all() is a no-op when nothing is
    grabbed (the portal case).

    Returns the reloaded hotkeys when the config changed, so a caller that
    keeps loop locals (the evdev loop's hold_key and toggle_key) can rebind them; None
    otherwise. The return value matters because live.config_file_changed() is
    edge-triggered: it reports a given change exactly once, so the caller cannot
    simply ask again.

    The microphone comes off *svc*, not a parameter: it is re-resolved on a
    live reload, and a caller holding its own copy would keep the startup one."""
    if recording.quit_asked:
        _quit(cfg, svc)
    now = time.time()
    _take_toggle(svc)
    _take_start_request(cfg, svc, now)
    _take_stop_request(cfg, svc, now)
    # Not mid-dictation, nor with one waiting to be typed; idle counts from key-up.
    if (svc.timeout_enabled and not rs.state.is_recording and engine.model is not None
            and dictation_worker.idle()
            and now - svc.last_activity > svc.timeout_seconds):
        engine.rest(svc.timeout_minutes)
    # Nor while a dictation is typed: a reload can stop the AI engine a
    # correction across a pause is using.
    if (not rs.state.is_recording and dictation_worker.idle()
            and now - svc.last_config_check >= CONFIG_RECHECK_SECONDS):
        return _reload_if_changed(cfg, svc, now)
    return None


def _take_toggle(svc: ServiceState) -> None:
    """A toggle (SIGUSR1) since the last pass: a start or a stop request.
    Several before one pass count as one, as letting the handler set the
    request itself did."""
    if recording.toggles_asked == svc.toggles_seen:
        return
    svc.toggles_seen = recording.toggles_asked
    if rs.state.is_recording:
        recording.stop_requested.set()
    else:
        recording.start_requested.set()


def _take_start_request(cfg, svc: ServiceState, now: float) -> None:
    """Start a recording if the tray, D-Bus or the portal asked for one."""
    if not recording.start_requested.is_set():
        return
    recording.start_requested.clear()
    # Only the Flatpak's toggle shortcut can double-tap; menus and the hold key can't.
    from_toggle = recording.started_from_toggle_key.is_set()
    recording.started_from_toggle_key.clear()
    if not rs.state.is_recording:
        recording.start_recording(cfg.beeps, cfg.notify, svc.input_device_idx,
                                  from_toggle=from_toggle)
        svc.last_activity = now


# How long quitting waits for the dictation worker: a long dictation on the
# processor, or the model woken from Power Saving, takes a while. Within
# service_launcher.QUIT_LIMIT_S, with the exit hooks.
QUIT_DRAIN_S = 20.0


def _quit(cfg, svc: ServiceState) -> None:
    """Quit (app's SIGTERM handler: Quit, Restart Service, an update): a
    recording under way stops as letting go of the key would (Recent
    Dictations gets it), what the dictation worker holds is typed, up to
    QUIT_DRAIN_S (past that, a notification says one wasn't typed), and the
    process exits, so its exit hooks run: the music plays again, the other
    sounds come back up. Killed outright, recordings not transcribed yet
    were lost without a word."""
    if rs.state.is_recording:
        recording.stop_requested.set()
        _take_stop_request(cfg, svc, time.time())
    if not dictation_worker.drain(QUIT_DRAIN_S):
        logger.warning(f"Quitting with dictation work still unfinished after {QUIT_DRAIN_S:g} s")
        from .notify import notify
        notify("A dictation wasn't typed",
               "TuxTalk was quitting and couldn't finish it in time. Say it again once "
               "TuxTalk is back.")
    clipboard.wait_for_x11_paste()
    logger.info("Quitting")
    raise SystemExit(0)


def _take_stop_request(cfg, svc: ServiceState, now: float) -> None:
    """Stop the recording and type it if the tray, D-Bus or the portal
    asked, or hands-free heard the speaker finish it."""
    asked = recording.stop_requested.is_set()
    if asked:
        recording.stop_requested.clear()
    if not (rs.state.is_recording and (asked or rs.state.current.went_quiet)):
        return
    keyboard_grab.release_all()
    pipeline.stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                            cfg.auto_space, cfg.auto_period, cfg.injection_mode)
    svc.last_activity = now


def _reload_if_changed(cfg, svc: ServiceState, now: float):
    """Pick up Preferences changes without a restart (indicator style and
    position, typing delay, etc.); the reloaded hotkeys, or None.

    Both backends run this from the tick: Backend B (the Flatpak's portal
    path) runs only the tick, so without it live settings would never apply
    there. Idle-only and throttled. _reload_live_settings updates cfg in
    place and pushes the new values into the running recording indicator."""
    svc.last_config_check = now
    # Custom commands live in their own file and are checked on the same
    # tick. Unconditionally, and before the config check: an edit to the
    # Commands tab alone leaves config.toml untouched, so gating this on a
    # config change would be gating it on the thing that did not happen.
    if live.commands_file_changed():
        live.reload_commands()
    if live.words_file_changed():
        live.reload_words()
    if not live.config_file_changed():
        return None
    # Saving settings is activity too. Switching the auto-timeout on must
    # not stop the service a second later because the clock still counts
    # from the last D-Bus command.
    svc.last_activity = now
    reloaded = _reload_live_settings(cfg, recording.indicator)
    # cfg.mic is fresh now; the device number behind it may not be.
    svc.refresh_input_device()
    return reloaded
