"""The hotkeys, read from the keyboards themselves (evdev) or from the
installed hotkey helper: hold to dictate, tap to toggle, a double-tap for
hands-free, Shift for a voice edit, Esc to cancel, and the Voice Commands
and Paste Last Dictation combos. run() is the input loop; each pass it also
runs the service's shared tick (service_tick.tick), and keyboards that come
and go are picked up while no recording runs."""
from .logger import setup_logger
import os
import sys
import time
import atexit
import select
from evdev import InputDevice, ecodes, list_devices
from .config import Settings
from . import beeps
from .beeps import CANCEL_BEEP
from . import (delivery_notices, modifier_hotkeys, secure_input, send_countdown,
               uinput_helper)
from .hotkey_keys import (MODIFIER_CODES, check_modifiers_held, track_modifier)
from . import recording_state as rs
from . import live_settings as live
from . import keyboard_grab
from . import tray_link
from . import recording
from . import notices
from . import delivery
from . import pipeline
from . import hotkey_keys, service_tick

logger = setup_logger(__name__)

# Hotkey test mode: while Preferences' Test Hotkeys dialog keeps its flag file
# fresh (service_launcher.hotkey_test_flag_path), hotkey presses are reported via
# the D-Bus HotkeyPressed signal instead of starting/stopping recording. This
# keeps the evdev grab alive and avoids XWayland phantom key-repeat floods.
_hotkey_test_flag = None    # the flag's path, worked out on first use

_hotkey_test_was_on = False  # for logging when test mode starts and ends


def _hotkey_test_active() -> bool:
    """Whether the Test Hotkeys dialog is open: its flag exists and is fresh."""
    global _hotkey_test_flag, _hotkey_test_was_on
    from . import service_launcher
    if _hotkey_test_flag is None:
        _hotkey_test_flag = service_launcher.hotkey_test_flag_path()
    try:
        age = time.time() - os.path.getmtime(_hotkey_test_flag)
        on = age < service_launcher.HOTKEY_TEST_LEASE_S
    except OSError:
        on = False
    if on != _hotkey_test_was_on:
        _hotkey_test_was_on = on
        logger.info(f"Hotkey test mode {'on' if on else 'off'}")
    return on


def _claim_desktop_hotkeys(cfg):
    """Ask the desktop to keep the hotkeys away from the focused app.

    We read them below the desktop, so without this F8 also reaches the app
    ("~" in a terminal). On KDE a KWin script holds them (kwin_hotkeys); on
    GNOME the extension does, from the list sent to the tray here; on COSMIC
    shortcuts of its own (cosmic_hotkeys); on other X11 desktops the X server
    (x11_hotkeys). Called at startup and after every live settings change.
    None of it can fail dictation: the claims never raise, and the tray call
    is fire-and-forget.
    """
    from . import cosmic_hotkeys, gnome_hotkeys, kwin_hotkeys, x11_hotkeys
    kwin_hotkeys.claim(cfg)
    cosmic_hotkeys.claim(cfg)
    x11_hotkeys.claim(cfg)
    import dbus
    tray_link.call("NotifyClaimedHotkeys",
                   dbus.Array(gnome_hotkeys.accelerators(cfg), signature='s'),
                   what="send the hotkeys to the tray")


# How often to look for input devices that have appeared or come back.
# Frequent enough that a reconnected keyboard starts working on its own,
# rare enough that it costs nothing.
DEVICE_RESCAN_SECONDS = 3.0

# How long the input loop sleeps waiting for a key event before it does its
# other work: D-Bus start/stop commands, the auto-timeout, settings changes.
# Waiting on the devices instead of reading each one every few milliseconds
# lets an idle service sleep; a key press still wakes it at once.
INPUT_WAIT_S = 0.05

# The shortest pass through the input loop, so a mouse sending 1000 events a
# second can't spin the loop flat out.
INPUT_MIN_PASS_S = 0.005


def _devices_with_input(devices, timeout):
    """The devices with events waiting, after sleeping up to *timeout* for one."""
    # The hotkey helper may already hold events it took in while answering
    # another call (active_keys), or have a reconnect due: ready now.
    due = [d for d in devices if getattr(d, "needs_service", None) and d.needs_service()]
    if due:
        return due
    try:
        ready, _, _ = select.select(devices, [], [], timeout)
    except (OSError, ValueError):
        # A device closed under us. Reading every device finds it, and the
        # loop's error handling drops it.
        return list(devices)
    return ready


def _rediscover_devices(devices) -> int:
    """Add keyboards that have appeared since the last scan. Returns how many.

    Without this, a device dropped by _drop_dead_devices (after a wireless
    receiver blip, a USB reset or a resume from suspend) would be gone for the
    life of the process, and on a single-keyboard machine the hotkey would
    silently stop working.

    Matched on the device node path, which is what identifies a device across
    reconnects here. Non-keyboards are ignored so this never grows the poll
    list with mice or power buttons.
    """
    known = {getattr(d, "path", None) for d in devices}
    added = 0
    for path in list_devices():
        if path in known:
            continue
        try:
            dev = InputDevice(path)
            if not uinput_helper.is_keyboard_device(dev):
                # Closed now, not whenever it is garbage-collected: every
                # mouse and button node is opened again on each scan.
                dev.close()
                continue
            devices.append(dev)  # evdev opens it non-blocking already
            added += 1
            logger.info(f"Input device appeared, now monitoring: {dev.name}")
        except Exception as e:
            # A node we cannot open (permissions, disappeared mid-scan) must
            # not stop the rest of the scan.
            logger.debug(f"Could not open {path}: {e}")
    return added


def _drop_dead_devices(dead_devices, devices, cfg) -> None:
    """Remove input devices that failed mid-read, ending a recording we could
    no longer end any other way.

    A hold-key recording (the only kind that keeps the keyboards grabbed) is
    stopped by a key-up that can only come from the device the key was
    pressed on. If that device dies — a wireless receiver blip, a
    USB reset, a resume from suspend — the release event is unreachable and
    state.is_recording would stay True for the life of the process.

    That matters far more than the lost device: both the stranded-grab
    backstop and the auto-timeout are gated on `not state.is_recording`, so a
    stuck flag leaves every *other* keyboard exclusively grabbed indefinitely
    and stops the service ever timing out. The user cannot type anywhere, and
    the only way back is the tray. So: end the recording deliberately.
    """
    if not dead_devices:
        return

    for dev in dead_devices:
        if dev in devices:
            devices.remove(dev)

    # The hold key works whatever the configured mode, so the grabs, not the
    # mode, tell a hold recording apart. A toggle or hands-free recording
    # holds no grab and carries on: any keyboard can still stop it.
    if rs.state.is_recording and keyboard_grab.grabbed_devices:
        logger.warning(
            "Input device died while the hold hotkey was down — ending the "
            "recording, since its key-up can no longer arrive"
        )
        keyboard_grab.release_all()
        _stop_recording(cfg)

    dead_devices.clear()


# A combo press whose keys aren't all let go within this long is dropped, so a
# missed key release can never make TuxTalk paste out of the blue later.
PASTE_LAST_EXPIRY_S = 5.0

_SHIFT_CODES = frozenset({ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT})

# How long the end of a Shift+toggle edit waits for Shift to be let go.
EDIT_SHIFT_WAIT_S = 2.0


def _held_shift(held_modifiers) -> set:
    """The Shift keys among *held_modifiers*: Shift + the dictation key starts
    a voice edit instead of a dictation."""
    return set(held_modifiers) & _SHIFT_CODES


def _someone_elses_shortcut(held_modifiers) -> bool:
    """A dictation key pressed with modifiers the desktop doesn't hold back
    for us (kwin_hotkeys.wanted_shortcuts: the plain key, and Shift with it
    when voice editing is on). Such a press reaches the app (Alt+F8 resizes
    the window on KDE and GNOME), so it mustn't start a dictation as well."""
    allowed = _SHIFT_CODES if live.voice_editing else frozenset()
    return bool(set(held_modifiers) - allowed)


def _paste_last_dictation(cfg) -> None:
    """Type the newest Recent Dictations entry again, the way a dictation is
    typed (line breaks, injection mode, the trailing space)."""
    from .history import get_entries, _LINE_BREAK_MARKER
    entries = get_entries()
    if not entries:
        logger.info("Paste last dictation: nothing dictated yet")
        beeps.beep(cfg.beeps, *CANCEL_BEEP)
        return
    text = entries[0].replace("\n", _LINE_BREAK_MARKER)
    if getattr(cfg, "auto_space", True):
        text += " "
    logger.info("Paste last dictation")
    outcome = delivery.inject_text(text, cfg.injection_mode)
    if outcome in delivery_notices.NOTICES:
        notices.report_undelivered(outcome, cfg.beeps)


def _report_test_press(code, hold_key, toggle_key) -> None:
    """In the hotkey test, tell the tray which dictation key *code* is."""
    if code == hold_key:
        tray_link.notify_hotkey_pressed("hold")
    elif toggle_key and code == toggle_key:
        tray_link.notify_hotkey_pressed("toggle")


def _hide_modifier_hotkey(event, toggle_key, devices, held_modifiers) -> None:
    """Hide a Right Ctrl or Right Alt dictation key from the desktop as soon
    as it's down: what TuxTalk types meanwhile (the dictation a tap stops, a
    phrase of hands-free, a send counting down) would otherwise arrive as
    Ctrl or Alt shortcuts (Ctrl+W closes the tab). The hold key hides itself
    when it starts a dictation (_handle_key_event). Kept until it's let go,
    or until something lets go of every grab sooner; the desktop has been
    told it's up either way. Not with another modifier held: that press is
    the user's own shortcut (Ctrl+Alt+T with Right Ctrl), which hiding the
    key would break."""
    if (event.value == 1 and not keyboard_grab.grabbed_devices
            and (event.code == toggle_key or rs.state.is_recording)
            and not _someone_elses_shortcut(held_modifiers - {event.code})):
        keyboard_grab.grab_keyboards_hiding(devices, {event.code})
        rs.state.hidden_key = event.code
    elif event.value == 0:
        modifier_hotkeys.note_released(event.code)
        if event.code == rs.state.hidden_key:
            rs.state.hidden_key = None
            keyboard_grab.release_all()


def _toggle_tap_holds_send(event, toggle_key, held_modifiers) -> bool:
    """A press of the toggle key while a send counts down holds it, and does
    nothing else. Asked before a Right Ctrl or Right Alt toggle is hidden
    (_hide_modifier_hotkey): once hidden, the send's Enter, which waits for
    the key, could go out before the hold."""
    return (event.code == toggle_key and event.value == 1 and not rs.state.is_recording
            and not _someone_elses_shortcut(held_modifiers - {toggle_key})
            and not (live.voice_editing and _held_shift(held_modifiers))
            and send_countdown.hold(announce=True))


def _start_hold_recording(hold_key, held_modifiers, devices, cfg, input_device_idx) -> None:
    """The hold key went down: hold a send counting down, then hide the key
    (and Shift, for voice editing) from the desktop, and record.

    The send goes first: once the key is hidden, an Enter that waits for it
    to come up goes out. The grab keeps the hotkey from the focused app,
    without leaving the desktop thinking it's still held (see
    _grab_keyboards_hiding); start_recording lets go of it if the mic fails
    to open. Shift unhidden, the Ctrl+C that copies the selection would
    arrive as Ctrl+Shift+C."""
    held = send_countdown.hold(announce=False)
    shift = _held_shift(held_modifiers) if live.voice_editing else set()
    keyboard_grab.grab_keyboards_hiding(devices, {hold_key, *shift})
    if (recording.start_recording(cfg.beeps, cfg.notify, input_device_idx, edit_mode=bool(shift),
                        held_send=held) and rs.state.held_send):
        # The hold time counts from the press to when its release is read,
        # which waits for the start: 0.15 to 0.2 s in the test VMs, so a
        # quick tap to hold the send came to over MIN_HOLD_MS on Pop!_OS and
        # recorded instead. A key already up after a start that quick was a
        # tap; after a slower one it could have been a short word, so the
        # hold time decides.
        rs.state.released_while_starting = (
            time.time() - rs.state.press_t0 < 2 * recording.MIN_HOLD_MS / 1000
            and keyboard_grab.wait_for_keys_up(devices, {hold_key}, 0))


def _handle_key_event(event, hold_key, toggle_key, voice_cmds_combo, held_modifiers,
                      devices, cfg, input_device_idx, paste_last_combo=None):
    """Handle a single keyboard event. Both hold (F8) and toggle (F9) are always active.

    Grabbed devices are tracked in module state (see _grab_all_devices), not
    returned, so that every way recording can end — release, toggle, ESC, or an
    exception inside start_recording — releases the keyboard.
    """
    if modifier_hotkeys.right_alt_alone(event, (hold_key, toggle_key), held_modifiers):
        modifier_hotkeys.guard_alt_tap()

    # Hotkey test mode: report presses via D-Bus instead of recording.
    if event.value == 1 and _hotkey_test_active():
        _report_test_press(event.code, hold_key, toggle_key)
        return
    if (_on_paste_last(event, paste_last_combo, held_modifiers, devices, cfg)
            or _on_voice_commands_combo(event, voice_cmds_combo, held_modifiers, devices)):
        return
    if toggle_key and _toggle_tap_holds_send(event, toggle_key, held_modifiers):
        return   # the tap only held the send: no recording
    if event.code in MODIFIER_CODES and event.code in (hold_key, toggle_key):
        _hide_modifier_hotkey(event, toggle_key, devices, held_modifiers)

    if event.code == hold_key:
        _on_hold_key(event, hold_key, held_modifiers, devices, cfg, input_device_idx)
    if (toggle_key and event.code == toggle_key
            and _on_toggle_key(event, toggle_key, held_modifiers, devices, cfg, input_device_idx)):
        return   # a voice edit was cancelled

    # ESC cancels in any mode.
    if event.code == ecodes.KEY_ESC and rs.state.is_recording and event.value == 1:
        recording.cancel_recording(cfg.beeps, cfg.notify, "Cancelled by ESC")
        keyboard_grab.release_all()


def _stop_recording(cfg) -> None:
    """Stop the recording and type what was said, with *cfg*'s settings."""
    pipeline.stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                            cfg.auto_space, cfg.auto_period, cfg.injection_mode)


def _on_paste_last(event, combo, held_modifiers, devices, cfg) -> bool:
    """Paste Last Dictation's combo (e.g. Ctrl+Alt+P); True when *event* was
    its. Typed only once every key of the combo is let go: typing while Ctrl
    and Alt are still down would turn each letter into a Ctrl+Alt shortcut."""
    pending = rs.state.paste_last
    if pending is not None:
        if time.time() - pending["at"] > PASTE_LAST_EXPIRY_S:
            rs.state.paste_last = None
        elif event.value == 0 and (event.code == pending["key"] or event.code in MODIFIER_CODES):
            # Only the combo's own keys are waited for. Any other release
            # goes on to the handlers: swallowing it would lose a hold
            # key's key-up and leave that recording running.
            if event.code == pending["key"]:
                pending["key_up"] = True
            if pending["key_up"] and not held_modifiers:
                rs.state.paste_last = None
                if not rs.state.is_recording:
                    _paste_last_dictation(cfg)
            return True
    if combo and event.value == 1 and not rs.state.is_recording:
        required_mods, main_key = combo
        if event.code == main_key and check_modifiers_held(required_mods, held_modifiers):
            # Same brief grab as the Voice Commands combo, so the key
            # press doesn't reach the focused app.
            keyboard_grab.grab_keyboards_hiding(devices, {main_key, *held_modifiers})
            keyboard_grab.release_all()
            rs.state.paste_last = {"key": main_key, "key_up": False, "at": time.time()}
            return True
    return False


def _on_voice_commands_combo(event, combo, held_modifiers, devices) -> bool:
    """The Voice Commands combo (e.g. Ctrl+Shift+H) opens the cheat sheet;
    True when *event* was its. Not while recording: its brief grab ends by
    releasing every grab, which would also drop the ones a hold recording
    depends on."""
    if not combo or event.value != 1 or rs.state.is_recording:
        return False
    required_mods, main_key = combo
    if event.code != main_key or not check_modifiers_held(required_mods, held_modifiers):
        return False
    # Briefly grab the keyboards (not the pointer) to stop the keypress
    # leaking into the focused app. The same tracked grabs as recording,
    # and a finally that always releases, so a failing tray call can't
    # strand them. The modifiers are balanced too: a stuck Ctrl in the
    # desktop would be far worse than a stuck F8.
    keyboard_grab.grab_keyboards_hiding(devices, {main_key, *held_modifiers})
    try:
        tray_link.show_voice_commands()
    finally:
        keyboard_grab.release_all()
    return True


def _on_hold_key(event, hold_key, held_modifiers, devices, cfg, input_device_idx) -> None:
    """Hold-to-talk: the hold key down records, let go stops and types."""
    if (event.value == 1 and not rs.state.is_recording
            and not _someone_elses_shortcut(held_modifiers - {hold_key})):
        _start_hold_recording(hold_key, held_modifiers, devices, cfg, input_device_idx)
    elif event.value == 0 and rs.state.is_recording:
        # Let go of the keyboards before typing, so the user's own keys
        # reach the app again
        keyboard_grab.release_all()
        _stop_recording(cfg)


def _on_toggle_key(event, toggle_key, held_modifiers, devices, cfg, input_device_idx) -> bool:
    """Tap-to-toggle: a tap starts a recording, the next stops it, and a
    quick second tap makes it hands-free. With Shift (voice editing on) it
    is a voice edit. True when a voice edit was cancelled."""
    if event.value == 0 and send_countdown.owes_space() and not rs.state.is_recording:
        send_countdown.type_owed_space()   # the tap that held the send is over
    if event.value != 1 or _someone_elses_shortcut(held_modifiers - {toggle_key}):
        return False
    shift = _held_shift(held_modifiers) if live.voice_editing else set()
    if not rs.state.is_recording:
        if shift:
            # An edit: no double-tap hands-free, so not "from_toggle".
            recording.start_recording(cfg.beeps, cfg.notify, input_device_idx, edit_mode=True)
        else:
            recording.start_recording(cfg.beeps, cfg.notify, input_device_idx, from_toggle=True)
    elif rs.state.edit_mode:
        return _stop_edit(shift, devices, cfg)
    elif not recording.toggle_pressed_while_recording(cfg.beeps):
        # Not the second tap of a double-tap (that one keeps recording,
        # hands-free). Also reached by brushing the toggle key mid-hold:
        # release first, or the hold key's release is skipped and the grab
        # is stranded.
        keyboard_grab.release_all()
        _stop_recording(cfg)
    return False


def _stop_edit(shift, devices, cfg) -> bool:
    """The toggle key stops a voice edit; True when it was cancelled instead."""
    # A hold edit holds the keyboards (brushing the toggle key mid-hold
    # lands here): let go first, or they stay grabbed for the whole edit.
    keyboard_grab.release_all()
    # If Shift is down again, wait for the user to let go, or the Ctrl+C
    # that copies the selection would arrive as Ctrl+Shift+C. Hiding it
    # with injected key events, as the hold key does, would replay them
    # after a slow edit and start a new recording by itself.
    if shift and not keyboard_grab.wait_for_keys_up(devices, _SHIFT_CODES, EDIT_SHIFT_WAIT_S):
        recording.cancel_recording(cfg.beeps, cfg.notify,
                                   "Voice edit cancelled: let go of Shift to finish it")
        return True
    _stop_recording(cfg)
    return False


def _helper_watch(devices, hold_key, toggle_key, voice_cmds_combo, paste_last_combo):
    """Tell the hotkey helper, when it stands in for the keyboards, which keys
    to report: the dictation keys, Esc, and the two combos."""
    for dev in devices:
        if hasattr(dev, "watch"):
            from .hotkey_helper_client import refused_combo, watch_specs
            for what, combo in (("Voice Commands", voice_cmds_combo),
                                ("Paste Last Dictation", paste_last_combo)):
                why = combo and refused_combo(*combo)
                if why:
                    logger.warning(f"The {what} shortcut won't work with the hotkey helper "
                                   f"({why}). Pick one with Ctrl+Alt, Ctrl+Super or Alt+Super.")
            dev.watch(watch_specs(hold_key, toggle_key, ecodes.KEY_ESC,
                                  (voice_cmds_combo, paste_last_combo)))


def run(cfg: Settings, input_device_idx, keyboards=None):
    """The input loop: read hotkey presses and act on them, for the life of
    the service.

    *keyboards* is the hotkey helper (hotkey_helper_client.HelperKeyboard) when
    it is installed: then no keyboard is opened here at all."""
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    logger.info(f"Session type: {session}, Wayland: {session=='wayland'}")
    # Both hotkeys work whatever the mode setting says.
    logger.info(f"Hold key: {cfg.hotkey}, Toggle key: {cfg.toggle_hotkey or 'none'}")
    svc = service_tick.ServiceState(cfg, input_device_idx)
    logger.info(f"Auto-timeout: enabled={svc.timeout_enabled}, minutes={svc.timeout_minutes}")

    use_helper = keyboards is not None
    devices = _open_keyboards(keyboards)
    last_device_scan = time.time()
    # Establish both files' current mtimes so the first poll does not report a
    # spurious change and re-apply what the service just started with.
    # The recheck timer itself lives on _ServiceState — _service_tick owns it
    # for both backends.
    live.config_file_changed()
    live.commands_file_changed()
    live.words_file_changed()

    keys = _keys_to_watch(cfg, devices)
    if keys is None:
        return
    _watch(cfg, devices, keys)
    # The desktop holds the hotkeys back from the focused app until
    # stop_dictation_service, the tray or this exit hook releases them;
    # registered even if the claim failed, since a later change can succeed.
    from .service_launcher import release_desktop_hotkeys
    atexit.register(release_desktop_hotkeys)

    # Which modifier keys are held now, for combo detection.
    held_modifiers: set[int] = set()
    while True:
        current_time = time.time()
        # Backend-agnostic work: D-Bus/portal start/stop commands, the
        # auto-timeout, and Preferences changes. Not _config_file_changed()
        # here: that check is edge-triggered, and the tick has consumed it.
        reloaded = service_tick.tick(cfg, svc)
        if reloaded is not None:
            keys = reloaded
            # Re-claimed even if the hotkeys look unchanged: claiming is cheap.
            _watch(cfg, devices, keys)
            logger.info("Applied settings change without restarting")

        dead_devices = _read_key_events(devices, keys, held_modifiers, cfg, svc, current_time)
        if dead_devices:
            # Their modifiers' key-ups can't come now, and a Ctrl left held
            # here would stop F8 dictating (_someone_elses_shortcut).
            held_modifiers.clear()
        _drop_dead_devices(dead_devices, devices, cfg)

        # Pick up keyboards that have (re)appeared. Only while idle: rescanning
        # mid-recording would add an ungrabbed device to a grabbed set, letting
        # the hotkey leak into the focused app. Throttled because it opens
        # every node under /dev/input.
        if (not use_helper and not rs.state.is_recording
                and current_time - last_device_scan >= DEVICE_RESCAN_SECONDS):
            last_device_scan = current_time
            _rediscover_devices(devices)

        # Backstop: we must never hold a keyboard grab while not recording.
        keyboard_grab.release_if_not_recording()

        pass_time = time.time() - current_time
        if pass_time < INPUT_MIN_PASS_S:
            time.sleep(INPUT_MIN_PASS_S - pass_time)


def _open_keyboards(keyboards) -> list:
    """The devices to read: the hotkey helper's one stand-in keyboard, or
    every keyboard (one that vanishes meanwhile is skipped). evdev opens them
    non-blocking, which the read loop relies on."""
    if keyboards is not None:
        logger.info("Hotkeys come from the TuxTalk hotkey helper; no keyboard is opened here")
        return list(keyboards)
    devices = []
    _rediscover_devices(devices)
    return devices


def _keys_to_watch(cfg: Settings, devices):
    """The hotkeys to watch; None when there are none yet: first-run setup
    isn't finished, or no hold key is chosen."""
    try:
        from tuxtalk.cuda_helper import is_first_run
        first_run = is_first_run()
    except Exception as e:
        logger.info(f"Couldn't tell whether setup is finished ({e}); taking it as finished")
        first_run = False
    if first_run:
        logger.info("First run detected - onboarding not complete")
        print("Onboarding not complete. No hotkeys will be active until setup is finished.")
        return None

    keys = hotkey_keys.Hotkeys.from_settings(cfg)
    if keys.hold_key is None:
        logger.info("No hotkey configured - service will not monitor any keys")
        print("No hotkey configured. Complete onboarding to activate dictation.")
        return None

    # A hotkey is configured and onboarding is done, so from here on every
    # keypress is supposed to reach us. If no device could be opened, say so
    # now rather than running a poll loop over an empty device list forever.
    if not devices:
        diagnosis = secure_input.input_access_diagnosis()
        logger.error(diagnosis)
        print(diagnosis, file=sys.stderr)
    if keys.voice_cmds_combo:
        logger.info(f"Voice Commands hotkey: {keys.vc_hotkey_str}")
    return keys


def _watch(cfg: Settings, devices, keys: "hotkey_keys.Hotkeys") -> None:
    """Have the desktop hold *keys* back from the focused app (see
    _claim_desktop_hotkeys), and tell the hotkey helper and the modifier-key
    check which keys to look for."""
    _claim_desktop_hotkeys(cfg)
    _helper_watch(devices, keys.hold_key, keys.toggle_key, keys.voice_cmds_combo,
                  keys.paste_last_combo)
    modifier_hotkeys.note(devices, keys.hold_key, keys.toggle_key)


def _read_key_events(devices, keys: "hotkey_keys.Hotkeys", held_modifiers, cfg: Settings, svc,
                     now: float) -> list:
    """Act on the key events waiting on *devices*; the devices that failed."""
    dead_devices = []
    for dev in _devices_with_input(devices, INPUT_WAIT_S):
        try:
            for event in dev.read():
                if event.type != ecodes.EV_KEY:
                    continue
                # Our own injected key events (see keyboard_grab) come back
                # through this same read; they are not input.
                if keyboard_grab.is_own_injected_event(dev, event):
                    continue
                track_modifier(held_modifiers, event)
                # Any hotkey counts as activity, whether or not the
                # auto-timeout is on now: it can be switched on later,
                # and must then count from the last real use.
                if event.code in keys.watched():
                    svc.last_activity = now
                _handle_key_event(event, keys.hold_key, keys.toggle_key, keys.voice_cmds_combo,
                                  held_modifiers, devices, cfg, svc.input_device_idx,
                                  paste_last_combo=keys.paste_last_combo)
        except BlockingIOError:
            pass
        except OSError as e:
            # Device unplugged or suspended mid-read. Drop it so we stop
            # polling a dead handle, but keep serving the others.
            logger.warning(f"Input device {getattr(dev, 'name', dev)} failed, dropping it: {e}")
            dead_devices.append(dev)
        except Exception as e:
            # Never swallow silently: an unlogged exception here would turn
            # a microphone error into an unexplained keyboard lockup.
            logger.error(f"Error handling input event: {e}", exc_info=True)
    return dead_devices
