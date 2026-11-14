"""Whether the cursor is in a password field, asked of the accessibility bus
(AT-SPI), so a dictation is never typed into one; and the few characters on
either side of the cursor, so a dictation can fit the sentence it goes into
(sentence_fit).

Plain D-Bus calls on a connection of TuxTalk's own (Gio), not libatspi: the
AppImage doesn't ship libatspi's typelib, and libatspi shares one bus
connection with GTK's own accessibility bridge, which the GTK thread of the
dictation service dispatches while this runs on another thread.

Which apps answer, as tried in the test VMs: GTK 3 and GTK 4 apps, unless their
accessibility is switched off (GTK_A11Y=none, NO_AT_BRIDGE=1). Qt (KDE
apps) and Firefox only while accessibility is switched on, by a screen reader
or the desktop's accessibility setting. Chromium-based browsers and Electron
apps (Chrome, Brave, Claude Desktop) showed no windows at all on Plasma 6,
in the KDE VM and on a real desktop, even started with the setting on (and
with ACCESSIBILITY_ENABLED=1 in the VM): there they get neither the password
check nor the text around the cursor. An app that doesn't answer, a slow
bus or none at all counts as "not a password field", so typing goes ahead
as it always did.
"""

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
# The read of the text around the cursor, right before typing: a little
# longer than a line's recheck, as it walks to the focused widget first.
CONTEXT_BUDGET_S = 0.1

_ROOT = "/org/a11y/atspi/accessible/root"
_REGISTRY = "org.a11y.atspi.Registry"
_ACCESSIBLE = "org.a11y.atspi.Accessible"
_COLLECTION = "org.a11y.atspi.Collection"
_TEXT = "org.a11y.atspi.Text"
# U+FFFC, which Firefox gives in a web editor's text for each paragraph or
# list, and for each link, image or emoji, in place of its own text;
# written as a code: no window shows it.
_PLACEHOLDER = chr(0xFFFC)
# How far into a web editor's blocks (a paragraph in a list item in a
# list...) the cursor is looked for.
_MAX_DEPTH = 4
# AtspiRole numbers of a web editor's blocks of text, and of those that
# hold others (atspi-constants.h).
_BLOCKS = {32, 73, 83, 85, 105}     # list item, paragraph, heading, section, block quote
_CONTAINERS = {31, 32, 85, 105}     # list, list item, section, block quote

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


def focused_is_password(budget: float = BUDGET_S, focused_app=lambda: None) -> bool:
    """True when the focused widget of an active window is a password field.
    False when it isn't, and whenever that can't be found out in *budget*
    seconds, unless one was found before the time ran out. *focused_app*()
    is (class, process) of the window the desktop says has the focus, the
    process None when not told, None in all when not known: which app's
    answer counts (_password_focus)."""
    started = time.monotonic()
    deadline = started + budget
    try:
        bus = _a11y_bus(deadline)
        return bus is not None and _password_focus(bus, focused_app, deadline)
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


def _password_focus(bus, focused_app, deadline) -> bool:
    """Whether a password field has the focus in an active window.

    Every active window is asked, not the first: a desktop shell other than
    GNOME's may call its own window active too. Each as soon as it's found,
    so apps that hang further down the list can't use up the time before a
    password field already found is seen. The shell's tree is the biggest:
    its turn comes last.

    An app left in the background can still say a window of its own is
    active, with a password field focused in it, and refuse the dictation
    for nothing. So a password field counts at once only in the app that
    has the focus, by *focused_app*() (asked only then: a look each time
    would slow every line; _is_focused), or when that can't be told in
    time. Another app's counts unless the focused app's window said no: an
    app not on the bus, there by another name or not answering may be the
    one. A password field found stays found when the time runs out or a
    later app answers in a shape nobody expected."""
    focused = functools.lru_cache(maxsize=1)(lambda: _in_time(focused_app, deadline))
    apps, shells, suspects, said_no = [], [], [], []
    cleared = False     # the focused app's own window said no
    try:
        for app, window in _active_windows(bus, deadline):
            apps.append(app)
            if app in _SHELLS:
                shells.append((app, window))
                continue
            answer = _has_password_focus(bus, window, deadline)
            if answer is False:
                said_no.append((app, window))
            elif answer and _counts_at_once(bus, app, window, focused, deadline):
                _say_found(app, focused())
                return True
            elif answer:
                suspects.append(app)
            if suspects and said_no:
                # Placed now, while there's time to ask for a process: one
                # left for later would go unasked when the time runs out.
                cleared = cleared or _focused_said_no(bus, said_no, focused(), deadline)
                said_no.clear()
        if suspects and not cleared:
            _say_found(suspects[0], focused())
            return True
        return _shell_prompt(bus, shells, deadline)
    except Exception:
        if not suspects or cleared:
            raise
        logger.info("Password field check cut short after it found a password field")
        return True
    finally:
        _log_several_active(apps)


def text_around_cursor(chars: int, budget: float = CONTEXT_BUDGET_S,
                       focused_app=lambda: None) -> tuple[str, str] | None:
    """(before, after): up to *chars* characters on either side of the
    cursor in the focused text widget, or of its selection, which typing
    replaces. None when that can't be told in *budget* seconds: no answer,
    a widget that isn't text, or no telling which app has the focus
    (*focused_app*, as for focused_is_password). Never logged: it's
    whatever the user wrote."""
    deadline = time.monotonic() + budget
    try:
        bus = _a11y_bus(deadline)
        widget = None if bus is None else _focused_widget(bus, focused_app, deadline)
        return None if widget is None else _around(bus, widget, chars, deadline)
    except (_OutOfTime, GLib.Error) as e:
        logger.debug(f"Text around the cursor: no answer ({type(e).__name__})")
        return None
    except Exception as e:
        # An answer in a shape nobody expected: typed as it always was.
        logger.info(f"Text around the cursor: couldn't be read ({type(e).__name__})")
        return None


def _focused_widget(bus, focused_app, deadline):
    """The focused widget of the app with the focus, or None. An app left
    in the background can still call a window of its own active (issue
    #40), so only the app the desktop names counts, and nothing when it
    can't name one in time."""
    focus = _in_time(focused_app, deadline)
    if focus is None:
        return None
    windows = [(app, window) for app, window in _active_windows(bus, deadline)
               if app not in _SHELLS and _is_focused(bus, app, window, focus, deadline)]
    return _focused_text(bus, windows[0][1], deadline) if len(windows) == 1 else None


def _focused_text(bus, window, deadline):
    """The focused widget with text in *window*, or None. A frame around it
    can say it has the focus too, ahead of it in the tree (KDE's editor,
    KTextEditor, whose outer view is focused along with the text inside),
    so a focused widget without text is looked into for one with it."""
    focused = _focused_in(bus, window, deadline)
    while focused is not None and _TEXT not in _call(bus, focused, _ACCESSIBLE,
                                                     "GetInterfaces", None, deadline)[0]:
        focused = _walk_for_focus(bus, focused, deadline)
    # The focus may have moved into a password field since its check.
    if focused is not None and _call(bus, focused, _ACCESSIBLE, "GetRole", None,
                                     deadline)[0] == _ROLE_PASSWORD_TEXT:
        return None
    return focused


def _around(bus, widget, chars, deadline, depth=0):
    """*widget*'s text, up to *chars* characters each side of its cursor or
    of its first selection. A web page's rich text box (Gmail and the like,
    in Firefox) gives each paragraph or list as one U+FFFC, an object of
    its own: where the cursor is in one, that one is read (_holder). Past
    one beside the cursor counts as another line, which leaves that side
    of the dictation as it would be anyway. None where no plain text
    around the cursor can be found."""
    if _call(bus, widget, _TEXT, "GetNSelections", None, deadline)[0]:
        start, end = sorted(_call(bus, widget, _TEXT, "GetSelection",
                                  GLib.Variant("(i)", (0,)), deadline))
    else:
        start = end = _property(bus, widget, "CaretOffset", deadline)
    if start < 0:
        return _inside(bus, widget, chars, deadline, depth)
    # Asked only up to the end: Firefox answers nothing for a range past it.
    low = max(0, start - chars)
    text = _call(bus, widget, _TEXT, "GetText", GLib.Variant("(ii)", (
        low, min(end + chars, _property(bus, widget, "CharacterCount", deadline)))), deadline)[0]
    chosen = text[start - low:end - low]
    if _PLACEHOLDER in chosen:
        # Selected: one whole paragraph, whose own selection says more, or
        # more than one, whose two ends can't both be read.
        return _inside(bus, widget, chars, deadline, depth) if chosen == _PLACEHOLDER else None
    return (text[:start - low].replace(_PLACEHOLDER, "\n"),
            text[end - low:].replace(_PLACEHOLDER, "\n"))


def _inside(bus, widget, chars, deadline, depth):
    """_around for the paragraph inside *widget* the cursor is in, if any."""
    inner = _holder(bus, widget, deadline) if depth < _MAX_DEPTH else None
    return None if inner is None else _around(bus, inner, chars, deadline, depth + 1)


def _property(bus, widget, name, deadline):
    """The Text interface's property *name* of *widget*."""
    return _call(bus, widget, "org.freedesktop.DBus.Properties", "Get",
                 GLib.Variant("(ss)", (_TEXT, name)), deadline)[0]


def _holder(bus, widget, deadline, depth=0):
    """The paragraph (or heading...) inside *widget* the cursor is in, or
    None. Firefox gives the cursor's offset only to the object whose own
    text holds it, -1 elsewhere, lists and their items included, so those
    are looked into. Only a web editor's own kinds of block count: never a
    password field."""
    for child in _children(bus, widget, deadline):
        role = _call(bus, child, _ACCESSIBLE, "GetRole", None, deadline)[0]
        if role in _BLOCKS and _caret_of(bus, child, deadline) >= 0:
            return child
        if role in _CONTAINERS and depth < _MAX_DEPTH:
            found = _holder(bus, child, deadline, depth + 1)
            if found is not None:
                return found
    return None


def _caret_of(bus, widget, deadline) -> int:
    """*widget*'s cursor offset; -1 where it has none or no text at all."""
    try:
        return _property(bus, widget, "CaretOffset", deadline)
    except GLib.Error as e:
        # A list without text of its own, or a block that didn't answer.
        logger.debug(f"Accessibility bus: no cursor offset for a block: {e.message}")
        return -1


def _counts_at_once(bus, app, window, focused, deadline) -> bool:
    """Whether a password field in *app*'s active *window* counts at once:
    *app* has the focus by *focused*(), or which app has it can't be told."""
    return focused() is None or _is_focused(bus, app, window, focused(), deadline)


def _focused_said_no(bus, said_no, focus, deadline) -> bool:
    """Whether the app with the focus by *focus* is one whose window said
    no password field (*said_no*, its apps and windows)."""
    return any(_is_focused(bus, app, window, focus, deadline) for app, window in said_no)


def _shell_prompt(bus, shells, deadline) -> bool:
    """Whether a desktop shell's own password prompt has the focus: it
    counts wherever the focus is, so the focused app isn't asked."""
    for app, window in shells:
        if _has_password_focus(bus, window, deadline):
            logger.info(f"Password field check: {app}'s own password prompt has the focus")
            return True
    return False


def _is_focused(bus, app, window, focus, deadline) -> bool:
    """Whether *app*, whose active *window* this is, is the one with the
    focus by *focus* (class, process): the same process where the desktop
    and the bus both tell it, else the same name (_names_the_app). The
    names alone missed apps on the bus by another name, whose own no then
    didn't clear a background browser's password field."""
    wm_class, pid = focus
    return ((pid is not None and _process_of(bus, window[0], deadline) == pid)
            or _names_the_app(app, wm_class))


def _process_of(bus, name, deadline):
    """The process behind the bus name *name*, as the accessibility bus
    tells it; None when it doesn't. Out of time it's None too: then the
    names decide, which never clears a password field they can't place."""
    try:
        return _call(bus, ("org.freedesktop.DBus", "/org/freedesktop/DBus"),
                     "org.freedesktop.DBus", "GetConnectionUnixProcessID",
                     GLib.Variant("(s)", (name,)), deadline)[0]
    except (GLib.Error, _OutOfTime) as e:
        logger.debug(f"Password field check: no process for {name}: {e!r}")
        return None


def _say_found(app, focus):
    """Say which app's password field refuses the dictation, and which app
    the desktop says has the focus (*focus*, None when it can't), so a
    wrong refusal can be told apart from a waiting password prompt
    (focused_window)."""
    where = (f"while {focus[0]} has the focus" if focus
             else "while which app has the focus can't be told")
    logger.info(f"Password field check: a password field has the focus in {app} {where}")


# The look at the focused app (_in_time) runs on a daemon thread of its
# own, one at a time: a look that hangs (an X server that stopped answering
# XCB, which has no time limit) must hold up neither the dictation nor
# TuxTalk quitting, which an executor's worker would, joined at exit.
_look_lock = threading.Lock()
_looking = False


def _in_time(focused_app, deadline):
    """*focused_app*(), or None when it has no answer by *deadline*. It
    asks the tray, which has 1.5 s to answer when its loop is busy, and the
    check before each line of a dictation has 50 ms in all: a slow tray
    must not hold up every line by seconds. While an earlier look is still
    running, this one is None at once; a late answer is thrown away."""
    global _looking
    left = deadline - time.monotonic()
    with _look_lock:
        if left <= 0 or _looking:
            return None
        _looking = True
    answer, done = [], threading.Event()
    threading.Thread(target=_look, args=(focused_app, answer, done),
                     name="password-look", daemon=True).start()
    if not done.wait(left):
        logger.debug("Password field check: which app has the focus wasn't told in time")
        return None
    return answer[0] if answer else None


def _look(focused_app, answer, done):
    """_in_time's thread: *focused_app*() into *answer*, then *done*."""
    global _looking
    try:
        answer.append(focused_app())
    except Exception as e:
        # Not known, as when it's late: a password field found still counts.
        logger.info(f"Password field check: couldn't tell which app has the focus: {e!r}")
    finally:
        with _look_lock:
            _looking = False
        done.set()


# Apps whose name on the accessibility bus is in neither their window class
# nor its last part: the class and the name, as _plain has them. Brave
# names itself "Brave" there, and its windows "brave-browser"; GNOME
# Terminal "gnome-terminal-server" (its windows "Gnome-terminal" on X11,
# "org.gnome.Terminal" on Wayland); LibreOffice "soffice". Only known
# pairs: a name like a class's first word ("Code", "code-oss") may be
# another app, whose stale password field would then refuse the dictation.
# On X11 the process tells them apart without a pair (_is_focused).
_BUS_NAMES = {
    "bravebrowser": "brave",
    "gnometerminal": "gnometerminalserver",
    "orggnometerminal": "gnometerminalserver",
    **{f"libreoffice{part}": "soffice"
       for part in ("writer", "calc", "impress", "draw", "math", "base", "startcenter")},
}


def _plain(text: str) -> str:
    """*text*'s letters and digits, without case."""
    return re.sub(r"[^0-9a-z]", "", text.casefold())


def _names_the_app(name: str, wm_class: str) -> bool:
    """Whether the accessibility bus's app *name* ("kwrite", "Text Editor",
    "Brave") is the app of the window class *wm_class* ("org.kde.kwrite",
    "org.gnome.TextEditor", "brave-browser"): the same letters and digits,
    ignoring case, as the whole class or its last part, or a known pair
    (_BUS_NAMES)."""
    said, whole = _plain(name), _plain(wm_class)
    return bool(said) and said in {whole, _plain(wm_class.rsplit(".", 1)[-1]),
                                   _BUS_NAMES.get(whole)}


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
        except GLib.Error as e:
            # An app that quit or hangs; the next one may be it.
            logger.debug(f"Accessibility bus: an app didn't answer: {e.message}")
            continue
        for window in windows:
            try:
                if _states(bus, window, deadline) & 1 << _STATE_ACTIVE:
                    yield name, window
            except GLib.Error as e:
                logger.debug(f"Accessibility bus: {name}'s window didn't answer: {e.message}")
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
