"""Pause music, play music, next and previous track by voice (TuxTalk Labs).

Through MPRIS, as media_pause pauses music while you dictate, and on its
worker thread (MediaPauser.run_command), so the two never fight: the command
sees which players this recording paused, and a "pause music" said at a pause
in Type at pause or hands-free makes TuxTalk forget it was going to resume
them, so the end of the recording doesn't start the music again. With the
hold key the music has already come back when the words are read, and
"pause music" pauses it again: a moment of music, accepted.

The players are read when the command is said; nothing watches them in the
background."""
from dataclasses import dataclass

from . import beeps, command_notices, keyboard_output, recording
from .command_phrases import MusicRequest
from .logger import setup_logger
from .media_pause import MPRIS_PREFIX, PROXIES, status_of

logger = setup_logger(__name__)

METHODS = {"pause": "Pause", "play": "Play", "next": "Next", "previous": "Previous"}


@dataclass(frozen=True)
class Choice:
    """Where a music command goes: *targets* get the call, *forget* aren't
    resumed when the recording ends, and *note* is the command_notices kind
    when nothing could be picked."""
    targets: tuple[str, ...]
    forget: frozenset[str]
    note: str | None


def _groups(status: dict[str, str], paused_by_us: tuple[str, ...]):
    """(paused by TuxTalk for this recording, playing now, paused now)."""
    by_us = tuple(n for n in paused_by_us if n not in PROXIES)
    playing = tuple(n for n, s in status.items() if s == "Playing" and n not in PROXIES)
    paused = tuple(n for n, s in status.items() if s == "Paused" and n not in PROXIES)
    return by_us, playing, paused


def _one_of(status: dict[str, str], paused_by_us: tuple[str, ...]) -> tuple[str, ...]:
    """The players a play, next or previous picks one from: those TuxTalk
    paused for this recording (they were playing when it was said), else
    the ones playing, else the paused ones."""
    by_us, playing, paused = _groups(status, paused_by_us)
    return by_us or playing or paused


def choose(action: str, status: dict[str, str], paused_by_us: tuple[str, ...]) -> Choice:
    """Which players *action* goes to, from each player's PlaybackStatus
    (*status*) and the ones TuxTalk paused for this recording. "pause"
    pauses every player that plays, and keeps the ones TuxTalk paused
    paused; "play" starts what TuxTalk paused, else does nothing when
    something plays, else starts the one paused player. Never a guess
    between several: the which_playing note when they play (or did when it
    was said), which_player when the user paused them."""
    by_us, playing, _ = _groups(status, paused_by_us)
    if action == "pause":
        if by_us or playing:
            return Choice(playing, frozenset(by_us), None)
        players = [n for n in status if n not in PROXIES]
        return Choice((), frozenset(), "nothing_playing" if players else "no_player")
    if action == "play" and by_us:
        return Choice(by_us, frozenset(by_us), None)
    if action == "play" and playing:
        return Choice((), frozenset(), None)        # already playing
    group = _one_of(status, paused_by_us)
    if len(group) == 1:
        return Choice(group, frozenset(), None)
    if not group:
        return Choice((), frozenset(), "no_player")
    return Choice((), frozenset(), "which_playing" if by_us or playing else "which_player")


def _label(bus, name: str) -> str:
    """The player's name for the screen: its own ("Spotify"), else its bus name."""
    return bus.identity(name) or name.removeprefix(MPRIS_PREFIX)


def _call_each(bus, names: tuple[str, ...], method: str) -> list[str]:
    """Call *method* on each player; the ones that took it."""
    called = []
    for name in names:
        try:
            bus.call(name, method)
        except Exception as e:
            logger.info(f"Music command: {name} didn't take {method}: {e}")
            continue
        called.append(name)
    return called


def _on_worker(bus, paused_by_us: tuple[str, ...], go, action: str, esc):
    """MediaPauser.run_command's act: read the players, choose, and call the
    ones chosen unless esc() says Esc came meanwhile or go() says the
    command was given up. Returns ((note, names), forget): the note's kind
    ("esc" when Esc has said so itself), or None when done, with the
    players to name in the note or the log."""
    status = {}
    for name in bus.players():
        if (state := status_of(bus, name)) is not None:
            status[name] = state
    choice = choose(action, status, paused_by_us)
    if choice.note in ("which_player", "which_playing"):
        names = [_label(bus, n) for n in _one_of(status, paused_by_us)]
        return (choice.note, names), frozenset()
    if choice.note:
        return (choice.note, []), frozenset()
    if esc():
        return ("esc", []), frozenset()
    if not go():
        return ("player_no_answer", []), frozenset()
    called = _call_each(bus, choice.targets, METHODS[action])
    if choice.targets and not called:
        return ("no_player", []), frozenset()
    names = called + sorted(choice.forget.difference(called))
    return (None, [n.removeprefix(MPRIS_PREFIX) for n in names]), choice.forget


def run(request: MusicRequest, beeps_on: bool) -> bool:
    """Do *request* on the media players. True: done, with the ready beep;
    False: nothing picked, or no player answered in time, said with a note,
    or Esc came while the players were read."""
    action, esc = request.action, keyboard_output.esc_check()
    answer = recording.media_pauser().run_command(
        lambda bus, paused, go: _on_worker(bus, paused, go, action, esc))
    note, names = answer if answer is not None else ("player_no_answer", [])
    if note == "esc":
        logger.info(f"Not done ({action} music): Esc cancelled the dictation")
        return False
    if note:
        command_notices.say(note, beeps_on, players=", ".join(names))
        return False
    logger.info(f"Voice command: {action} music, " + (", ".join(names) or "already playing"))
    beeps.beep(beeps_on, *beeps.READY_BEEP)
    return True
