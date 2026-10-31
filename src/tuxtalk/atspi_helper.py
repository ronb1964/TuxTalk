"""Whether the cursor is in a password field, asked of the accessibility bus
(AT-SPI), so a dictation is never typed into one.

Plain D-Bus calls on a connection of TuxTalk's own (Gio), not libatspi: the
AppImage doesn't ship libatspi's typelib, and libatspi shares one bus
connection with GTK's own accessibility bridge, which the GTK thread of the
dictation service dispatches while this runs on another thread.

Which apps answer, as tried in the test VMs: GTK 3 and GTK 4 apps, unless their
accessibility is switched off (GTK_A11Y=none, NO_AT_BRIDGE=1). Qt (KDE
apps) and Firefox only while accessibility is switched on, by a screen reader
or the desktop's accessibility setting. Chromium-based browsers and Electron
apps (Brave, Discord, VS Code) only if it was on when they started
(ACCESSIBILITY_ENABLED=1 or the desktop setting); turned on later, they need
a restart. An app that doesn't answer, a slow bus or none at all counts as
"not a password field", so typing goes ahead as it always did.
"""

import concurrent.futures
import functools
import logging
import re
import threading
import time

from gi.repository import Gio, GLib

logger = logging.getLogger(__name__)

# The whole question is given this long, each D-Bus call a part of it: an
# app that hangs must not hold up the dictation waiting to be typed.
BUDGET_S = 0.25
# A dictation's checks again right before each line, line break and paste
# key (keyboard_output.refuse_password_field): a slow bus there would hold
# up every line by the whole budget. Asked afresh each time, never taken from
# an earlier answer: the focus is what may have moved.
RECHECK_BUDGET_S = 0.05
CALL_TIMEOUT_MS = 100

_ROOT = "/org/a11y/atspi/accessible/root"
_REGISTRY = "org.a11y.atspi.Registry"
_ACCESSIBLE = "org.a11y.atspi.Accessible"
_COLLECTION = "org.a11y.atspi.Collection"

# AtspiStateType and AtspiRole numbers (at-spi2-core's atspi-constants.h).
_STATE_ACTIVE = 1
_STATE_FOCUSED = 12
_STATE_SHOWING = 25
_ROLE_PASSWORD_TEXT = 40

# GNOME Shell's own "window" is always active, so it's no sign of a second
# app with the focus. It's asked all the same: the shell's password prompts
# (polkit, keyring, Wi-Fi) are in it, a password field in gnome-shell.
_SHELLS = {"gnome-shell"}

# Replies from an app without the Collection interface (Qt 5, some others).
_NO_COLLECTION = {
    "org.freedesktop.DBus.Error.UnknownMethod",
    "org.freedesktop.DBus.Error.UnknownInterface",
    "org.freedesktop.DBus.Error.UnknownObject",
    "org.freedesktop.DBus.Error.NotSupported",
}

_bus = None
_connecting = False
_bus_changed = threading.Condition()
_last_active = ()


class _OutOfTime(Exception):
    pass


def focused_is_password(budget: float = BUDGET_S, focused_class=lambda: None) -> bool:
    """True when the focused widget of an active window is a password field.
    False when it isn't, and whenever that can't be found out in *budget*
    seconds, unless one was found before the time ran out. *focused_class*()
    is the window class the desktop says has the focus, None when not known:
    which app's answer counts (_password_focus)."""
    started = time.monotonic()
    deadline = started + budget
    try:
        bus = _a11y_bus(deadline)
        return bus is not None and _password_focus(bus, focused_class, deadline)
    except _OutOfTime:
        logger.info(f"Password field check: no answer in {budget}s, typing as usual")
        return False
    except GLib.Error as e:
        logger.debug(f"Password field check: the accessibility bus didn't answer: {e.message}")
        return False
    except Exception as e:
        # An app answering in a shape nobody expected: the dictation is
        # still typed, as before this check existed.
        logger.warning(f"Password field check failed, typing as usual: {e!r}")
        return False
    finally:
        took = time.monotonic() - started
        # Said in the log where it starts to show: half its time or more.
        (logger.info if took >= budget / 2 else logger.debug)(
            f"Password field check took {took:.3f}s")


def _password_focus(bus, focused_class, deadline) -> bool:
    """Whether a password field has the focus in an active window.

    Every active window is asked, not the first: a desktop shell other than
    GNOME's may call its own window active too. Each as soon as it's found,
    so apps that hang further down the list can't use up the time before a
    password field already found is seen. The shell's tree is the biggest:
    its turn comes last.

    An app left in the background can still say a window of its own is
    active, with a password field focused in it, and refuse the dictation
    for nothing. So a password field counts at once only in the app that
    has the focus, by *focused_class*() (asked only then: a look each time
    would slow every line), or when that can't be told in time. Another
    app's counts unless the focused app's window said no: an app not on the
    bus, there by another name or not answering may be the one. A password
    field found stays found when the time runs out or a later app answers
    in a shape nobody expected."""
    focused = functools.lru_cache(maxsize=1)(lambda: _in_time(focused_class, deadline))
    apps, shells, suspects, said_no = [], [], [], []
    try:
        for app, window in _active_windows(bus, deadline):
            apps.append(app)
            if app in _SHELLS:
                shells.append(window)
                continue
            answer = _has_password_focus(bus, window, deadline)
            if answer is False:
                said_no.append(app)
            elif answer and (focused() is None or _names_the_app(app, focused())):
                return True
            elif answer:
                suspects.append(app)
        if _still_counts(suspects, said_no, focused):
            return True
        return any(_has_password_focus(bus, window, deadline) for window in shells)
    except Exception:
        if not _still_counts(suspects, said_no, focused):
            raise
        logger.info("Password field check cut short after it found a password field")
        return True
    finally:
        _log_several_active(apps)


def _still_counts(suspects, said_no, focused) -> bool:
    """Whether a password field in an app the desktop doesn't say has the
    focus (*suspects*) counts: unless the focused app is one whose window
    said no (*said_no*). *focused*() is known by then: an unknown one made
    the first password field count at once."""
    return bool(suspects) and not any(_names_the_app(app, focused()) for app in said_no)


# One look at the focused app at a time (_in_time): one that outlives the
# check that asked finishes here on its own, and the next check doesn't
# start another behind it.
_looks = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="password-look")


def _in_time(focused_class, deadline):
    """*focused_class*(), or None when it has no answer by *deadline*. It
    asks the tray, which has 1.5 s to answer when its loop is busy, and the
    check before each line of a dictation has 50 ms in all: a slow tray
    must not hold up every line by seconds."""
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    look = _looks.submit(focused_class)
    try:
        return look.result(timeout=left)
    except concurrent.futures.TimeoutError:
        look.cancel()       # not started yet: it never needs to be
        logger.debug("Password field check: which app has the focus wasn't told in time")
        return None


def _names_the_app(name: str, wm_class: str) -> bool:
    """Whether the accessibility bus's app *name* ("kwrite", "Text Editor",
    "Brave") is the app of the window class *wm_class* ("org.kde.kwrite",
    "org.gnome.TextEditor", "brave-browser"): the same letters and digits,
    ignoring case, as the whole class, its last part or that part's first
    word before a dash."""
    def plain(text):
        return re.sub(r"[^0-9a-z]", "", text.casefold())
    last = wm_class.rsplit(".", 1)[-1]
    return bool(plain(name)) and plain(name) in {plain(wm_class), plain(last),
                                                  plain(last.split("-", 1)[0])}


def _a11y_bus(deadline):
    """TuxTalk's own connection to the accessibility bus, or None while there
    is none. It is opened on a thread of its own: a bus that accepts the
    socket but never answers holds the handshake for D-Bus's 25 s, which no
    timeout of Gio's covers. Only the call that starts the opening waits for
    it, and only until *deadline*; later ones don't while it's still trying."""
    global _connecting
    with _bus_changed:
        if _bus is not None and not _bus.is_closed():
            return _bus
        if _connecting:
            return None
        _connecting = True
        threading.Thread(target=_connect, name="a11y-bus", daemon=True).start()
        _bus_changed.wait_for(lambda: not _connecting,
                              timeout=max(0.0, deadline - time.monotonic()))
        return _bus if _bus is not None and not _bus.is_closed() else None


def _connect():
    global _bus, _connecting
    bus = None
    try:
        bus = _open_bus()
    except GLib.Error as e:
        logger.debug(f"No accessibility bus: {e.message}")
    finally:
        with _bus_changed:
            _bus, _connecting = bus, False
            _bus_changed.notify_all()


def _open_bus():
    session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    address = session.call_sync(
        "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress", None,
        GLib.VariantType("(s)"), Gio.DBusCallFlags.NO_AUTO_START, CALL_TIMEOUT_MS, None)[0]
    if not address:
        return None
    return Gio.DBusConnection.new_for_address_sync(
        address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)


def _call(bus, node, interface, method, args, deadline):
    """One call on the accessible *node*, a (bus name, path) pair."""
    left_ms = int((deadline - time.monotonic()) * 1000)
    if left_ms <= 0:
        raise _OutOfTime
    reply = bus.call_sync(node[0], node[1], interface, method, args, None,
                          Gio.DBusCallFlags.NO_AUTO_START,
                          min(CALL_TIMEOUT_MS, left_ms), None)
    return reply.unpack()


def _children(bus, node, deadline):
    return _call(bus, node, _ACCESSIBLE, "GetChildren", None, deadline)[0]


def _states(bus, node, deadline) -> int:
    """The state set as one number: bit n is AtspiStateType n."""
    low, high = _call(bus, node, _ACCESSIBLE, "GetState", None, deadline)[0]
    return low | high << 32


def _active_windows(bus, deadline):
    """(app name, window) for each window that says it's active."""
    for app in _children(bus, (_REGISTRY, _ROOT), deadline):
        try:
            name = _call(bus, app, "org.freedesktop.DBus.Properties", "Get",
                         GLib.Variant("(ss)", (_ACCESSIBLE, "Name")), deadline)[0]
            windows = _children(bus, app, deadline)
        except GLib.Error:
            continue    # an app that quit or hangs; the next one may be it
        for window in windows:
            try:
                if _states(bus, window, deadline) & 1 << _STATE_ACTIVE:
                    yield name, window
            except GLib.Error:
                continue


def _has_password_focus(bus, window, deadline) -> bool | None:
    """Whether the focused widget in *window* is a password field; None
    when the window doesn't answer."""
    try:
        focused = _focused_in(bus, window, deadline)
        return focused is not None and _call(bus, focused, _ACCESSIBLE, "GetRole",
                                             None, deadline)[0] == _ROLE_PASSWORD_TEXT
    except GLib.Error as e:
        logger.debug(f"Password field check: a window didn't answer: {e.message}")
        return None


def _log_several_active(apps):
    """Say so when more than one app calls a window active, once per change:
    then the focus may be in either, and both are asked."""
    global _last_active
    names = tuple(sorted(name for name in apps if name not in _SHELLS))
    if len(names) > 1 and names != _last_active:
        logger.info(f"Password field check: several apps say a window is active: {names}")
    _last_active = names


def _focused_in(bus, window, deadline):
    """The focused widget inside *window*, or None."""
    # Every part of the rule is "match all" (1): at-spi2-atk turns any other
    # match type on an empty part, even "invalid", into "no match".
    rule = GLib.Variant("((aiia{ss}iaiiasib)uib)", (
        ([1 << _STATE_FOCUSED, 0], 1, {}, 1, [0, 0, 0, 0], 1, [], 1, False),
        1, 1, True))    # in tree order, one match, the whole window
    try:
        found = _call(bus, window, _COLLECTION, "GetMatches", rule, deadline)[0]
        return found[0] if found else None
    except GLib.Error as e:
        if Gio.DBusError.get_remote_error(e) not in _NO_COLLECTION:
            raise   # a timeout: walking the same slow app would only take longer
        return _walk_for_focus(bus, window, deadline)


def _walk_for_focus(bus, node, deadline):
    for child in _children(bus, node, deadline):
        states = _states(bus, child, deadline)
        if states & 1 << _STATE_FOCUSED:
            return child
        if states & 1 << _STATE_SHOWING:
            found = _walk_for_focus(bus, child, deadline)
            if found is not None:
                return found
    return None
