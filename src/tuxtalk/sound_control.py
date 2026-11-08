"""Volume up, volume down, mute and unmute by voice (TuxTalk Labs).

These act on the system's default OUTPUT, the level the desktop's own volume
keys change, and never on an app's stream: the per-app levels belong to
audio_duck, which turns streams down while you dictate and puts them back
afterwards. Touching a stream here would be undone, or would undo itself, when
the sounds come back. Runs through wpctl (PipeWire), or pactl where wpctl is
missing."""
import shutil

from . import command_notices, keyboard_output
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


def _tool() -> str:
    """The sound tool this command uses, looked for each time: wpctl
    (PipeWire), else pactl, which fails like any missing program when it
    isn't there."""
    return "wpctl" if shutil.which("wpctl") else "pactl"


def output_volume(tool: str) -> int | None:
    """The default output's level in percent, through *tool*; None when it
    can't tell."""
    if tool == "wpctl":
        out = run_sound_tool(["wpctl", "get-volume", _WPCTL_SINK])
        found = out and WPCTL_VOLUME.search(out)
        return round(float(found.group(1)) * 100) if found else None
    out = run_sound_tool(["pactl", "get-sink-volume", _PACTL_SINK])
    found = out and PACTL_PERCENT.search(out)
    return int(found.group(1)) if found else None


def _set_volume(tool: str, percent: int) -> bool:
    if tool == "wpctl":
        cmd = ["wpctl", "set-volume", _WPCTL_SINK, f"{percent / 100:.2f}"]
    else:
        cmd = ["pactl", "set-sink-volume", _PACTL_SINK, f"{percent}%"]
    return run_sound_tool(cmd) is not None


def _set_mute(tool: str, muted: bool) -> bool:
    flag = "1" if muted else "0"
    if tool == "wpctl":
        cmd = ["wpctl", "set-mute", _WPCTL_SINK, flag]
    else:
        cmd = ["pactl", "set-sink-mute", _PACTL_SINK, flag]
    return run_sound_tool(cmd) is not None


def run(request: SoundRequest, beeps_on: bool) -> bool:
    """Do *request* on the default output. True: done and told; False: the
    sound system didn't answer, said with a note, or Esc came while the
    level was read. "Volume up" also unmutes, as the desktop's volume keys
    do."""
    action, tool = request.action, _tool()
    if action in ("mute", "unmute"):
        if not _set_mute(tool, action == "mute"):
            command_notices.say("no_sound_control", beeps_on)
            return False
        logger.info(f"Voice command: {action}")
        command_notices.tell("muted" if action == "mute" else "unmuted", beeps_on)
        return True
    current = output_volume(tool)
    if current is None:
        command_notices.say("no_sound_control", beeps_on)
        return False
    if keyboard_output.esc_stopped(f"done ({action})"):
        return False
    percent = target_percent(current, action, request.steps)
    if not _set_volume(tool, percent) or (action == "volume up" and not _set_mute(tool, False)):
        command_notices.say("no_sound_control", beeps_on)
        return False
    logger.info(f"Voice command: {action} ({percent}%)")
    command_notices.tell("volume", beeps_on, percent=percent)
    return True
