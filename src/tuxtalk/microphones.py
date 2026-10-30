"""Which microphone to record from: the one named in Preferences, or the
desktop's default as PipeWire reports it. PortAudio's own "default" can't be
trusted for that: on PipeWire it is a virtual 128-channel device that can
record clipped noise instead of the microphone.
"""
import subprocess

from .logger import setup_logger

logger = setup_logger(__name__)

# The default source's names that PortAudio lists its devices by.
_LISTED_NAMES = ("node.nick", "node.description")
# A device with more inputs than this is a virtual mixer, not a microphone.
_MAX_MIC_CHANNELS = 10
# Not microphones: monitors of outputs, and the sound server's own wrappers.
_NOT_HARDWARE = ("monitor", "default", "pipewire", "sysdefault", "spdif")


def _default_source_properties() -> list[tuple[str, str]] | None:
    """The default source's properties as `wpctl inspect` lists them, in its
    order, or None without PipeWire (no wpctl) or when it doesn't answer."""
    try:
        # errors="replace": a device name in another encoding is still a name.
        result = subprocess.run(["wpctl", "inspect", "@DEFAULT_SOURCE@"],
                                capture_output=True, text=True, errors="replace", timeout=2)
    except FileNotFoundError:
        return None                     # not a PipeWire system
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"PipeWire didn't say which microphone is the default: {e}")
        return None
    if result.returncode != 0:
        return None
    properties = []
    for line in result.stdout.splitlines():
        key, sep, value = line.strip().lstrip("*").partition("=")
        if sep:
            properties.append((key.strip(), value.strip().strip('"').strip()))
    return properties


def default_source_name() -> str | None:
    """PipeWire's default microphone, by its unique node name, or None.

    Used to notice the desktop switching its default input (GNOME Quick
    Settings, KDE's volume applet). None when wpctl is missing or fails, so a
    system without PipeWire never looks like a switch.
    """
    for key, value in _default_source_properties() or ():
        if key == "node.name":
            return value or None
    return None


def _inputs(devices):
    """(index, device) for each device that records, in PortAudio's order."""
    return [(i, d) for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]


def _is_microphone(device) -> bool:
    return device.get("max_input_channels", 0) < _MAX_MIC_CHANNELS


def _named_device(devices, name: str) -> int | None:
    """The first input whose name holds *name*, or None (unplugged, say)."""
    name = name.lower()
    return next((i for i, d in _inputs(devices) if name in d.get("name", "").lower()), None)


def _default_source_device(devices, source_names) -> int | None:
    """The microphone PortAudio lists under one of the default source's
    names, trying them in wpctl's order."""
    for source in source_names:
        for i, d in _inputs(devices):
            if _is_microphone(d) and source.lower() in d.get("name", "").lower():
                logger.info(f"Auto-detected mic: [{i}] {d['name']} (via PipeWire default source)")
                return i
    return None


def _sound_server_device(devices, source: str) -> int | None:
    """PortAudio's "pipewire" or "pulse" device, which records from whatever
    the default source is. PortAudio lists only hardware under its own name,
    so a virtual default (noise suppression, a remapped source) is reached
    this way; a raw sound card would bypass it."""
    for i, d in _inputs(devices):
        if d.get("name", "").lower() in ("pipewire", "pulse"):
            logger.info(f"Auto-detected mic: [{i}] {d['name']}"
                        f" (the default source {source!r} is not a hardware device)")
            return i
    return None


def _hardware_mic(devices) -> int | None:
    """A real microphone when the default can't be found: a USB one (plugged
    in on purpose) before the built-in sound card."""
    mics = [(i, d) for i, d in _inputs(devices)
            if _is_microphone(d)
            and not any(s in d.get("name", "").lower() for s in _NOT_HARDWARE)]
    if not mics:
        return None
    usb = [(i, d) for i, d in mics if "usb" in d.get("name", "").lower()]
    chosen_i, chosen_d = (usb or mics)[0]
    logger.info(f"Auto-detected mic (hardware fallback): [{chosen_i}] {chosen_d['name']}")
    return chosen_i


def find_input_device(mic_substring: str | None) -> int | None:
    """The sounddevice index to record from, or None for sounddevice's own
    default.

    With *mic_substring* set, the first input whose name holds it. Without
    one, the desktop's default microphone as PipeWire names it, then the
    sound server's device for a virtual default, then a hardware microphone.
    """
    try:
        import sounddevice as sd
        devices = sd.query_devices()
    except Exception as e:      # no PortAudio, or it can't list: sounddevice's default
        logger.warning(f"Could not list the sound devices: {e}")
        return None
    if mic_substring:
        return _named_device(devices, mic_substring)
    sources = [value for key, value in _default_source_properties() or ()
               if key in _LISTED_NAMES and value]
    found = _default_source_device(devices, sources)
    if found is None and sources:
        found = _sound_server_device(devices, sources[0])
    return _hardware_mic(devices) if found is None else found
