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

from . import (engine, gtk_loop, keyboard_output, recording, single_instance, tray_link,
               virtual_keyboard)
from . import live_settings as live
from . import recording_state as rs
from .config import load_config, load_custom_commands
from .logger import setup_logger
from .recording_indicator import RecordingIndicator
from .service_launcher import QUIT_LIMIT_S, QUIT_WAIT_S

logger = setup_logger(__name__)


# The single-instance lock's file, open while the service runs.
_lockfile_handle = None
# How long a new service waits for the old one's lock: its quit.
LOCK_WAIT_S = QUIT_WAIT_S

# The service's own D-Bus service when no tray runs (_start_dbus_stand_in),
# held here for the life of the process.
dbus_service = None


def _acquire_single_instance():
    """Take the service's single-instance lock (single_instance.py), or exit
    if another service holds it. A restart starts this one while the old
    service may still be finishing what it types (service_tick._quit): the
    lock is waited for that long, and a little more."""
    global _lockfile_handle
    try:
        lockfile = single_instance.open_lock("talktype.lock")
        claimed = single_instance.claim(lockfile, wait_s=LOCK_WAIT_S)
    except OSError as e:        # no runtime folder, or it's full: not "already running"
        logger.warning(f"Could not take the single-instance lock: {e}")
        return
    if not claimed:
        print("Another TuxTalk instance is already running. Exiting.", file=sys.stderr)
        sys.exit(0)
    _lockfile_handle = lockfile     # closing it, even by garbage collection, lets go


def _handle_sigusr1(signum, frame):
    """SIGUSR1 = toggle recording. Sent by the tray process via os.kill().
    Only counted here; the service tick turns it into a start or a stop. An
    Event set in a handler could wait forever on a lock the interrupted
    code holds (Event.clear on the same thread)."""
    recording.toggles_asked += 1


def _handle_sigterm(signum, frame):
    """SIGTERM (Quit, Restart Service, an update): the input loop's next
    pass (service_tick._quit) finishes what's being typed, then the process
    exits. Before the loop runs (waiting for the lock, the model loading)
    there's nothing to finish.

    The alarm is the quit's deadline: SIGALRM keeps its default action
    (_install_signal_handlers), so the kernel ends the process QUIT_LIMIT_S
    after this runs, whatever any thread is doing then (a hung exit hook,
    native code holding the GIL); a thread of our own couldn't promise that.
    Python runs this only between bytecodes of the main thread, so native
    code there delays the deadline's start too: whoever stops the service
    escalates as well (service_launcher.end_dictation_service). A second
    SIGTERM leaves the deadline alone. A plain flag, not an Event: Event.set
    takes a lock the interrupted code may hold."""
    if recording.quit_asked:
        return
    recording.quit_asked = True
    signal.alarm(QUIT_LIMIT_S)
    if not _service_ready:
        raise SystemExit(0)


def _install_signal_handlers():
    """Handle SIGUSR1 and SIGTERM, and ignore SIGUSR2, from the very start:
    unhandled, the first two end the process, and the tray and Preferences
    can send them while the model is still loading (an older Preferences
    sent SIGUSR2 for hotkey test mode). Ctrl+C and a terminal hanging up
    (SIGINT, SIGHUP: the tray's terminal reaches its service too) quit as
    SIGTERM does, typing what's held. SIGALRM gets its default action and
    is unblocked, whatever this process inherited: it is the quit's deadline."""
    signal.signal(signal.SIGUSR1, _handle_sigusr1)
    signal.signal(signal.SIGUSR2, signal.SIG_IGN)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _handle_sigterm)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM})


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
        if rs.state.is_recording:
            recording.stop_requested.set()
        else:
            recording.start_requested.set()
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
    tray_link.call("NotifyServiceReady", os.getpid(), signature="u",
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
    # The signals first: a restart's tray signals this service by its PID
    # (a toggle from the GNOME menu) while it still waits for the lock, and
    # an unhandled SIGUSR1 ends the process.
    _install_signal_handlers()
    _acquire_single_instance()

    cfg = load_config()
    _log_what_runs(cfg)
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


def _log_what_runs(cfg) -> None:
    """The version, how it's installed and the hands-free setting, at the top
    of each start in the log: a log sent with a bug report then says what it
    came from."""
    from . import __version__
    from .update_checker import get_install_type
    hands_free = (f"stops after {cfg.auto_stop_seconds:g} s of quiet" if cfg.auto_stop_silence
                  else "off")
    logger.info(f"TuxTalk {__version__} ({get_install_type()}) starting; "
                f"hands-free (double-tap): {hands_free}")


def _start_gtk(cfg) -> bool:
    """Start GTK's main loop and make what needs it, the stand-in D-Bus
    service and the recording indicator, on its thread; whether GTK could be
    set up at all."""
    global dbus_service
    dbus_service = None
    recording.indicator = None
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk  # noqa: F401  (fails here when GTK can't load)
    except Exception as e:
        logger.error(f"GTK initialization failed: {e}", exc_info=True)
        return False
    gtk_loop.start()
    gtk_loop.call(_make_gtk_parts, cfg)
    return True


def _make_gtk_parts(cfg) -> None:
    """On GTK's thread: the stand-in D-Bus service, and the indicator if it's on."""
    global dbus_service
    dbus_service = _start_dbus_stand_in(cfg)
    if cfg.recording_indicator:
        _make_indicator(cfg)


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
    """Load the speech model. One still to be downloaded is downloaded on
    GTK's thread, which its windows need; one already downloaded (the
    normal case) loads here, while GTK's loop answers D-Bus meanwhile."""
    from .model_helper import is_model_cached_fast
    if gtk_ok and not is_model_cached_fast(cfg.model):
        engine.model = gtk_loop.call(engine.build_or_exit, cfg)
    else:
        engine.model = engine.build_or_exit(cfg)
    engine.built_from = cfg
    logger.info(f"Configuration: model={cfg.model}, device={cfg.device}, "
                f"language={cfg.language or 'auto'}, auto_space={cfg.auto_space}, "
                f"auto_period={cfg.auto_period}")
    engine.notify_tray()


def _run_service_entry():
    """Entry for `python -m tuxtalk.app` (the dictation service).

    `-m` runs this file as the module "__main__", and anything importing
    "tuxtalk.app" (the tests do) gets a DIFFERENT module object with its own
    globals. Running the package module's main() keeps one copy of what
    main() sets: the ready flag, the lock and the stand-in D-Bus service.
    """
    from tuxtalk.app import main
    main()


if __name__ == "__main__":
    _run_service_entry()
