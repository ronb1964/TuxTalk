"""Whether the cursor is in a password field, asked of the accessibility bus
(AT-SPI), so a dictation is never typed into one; and the few characters on
either side of the cursor, so a dictation can fit the sentence it goes into
(sentence_fit); and whether anything is selected, which a voice edit in
KDE's editors asks before its Ctrl+C (voice_edit._copy_in).

Plain D-Bus calls on a connection of TuxTalk's own (Gio), not libatspi: the
AppImage doesn't ship libatspi's typelib, and libatspi shares one bus
connection with GTK's own accessibility bridge, which the GTK thread of the
dictation service dispatches while this runs on another thread.

Which apps answer, as tried in the test VMs: GTK 3 and GTK 4 apps, unless their
accessibility is switched off (GTK_A11Y=none, NO_AT_BRIDGE=1). Qt (KDE
apps) and Firefox only while accessibility is switched on, by a screen reader
or the desktop's accessibility setting. Chromium-based browsers and Electron
apps (Chrome, Brave, Claude Desktop) only if the setting was on when they
started, as Chromium asks it once; they build their tree of widgets
when first asked (wake), and seldom say which window is active
(_unmarked_focus). An app that doesn't answer, a slow bus or none at all
counts as "not a password field", so typing goes ahead as it always did.
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
# A read of the focused text widget (the text around the cursor, right
# before typing; whether anything is selected, before a voice edit): a
# little longer than a line's recheck, as it walks to the widget first.
CONTEXT_BUDGET_S = 0.1
# The wake-up question when a recording starts (wake): on a thread of its
# own, nothing waits for it.
WAKE_BUDGET_S = 1.0

_ROOT = "/org/a11y/atspi/accessible/root"
_REGISTRY = "org.a11y.atspi.Registry"
_ACCESSIBLE = "org.a11y.atspi.Accessible"
_APPLICATION = "org.a11y.atspi.Application"
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
_STATE_EDITABLE = 7
_STATE_FOCUSED = 12
_STATE_SHOWING = 25
_ROLE_PASSWORD_TEXT = 40
_ROLE_TERMINAL = 60
# Qt's item views say their current item is focused whether the view has
# the keyboard or not, and the frames and buttons around them too: in Kate
# a side panel's list comes first, and the editor or terminal panel with
# the keyboard is the first focused widget with text (_focused_in).
_ITEM_ROLES = frozenset({32, 37, 56, 91})     # list item, page tab, table cell, tree item

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
# Replies from an app that has no toolkit to tell (_toolkit).
_NO_TOOLKIT = _NO_COLLECTION | {"org.freedesktop.DBus.Error.UnknownProperty",
                                "org.freedesktop.DBus.Error.InvalidArgs"}

_bus = None
_connecting = False
# Each app's toolkit by its connection's name on the bus (_toolkit), a few
# hundred at most: names aren't reused, so apps that quit stay until then.
# The checks before each line and the final delivery may ask at once.
_toolkits = {}
_toolkits_lock = threading.Lock()
_TOOLKITS_KEPT = 256
# One wake at a time: each recording starts one, on a thread of its own.
_wake_lock = threading.Lock()
_waking = False
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
    apps, shells, suspects, said_no, seen = [], [], [], [], []
    cleared = False     # the focused app's own window said no
    try:
        for app, window in _active_windows(bus, deadline, seen):
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
        # The shell's prompt counts wherever the focus is: asked before the
        # look at an unmarked app, which could use up the time.
        if _shell_prompt(bus, shells, deadline):
            return True
        return _settle(_unmarked_password(bus, seen, apps, focused, deadline),
                       suspects, cleared, focused)
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
    return _ask_focused_text(
        "Text around the cursor", budget, focused_app,
        lambda bus, widget, deadline: _around(bus, widget, chars, deadline))


def has_selection(budget: float = CONTEXT_BUDGET_S, focused_app=lambda: None) -> bool | None:
    """Whether text is selected in the focused text widget of the app with
    the focus (*focused_app*, as for focused_is_password); None when that
    can't be told in *budget* seconds. KDE's editors move the cursor on a
    Ctrl+C with nothing selected, so a voice edit asks here first."""
    return _ask_focused_text("Selection", budget, focused_app, _selected)


def focused_is_terminal(budget: float = CONTEXT_BUDGET_S,
                        focused_app=lambda: None) -> bool | None:
    """Whether the focused text widget of the app with the focus (as for
    has_selection) is a terminal: Konsole's panel inside Kate, KDevelop or
    Dolphin has the app's window class, so only the widget tells it. None
    when that can't be told in *budget* seconds."""
    return _ask_focused_text("Terminal", budget, focused_app,
                             lambda bus, widget, deadline:
                             _role(bus, widget, deadline) == _ROLE_TERMINAL)


def _selected(bus, widget, deadline) -> bool:
    """Whether *widget* has a selection that holds any text."""
    if not _call(bus, widget, _TEXT, "GetNSelections", None, deadline)[0]:
        return False
    start, end = _call(bus, widget, _TEXT, "GetSelection", GLib.Variant("(i)", (0,)), deadline)
    return start != end


def _ask_focused_text(what, budget, focused_app, read):
    """*read*(bus, widget, deadline) of the focused text widget of the app
    with the focus, or None when there's none, or no answer in *budget*
    seconds; *what* names the question in the log."""
    deadline = time.monotonic() + budget
    try:
        bus = _a11y_bus(deadline)
        widget = None if bus is None else _focused_widget(bus, focused_app, deadline)
        return None if widget is None else read(bus, widget, deadline)
    except (_OutOfTime, GLib.Error) as e:
        logger.debug(f"{what}: no answer ({type(e).__name__})")
        return None
    except Exception as e:
        # An answer in a shape nobody expected: things go on as they always did.
        logger.info(f"{what}: couldn't be read ({type(e).__name__})")
        return None


def _focused_widget(bus, focused_app, deadline):
    """The focused text widget of the app with the focus, or None; never a
    password field. An app left in the background can still call a window
    of its own active (issue #40), so only the app the desktop names
    counts, and nothing when it can't name one in time. Where it calls
    none of its windows active, its own windows are looked through
    (_unmarked_focus), if it's a Chromium or Electron app."""
    focus = _in_time(focused_app, deadline)
    if focus is None:
        return None
    seen, active, windows = [], [], []
    for app, window in _active_windows(bus, deadline, seen):
        active.append(app)
        if app not in _SHELLS and _is_focused(bus, app, window, focus, deadline):
            windows.append(window)
    if len(windows) > 1:
        return None
    focused = (_focused_with_text(bus, windows[0], deadline) if windows
               else _unmarked_focus(bus, _unmarked_apps(bus, seen, active, deadline),
                                    focus, deadline))
    # The focus may have moved into a password field since its check.
    return None if focused is None or _is_password(bus, focused, deadline) else focused


def _focused_with_text(bus, window, deadline):
    """The focused widget with text in *window*, or None. A frame around it
    can say it has the focus too, ahead of it in the tree (KDE's editor,
    KTextEditor, whose outer view is focused along with the text inside),
    so a focused widget without text is looked into for one with it."""
    focused = _focused_in(bus, window, deadline)
    while focused is not None and not _has_text(bus, focused, deadline):
        focused = _walk_for_focus(bus, focused, deadline)
    return focused


def _is_password(bus, widget, deadline) -> bool:
    return _role(bus, widget, deadline) == _ROLE_PASSWORD_TEXT


def _role(bus, widget, deadline) -> int:
    return _call(bus, widget, _ACCESSIBLE, "GetRole", None, deadline)[0]


def _unmarked_focus(bus, seen, focus, deadline):
    """The focused widget with text in the app with the focus by *focus*,
    when none of its windows says it's active; None when there isn't
    exactly one. *seen*: (name, app) of the apps on the bus. Chromium and
    Electron apps mark a window active only now and then on Plasma 6, and
    each of their windows keeps a focused widget of its own: with two,
    either may be the one in front. Only a widget that can be typed into
    counts: Claude Desktop's hidden second window keeps a focused page."""
    found = []
    for name, app in seen:
        if name in _SHELLS or not _is_focused(bus, name, app, focus, deadline):
            continue
        for window in _children(bus, app, deadline):
            widget = _focused_with_text(bus, window, deadline)
            if widget is not None and _states(bus, widget, deadline) & 1 << _STATE_EDITABLE:
                found.append(widget)
    return found[0] if len(found) == 1 else None


def _unmarked_password(bus, seen, active_apps, focused, deadline) -> bool | None:
    """Whether the focused widget of the app with the focus by *focused*()
    is a password field, where that is a Chromium or Electron app that
    marks none of its windows active (named like none of *active_apps*)
    and _unmarked_focus can tell which widget it is; None otherwise. Which
    app has the focus is asked only when one such app is on the bus: a
    look for every check would slow every line."""
    unmarked = _unmarked_apps(bus, seen, active_apps, deadline)
    focus = focused() if unmarked else None
    try:
        widget = None if focus is None else _unmarked_focus(bus, unmarked, focus, deadline)
        return None if widget is None else _is_password(bus, widget, deadline)
    except GLib.Error as e:
        # A tree not built yet (wake) answers "unknown method": as when it
        # can't be told, another app's password field may still count.
        logger.debug(f"Password field check: the focused app's windows didn't answer: {e.message}")
        return None


def _unmarked_apps(bus, seen, active_apps, deadline):
    """(name, app) of the Chromium and Electron apps among *seen* that
    have no window among the active ones (*active_apps*, by name): those
    whose windows _unmarked_focus may look through."""
    return [(name, app) for name, app in seen
            if name not in active_apps and name not in _SHELLS
            and _toolkit(bus, app[0], deadline) == "Chromium"]


def _toolkit(bus, name, deadline) -> str:
    """The toolkit of the app at bus name *name*, as it tells the bus
    ("Chromium" for Chrome and Electron apps, "GTK", "Qt"), "" when it has
    none to tell. An answer is kept, as it never changes for one
    connection; no answer in time is asked again next time, or a slow
    first answer would leave Chrome's password fields unchecked."""
    with _toolkits_lock:
        if name in _toolkits:
            return _toolkits[name]
    try:
        answer = _call(bus, (name, _ROOT), "org.freedesktop.DBus.Properties", "Get",
                       GLib.Variant("(ss)", (_APPLICATION, "ToolkitName")), deadline)[0]
    except GLib.Error as e:
        if Gio.DBusError.get_remote_error(e) not in _NO_TOOLKIT:
            logger.debug(f"Accessibility bus: {name} didn't say its toolkit: {e.message}")
            return ""
        answer = ""
    with _toolkits_lock:
        if len(_toolkits) > _TOOLKITS_KEPT:
            _toolkits.clear()   # apps that quit long ago
        _toolkits[name] = answer
    return answer


def wake(focused_app) -> None:
    """Ask the app with the focus by *focused_app*() a question about
    itself, which any app answers. Chromium and Electron build their tree
    of widgets, web page included, only once something on the bus asks
    them (and let a tab hidden for five minutes go back to sleep), so the
    first look at their text box would find nothing; asked when a recording
    starts, the tree is ready by the time the words are typed (in about 25
    ms on a small page)."""
    global _waking
    with _wake_lock:
        if _waking:
            return      # one still waits on a look that hangs (an X server stuck)
        _waking = True
    deadline = time.monotonic() + WAKE_BUDGET_S
    try:
        bus = _a11y_bus(deadline)
        # Asked here, not through _in_time: this runs on a thread of its
        # own, and _in_time's one look at a time would leave a password
        # check made meanwhile without an answer.
        focus = None if bus is None else focused_app()
        if focus is None:
            return
        for name, app in _named_apps(bus, deadline):
            if name not in _SHELLS and _is_focused(bus, name, app, focus, deadline):
                _call(bus, app, _ACCESSIBLE, "GetAttributes", None, deadline)
                _toolkit(bus, app[0], deadline)     # known before the checks need it
    except (_OutOfTime, GLib.Error) as e:
        logger.debug(f"Waking the focused app on the accessibility bus: no answer: {e!r}")
    except Exception as e:
        # An answer in a shape nobody expected: a dictation reads as before.
        logger.info(f"Waking the focused app on the accessibility bus failed: {e!r}")
    finally:
        with _wake_lock:
            _waking = False


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
    if start == end and text[start - low:start - low + 1] == _PLACEHOLDER:
        # Chrome gives the box the offset of the paragraph holding the
        # cursor, where Firefox gives -1. Right before an image, which is
        # no paragraph, it's read as before.
        inside = _inside(bus, widget, chars, deadline, depth)
        if inside is not None:
            return inside
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
        role = _role(bus, child, deadline)
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


def _settle(unmarked, suspects, cleared, focused) -> bool:
    """The password check's answer once every active window and the shell
    were asked: the focused app's own where it marks no window active
    (*unmarked*, _unmarked_password), else a password field in an app that
    may be in the background (*suspects*) unless the focused app said no
    (*cleared*, or *unmarked* False)."""
    if unmarked:
        _say_found(focused()[0], focused())
        return True
    if suspects and not cleared and unmarked is None:
        _say_found(suspects[0], focused())
        return True
    return False


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
    focus by *focus* (class, process): by the process where the desktop and
    the bus both tell it, else by the name (_names_the_app). The names alone
    missed apps on the bus by another name, whose own no then didn't clear
    a background browser's password field; and they took a second process
    of one app (two Firefox profiles) for the one in front, whose stale
    password field then refused the dictation. A Flatpak app's process
    isn't told (_process_of), so its name decides."""
    wm_class, pid = focus
    if pid is not None:
        owner = _process_of(bus, window[0], deadline)
        if owner is not None:
            return owner == pid
    return _names_the_app(app, wm_class)


# A Flatpak app reaches the accessibility bus through this go-between, so
# the bus daemon names its process, not the app's.
_FLATPAK_PROXY = "xdg-dbus-proxy"


def _process_of(bus, name, deadline):
    """The process behind the bus name *name*, as the accessibility bus
    tells it; None when it doesn't, or names only a Flatpak app's proxy.
    Out of time it's None too: then the names decide, which never clears a
    password field they can't place."""
    try:
        pid = _call(bus, ("org.freedesktop.DBus", "/org/freedesktop/DBus"),
                    "org.freedesktop.DBus", "GetConnectionUnixProcessID",
                    GLib.Variant("(s)", (name,)), deadline)[0]
    except (GLib.Error, _OutOfTime) as e:
        logger.debug(f"Password field check: no process for {name}: {e!r}")
        return None
    return None if _command_of(pid) == _FLATPAK_PROXY else pid


def _command_of(pid):
    """The program name of process *pid* (/proc's comm), None when it's
    gone or can't be read."""
    try:
        with open(f"/proc/{pid}/comm", "rb") as comm:
            return comm.read().decode(errors="replace").rstrip("\n")
    except OSError as e:
        logger.debug(f"Accessibility bus: no program name for process {pid}: {e}")
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


# Apps whose name on the accessibility bus _names_the_app's rules don't
# find in their window class: the class and the name, as _plain has them. Brave
# names itself "Brave" there, and its windows "brave-browser" (Fedora's
# Chromium "Chromium" and "chromium-browser", Claude Desktop
# "claude-desktop" and "com.anthropic.Claude"); GNOME
# Terminal "gnome-terminal-server" (its windows "Gnome-terminal" on X11,
# "org.gnome.Terminal" on Wayland); LibreOffice "soffice". Only known
# pairs: a name like a class's first word ("Code", "code-oss") may be
# another app, whose stale password field would then refuse the dictation.
# On X11 the process tells them apart without a pair, except a Flatpak
# app's (_is_focused).
_BUS_NAMES = {
    "bravebrowser": "brave",
    "chromiumbrowser": "chromium",
    "comanthropicclaude": "claudedesktop",
    "gnometerminal": "gnometerminalserver",
    "orggnometerminal": "gnometerminalserver",
    **{f"libreoffice{part}": "soffice"
       for part in ("writer", "calc", "impress", "draw", "math", "base", "startcenter")},
}


def _plain(text: str) -> str:
    """*text*'s letters and digits, without case."""
    return re.sub(r"[^0-9a-z]", "", text.casefold())


def _names_the_app(name: str, wm_class: str) -> bool:
    """Whether the accessibility bus's app *name* ("kwrite", "gnome-text-editor",
    "Brave") is the app of the window class *wm_class* ("org.kde.kwrite",
    "org.gnome.TextEditor", "brave-browser"): the same letters and digits,
    ignoring case, as the whole class, its last part, a three-part app id's
    last two (GNOME apps go by "gnome-text-editor" on the bus), or a known
    pair (_BUS_NAMES). Only three parts: in a longer id the second-to-last
    can be a project's owner (io.github.<user>.App), who could be called
    gnome. So KDE's few longer ids (org.kde.kdeconnect.app) don't match it,
    as before: an app there by such a name would need a pair."""
    said, whole = _plain(name), _plain(wm_class)
    parts = wm_class.split(".")
    vendor_and_app = _plain("".join(parts[-2:])) if len(parts) == 3 else None
    return bool(said) and said in {whole, _plain(parts[-1]), vendor_and_app,
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


def _named_apps(bus, deadline):
    """(name, app) for each app on the bus that tells its name."""
    for app in _children(bus, (_REGISTRY, _ROOT), deadline):
        try:
            name = _call(bus, app, "org.freedesktop.DBus.Properties", "Get",
                         GLib.Variant("(ss)", (_ACCESSIBLE, "Name")), deadline)[0]
        except GLib.Error as e:
            # An app that quit or hangs; the next one may be it.
            logger.debug(f"Accessibility bus: an app didn't answer: {e.message}")
            continue
        yield name, app


def _active_windows(bus, deadline, seen=None):
    """(app name, window) for each window that says it's active. *seen*, a
    list, gets (name, app) of each app as it's asked."""
    for name, app in _named_apps(bus, deadline):
        if seen is not None:
            seen.append((name, app))
        try:
            windows = _children(bus, app, deadline)
        except GLib.Error as e:
            logger.debug(f"Accessibility bus: {name} didn't answer: {e.message}")
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
        return focused is not None and _is_password(bus, focused, deadline)
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
    """The focused widget inside *window*, or None. In a Chromium or
    Electron app several can say they have the focus, and the last one
    that can be typed into is it: Claude Desktop's own page says so, ahead
    of the claude.ai page whose box has the cursor. Elsewhere the first
    (every match would walk a whole web page or a big GTK window), unless
    it's a Qt item view's (_qt_item): then the first with text. Only a
    toolkit already known counts (wake learns the focused app's): asking
    here, before each line, a slow app could use up the time before the
    shell's own password prompt is asked."""
    with _toolkits_lock:
        every = _toolkits.get(window[0]) == "Chromium"
    try:
        found = _focused_matches(bus, window, every, deadline)
        if len(found) > 1:
            typed_into = [w for w in found
                          if _states(bus, w, deadline) & 1 << _STATE_EDITABLE]
            return typed_into[-1] if typed_into else found[0]
        if found and _qt_item(bus, window, found[0], deadline):
            return next((w for w in _focused_matches(bus, window, True, deadline)
                         if _has_text(bus, w, deadline)), None)
        return found[0] if found else None
    except GLib.Error as e:
        if Gio.DBusError.get_remote_error(e) not in _NO_COLLECTION:
            raise   # a timeout: walking the same slow app would only take longer
    first = _walk_for_focus(bus, window, deadline)
    if first is not None and _qt_item(bus, window, first, deadline):
        return _walk_for_focus(bus, window, deadline, with_text=True)
    return first


def _qt_item(bus, window, widget, deadline) -> bool:
    """Whether *widget*, the first focused one in *window*, is a Qt item
    view's (_ITEM_ROLES). GTK 4's list rows and web pages' tabs and rows
    have the same roles, but only in Qt do the other widgets say they're
    focused too, so only there is the rest of the window looked through.
    The toolkit is asked only on this rare path: it's kept once known."""
    return (_role(bus, widget, deadline) in _ITEM_ROLES
            and _toolkit(bus, window[0], deadline) == "Qt")


def _focused_matches(bus, window, every, deadline):
    """The focused widgets in *window* in tree order, by its Collection
    interface: every one, or only the first."""
    # Every part of the rule is "match all" (1): at-spi2-atk turns any other
    # match type on an empty part, even "invalid", into "no match".
    rule = GLib.Variant("((aiia{ss}iaiiasib)uib)", (
        ([1 << _STATE_FOCUSED, 0], 1, {}, 1, [0, 0, 0, 0], 1, [], 1, False),
        1, 0 if every else 1, True))    # in tree order, the whole window
    return _call(bus, window, _COLLECTION, "GetMatches", rule, deadline)[0]


def _walk_for_focus(bus, node, deadline, with_text=False):
    """The first focused widget under *node*, in tree order, or None; with
    *with_text*, the first that has text."""
    for child in _children(bus, node, deadline):
        states = _states(bus, child, deadline)
        if states & 1 << _STATE_FOCUSED and (not with_text or _has_text(bus, child, deadline)):
            return child
        if states & 1 << _STATE_SHOWING:
            found = _walk_for_focus(bus, child, deadline, with_text)
            if found is not None:
                return found
    return None


def _has_text(bus, widget, deadline) -> bool:
    return _TEXT in _call(bus, widget, _ACCESSIBLE, "GetInterfaces", None, deadline)[0]
