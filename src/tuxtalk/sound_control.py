"""Volume up, volume down, mute and unmute by voice (TuxTalk Labs).

These act on the system's default OUTPUT, the level the desktop's own volume
keys change, and never on an app's stream: the per-app levels belong to
audio_duck, which turns streams down while you dictate and puts them back
afterwards. Touching a stream here would be undone, or would undo itself, when
the sounds come back. Runs through wpctl (PipeWire), or pactl where wpctl is
missing."""
import shutil

from . import command_notices
from .command_phrases import SoundRequest
from .logger import setup_logger
from .mic_volume import PACTL_PERCENT, WPCTL_VOLUME, run_sound_tool

logger = setup_logger(__name__)

STEP_PERCENT = 5        # what one "volume up" or "volume down" moves
MAX_PERCENT = 100       # "volume up" stops here, as the desktop's keys do

_WPCTL_SINK = "@DEFAULT_AUDIO_SINK@"
_PACTL_SINK = "@DEFAULT_SINK@"


def target_percent(current: int, action: str, steps: int) -> int:
    """The level after *steps* presses of "volume up" or "volume down" from
    *current*. Up stops at full, but never lowers a volume already boosted
    past it."""
    if action == "volume up":
        return min(current + STEP_PERCENT * steps, max(current, MAX_PERCENT))
    return max(current - STEP_PERCENT * steps, 0)


def output_volume() -> tuple[int, bool] | None:
    """(percent, muted) of the default output, or None when neither wpctl
    nor pactl can tell."""
    if shutil.which("wpctl"):
        out = run_sound_tool(["wpctl", "get-volume", _WPCTL_SINK])
        found = out and WPCTL_VOLUME.search(out)
        return (round(float(found.group(1)) * 100), "[MUTED]" in out) if found else None
    out = run_sound_tool(["pactl", "get-sink-volume", _PACTL_SINK])
    found = out and PACTL_PERCENT.search(out)
    mute = run_sound_tool(["pactl", "get-sink-mute", _PACTL_SINK])
    if not found or mute is None:
        return None
    return int(found.group(1)), "yes" in mute


def _set_volume(percent: int) -> bool:
    if shutil.which("wpctl"):
        cmd = ["wpctl", "set-volume", _WPCTL_SINK, f"{percent / 100:.2f}"]
    else:
        cmd = ["pactl", "set-sink-volume", _PACTL_SINK, f"{percent}%"]
    return run_sound_tool(cmd) is not None


def _set_mute(muted: bool) -> bool:
    flag = "1" if muted else "0"
    if shutil.which("wpctl"):
        cmd = ["wpctl", "set-mute", _WPCTL_SINK, flag]
    else:
        cmd = ["pactl", "set-sink-mute", _PACTL_SINK, flag]
    return run_sound_tool(cmd) is not None


def run(request: SoundRequest, beeps_on: bool) -> bool:
    """Do *request* on the default output. True: done and told; False: the
    sound system didn't answer, said with a note. "Volume up" also unmutes,
    as the desktop's volume keys do."""
    action = request.action
    state = output_volume()
    if state is None:
        command_notices.say("no_sound_control", beeps_on)
        return False
    percent = state[0]
    if action in ("mute", "unmute"):
        if not _set_mute(action == "mute"):
            command_notices.say("no_sound_control", beeps_on)
            return False
        logger.info(f"Voice command: {action} ({percent}%)")
        command_notices.tell("muted" if action == "mute" else "unmuted", beeps_on)
        return True
    percent = target_percent(percent, action, request.steps)
    if not _set_volume(percent) or (action == "volume up" and not _set_mute(False)):
        command_notices.say("no_sound_control", beeps_on)
        return False
    logger.info(f"Voice command: {action} ({percent}%)")
    command_notices.tell("volume", beeps_on, percent=percent)
    return True
