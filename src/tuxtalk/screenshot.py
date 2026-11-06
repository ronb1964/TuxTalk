""""Take a screenshot" by voice (TuxTalk Labs): the whole screen, or with
"take a screenshot of this window" the window with the focus, saved where
the desktop keeps screenshots, with a note saying it was saved.

KDE uses Spectacle in the background, for either. Elsewhere the whole
screen comes from the screenshot portal (GNOME may show its own dialog the
first time), then gnome-screenshot or xfce4-screenshooter. A window comes
from those tools on X11, and from the desktop's own picker on GNOME and
COSMIC, whose portals can't take one window by themselves; elsewhere
TuxTalk says it can't. A portal's picture left in a folder emptied at
logout or restart (COSMIC's /tmp, Hyprland's runtime folder) is moved in
with the others. Nothing is uploaded. Programs run with a timeout and no
shell, and the log names the tool, never a path: a path holds the user's
name. The waits are on the dictation worker, the picker's on a thread of
its own, never GTK's."""
import os
import shutil
import stat
import subprocess
import tempfile
import threading
from datetime import datetime

from gi.repository import Gio, GLib

from . import command_notices, desktop_detect, portal_common, x11_desktop
from .logger import setup_logger

logger = setup_logger(__name__)

# The portal may be waiting for the user to allow screenshots the first
# time, which takes as long as a person takes to read and click; with its
# picker open (a window's screenshot), as long as they take to choose.
PORTAL_WAIT_S = 30.0
PICKER_WAIT_S = 90.0
# The call only hands the request over; the answer comes later, as a signal.
PORTAL_CALL_TIMEOUT_MS = 10_000
TOOL_TIMEOUT_S = 10.0

_PORTAL_IFACE = "org.freedesktop.portal.Screenshot"
_PORTAL_SAVED, _PORTAL_CANCELLED = 0, 1
# Each desktop tool's command for the whole screen and for the window with
# the focus, the file to write coming last.
_SCREEN_TOOLS = [["gnome-screenshot", "-f"], ["xfce4-screenshooter", "-f", "-s"]]
_WINDOW_TOOLS = [["gnome-screenshot", "-w", "-f"], ["xfce4-screenshooter", "-w", "-s"]]

# The desktops whose portal opens a picker in which a window can be chosen.
# Sway's and Hyprland's choose an area, or take the whole screen.
_PICKER_DESKTOPS = ("gnome", "cosmic")
# Under XDG_RUNTIME_DIR, the document portal's view of files kept elsewhere.
_DOCUMENT_PORTAL = "doc"
# The picker still being waited for: one at a time.
_picker = None
_picker_lock = threading.Lock()
# The file names new_path() has handed out whose files aren't written yet.
_handed_out = set()
_names_lock = threading.Lock()

# A call that ended with one of these may still have reached the portal.
_NO_REPLY = frozenset(f"org.freedesktop.DBus.Error.{name}"
                      for name in ("NoReply", "Timeout", "TimedOut"))


def folder() -> str:
    """Where TuxTalk's screenshots are saved: Screenshots in the desktop's
    Pictures folder, which is also where Spectacle keeps its own."""
    pictures = (GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
                or os.path.expanduser("~/Pictures"))
    return os.path.join(pictures, "Screenshots")


def new_path() -> str:
    """A file in folder() named as Spectacle names its own, folder made.
    Never an existing file's name, nor one handed out already whose file
    isn't written yet (the picker's picture can be moved in while another
    is taken), even for two in the same second, so a file found there
    afterwards is this screenshot."""
    os.makedirs(folder(), exist_ok=True)
    stem = os.path.join(folder(), datetime.now().strftime("Screenshot_%Y%m%d_%H%M%S"))
    with _names_lock:
        # A name whose file is written needs remembering no longer.
        _handed_out.difference_update({name for name in _handed_out if os.path.exists(name)})
        path, number = stem + ".png", 1
        while os.path.exists(path) or path in _handed_out:
            number += 1
            path = f"{stem}-{number}.png"
        _handed_out.add(path)
    return path


def _path_for_tool() -> str | None:
    """new_path(), or None when the folder can't be made."""
    try:
        return new_path()
    except OSError as e:
        logger.warning(f"Screenshot folder could not be made: {type(e).__name__}")
        return None


def _shown(directory: str) -> str:
    """*directory* with the home folder written as ~, as the note shows it."""
    home = os.path.expanduser("~")
    if directory == home or directory.startswith(home + os.sep):
        return "~" + directory[len(home):]
    return directory


def _run_tool(command: list[str]) -> bool:
    """Whether the program exits 0 and wrote the file it was told to: the
    last argument of every command, a name no file had before (new_path)."""
    name = command[0]
    try:
        result = subprocess.run(command, capture_output=True, timeout=TOOL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        # The error's text holds the command, and with it the path.
        logger.warning(f"Screenshot with {name} failed: {type(e).__name__}")
        return False
    if result.returncode != 0 or not _written(command[-1]):
        logger.warning(f"Screenshot with {name} saved nothing (exit code {result.returncode})")
        return False
    return True


def _written(path: str) -> bool:
    """Whether *path* holds something."""
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def _first_tool(commands: list[list[str]]) -> str | None:
    """The name of the first installed tool in *commands* if it took the
    screenshot, given a new file as its last argument. Only that one is
    tried: a tool that opened a window or timed out may still save a shot,
    and a second tool would take another."""
    for command in commands:
        if shutil.which(command[0]):
            path = _path_for_tool()
            return command[0] if path and _run_tool(command + [path]) else None
    return None


def _ask_portal(interactive: bool = False) -> tuple[str, str]:
    """Ask the screenshot portal for the whole screen, or with *interactive*
    to open the desktop's own screenshot picker. Returns the outcome
    and, for "saved", the file: "saved", "cancelled" (the user said no),
    "failed" (the portal answered it couldn't), "unavailable" (no portal, or
    one that refused the call) or "unconfirmed": one may have been saved all
    the same (no answer in PORTAL_WAIT_S, or PICKER_WAIT_S for the picker,
    with the request still open for a late answer; a call that timed out;
    or done without a file it can read).
    The response is waited for on a main context of its own, so GTK's loop
    is never involved."""
    context = GLib.MainContext()
    loop = GLib.MainLoop(context)
    answer = {"outcome": "unconfirmed", "file": ""}

    def on_response(code, results):
        if code == _PORTAL_SAVED:
            if results.get("uri"):
                try:
                    answer.update(outcome="saved",
                                  file=GLib.filename_from_uri(results["uri"])[0])
                except GLib.Error:
                    # Its message quotes the address, and with it the path.
                    logger.info("Screenshot portal answered with an address it can't read")
        else:
            answer["outcome"] = "cancelled" if code == _PORTAL_CANCELLED else "failed"
        loop.quit()

    def on_error(_error):
        # A response came but couldn't be read (a uri naming no local file):
        # the portal may have saved one, so no tool may take another.
        answer["outcome"] = "unconfirmed"
        loop.quit()

    def out_of_time(*_):
        answer["out_of_time"] = True
        loop.quit()
        return GLib.SOURCE_REMOVE

    def options(token):
        return GLib.Variant("(sa{sv})", ("", {"handle_token": GLib.Variant("s", token),
                                              "interactive": GLib.Variant("b", interactive)}))

    timeout = GLib.timeout_source_new(int((PICKER_WAIT_S if interactive else PORTAL_WAIT_S)
                                          * 1000))
    timeout.set_callback(out_of_time)
    context.push_thread_default()
    bus, token, unsubscribe = None, portal_common.new_request_token(), None
    try:
        bus = portal_common.session_bus()
        unsubscribe = portal_common.call_portal(
            bus, _PORTAL_IFACE, "Screenshot", options, on_response, on_error,
            PORTAL_CALL_TIMEOUT_MS, token=token)
        timeout.attach(context)
        loop.run()
    except GLib.Error as e:
        # The error's name, not its message, which could quote a path.
        name = Gio.DBusError.get_remote_error(e) or f"{e.domain} {e.code}"
        if _may_have_reached(e):
            logger.info(f"Screenshot portal didn't answer the call ({name})")
            answer["out_of_time"] = True
        else:
            logger.info(f"Screenshot portal not available ({name})")
            answer["outcome"] = "unavailable"
    finally:
        timeout.destroy()
        if unsubscribe:
            unsubscribe()
        context.pop_thread_default()
    if interactive and answer.get("out_of_time") and bus is not None:
        # Left open (no answer in time, or a call that timed out but may
        # have reached it), the next "take a screenshot of this window"
        # would open a second picker beside it.
        portal_common.close_request(bus, portal_common.request_path(bus, token))
    return answer["outcome"], answer["file"]


def _may_have_reached(error: GLib.Error) -> bool:
    """Whether the portal may have had the request despite *error*: the call
    timed out or wasn't replied to. Any other error is an answer: no portal,
    or one that refused."""
    if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT):
        return True
    return Gio.DBusError.get_remote_error(error) in _NO_REPLY


def take(beeps_on: bool, window: bool = False) -> bool:
    """Take a screenshot of the whole screen, or of the window with the
    focus, and say where it was saved. False: nothing saved, or not known
    to be, with a note saying why; True for the picker once it is asked
    for, its outcome said when it comes. Once a screenshot may have been
    taken (a tool ran, or the portal was asked and was slow) no other way
    is tried, so there is never a second one."""
    what = "a screenshot of the window" if window else "a screenshot"
    desktop = desktop_detect.get_desktop_environment()
    if desktop == "kde" and shutil.which("spectacle"):
        # KDE's portal is Spectacle again, so there is nothing else to ask.
        return _with_spectacle("--activewindow" if window else "--fullscreen", what, beeps_on)
    if not window:
        return _from_portal(False, _SCREEN_TOOLS, what, beeps_on)
    if desktop_detect.is_x11_session(os.environ) and any(
            shutil.which(command[0]) for command in _WINDOW_TOOLS):
        return _window_with_tool(what, beeps_on)
    if desktop in _PICKER_DESKTOPS:
        return _open_picker(what, beeps_on)
    logger.info(f"Voice command: take {what} (no way to take one window here)")
    command_notices.say("cant_window_screenshot_here", beeps_on)
    return False


def _with_spectacle(mode: str, what: str, beeps_on: bool) -> bool:
    """Spectacle in the background, *mode* (--fullscreen or --activewindow)."""
    path = _path_for_tool()
    if path and _run_tool(["spectacle", "--background", mode, "--nonotify", "--output", path]):
        return _saved("spectacle", folder(), what, beeps_on)
    return _nothing_saved(what, beeps_on)


def _window_with_tool(what: str, beeps_on: bool) -> bool:
    """The window with the focus, by an X11 tool; only with an app window in
    front, since both take the whole desktop when there is none."""
    if not x11_desktop.front_window_open():
        logger.info(f"Voice command: take {what} (no app window in front)")
        command_notices.say("no_focused_window", beeps_on)
        return False
    return _with_tool(_WINDOW_TOOLS, what, beeps_on)


def _with_tool(commands: list[list[str]], what: str, beeps_on: bool) -> bool:
    """The first installed of the desktop tools *commands*, said as saved or not."""
    tool = _first_tool(commands)
    return _saved(tool, folder(), what, beeps_on) if tool else _nothing_saved(what, beeps_on)


def _from_portal(interactive: bool, tools: list[list[str]], what: str, beeps_on: bool) -> bool:
    """Ask the portal (its picker with *interactive*), then, only when it
    answered that it can't or isn't there, the first installed of *tools*."""
    outcome, file = _ask_portal(interactive)
    answered = ""
    if interactive and outcome == "failed":
        # A picker closed with Escape answers "failed" on GNOME; a real
        # failure looks the same, so the log keeps the portal's answer.
        outcome, answered = "cancelled", ", the portal said failed"
    if outcome == "saved":
        # The portal said so; not seeing the file (a sandbox) doesn't change that.
        return _saved("the portal", _keep(file) if os.path.exists(file) else None, what,
                      beeps_on)
    if outcome in ("cancelled", "unconfirmed"):
        logger.info(f"Voice command: take {what} ({outcome}{answered})")
        command_notices.say("screenshot_cancelled" if outcome == "cancelled"
                            else "screenshot_unconfirmed", beeps_on)
        return False
    return _with_tool(tools, what, beeps_on) if tools else _nothing_saved(what, beeps_on)


def _open_picker(what: str, beeps_on: bool) -> bool:
    """Open the desktop's screenshot picker, its outcome waited for on a
    thread of its own: the person may take as long as PICKER_WAIT_S, and on
    the dictation worker every dictation said meanwhile waited, and a quit
    lost them. One picker at a time."""
    global _picker
    with _picker_lock:
        if _picker is not None and _picker.is_alive():
            logger.info(f"Voice command: take {what} (the picker is already open)")
            command_notices.say("screenshot_picker_open", beeps_on)
            return False
        logger.info(f"Voice command: take {what} (asked the desktop's picker)")
        _picker = threading.Thread(target=_picked, args=(what, beeps_on), daemon=True,
                                   name="screenshot-picker")
        _picker.start()
    return True


def _picked(what: str, beeps_on: bool):
    """The picker's outcome, said when it comes. The window tools see no
    window on these desktops, so none is tried after it. An error is logged
    here, at the thread's end, or it would reach only stderr."""
    try:
        _from_portal(True, [], what, beeps_on)
    except Exception as e:
        logger.warning(f"Screenshot picker failed: {type(e).__name__}")


def _inside(file: str, folder: str) -> bool:
    """Whether *file* (its folder's links resolved) lies in *folder*."""
    return file.startswith(os.path.realpath(folder) + os.sep)


def _keep(file: str) -> str:
    """Keep the portal's screenshot *file* and give the folder it is in.
    COSMIC's portal answers with a file in /tmp and Hyprland's with one in
    XDG_RUNTIME_DIR (emptied at logout, and replaced by its next one):
    those are moved into folder() with the others. Not the document
    portal's, which shows a file kept elsewhere. Only a file of this user's
    is moved, never a link: another user could have put one in /tmp. A
    file left where it is keeps its folder as named, links and all."""
    named = "/" + file.lstrip("/")          # COSMIC's is named //tmp/...
    where = os.path.dirname(named)
    real = os.path.join(os.path.realpath(where), os.path.basename(named))
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    kept = tuple(filter(None, (os.path.expanduser("~"), os.path.dirname(folder()),
                               runtime and os.path.join(runtime, _DOCUMENT_PORTAL))))
    temporary = tuple(filter(None, (tempfile.gettempdir(), "/tmp", "/var/tmp", runtime)))
    if any(_inside(real, root) for root in kept) or not any(
            _inside(real, root) for root in temporary):
        return where
    try:
        info = os.lstat(real)
    except OSError as e:
        logger.warning(f"Screenshot from the portal can't be looked at: {type(e).__name__}")
        return where
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        logger.warning("Screenshot left where the portal put it: not a file of this user's")
        return where
    path = _path_for_tool()
    if path is None:
        return where
    try:
        shutil.move(real, path)
    except OSError as e:
        logger.warning(f"Screenshot couldn't be moved out of the portal's folder: "
                       f"{type(e).__name__}")
        return where
    return os.path.dirname(path)


def _nothing_saved(what: str, beeps_on: bool) -> bool:
    """Say that no screenshot was taken."""
    logger.info(f"Voice command: take {what} (no way to take one)")
    command_notices.say("no_screenshot_tool", beeps_on)
    return False


def _saved(tool: str, directory: str | None, what: str, beeps_on: bool) -> bool:
    """Log and say that the screenshot (*what*) was saved in *directory*
    (None: where isn't known)."""
    logger.info(f"Voice command: take {what} (saved, with {tool})")
    if directory is None:
        command_notices.tell("screenshot_saved_unseen", beeps_on)
    else:
        command_notices.tell("screenshot_saved", beeps_on, folder=_shown(directory))
    return True
