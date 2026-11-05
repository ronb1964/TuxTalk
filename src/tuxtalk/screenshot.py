""""Take a screenshot" by voice (TuxTalk Labs): the whole screen, saved where
the desktop keeps screenshots, with a note saying it was saved.

KDE uses Spectacle in the background. Elsewhere the screenshot portal is
asked first (GNOME may show its own dialog the first time), then
gnome-screenshot or xfce4-screenshooter. Nothing is uploaded or copied
anywhere. Programs run with a timeout and no shell, and the log names the
tool, never a path: a path holds the user's name. The wait is on the
dictation worker, never GTK's thread."""
import os
import shutil
import subprocess
from datetime import datetime

from gi.repository import GLib

from . import command_notices, desktop_detect, portal_common
from .logger import setup_logger

logger = setup_logger(__name__)

# The portal may be waiting for the user to allow screenshots the first
# time, which takes as long as a person takes to read and click.
PORTAL_WAIT_S = 30.0
TOOL_TIMEOUT_S = 10.0

_PORTAL_IFACE = "org.freedesktop.portal.Screenshot"
_PORTAL_SAVED, _PORTAL_CANCELLED = 0, 1


def folder() -> str:
    """Where TuxTalk's screenshots are saved: Screenshots in the desktop's
    Pictures folder, which is also where Spectacle keeps its own."""
    pictures = (GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
                or os.path.expanduser("~/Pictures"))
    return os.path.join(pictures, "Screenshots")


def new_path() -> str:
    """A file in folder() named as Spectacle names its own, folder made."""
    os.makedirs(folder(), exist_ok=True)
    return os.path.join(folder(), datetime.now().strftime("Screenshot_%Y%m%d_%H%M%S.png"))


def _shown(directory: str) -> str:
    """*directory* with the home folder written as ~, as the note shows it."""
    home = os.path.expanduser("~")
    if directory == home or directory.startswith(home + os.sep):
        return "~" + directory[len(home):]
    return directory


def _run_tool(command: list[str]) -> bool:
    """Whether the program exits 0 and the file it was told to write exists.
    The last argument of every command is that file."""
    name = command[0]
    try:
        result = subprocess.run(command, capture_output=True, timeout=TOOL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        # The error's text holds the command, and with it the path.
        logger.warning(f"Screenshot with {name} failed: {type(e).__name__}")
        return False
    if result.returncode != 0 or not os.path.exists(command[-1]):
        logger.warning(f"Screenshot with {name} saved nothing (exit code {result.returncode})")
        return False
    return True


def _first_tool(commands: list[list[str]]) -> str | None:
    """The name of the first installed tool in *commands* if it took the
    screenshot. Only that one is tried: a tool that opened a window or timed
    out may still save a shot, and a second tool would take another."""
    for command in commands:
        if shutil.which(command[0]):
            return command[0] if _run_tool(command) else None
    return None


def _ask_portal() -> tuple[str, str]:
    """Ask the screenshot portal for the whole screen. Returns the outcome
    and, for "saved", the file: "saved", "cancelled" (the user said no),
    "failed" (the portal answered it couldn't), "unavailable" (no portal) or
    "timeout" (no answer in PORTAL_WAIT_S; the request is still open, so a
    late Allow can still save one). The response is waited for on a main
    context of its own, so GTK's loop is never involved."""
    context = GLib.MainContext()
    loop = GLib.MainLoop(context)
    answer = {"outcome": "timeout", "file": ""}

    def on_response(code, results):
        if code == _PORTAL_SAVED and results.get("uri"):
            answer.update(outcome="saved", file=GLib.filename_from_uri(results["uri"])[0])
        else:
            answer["outcome"] = "cancelled" if code == _PORTAL_CANCELLED else "failed"
        loop.quit()

    def on_error(_error):
        answer["outcome"] = "failed"
        loop.quit()

    def out_of_time(*_):
        loop.quit()
        return GLib.SOURCE_REMOVE

    def options(token):
        return GLib.Variant("(sa{sv})", ("", {"handle_token": GLib.Variant("s", token),
                                              "interactive": GLib.Variant("b", False)}))

    timeout = GLib.timeout_source_new(int(PORTAL_WAIT_S * 1000))
    timeout.set_callback(out_of_time)
    context.push_thread_default()
    unsubscribe = None
    try:
        unsubscribe = portal_common.call_portal(
            portal_common.session_bus(), _PORTAL_IFACE, "Screenshot", options,
            on_response, on_error)
        timeout.attach(context)
        loop.run()
    except GLib.Error as e:
        logger.info(f"Screenshot portal not available: {e.message}")
        answer["outcome"] = "unavailable"
    finally:
        timeout.destroy()
        if unsubscribe:
            unsubscribe()
        context.pop_thread_default()
    return answer["outcome"], answer["file"]


def take(beeps_on: bool) -> bool:
    """Take a screenshot of the whole screen and say where it was saved.
    False: nothing saved, or not known to be, with a note saying why. Once
    a screenshot may have been taken (Spectacle ran, or the portal was asked
    and was slow) no other way is tried, so there is never a second one."""
    try:
        path = new_path()
    except OSError as e:
        logger.warning(f"Screenshot folder could not be made: {type(e).__name__}")
        path = None
    if desktop_detect.get_desktop_environment() == "kde" and shutil.which("spectacle"):
        # KDE's portal is Spectacle again, so there is nothing else to ask.
        if path and _run_tool(["spectacle", "--background", "--fullscreen", "--nonotify",
                               "--output", path]):
            return _saved("spectacle", folder(), beeps_on)
        return _nothing_saved(beeps_on)
    outcome, file = _ask_portal()
    if outcome == "saved":
        # The portal said so; not seeing the file (a sandbox) doesn't change that.
        return _saved("the portal", os.path.dirname(file) if os.path.exists(file) else None,
                      beeps_on)
    if outcome in ("cancelled", "timeout"):
        logger.info(f"Voice command: take a screenshot ({outcome})")
        command_notices.say("screenshot_cancelled" if outcome == "cancelled"
                            else "screenshot_unconfirmed", beeps_on)
        return False
    tool = path and _first_tool([["gnome-screenshot", "-f", path],
                                 ["xfce4-screenshooter", "-f", "-s", path]])
    if tool:
        return _saved(tool, folder(), beeps_on)
    return _nothing_saved(beeps_on)


def _nothing_saved(beeps_on: bool) -> bool:
    """Say that no screenshot was taken."""
    logger.info("Voice command: take a screenshot (no way to take one)")
    command_notices.say("no_screenshot_tool", beeps_on)
    return False


def _saved(tool: str, directory: str | None, beeps_on: bool) -> bool:
    """Log and say that the screenshot was saved in *directory* (None: where
    isn't known)."""
    logger.info(f"Voice command: take a screenshot (saved, with {tool})")
    if directory is None:
        command_notices.tell("screenshot_saved_unseen", beeps_on)
    else:
        command_notices.tell("screenshot_saved", beeps_on, folder=_shown(directory))
    return True
