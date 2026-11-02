"""The text "Report a Problem" shows (problem_report_window.py): what someone
helping with a TuxTalk problem asks for first, gathered on this computer.

TuxTalk sends none of it anywhere. The person reads it all, then copies it,
saves it or pastes it into a GitHub issue themselves. What they dictate is
never in it: the log keeps it out (logger.redact_text) unless that logging
was turned on, and the lines from then are hidden here the same way. Their
home folder, user name and computer's name are taken out too. The hotkey
helper's own lines from the system log come too: hotkeys that never arrive
are explained there, never in TuxTalk's log.
"""
import ast
import logging
import os
import platform
import re
import subprocess
from dataclasses import asdict
from typing import NamedTuple

logger = logging.getLogger(__name__)

LOG_LINES = 300
# The hotkey helper's lines from the system log: which keyboards it watches
# and what it refused, since the computer started. It never logs a key.
HELPER_LOG_LINES = 40


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
    real_name = _real_name()
    log, words_left_out = hide_dictations(_log_tail())
    # The names only where a person's own words can be: a user called
    # "mint" or "ubuntu" mustn't take the distro's name out of the facts.
    sections = [
        ("TuxTalk", scrub(_tuxtalk(cfg), home, None)),
        ("This computer", scrub(_computer(cfg, layout), home, None)),
        ("Settings", scrub(_settings(cfg), home, user, real_name)),
        (f"Log, the last {LOG_LINES} lines", scrub(log, home, user, real_name)),
    ]
    helper_log = _helper_log()
    if helper_log is not None:
        sections.append(("Hotkey helper log, since the computer started",
                         scrub(helper_log, home, user, real_name)))
    text = "\n\n".join(f"## {title}\n{body}" for title, body in sections)
    return Report(text, words_left_out)


def scrub(text, home, user, real_name="") -> str:
    """*text* with the home folder written as ~, the user name taken out,
    a headset named after them too, and each capitalised word of the account's
    *real_name*. A one-letter name is left: a user called "a" or "i" would
    have that word replaced all through the settings and the log. The real
    name's words go only as written, and only those starting with a capital:
    a "Will" mustn't take "will" out of the log, nor "Maria de la Cruz" the
    language "de"."""
    home = (home or "").rstrip("/")
    if home:
        text = re.sub(rf"{re.escape(home)}(?![^/\s'\"])", "~", text)
    if user and len(user) > 1:
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", "<user>", text,
                      flags=re.IGNORECASE)
    for word in (real_name or "").split():
        if len(word) > 1 and word[0].isupper():
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", "<name>", text)
    return text


# Where each log line that can hold what was said (every live.loggable caller)
# says so. With "Log transcribed text" on, the words follow as a Python repr,
# in double quotes when they have an apostrophe; off, as "[hidden, N chars]".
# The voice-edit ones are named exactly: others start "Voice edit:" too and
# quote a window class or the AI's answer, which aren't what was said.
_SPOKEN = re.compile(r"Raw transcription \(|Normalized text: |Custom commands applied: "
                     r"|Stripped a hallucination|keyboard layout can't type "
                     r"|Voice edit(?: result)?: (?=['\"])|Voice edit: no change for "
                     r"|Voice edit done in code, without the AI \("
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


# What a live session's account is called instead of a person's name
# (Fedora's livesys, and casper on Ubuntu, Mint, Pop!_OS and Zorin): its
# words would take every "System" and "User" out of the log.
_LIVE_SESSION_NAMES = {"live system user", "live session user"}


def _real_name():
    """The account's full name ("Jo Ann Smith"), the first field of its GECOS
    entry, or "" when it has none or it's a live session's."""
    try:
        import pwd
        name = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except (ImportError, KeyError):
        return ""
    return "" if name.lower() in _LIVE_SESSION_NAMES else name


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
    if not uinput_helper.check_uinput_exists():
        return "can't: no /dev/uinput (the uinput module isn't loaded)"
    return uinput_helper.typing_blocked_reason() or "can type"


def _helper_state():
    from . import secure_input
    if os.environ.get("FLATPAK_ID"):
        return "not used (Flatpak: the desktop's shortcuts portal)"
    if not secure_input.is_installed():
        return "not installed (the input group is used instead)"
    version, count = secure_input.helper_hello() or (None, None)    # count: None before 1.12
    if not version:
        return "installed but not answering"
    watching = "" if count is None else f", watching {count} keyboard{'' if count == 1 else 's'}"
    return f"running, version {version}{watching}"


def _helper_log():
    """The hotkey helper's last lines in the system log since the computer
    started, the computer's name taken out; None when there's no helper. An
    account outside the wheel, adm or systemd-journal group may not read them,
    and the section says so."""
    from . import secure_input
    if os.environ.get("FLATPAK_ID") or not secure_input.is_installed():
        return None
    try:
        result = subprocess.run(
            ["journalctl", "-u", secure_input.SERVICE, "-b", "-q", "--no-pager",
             "-o", "short-iso", "-n", str(HELPER_LOG_LINES)],
            capture_output=True, text=True, errors="replace", timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.info(f"Report a problem: the hotkey helper's log couldn't be read ({e})")
        return f"(couldn't read the system log: {type(e).__name__})"
    lines = result.stdout.rstrip("\n")
    if not lines:
        return ("(nothing here this account may read: reading the system log needs the "
                "wheel, adm or systemd-journal group, or sudo journalctl -u "
                f"{secure_input.SERVICE} -b)")
    host = platform.node()
    if len(host) > 1:
        lines = re.sub(rf"(?<=\s){re.escape(host)}(?=\s)", "<computer>", lines)
    return lines


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
