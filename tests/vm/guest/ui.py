#!/usr/bin/python3
"""Reads and drives TuxTalk's windows through AT-SPI, the accessibility
interface screen readers use, so the VM tests find a button by its label
instead of by where it happens to be drawn. Runs with the guest's own
python3 inside the desktop session (session-run.sh). Prints JSON.

  ui.py windows                      every app's top-level windows
  ui.py dump [WINDOW]                the widget tree, for writing new checks
  ui.py find ROLE NAME [WINDOW]      wait for a widget, print it
  ui.py click ROLE NAME [WINDOW]     wait for a widget and press it
  ui.py texts WINDOW                 every label and text in a window
  ui.py items ROLE NAME [WINDOW]     the entries under a widget (a dropdown)
  ui.py pick ROLE NAME ITEM [WINDOW] choose the entry ITEM in a dropdown
  ui.py tab NAME [WINDOW]            switch a notebook to the tab called NAME

NAME matches exactly; "~part" matches any name containing "part". WINDOW is
matched the same way against window titles (default: any window). Roles are
AT-SPI role names: "button", "check box", "combo box", "label", ...
Options go first. --timeout N: waiting stops after N seconds (default 20)
with exit code 1. --hidden: also find widgets that aren't on screen (the
language list of a dropdown that only shows in Manual mode). They are
options, not environment variables, because session-run.sh starts the
program with the desktop's environment only.
"""
import json
import sys
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

TIMEOUT = 20.0
HIDDEN_TOO = False
PRESS_ACTIONS = ("click", "press", "activate", "toggle")
ITEM_ROLES = ("menu item", "check menu item", "radio menu item", "list item", "table cell")


def matches(pattern, name):
    if pattern is None:
        return True
    name = name or ""
    if pattern.startswith("~"):
        return pattern[1:].lower() in name.lower()
    return name == pattern


def states(obj):
    wanted = ("visible", "showing", "sensitive", "checked", "selected", "focused", "active",
              "editable")
    found = obj.get_state_set()
    return [s for s in wanted if found.contains(getattr(Atspi.StateType, s.upper()))]


def role(obj):
    # Newer at-spi2-core names a button "button"; older ones (Zorin 18) say
    # "push button". The checks ask for the new name everywhere.
    name = obj.get_role_name()
    return "button" if name == "push button" else name


def describe(obj):
    info = {"role": role(obj), "name": obj.get_name() or "", "states": states(obj)}
    text = obj.get_text_iface() if hasattr(obj, "get_text_iface") else None
    if text is not None:
        info["text"] = Atspi.Text.get_text(obj, 0, Atspi.Text.get_character_count(obj))
    return info


def children(obj):
    try:
        count = obj.get_child_count()
    except gi.repository.GLib.Error:
        return
    for i in range(count):
        try:
            child = obj.get_child_at_index(i)
        except gi.repository.GLib.Error:
            continue
        if child is not None:
            yield child


def windows():
    desktop = Atspi.get_desktop(0)
    for app in children(desktop):
        for win in children(app):
            yield app, win


def walk(obj, depth=0, limit=40):
    yield depth, obj
    if depth < limit:
        for child in children(obj):
            yield from walk(child, depth + 1, limit)


def find(wanted_role, name, window=None):
    for _app, win in windows():
        if not matches(window, win.get_name()):
            continue
        for _depth, obj in walk(win):
            # "visible", not "showing": GTK says showing only for widgets
            # scrolled into view, and buttons below the fold still work.
            if role(obj) == wanted_role and matches(name, obj.get_name()) \
                    and (HIDDEN_TOO or "visible" in states(obj)):
                return obj
    return None


def wait_for(wanted_role, name, window=None):
    deadline = time.monotonic() + TIMEOUT
    while True:
        obj = find(wanted_role, name, window)
        if obj is not None or time.monotonic() > deadline:
            return obj
        time.sleep(0.3)


def entries(obj):
    return [o for _d, o in walk(obj) if role(o) in ITEM_ROLES]


def press(obj):
    if obj.get_action_iface() is None:
        return False
    for i in range(obj.get_n_actions()):
        if obj.get_action_name(i) in PRESS_ACTIONS:
            return obj.do_action(i)
    return False


def main(argv):
    global TIMEOUT, HIDDEN_TOO
    while argv and argv[0].startswith("--"):
        if argv[0] == "--timeout":
            TIMEOUT, argv = float(argv[1]), argv[2:]
        elif argv[0] == "--hidden":
            HIDDEN_TOO, argv = True, argv[1:]
        else:
            print(__doc__, file=sys.stderr)
            return 2
    command, args = argv[0], argv[1:]
    if command == "windows":
        out = [{"app": app.get_name(), "title": win.get_name(), "states": states(win)}
               for app, win in windows()]
    elif command == "dump":
        out = []
        for _app, win in windows():
            if args and not matches(args[0], win.get_name()):
                continue
            for depth, obj in walk(win):
                out.append({"depth": depth, **describe(obj)})
    elif command in ("find", "click", "items", "pick"):
        at = 3 if command == "pick" else 2     # where WINDOW is
        obj = wait_for(args[0], args[1], args[at] if len(args) > at else None)
        if obj is None:
            print(json.dumps({"error": f"no {args[0]} {args[1]!r} showing"}))
            return 1
        if command == "click":
            out = describe(obj)   # before pressing: a button that closes its window is gone after
            if not press(obj):
                print(json.dumps({"error": f"{args[1]!r} can't be pressed"}))
                return 1
        elif command == "items":
            out = [describe(o) for o in entries(obj)]
        elif command == "pick":
            # The entry's index is the dropdown's row: right for a flat list
            # with no separators, which is what TuxTalk's dropdowns are.
            found = entries(obj)
            index = next((i for i, o in enumerate(found) if matches(args[2], o.get_name())), None)
            if index is None:
                print(json.dumps({"error": f"no {args[2]!r} in {args[1]!r}",
                                  "items": [o.get_name() for o in found]}))
                return 1
            # Through the dropdown's selection, as a screen reader picks: its
            # list is never popped up, so its entries can't be clicked.
            if not obj.select_child(index):
                print(json.dumps({"error": f"{args[2]!r} can't be picked"}))
                return 1
            out = describe(found[index])
        else:
            out = describe(obj)
    elif command == "tab":
        obj = wait_for("page tab", args[0], args[1] if len(args) > 1 else None)
        if obj is None:
            print(json.dumps({"error": f"no tab {args[0]!r}"}))
            return 1
        tab_list = obj.get_parent()
        tab_list.select_child(obj.get_index_in_parent())
        out = describe(obj)
    elif command == "texts":
        out = []
        for _app, win in windows():
            if not matches(args[0], win.get_name()):
                continue
            for _depth, obj in walk(win):
                info = describe(obj)
                if "visible" in info["states"] and (info["role"] == "label" or "text" in info):
                    out.append(info.get("text") or info["name"])
    else:
        print(__doc__, file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
