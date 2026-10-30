"""The microphone's volume in the sound server, for Preferences' Input Volume
slider: which source a microphone setting records from, and its volume,
read and set through PipeWire (wpctl) or PulseAudio (pactl)."""
import json
import os
import re
import subprocess

from .logger import setup_logger

logger = setup_logger(__name__)

# wpctl get-volume: "Volume: 0.90", or "Volume: 0.90 [MUTED]".
_WPCTL_VOLUME = re.compile(r"Volume:\s*([\d.]+)")
# pactl get-source-volume: "Volume: mono: 43423 /  66% / -10.73 dB".
_PACTL_PERCENT = re.compile(r"/\s*(\d+)%\s*/")

# The Input Volume slider's target when TuxTalk uses the system default mic.
DEFAULT_SOURCE = "@DEFAULT_SOURCE@"
# Mic settings that record from the system default: "" (System Default) and
# the sound server's own catch-all devices.
DEFAULT_MIC_NAMES = ("", "default", "pipewire", "pulse")


def recorded_mic(mic, device_names):
    """The input device the service records from for the mic setting *mic*,
    or "" for the system default. Same rule as microphones.find_input_device: the
    first name containing *mic*, and the default when none does (a mic that
    was unplugged, say)."""
    if mic:
        for name in device_names:
            if mic.lower() in name.lower():
                return name
    return ""


def list_sources():
    """The PipeWire/PulseAudio sources as pactl reports them, or [] if it can't."""
    try:
        result = subprocess.run(["pactl", "-f", "json", "list", "sources"],
                                capture_output=True, text=True, errors="replace", timeout=2)
        if result.returncode == 0:
            return json.loads(result.stdout)
        # pactl before 16 (Ubuntu 22.04, Mint 21) has no JSON output.
        result = subprocess.run(["pactl", "list", "sources"],
                                capture_output=True, text=True, errors="replace", timeout=2,
                                env={**os.environ, "LC_ALL": "C"})
        if result.returncode == 0:
            return parse_pactl_sources(result.stdout)
    except Exception as e:
        logger.warning(f"Could not list audio sources: {e}")
    return []


def parse_pactl_sources(text):
    """`pactl list sources` text (C locale) as the dicts `pactl -f json` gives:
    name, description and properties, which is all the slider needs."""
    sources = []
    current = None
    in_properties = False
    for line in text.splitlines():
        if line.startswith("Source #"):
            current = {"name": "", "description": "", "properties": {}}
            sources.append(current)
            in_properties = False
        elif current is None:
            continue
        elif line.startswith("\t\t"):
            if in_properties and " = " in line:
                key, _, value = line.strip().partition(" = ")
                current["properties"][key] = value.strip('"')
        elif line.startswith("\t"):
            field, _, value = line.strip().partition(": ")
            in_properties = field.rstrip(":") == "Properties"
            if field == "Name":
                current["name"] = value
            elif field == "Description":
                current["description"] = value
    return sources


def source_for_mic(mic, sources):
    """The PipeWire/PulseAudio source behind the sounddevice input named *mic*.

    ALSA devices carry their card and device numbers ("USB Audio (hw:3,0)"),
    which the source's alsa.card and alsa.device properties also hold;
    PipeWire's own devices are named after the source's description. None
    when neither matches, so the slider never quietly changes some other mic.
    """
    inputs = [src for src in sources
              if src.get("properties", {}).get("device.class") != "monitor"
              and not str(src.get("name", "")).endswith(".monitor")]
    hw = re.search(r"\(hw:(\d+),(\d+)\)", mic)
    if not hw:
        for src in inputs:
            if src.get("description") == mic:
                return src.get("name")
        return None
    on_card = [src for src in inputs
               if str(src.get("properties", {}).get("alsa.card")) == hw.group(1)]
    # A card can have several inputs (hw:2,0 and hw:2,2); only a source that
    # names no PCM device of its own may stand in for one it doesn't name.
    for src in on_card:
        if str(src.get("properties", {}).get("alsa.device")) == hw.group(2):
            return src.get("name")
    for src in on_card:
        if "alsa.device" not in src.get("properties", {}):
            return src.get("name")
    return None


def get_volume(source=DEFAULT_SOURCE):
    """*source*'s volume in percent, or None when neither wpctl nor pactl
    can tell. wpctl knows only the default, by name; a named source goes
    to pactl."""
    if source == DEFAULT_SOURCE:
        out = run_sound_tool(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"])
        if out and (found := _WPCTL_VOLUME.search(out)):
            return round(float(found.group(1)) * 100)
    out = run_sound_tool(["pactl", "get-source-volume", source])
    if out and (found := _PACTL_PERCENT.search(out)):
        return int(found.group(1))
    return None


def set_volume(percent, source=DEFAULT_SOURCE) -> bool:
    """Set *source*'s volume to *percent*; False when neither tool did."""
    if source == DEFAULT_SOURCE and run_sound_tool(
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SOURCE@", f"{percent / 100}"]) is not None:
        return True
    return run_sound_tool(["pactl", "set-source-volume", source, f"{percent}%"]) is not None


def run_sound_tool(cmd):
    """*cmd*'s (wpctl's or pactl's) output, or None when it isn't installed,
    fails or takes over two seconds. A failure is logged with why, so a mic
    chosen by a fallback leaves a reason in the log; a tool that isn't
    installed (that sound server isn't running here) isn't."""
    try:
        # errors="replace": a device name in another encoding is still a name.
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=2)
    except FileNotFoundError:
        return None             # not installed here; the other tool may be
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"{' '.join(cmd)} failed: {e}")
        return None
    if result.returncode != 0:
        logger.warning(f"{' '.join(cmd)} failed with exit code {result.returncode}: "
                       f"{result.stderr.strip()[:200]}")
        return None
    return result.stdout
