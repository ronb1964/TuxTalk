"""Open apps by voice (TuxTalk Labs): a dictation that is only "open Firefox"
opens Firefox, or brings its window forward, instead of typing the words.

The steps are other modules': app_commands reads the command, app_finder
picks the app, app_focus brings a running one forward and app_launcher
starts it. This decides between them and tells the user when nothing opened.

Run on the dictation worker, never GTK's thread: bringing a window forward
and starting an app can each wait seconds for the desktop. Works in terminals
too: unlike select and move, it sends no keys to the focused window.
"""
from . import app_finder, app_focus, app_launcher, beeps, keyboard_output
from . import notices
from .app_commands import OpenRequest
from .beeps import READY_BEEP
from .logger import setup_logger

logger = setup_logger(__name__)

NOTHING_OPENED = "Nothing was opened."


def open_request(request: OpenRequest, beeps_on: bool, ongoing=None) -> bool | None:
    """Open the app *request* asks for. None: it names no app ("Start
    over."), so it's typed; True: opened or brought forward; False: nothing
    opened, said with a notice, or stopped by Esc.

    *ongoing*: the recording_state.Message of the Type at pause recording
    the request is a phrase of, cut at a pause, else None. Opening the app
    ends that recording."""
    entries = app_finder.installed_apps()
    found = app_finder.find(request, entries, app_finder.default_apps())
    if found.kind == "none":
        # A sentence lost is worse than a typed "Open fierfox.", which is
        # easy to undo. The log keeps the user's words out.
        logger.info("Open command: no app matched; typed as words")
        return None
    if found.kind == "tie":
        logger.info("Voice command: open, but these fit equally: "
                    + ", ".join(e.app_id for e in found.entries))
        label = tie_label(found.entries) + " Say the full name."
        notices.command_refused(label, f"{label} {NOTHING_OPENED}", beeps_on)
        return False
    entry = found.entries[0]
    exact = bool(request.install) or len(app_finder.copies(entry, entries)) > 1
    return _open(entry, request.new, beeps_on, ongoing, exact)


def _open(entry, new: bool, beeps_on: bool, ongoing, exact: bool) -> bool:
    """Bring forward *entry*'s app, or start it (always, with *new*).
    *exact*: other copies are installed, so a window counts only by its
    menu entry (app_focus.window_names)."""
    logger.info(f"Voice command: open {entry.app_id}")
    if keyboard_output.esc_stopped("opened"):
        return False
    if ongoing is not None:
        # Before the app starts, which takes a moment: nothing more of the
        # recording is typed, as after Esc but without its beep, so words
        # said meanwhile can't land in whichever window has the focus. Its
        # phrases still queued and the rest at the stop are dropped (the
        # key may be up already), and the service tick stops the microphone
        # (service_tick._end_recording_a_command_cancelled).
        ongoing.cancelled = True
    if not new and app_focus.focus_running(entry, exact=exact):
        beeps.beep(beeps_on, *READY_BEEP)
        return True
    # Esc while it looked for a window still stops it. Mid-recording the
    # message was just cancelled here, which looks the same as Esc.
    if ongoing is None and keyboard_output.esc_stopped("opened"):
        return False
    if not app_launcher.launch(entry):
        notices.command_refused(f"Couldn't open {entry.name}",
                                f"{entry.name} didn't start. Try opening it from the menu.",
                                beeps_on)
        return False
    beeps.beep(beeps_on, *READY_BEEP)
    return True


def tie_label(entries) -> str:
    """"Two apps fit: Konsole, Kitty." Up to three names, so it stays short."""
    names = [e.name for e in entries]
    count = {2: "Two", 3: "Three"}.get(len(names), "Several")
    shown = ", ".join(names[:3]) + (" and more" if len(names) > 3 else "")
    return f"{count} apps fit: {shown}."
