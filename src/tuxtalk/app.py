import os
# CRITICAL: Disable HuggingFace XET downloads BEFORE any imports
# XET bypasses tqdm_class progress tracking, breaking our download progress UI
os.environ["HF_HUB_DISABLE_XET"] = "1"

# Import torch_init FIRST to configure CUDA library paths before any torch imports
from .torch_init import init_cuda_for_pytorch
init_cuda_for_pytorch()

import sys
import time
import signal


from .config import load_config, load_custom_commands
# Imported as a module too, so the live-settings reload can read CONFIG_PATH and
# LIVE_APPLIED_KEYS without pulling each name in separately.
from . import config as config_module
from .logger import setup_logger
from .recording_indicator import RecordingIndicator
from . import (focused_window, single_instance, virtual_keyboard)
from . import recording_state as rs
from . import live_settings as live
from . import keyboard_grab
from . import keyboard_output
from . import tray_link
from . import engine
from . import recording
from . import type_at_pause
from . import pipeline
from . import hotkeys
import threading

logger = setup_logger(__name__)


# Optional desktop notifications via libnotify

def _acquire_single_instance():
    """Take the service's single-instance lock (single_instance.py), or exit
    if another service holds it."""
    global _lockfile_handle
    try:
        lockfile = single_instance.open_lock("talktype.lock")
        claimed = single_instance.claim(lockfile)
    except OSError as e:        # no runtime folder, or it's full: not "already running"
        logger.warning(f"Could not take the single-instance lock: {e}")
        return
    if not claimed:
        print("Another TuxTalk instance is already running. Exiting.", file=sys.stderr)
        sys.exit(0)
    _lockfile_handle = lockfile     # closing it, even by garbage collection, lets go


# The single-instance lock's file, open while the service runs.
_lockfile_handle = None


dbus_service = None

# Undo voice-command parsing/length logic lives in tuxtalk.undo so it can
# be unit-tested without dragging in heavy audio/CUDA imports.




def _handle_sigusr1(signum, frame):
    """SIGUSR1 = toggle recording. Sent by the tray process via os.kill().
    Sets the appropriate Event; the evdev loop consumes it from the main thread.
    """
    if rs.state.is_recording:
        recording.stop_requested.set()
    else:
        recording.start_requested.set()


def _handle_sigusr2(signum, frame):
    """SIGUSR2 is accepted and ignored: unhandled, it ends the process, and an
    older Preferences may still send one to switch hotkey test mode."""
    logger.info("Ignored SIGUSR2 (hotkey test mode follows Preferences' flag file)")


def _install_signal_handlers():
    """Handle SIGUSR1 and SIGUSR2 from the very start. Both end a process
    that doesn't handle them, and the tray and Preferences can send them
    while the model is still loading."""
    signal.signal(signal.SIGUSR1, _handle_sigusr1)
    signal.signal(signal.SIGUSR2, _handle_sigusr2)



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

    Returns the hotkeys (hotkeys.Hotkeys) for the caller to rebind its loop
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

        return hotkeys.Hotkeys.from_settings(cfg)
    except Exception as e:
        logger.warning(f"Could not apply reloaded settings: {e}", exc_info=True)
        return None



# --- Voice editing ---




class _ServiceState:
    """Backend-agnostic per-service state (auto-timeout tracking), shared by the
    evdev loop (Backend A) and the portal loop (Backend B) via _service_tick."""

    def __init__(self, cfg, input_device_idx=None):
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


def _service_tick(cfg, svc: "_ServiceState") -> "hotkeys.Hotkeys | None":
    """One iteration of backend-agnostic service work: consume the thread-safe
    start/stop commands (set by the evdev loop OR the portal signal handlers),
    enforce the auto-timeout, and pick up settings changed in Preferences. Safe
    from either backend — _release_all_grabs() is a no-op when nothing is
    grabbed (the portal case).

    Returns the reloaded hotkeys when the config changed, so a caller that
    keeps loop locals (the evdev loop's hold_key and toggle_key) can rebind them; None
    otherwise. The return value matters because _config_file_changed() is
    edge-triggered: it reports a given change exactly once, so the caller cannot
    simply ask again.

    The microphone comes off *svc*, not a parameter: it is re-resolved on a
    live reload, and a caller holding its own copy would keep the startup one."""
    now = time.time()
    if recording.start_requested.is_set():
        recording.start_requested.clear()
        # Only the Flatpak's toggle shortcut can double-tap; menus and the hold key can't.
        from_toggle = recording.started_from_toggle_key.is_set()
        recording.started_from_toggle_key.clear()
        if not rs.state.is_recording:
            recording.start_recording(cfg.beeps, cfg.notify, svc.input_device_idx,
                                      from_toggle=from_toggle)
            svc.last_activity = now
    if recording.stop_requested.is_set():
        recording.stop_requested.clear()
        if rs.state.is_recording:
            keyboard_grab.release_all()
            if os.environ.get("FLATPAK_ID"):
                # Backend B (Flatpak): transcription + portal typing are slow
                # and would freeze THIS GLib loop — and F3/F4 with it — for as
                # long as they run (10–20s on a CPU-only box). Snapshot the
                # recording here (fast) and run the slow half on a worker
                # thread so the hotkey stays responsive. type_at_pause.inject_lock serialises
                # workers so two dictations never transcribe/type at once.
                cap = recording.finish_capture(cfg.beeps, cfg.notify)
                if cap is not None:
                    frames, rec_sr = cap
                    if live.chunk_pause_seconds > 0:
                        # Chunked dictation: through the chunk worker, so the
                        # rest is typed after this recording's chunks and
                        # before the next recording's. No wait, as below.
                        role = "last" if rs.state.chunks_submitted else None
                        if role:
                            frames = recording.drop_speechless_rest(frames)
                        type_at_pause.chunk_worker().submit(
                            lambda frames=frames, rec_sr=rec_sr, cfg=cfg, role=role,
                            message=rs.state.message: pipeline.transcribe_and_inject(
                                frames, rec_sr, cfg.beeps, cfg.smart_quotes,
                                cfg.notify, cfg.language, cfg.auto_space,
                                cfg.auto_period, cfg.injection_mode, chunk_role=role,
                                message=message))
                    else:
                        def _worker(frames=frames, rec_sr=rec_sr, cfg=cfg,
                                    message=rs.state.message):
                            with type_at_pause.inject_lock:
                                pipeline.transcribe_and_inject(
                                    frames, rec_sr, cfg.beeps, cfg.smart_quotes,
                                    cfg.notify, cfg.language, cfg.auto_space,
                                    cfg.auto_period, cfg.injection_mode, message=message)
                        threading.Thread(target=_worker, daemon=True,
                                         name="tt-inject").start()
            else:
                pipeline.stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                               cfg.auto_space, cfg.auto_period, cfg.injection_mode)
            svc.last_activity = now
    # Not mid-dictation (every worker holds inject_lock); idle counts from key-up.
    if (svc.timeout_enabled and not rs.state.is_recording and engine.model is not None
            and not type_at_pause.inject_lock.locked()
            and now - svc.last_activity > svc.timeout_seconds):
        engine.rest(svc.timeout_minutes)

    # Pick up Preferences changes without a restart — indicator style/position,
    # typing delay, etc. The evdev loop (Backend A) does this inline; Backend B
    # (the portal path used by the Flatpak) runs ONLY this tick, so without the
    # same recheck live settings never apply there — the recording indicator
    # stays on whatever it was built with. Idle-only and throttled, mirroring the
    # evdev path. _reload_live_settings updates cfg in place and pushes the new
    # values into the running recording indicator.
    if not rs.state.is_recording and now - svc.last_config_check >= CONFIG_RECHECK_SECONDS:
        svc.last_config_check = now
        # Custom commands live in their own file and are checked on the same
        # tick. Unconditionally, and before the early return below: an edit to
        # the Commands tab alone leaves config.toml untouched, so gating this on
        # a config change would be gating it on the thing that did not happen.
        if live.commands_file_changed():
            live.reload_commands()
        if live.words_file_changed():
            live.reload_words()
        if live.config_file_changed():
            # Saving settings is activity too. Switching the auto-timeout on
            # must not stop the service a second later because the clock still
            # counts from the last D-Bus command.
            svc.last_activity = now
            reloaded = _reload_live_settings(cfg, recording.indicator)
            # cfg.mic is fresh now; the device number behind it may not be.
            svc.refresh_input_device()
            return reloaded
    return None



# tuxtalk --toggle/--start/--stop (control.py). The tray normally answers
# them; these serve a service running without a tray, which then owns the
# D-Bus name itself.
_service_ready = False
_ready_lock = threading.Lock()
_waiting_start = None       # control.WaitingStart, made on first use


def _control_in_service(action):
    """A command-line action for a service with no tray; returns its status."""
    from . import control
    global _waiting_start
    with _ready_lock:
        if not _service_ready:
            if _waiting_start is None:
                _waiting_start = control.WaitingStart()
            return _waiting_start.apply(action)
    send, status = control.act_now(action, rs.state.is_recording)
    if send:
        _handle_sigusr1(None, None)
    return status


def _mark_service_ready():
    """From here a toggle is acted on: start a recording the command line
    asked for meanwhile, and tell the tray (NotifyServiceReady)."""
    global _service_ready
    with _ready_lock:
        _service_ready = True
        start = _waiting_start is not None and _waiting_start.take()
    if start:
        logger.info("Starting the recording the command line asked for")
        recording.start_requested.set()
    from .service_launcher import write_ready_pid
    write_ready_pid(os.getpid())
    try:
        import dbus
        tray_link.get_proxy().NotifyServiceReady(
            dbus.UInt32(os.getpid()),
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=focused_window.TRAY_TIMEOUT_S,
        )
    except Exception as e:
        logger.debug(f"Could not tell the tray the service is ready: {e}")



def main():
    from . import desktop_identity, ui_style
    desktop_identity.apply()        # TuxTalk's icon on its windows, not KDE's generic one
    ui_style.prepare_windows()   # emoji that show, buttons clear of panels and docks
    # The loaded speech model. Usually built after the GTK loop starts; built
    # earlier when it must first be downloaded (see the GTK section below).
    _acquire_single_instance()
    _install_signal_handlers()

    cfg = load_config()
    engine.guard_large_v3_without_cuda(cfg)

    keyboard_output.key_delay_ms = cfg.key_delay
    # Made now, so the desktop has taken it on long before the first
    # dictation is typed. The Flatpak types through the portal instead.
    if not os.environ.get("FLATPAK_ID"):
        virtual_keyboard.start()

    # A service that stopped mid-dictation may have left other apps quiet.
    try:
        from .audio_duck import restore_left_over
        restore_left_over()
    except Exception as e:
        logger.debug(f"Could not check for sounds left turned down: {e}")
    # The live options; the AI engine (if on) loads in the background.
    live.apply(cfg)

    live.commands = load_custom_commands()
    if live.commands:
        logger.info(f"Loaded {len(live.commands)} custom voice command(s)")
    from . import custom_words
    live.words = custom_words.load()
    if live.words:
        logger.info(f"Loaded your words ({len(live.words)})")

    input_device_idx = recording.pick_input_device(cfg.mic)

    # The D-Bus service and the recording indicator both need GTK's main loop.
    global dbus_service
    dbus_service = None
    recording.indicator = None
    gtk_ok = False
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk

        # Initialize D-Bus service for GNOME extension integration
        try:
            from .dbus_service import TuxTalkDBusService

            from .service_dbus import AppInstance

            app_instance = AppInstance(cfg, recording.start_requested, recording.stop_requested,
                                       _control_in_service)
            # A stand-in only: the tray owns the bus name (see
            # TuxTalkDBusService). This succeeds only with no tray running.
            dbus_service = TuxTalkDBusService(app_instance, primary=False)
            app_instance.dbus_service = dbus_service

            # Emit initial state so extension syncs properly
            dbus_service.emit_service_state(True)
            dbus_service.emit_model_changed(cfg.model)

            logger.info("D-Bus service started successfully (no tray running)")
        except Exception as e:
            dbus_service = None
            if type(e).__name__ == "NameExistsException":
                # The normal case: the tray already owns the name.
                logger.info("The tray owns the D-Bus name; the service doesn't register its own")
            else:
                logger.error(f"D-Bus service initialization failed: {e}", exc_info=True)

        if cfg.recording_indicator:
            try:
                recording.indicator = RecordingIndicator(
                    position=cfg.indicator_position,
                    offset_x=cfg.indicator_offset_x,
                    offset_y=cfg.indicator_offset_y,
                    size=cfg.indicator_size,
                    style=cfg.indicator_style,
                    color_mode=cfg.indicator_color_mode,
                    custom_color=cfg.indicator_color,
                    sensitivity=cfg.indicator_sensitivity,
                    follow_window=getattr(cfg, "indicator_follow_window", False),
                )
                # Spectrum processor for the bars style; recording.feed_indicator uses it.
                from .indicator_dsp import SpectrumProcessor
                recording.spectrum_processor = SpectrumProcessor(bins=20)
                logger.info(f"Recording indicator initialized at position: {cfg.indicator_position}, size: {cfg.indicator_size}")
            except Exception as e:
                logger.error(f"Recording indicator initialization failed: {e}", exc_info=True)
        gtk_ok = True
    except Exception as e:
        logger.error(f"GTK initialization failed: {e}", exc_info=True)

    # Outside the try above: a model that fails to load is not a GTK
    # failure, and must not be loaded a second time below.
    if gtk_ok:
        # If the model still has to be downloaded, do it NOW, before the GTK
        # loop thread starts. The download shows GTK dialogs and runs them on
        # this thread; with Gtk.main() already running on the thread below,
        # two threads would drive GTK at once and crash the service. An
        # already-downloaded model (the normal case) loads later, after the
        # loop is up.
        from .model_helper import is_model_cached_fast
        if not is_model_cached_fast(cfg.model):
            engine.model = engine.build_or_exit(cfg)

        # Start single GTK main loop in a background thread for both D-Bus and recording indicator
        def run_gtk_loop():
            print("Starting GTK main loop...")
            Gtk.main()

        gtk_thread = threading.Thread(target=run_gtk_loop, daemon=True)
        gtk_thread.start()
        engine.gtk_loop_running = True
        print("GTK main loop started in background thread")

    if engine.model is None:
        engine.model = engine.build_or_exit(cfg)
    logger.info(f"Configuration: model={cfg.model}, device={cfg.device}, language={cfg.language or 'auto'}, auto_space={cfg.auto_space}, auto_period={cfg.auto_period}")
    engine.notify_tray()
    # A toggle sent while the model was loading would otherwise start a
    # recording seconds later, out of the blue, when the input loop starts.
    # One asked for on the command line waits in the tray instead (control.py).
    recording.start_requested.clear()
    recording.stop_requested.clear()
    recording.started_from_toggle_key.clear()
    _mark_service_ready()

    # Run the input backend chosen by FLATPAK_ID: evdev everywhere
    # (EvdevInputBackend -> hotkeys.run), portals inside a Flatpak.
    from .input_backends import get_input_backend
    get_input_backend(cfg=cfg, input_device_idx=input_device_idx).start()

def _run_service_entry():
    """Entry for `python -m tuxtalk.app` (the dictation service).

    `-m` runs this file as the module "__main__", but the input backends import
    it as "tuxtalk.app", a DIFFERENT module object with its own globals. Running
    the package module's main() keeps the whole service in ONE namespace, so the
    globals main() sets (model, _custom_commands, ...) are the ones the
    recording loop reads; otherwise transcription fails with
    `NameError: name 'model'`.
    """
    from tuxtalk.app import main
    main()


if __name__ == "__main__":
    _run_service_entry()
