"""What the dictation service tells the user when something went wrong:
nothing heard from the microphone, a dictation that didn't reach the
window, a refused password field, a voice edit that didn't happen. The
wording lives in delivery_notices.py and edit_notices.py; this decides how
it's said (a notification, a label over the window, the cancel beep)."""
import os

import sounddevice as sd

from . import (
    beeps,
    delivery_notices,
    edit_notices,
    engine,
    focused_window,
    uinput_helper,
)
from . import live_settings as live
from . import notify as notify_module
from .beeps import CANCEL_BEEP
from .logger import setup_logger

logger = setup_logger(__name__)


SILENT_MIC_TITLE = "No sound from the microphone"

SILENT_MIC_NOTICE = ("TuxTalk heard only silence. If you spoke, check that the microphone "
                     "isn't muted or unplugged, and that the right one is picked in "
                     "Preferences \u2192 Audio.")


def warn_silent_mic():
    """The microphone sent nothing at all (silence.is_dead_mic). Always
    said, like a dictation that didn't arrive: a dead wireless mic otherwise
    looks like TuxTalk ignoring you (asked for in Handy, Wispr Flow added it
    Oct 2026)."""
    print(SILENT_MIC_NOTICE)
    logger.warning("The microphone sent only silence (muted, unplugged or the wrong one?)")
    notify_module.notify(SILENT_MIC_TITLE, SILENT_MIC_NOTICE)


def device_name(idx) -> str:
    """The microphone's name for a notice, or "" when it can't be read."""
    try:
        return sd.query_devices(idx)["name"] if idx is not None else ""
    except Exception as e:
        logger.info(f"Could not read the name of sound device {idx}: {e}")
        return ""


def warn_mic_switched(old_idx, new_idx):
    """The microphone wouldn't open and TuxTalk is recording from another one.
    Always said: otherwise you talk into a jack with nothing plugged in and
    get silence or room noise with no idea why (Ron, 2026-10-18: a USB
    headset stopped answering and TuxTalk quietly used the motherboard input)."""
    old, new = device_name(old_idx), device_name(new_idx)
    notice = (f"{old or 'Your microphone'} didn't respond, so TuxTalk is recording from "
              f"{new or 'another input'} instead. Unplugging the microphone and plugging it "
              "back in usually fixes this.")
    print(notice)
    logger.warning(f"Recording from {new or new_idx!r}: {old or old_idx!r} didn't open")
    notify_module.notify("Recording from another microphone", notice)


def report_undelivered(outcome: str, beeps_on: bool):
    """Tell the user a dictation didn't (fully) reach the window.

    Always shown, whatever the notifications setting, like a missing microphone:
    otherwise words that are transcribed but never arrive look like a computer
    too slow to dictate. The low cancel beep follows the "done" beep, which has already played, so a
    desktop without notifications still hears that something went wrong.
    """
    if outcome == "password_field":
        password_field_refused(beeps_on)
        return
    message = delivery_notices.NOTICES[outcome]
    if outcome == "failed":
        # When the typing setup is the reason, say what fixes it.
        try:
            reason = uinput_helper.typing_blocked_reason()
            if reason:
                message = (f"{reason} The dictation is saved in Recent Dictations "
                           "in the TuxTalk menu: click it to copy, then paste with Ctrl+V.")
        except Exception as e:
            logger.info(f"Couldn't work out why typing failed: {e}")
    if live.paste_last_hotkey:
        message += f" Or press {live.paste_last_hotkey} to type it again where your cursor is."
    logger.warning(f"Dictation not delivered ({outcome}); told the user")
    beeps.beep(beeps_on, *CANCEL_BEEP)
    notify_module.notify(delivery_notices.TITLES[outcome], message)


def refuse_password_field(beeps_on: bool) -> bool:
    """True when the cursor is in a password field (focused_window.is_password), after
    saying so (password_field_refused)."""
    if not focused_window.is_password():
        return False
    password_field_refused(beeps_on)
    return True


def password_field_refused(beeps_on: bool) -> None:
    """Nothing is typed in a password field, and the text stays out of Recent
    Dictations too, since it may be the password itself. The cancel beep and
    a label over the window say so, whatever the notifications setting. The
    notification is only for when neither would be noticed: beeps off, or no
    X display or GTK loop for the label (the check below);
    otherwise it's a rule, not an error (like edit_problem's)."""
    logger.info("Not typed: the cursor is in a password field")
    beeps.beep(beeps_on, *CANCEL_BEEP)
    from . import screen_message
    screen_message.show_over_active_window(delivery_notices.PASSWORD_FIELD_LABEL)
    if not (beeps_on and engine.gtk_loop_running and os.environ.get("DISPLAY")):
        notify_module.notify(delivery_notices.PASSWORD_FIELD_TITLE,
                             delivery_notices.PASSWORD_FIELD_NOTICE)


def edit_problem(kind: str, beeps_on: bool) -> None:
    """Tell the user a voice edit didn't happen, and why. Always shown, like
    report_undelivered: otherwise nothing at all would seem to happen.

    A problem that is really a rule ("select works right after you dictate")
    opens a small window explaining it, every time, until "Don't explain
    this again" is ticked for it (explain.py); then the short floating label
    alone. The notification is for real errors only: a whole
    explanation in it is gone before it can be read."""
    message = edit_notices.NOTICES[kind]
    logger.info(f"Voice edit not done: {kind}")
    beeps.beep(beeps_on, *CANCEL_BEEP)
    from . import explain
    # "Don't explain this again" ticked, or a window already open: the
    # label is enough. Not opened (no window could start): the notification
    # as before.
    explained = False
    if explain.should_explain(kind) and not explain.window_open():
        if explain.open_window(kind):
            return
    else:
        explained = kind in explain.EXPLANATIONS
    # Where the user is looking: over the active window (screen_message.py).
    # The notification stays as a backup, in the notification history.
    try:
        from . import screen_message
        screen_message.show_over_active_window(edit_notices.LABELS[kind])
    except Exception as e:
        logger.info(f"Could not float the message over the window: {e}")
    if not explained:
        notify_module.notify(edit_notices.LABELS[kind], message)
