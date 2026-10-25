"""The dictation service's shell: one process that loads the speech model,
reads the hotkeys (hotkeys.py) and runs each dictation through the pipeline
(pipeline.py). Here are its single-instance lock and signals, the tick both
input backends run each pass (start and stop requests, Power Saving, live
settings), the command-line toggle when no tray runs, and main()."""
import os
# CRITICAL: Disable HuggingFace XET downloads BEFORE any imports
# XET bypasses tqdm_class progress tracking, breaking our download progress UI
os.environ["HF_HUB_DISABLE_XET"] = "1"

# Import torch_init FIRST to configure CUDA library paths before any torch imports
from .torch_init import init_cuda_for_pytorch
init_cuda_for_pytorch()

import signal
import sys
import threading

from . import engine, keyboard_output, recording, single_instance, tray_link, virtual_keyboard
from . import live_settings as live
from . import recording_state as rs
from .config import load_config, load_custom_commands
from .logger import setup_logger
from .recording_indicator import RecordingIndicator

logger = setup_logger(__name__)


# The single-instance lock's file, open while the service runs.
_lockfile_handle = None

# The service's own D-Bus service when no tray runs (_start_dbus_stand_in),
# held here for the life of the process.
dbus_service = None


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


def _handle_sigusr1(signum, frame):
    """SIGUSR1 = toggle recording. Sent by the tray process via os.kill().
    Sets the matching request; the service tick takes it.
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
    import dbus
    tray_link.call("NotifyServiceReady", dbus.UInt32(os.getpid()),
                   what="tell the tray the service is ready")


def main():
    """The dictation service: load the settings, start GTK (the stand-in
    D-Bus service and the recording indicator) and the speech model, then
    run the input loop for the life of the process."""
    cfg = _load_settings()
    input_device_idx = recording.pick_input_device(cfg.mic)
    gtk_ok = _start_gtk(cfg)
    _load_model(cfg, gtk_ok)
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


def _load_settings():
    """Take the single-instance lock and the signals, then load the settings
    and everything they switch on: the virtual keyboard, the live options,
    custom commands and Your Words. Returns the settings."""
    from . import custom_words, desktop_identity, ui_style
    desktop_identity.apply()        # TuxTalk's icon on its windows, not KDE's generic one
    ui_style.prepare_windows()   # emoji that show, buttons clear of panels and docks
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
        logger.info(f"Could not check for sounds left turned down: {e}")
    # The live options; the AI engine (if on) loads in the background.
    live.apply(cfg)

    live.commands = load_custom_commands()
    if live.commands:
        logger.info(f"Loaded {len(live.commands)} custom voice command(s)")
    live.words = custom_words.load()
    if live.words:
        logger.info(f"Loaded your words ({len(live.words)})")
    return cfg


def _start_gtk(cfg) -> bool:
    """Make what needs GTK's main loop (the stand-in D-Bus service and the
    recording indicator); whether GTK could be set up at all."""
    global dbus_service
    dbus_service = None
    recording.indicator = None
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk  # noqa: F401  (fails here when GTK can't load)
        dbus_service = _start_dbus_stand_in(cfg)
        if cfg.recording_indicator:
            _make_indicator(cfg)
        return True
    except Exception as e:
        logger.error(f"GTK initialization failed: {e}", exc_info=True)
        return False


def _start_dbus_stand_in(cfg):
    """The service's own D-Bus service, for the GNOME extension when no tray
    runs; None in the normal case, where the tray owns the name."""
    try:
        from .dbus_service import TuxTalkDBusService
        from .service_dbus import AppInstance

        app_instance = AppInstance(cfg, recording.start_requested, recording.stop_requested,
                                   _control_in_service)
        # A stand-in only: the tray owns the bus name (see
        # TuxTalkDBusService). This succeeds only with no tray running.
        service = TuxTalkDBusService(app_instance, primary=False)
        app_instance.dbus_service = service

        # Emit initial state so extension syncs properly
        service.emit_service_state(True)
        service.emit_model_changed(cfg.model)

        logger.info("D-Bus service started successfully (no tray running)")
        return service
    except Exception as e:
        if type(e).__name__ == "NameExistsException":
            # The normal case: the tray already owns the name.
            logger.info("The tray owns the D-Bus name; the service doesn't register its own")
        else:
            logger.error(f"D-Bus service initialization failed: {e}", exc_info=True)
        return None


def _make_indicator(cfg) -> None:
    """The recording indicator, with its spectrum for the bars style."""
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
        logger.info(f"Recording indicator initialized at position: {cfg.indicator_position}, "
                    f"size: {cfg.indicator_size}")
    except Exception as e:
        logger.error(f"Recording indicator initialization failed: {e}", exc_info=True)


def _load_model(cfg, gtk_ok: bool) -> None:
    """Load the speech model, and start GTK's main loop when GTK is there."""
    # Outside _start_gtk's try: a model that fails to load is not a GTK
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
        _start_gtk_loop()

    if engine.model is None:
        engine.model = engine.build_or_exit(cfg)
    logger.info(f"Configuration: model={cfg.model}, device={cfg.device}, "
                f"language={cfg.language or 'auto'}, auto_space={cfg.auto_space}, "
                f"auto_period={cfg.auto_period}")
    engine.notify_tray()


def _start_gtk_loop() -> None:
    """One GTK main loop, on its own thread, for D-Bus and the indicator."""
    from gi.repository import Gtk

    def run_gtk_loop():
        print("Starting GTK main loop...")
        Gtk.main()

    threading.Thread(target=run_gtk_loop, daemon=True).start()
    engine.gtk_loop_running = True
    print("GTK main loop started in background thread")


def _run_service_entry():
    """Entry for `python -m tuxtalk.app` (the dictation service).

    `-m` runs this file as the module "__main__", but the input backends import
    it as "tuxtalk.app", a DIFFERENT module object with its own globals. Running
    the package module's main() keeps the whole service in ONE namespace, so the
    globals main() sets (the ready flag, the lock, the stand-in D-Bus service)
    are the ones the input loop's tick reads.
    """
    from tuxtalk.app import main
    main()


if __name__ == "__main__":
    _run_service_entry()
