"""The text "Report a Problem" shows (problem_report_window.py): what someone
helping with a TuxTalk problem asks for first, gathered on this computer.

TuxTalk sends none of it anywhere. The person reads it all, then copies it,
saves it or pastes it into a GitHub issue themselves. What they dictate is
already kept out of the log (logger.redact_text) unless they turned that
logging on, which the window warns about; their home folder and user name
are taken out here.
"""
import logging
import os
import platform
import re
from dataclasses import asdict

logger = logging.getLogger(__name__)

LOG_LINES = 300


def build(layout, home=None, user=None) -> str:
    """The whole report, scrubbed. *layout* is layout_text(), read on the
    GTK thread: on KDE it asks over the shared session D-Bus connection.
    Any one fact that can't be read says so instead of stopping the rest."""
    from .config import load_config
    cfg = load_config()
    sections = (
        ("TuxTalk", _tuxtalk(cfg)),
        ("This computer", _computer(cfg, layout)),
        ("Settings", _settings(cfg)),
        (f"Log, the last {LOG_LINES} lines", _log_tail()),
    )
    text = "\n\n".join(f"## {title}\n{body}" for title, body in sections)
    return scrub(text, home or os.path.expanduser("~"), user or _user_name())


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


# Log lines that show what was dictated, when logging it was on: the text in
# quotes, where it's otherwise "[hidden, N chars]" (logger.redact_text).
_DICTATED = re.compile(r"(Raw transcription \([^)]*\)|Normalized text): '")


def has_dictations(report) -> bool:
    """Whether the report's log shows what was dictated: logging it may be off
    now and the lines still there."""
    return bool(_DICTATED.search(report))


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
        return f"couldn't tell ({type(e).__name__}: {e})"
    return "unknown" if value in (None, "") else value


def _tuxtalk(cfg):
    from . import __version__, recommend, uinput_helper
    from .update_checker import get_install_type
    lines = [
        f"Version: {__version__}",
        f"Installed as: {fact(get_install_type)}",
        f"Speech: {fact(lambda: recommend.engine_line(cfg.model, cfg.device))}",
        f"Hotkey helper: {fact(_helper_state)}",
        f"Typing: {fact(lambda: uinput_helper.typing_blocked_reason() or 'can type')}",
    ]
    return "\n".join(lines)


def _helper_state():
    from . import secure_input
    if not secure_input.is_installed():
        return "not installed (the input group is used instead)"
    version = secure_input.running_version()
    return f"running, version {version}" if version else "installed but not answering"


def _computer(cfg, layout):
    from . import desktop_detect, recommend
    session = os.environ.get("XDG_SESSION_TYPE") or "unknown"
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or "unknown"
    lines = [
        f"System: {fact(os_name)}",
        f"Kernel: {platform.release()}",
        f"Desktop: {desktop} ({fact(desktop_detect.get_desktop_environment)}), {session}",
        f"Keyboard layout: {layout}",
        f"Graphics: {fact(lambda: recommend.detect_hardware(cfg).gpu_name or 'none TuxTalk would use')}",
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
    layout, variant = found
    return f"{layout} ({variant})" if variant else layout


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
