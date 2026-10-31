"""The text "Report a Problem" shows (problem_report_window.py): what someone
helping with a TuxTalk problem asks for first, gathered on this computer.

TuxTalk sends none of it anywhere. The person reads it all, then copies it,
saves it or pastes it into a GitHub issue themselves. What they dictate is
never in it: the log keeps it out (logger.redact_text) unless that logging
was turned on, and the lines from then are hidden here the same way. Their
home folder and user name are taken out too.
"""
import ast
import logging
import os
import platform
import re
from dataclasses import asdict
from typing import NamedTuple

logger = logging.getLogger(__name__)

LOG_LINES = 300


class Report(NamedTuple):
    """The report's text, and whether dictated words were left out of its log."""
    text: str
    words_left_out: bool


def build(layout, home=None, user=None) -> Report:
    """The whole report, scrubbed. *layout* is layout_text(), read on the
    GTK thread: on KDE it asks over the shared session D-Bus connection.
    Any one fact that can't be read says so instead of stopping the rest."""
    from .config import load_config
    cfg = load_config()
    home = home or os.path.expanduser("~")
    user = user or _user_name()
    log, words_left_out = hide_dictations(_log_tail())
    # The user name only where a person's own words can be: a user called
    # "mint" or "ubuntu" mustn't take the distro's name out of the facts.
    sections = (
        ("TuxTalk", scrub(_tuxtalk(cfg), home, None)),
        ("This computer", scrub(_computer(cfg, layout), home, None)),
        ("Settings", scrub(_settings(cfg), home, user)),
        (f"Log, the last {LOG_LINES} lines", scrub(log, home, user)),
    )
    text = "\n\n".join(f"## {title}\n{body}" for title, body in sections)
    return Report(text, words_left_out)


def scrub(text, home, user) -> str:
    """*text* with the home folder written as ~ and the user name taken out,
    "Ron's headset" too. A one-letter name is left: it would take out
    letters everywhere."""
    home = (home or "").rstrip("/")
    if home:
        text = re.sub(rf"{re.escape(home)}(?![^/\s'\"])", "~", text)
    if user and len(user) > 1:
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", "<user>", text,
                      flags=re.IGNORECASE)
    return text


# Where each log line that can hold what was said (every live.loggable caller)
# says so. With "Log transcribed text" on, the words follow as a Python repr,
# in double quotes when they have an apostrophe; off, as "[hidden, N chars]".
_SPOKEN = re.compile(r"Raw transcription \(|Normalized text: |Custom commands applied: "
                     r"|Stripped a hallucination|keyboard layout can't type |Voice edit"
                     r"|Heard \"press enter\" in context|Short phrase: |Chunk \([^)]*\): ")
_QUOTED = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"")


def _hidden(quoted):
    """A repr'd text as the log writes it with that logging off."""
    try:
        return f"[hidden, {len(ast.literal_eval(quoted.group()))} chars]"
    except (ValueError, SyntaxError):
        return "[hidden]"


def hide_dictations(log):
    """*log* with every dictated text in it hidden, as logging it off would
    have, and whether there was any: the lines from when it was on stay in
    the log after it's turned off."""
    hid = 0

    def hide(line):
        nonlocal hid
        found = _SPOKEN.search(line)
        if not found:
            return line
        rest, count = _QUOTED.subn(_hidden, line[found.end():])
        hid += count
        return line[:found.end()] + rest
    log = "\n".join(hide(line) for line in log.split("\n"))
    return log, hid > 0


def _user_name():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        return os.environ.get("USER", "")


def fact(read):
    """read(), or why it couldn't be read: the report still comes together."""
    try:
        value = read()
    except Exception as e:
        logger.info(f"Report a problem: a fact couldn't be read ({type(e).__name__}: {e})")
        return f"couldn't tell ({type(e).__name__}: {e})"
    return "unknown" if value in (None, "") else value


def _tuxtalk(cfg):
    from . import __version__, recommend
    from .installation import get_install_type
    lines = [
        f"Version: {__version__}",
        f"Installed as: {fact(get_install_type)}",
        f"Speech: {fact(lambda: recommend.engine_line(cfg.model, cfg.device))}",
        f"Hotkey helper: {fact(_helper_state)}",
        f"Typing: {fact(_typing_state)}",
    ]
    return "\n".join(lines)


def _typing_state():
    from . import uinput_helper
    if os.environ.get("FLATPAK_ID"):
        return "through the desktop's portal (Flatpak)"
    if not os.path.exists("/dev/uinput"):
        return "can't: no /dev/uinput (the uinput module isn't loaded)"
    return uinput_helper.typing_blocked_reason() or "can type"


def _helper_state():
    from . import secure_input
    if os.environ.get("FLATPAK_ID"):
        return "not used (Flatpak: the desktop's shortcuts portal)"
    if not secure_input.is_installed():
        return "not installed (the input group is used instead)"
    version = secure_input.running_version()
    return f"running, version {version}" if version else "installed but not answering"


def _computer(cfg, layout):
    from . import desktop_detect, recommend
    session = os.environ.get("XDG_SESSION_TYPE") or "unknown"
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or "unknown"
    gpu = fact(lambda: recommend.detect_hardware(cfg).gpu_name or "none TuxTalk would use")
    lines = [
        f"System: {fact(os_name)}",
        f"Kernel: {platform.release()}",
        f"Desktop: {desktop} ({fact(desktop_detect.get_desktop_environment)}), {session}",
        f"Keyboard layout: {layout}",
        f"Graphics: {gpu}",
        f"Python: {platform.python_version()}",
    ]
    return "\n".join(lines)


def os_name():
    """The distro's own name for itself, from /etc/os-release."""
    with open("/etc/os-release", encoding="utf-8") as f:
        for line in f:
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


def layout_text():
    """The keyboard layout in use, for the report."""
    from . import keyboard_layout
    found = keyboard_layout.active_layout()
    if not found:
        return None
    layout, variant, options = found
    text = f"{layout} ({variant})" if variant else layout
    return f"{text}, options {options}" if options else text


def _settings(cfg):
    return "\n".join(f"{key} = {value!r}" for key, value in asdict(cfg).items())


def _log_tail():
    from .logger import LOG_FILE_NAME, _log_dir
    path = _log_dir() / LOG_FILE_NAME
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-LOG_LINES:]
    except FileNotFoundError:
        return "(no log yet)"
    except OSError as e:
        return f"(couldn't read the log: {e})"
    return "".join(lines).rstrip("\n")
