"""Open apps by voice (TuxTalk Labs): a dictation that is only "open Firefox"
opens Firefox, or brings its window forward, instead of typing the words.

The steps are other modules': app_commands reads the command, app_finder
picks the app, app_focus brings a running one forward and app_launcher
starts it. This decides between them and tells the user when nothing opened.

Run on the dictation worker, never GTK's thread: bringing a window forward
and starting an app can each wait seconds for the desktop. Works in terminals
too: unlike select and move, it sends no keys to the focused window.
"""
import re

from . import app_commands, app_finder, app_focus, app_launcher, beeps, keyboard_output
from . import live_settings as live
from . import notices
from .beeps import READY_BEEP
from .logger import setup_logger

logger = setup_logger(__name__)

NOTHING_OPENED = "Nothing was opened."

# Only "literal": "the word open means unlocked" is a sentence.
_LITERAL_FIRST = re.compile(r"^\s*literal\s+", re.IGNORECASE)


def run(raw: str, beeps_on: bool, ongoing=None):
    """Open the app *raw* asks for. None: not an open command, or open apps
    is off, so it's typed; True: opened or brought forward; False: nothing
    opened, said with a notice, or stopped by Esc.

    *ongoing*: the recording_state.Message of the Type at pause recording
    *raw* is a phrase of, cut at a pause, else None. Opening the app ends
    that recording."""
    if not live.open_apps:
        return None
    request = app_commands.parse(raw)
    if request is None:
        return None
    found = app_finder.find(request, app_finder.installed_apps(), app_finder.default_apps())
    if found.kind == "none":
        # What was heard is on screen; the log keeps the user's words out.
        logger.info("Voice command: open, but no app matched")
        label = f"No app called “{request.name}”"
        notices.app_not_opened(label, f"{label}. {NOTHING_OPENED}", beeps_on)
        return False
    if found.kind == "tie":
        logger.info("Voice command: open, but these fit equally: "
                    + ", ".join(e.app_id for e in found.entries))
        label = _tie_label(found.entries) + " Say the full name."
        notices.app_not_opened(label, f"{label} {NOTHING_OPENED}", beeps_on)
        return False
    return _open(found.entries[0], request.new, beeps_on, ongoing)


def _open(entry, new: bool, beeps_on: bool, ongoing) -> bool:
    """Bring forward *entry*'s app, or start it (always, with *new*)."""
    logger.info(f"Voice command: open {entry.app_id}")
    if _esc_pressed():
        return False
    if ongoing is not None:
        # Before the app starts, which takes a moment: nothing more of the
        # recording is typed, as after Esc but without its beep, so words
        # said meanwhile can't land in whichever window has the focus. Its
        # phrases still queued and the rest at the stop are dropped (the
        # key may be up already), and the service tick stops the microphone
        # (service_tick._end_recording_a_command_cancelled).
        ongoing.cancelled = True
    if not new and app_focus.focus_running(entry):
        beeps.beep(beeps_on, *READY_BEEP)
        return True
    # Esc while it looked for a window still stops it. Mid-recording the
    # message was just cancelled here, which looks the same as Esc.
    if ongoing is None and _esc_pressed():
        return False
    if not app_launcher.launch(entry):
        notices.app_not_opened(f"Couldn't open {entry.name}",
                               f"{entry.name} didn't start. Try opening it from the menu.",
                               beeps_on)
        return False
    beeps.beep(beeps_on, *READY_BEEP)
    return True


def _esc_pressed() -> bool:
    """Whether Esc cancelled this dictation after its key came up (the Esc
    said so itself)."""
    if keyboard_output.esc_cancelled():
        logger.info("Not opened: Esc cancelled the dictation")
        return True
    return False


def _tie_label(entries) -> str:
    """"Two apps fit: Konsole, Kitty." Up to three names, so it stays short."""
    names = [e.name for e in entries]
    count = {2: "Two", 3: "Three"}.get(len(names), "Several")
    shown = ", ".join(names[:3]) + (" and more" if len(names) > 3 else "")
    return f"{count} apps fit: {shown}."


def typed_as_words(raw: str) -> str:
    """*raw* without the "literal" before an open command: "literal open
    Firefox" types "open Firefox", as "literal press enter" types "press
    enter". Anything else, or with open apps off, unchanged: "literal
    translations" stays as said."""
    if not live.open_apps:
        return raw
    match = _LITERAL_FIRST.match(raw)
    if match and app_commands.parse(raw[match.end():]) is not None:
        return raw[match.end():]
    return raw
