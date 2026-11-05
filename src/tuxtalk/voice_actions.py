"""Voice commands (TuxTalk Labs): the one place that turns a dictation that
is only a command into its action, instead of typing the words.

command_phrases reads the words; this hands the request to the module that
acts on it. Run on the dictation worker, after select and move has declined
the dictation (voice_commands.handle_navigation)."""
from . import (command_phrases, delivery, everyday_keys, focused_window, keyboard_output,
               open_app, web_commands, window_actions)
from . import live_settings as live
from .app_commands import OpenRequest
from .command_phrases import AddressRequest, KeyRequest, SearchRequest, WindowRequest


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
        return _send(everyday_keys.press, request, f"pressed {request.name}", beeps_on, ongoing)
    if isinstance(request, AddressRequest):
        return _send(web_commands.go_to, request, "opened: an address", beeps_on, ongoing)
    if isinstance(request, SearchRequest):
        return _send(web_commands.search, request, "done: a web search", beeps_on, ongoing)
    if isinstance(request, WindowRequest):
        return _window(request, beeps_on, ongoing)
    return None


def _send(act, request, what: str, beeps_on: bool, ongoing) -> bool:
    """A command that sends keys to the focused window: an everyday key, an
    address or a web search, done by act(request, where, beeps_on). The
    recording ends only after the keys went in, since the worker's
    until_cancelled would refuse them once it is cancelled; *what* is for
    the log when Esc came first."""
    where = focused_window.look()
    if keyboard_output.esc_stopped(what):
        return False
    if not act(request, where, beeps_on):
        return False
    # What was typed is no longer what's under the cursor, so "delete the last
    # sentence" must not erase from it.
    delivery.forget_typed()
    if ongoing is not None:
        ongoing.cancelled = True
    return True


def _window(request: WindowRequest, beeps_on: bool, ongoing) -> bool | None:
    """A window command. It sends no keys, so the recording can end after
    it; only one that was done ends it, so a refused one ("Couldn't tell
    which window") lets the user carry on talking."""
    if keyboard_output.esc_stopped(f"done ({request.action})"):
        return False
    done = window_actions.run(request, beeps_on)
    if done and ongoing is not None:
        ongoing.cancelled = True
    return done


def typed_as_words(raw: str) -> str:
    """*raw* without the "literal" before a voice command: "literal new tab"
    types "new tab", as "literal press enter" types "press enter". Anything
    else, or with voice commands off, unchanged."""
    return command_phrases.literal_words(raw, navigation=live.voice_navigation,
                                         actions=live.open_apps)
