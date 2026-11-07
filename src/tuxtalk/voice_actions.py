"""Voice commands (TuxTalk Labs): the one place that turns a dictation that
is only a command into its action, instead of typing the words.

command_phrases reads the words; this hands the request to the module that
acts on it. Run on the dictation worker, after select and move has declined
the dictation (voice_commands.handle_navigation)."""
from collections.abc import Callable

from . import (command_phrases, delivery, desktop_actions, everyday_keys, focused_window,
               keyboard_output, mouse_grid, music_control, open_app, sound_control,
               system_actions, web_commands, window_actions)
from . import live_settings as live
from .app_commands import OpenRequest
from .command_phrases import (AddressRequest, DesktopRequest, KeyRequest, MusicRequest,
                              SearchRequest, SoundRequest, SystemRequest, WindowRequest)
from .mouse_phrases import MouseRequest


def run(raw: str, beeps_on: bool, ongoing=None) -> bool | None:
    """Do the command *raw* is. None: not one (or one that names nothing, as
    "Close the door."), or select and move's, so it's typed or left to that;
    True: done; False: refused with a note, or stopped by Esc.

    *ongoing*: the recording_state.Message of the Type at pause recording
    *raw* is a phrase of, cut at a pause, else None. A command that acts ends
    that recording."""
    request = _parse(raw)
    if isinstance(request, OpenRequest):
        return open_app.open_request(request, beeps_on, ongoing)
    if isinstance(request, KeyRequest):
        return _send(everyday_keys.press, request, f"pressed {request.name}", beeps_on, ongoing)
    if isinstance(request, AddressRequest):
        return _send(web_commands.go_to, request, "opened: an address", beeps_on, ongoing)
    if isinstance(request, SearchRequest):
        return _send(web_commands.search, request, web_commands.SEARCH_DONE, beeps_on,
                     ongoing)
    if isinstance(request, (WindowRequest, DesktopRequest)):
        # Taken now: a window command or a snap is for the window it was said in.
        where = focused_window.look()
        acts = window_actions if isinstance(request, WindowRequest) else desktop_actions
        return _act(f"done ({request.action})", lambda: acts.run(request, where, beeps_on),
                    ongoing)
    if isinstance(request, (SoundRequest, MusicRequest)):
        acts = sound_control if isinstance(request, SoundRequest) else music_control
        return _act(f"done ({request.action})", lambda: acts.run(request, beeps_on), ongoing)
    if isinstance(request, SystemRequest):
        return _act(f"done ({request.action})",
                    lambda: system_actions.run(request, beeps_on), ongoing)
    if isinstance(request, MouseRequest):
        return _act(f"done (mouse: {request.action})",
                    lambda: mouse_grid.act(request, beeps_on), ongoing)
    return None


def _parse(raw: str):
    """command_phrases.parse with the settings and the mouse grid as they are now."""
    return command_phrases.parse(raw, navigation=live.voice_navigation,
                                 actions=live.open_apps, grid=mouse_grid.showing())


def is_mouse_command(raw: str) -> bool:
    """Whether the dictation *raw* is a mouse command, with the grid as it is
    now. Those type nothing, so a password field lets them through: a voice
    user can click out of one."""
    return isinstance(_parse(raw), MouseRequest)


def close_grid_unless_for_it(raw: str) -> None:
    """Close the mouse grid when the dictation *raw* isn't one of its
    commands: anything else is then typed or run as usual, with the grid gone."""
    if mouse_grid.showing() and not is_mouse_command(raw):
        mouse_grid.close("another dictation")


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


def _act(what: str, do: Callable[[], bool | None], ongoing) -> bool | None:
    """A command that sends no keys (a window, desktop, sound, music or mouse
    command), done by do(), so the recording can end after it; only one
    that was done ends it, so a refused one ("Couldn't tell which window")
    lets the user carry on talking. *what* is for the log when Esc came
    first; do() looks at Esc again right before the desktop is asked."""
    if keyboard_output.esc_stopped(what):
        return False
    done = do()
    if done is True and ongoing is not None:
        ongoing.cancelled = True
    return done


def typed_as_words(raw: str) -> str:
    """*raw* without the "literal" before a voice command: "literal new tab"
    types "new tab", as "literal press enter" types "press enter". Anything
    else, or with voice commands off, unchanged."""
    return command_phrases.literal_words(raw, navigation=live.voice_navigation,
                                         actions=live.open_apps, grid=mouse_grid.showing())
