"""Voice commands (TuxTalk Labs): the one place that turns a dictation that
is only a command into its action, instead of typing the words.

command_phrases reads the words; this hands the request to the module that
acts on it. Run on the dictation worker, after select and move has declined
the dictation (voice_commands.handle_navigation)."""
from . import command_phrases, delivery, focused_window, keyboard_output, open_app
from . import live_settings as live
from .app_commands import OpenRequest
from .command_phrases import KeyRequest
from .everyday_keys import press


def run(raw: str, beeps_on: bool, ongoing=None) -> bool | None:
    """Do the command *raw* is. None: not one (or one that names nothing, as
    "Close the door."), or select and move's, so it's typed or left to that;
    True: done; False: refused with a note, or stopped by Esc.

    *ongoing*: the recording_state.Message of the Type at pause recording
    *raw* is a phrase of, cut at a pause, else None. A command that acts ends
    that recording."""
    request = command_phrases.parse(raw, navigation=live.voice_navigation,
                                    actions=live.open_apps)
    if isinstance(request, OpenRequest):
        return open_app.open_request(request, beeps_on, ongoing)
    if isinstance(request, KeyRequest):
        return _press(request, beeps_on, ongoing)
    # Windows, addresses and searches have no branch yet and are typed.
    return None


def _press(request: KeyRequest, beeps_on: bool, ongoing) -> bool:
    """An everyday key. The recording ends only after the keys went in, since
    the worker's until_cancelled would refuse them once it is cancelled."""
    where = focused_window.look()
    if keyboard_output.esc_stopped(f"pressed {request.name}"):
        return False
    if not press(request, where, beeps_on):
        return False
    # What was typed is no longer what's under the cursor, so "delete that"
    # must not erase from it.
    delivery.forget_typed()
    if ongoing is not None:
        ongoing.cancelled = True
    return True


def typed_as_words(raw: str) -> str:
    """*raw* without the "literal" before a voice command: "literal new tab"
    types "new tab", as "literal press enter" types "press enter". Anything
    else, or with voice commands off, unchanged."""
    return command_phrases.literal_words(raw, navigation=live.voice_navigation,
                                         actions=live.open_apps)
