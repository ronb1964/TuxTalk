#!/usr/bin/python3
"""Asks KWin about the windows titled TITLE and prints JSON: a list with
{"minimized": ..., "active": ...} for each (empty when there's none), or
{"error": ...}. For KDE on Wayland, where an app is never told that its
window was minimized, so AT-SPI can't say it either.

A short KWin script looks and answers over D-Bus to this process's
connection, as TuxTalk's kwin_activate.py does. It is loaded under its own
name, started with Scripting.start() (never run() by its number: KWin hands
out a number again after an unload, and run() would start another script),
and unloaded straight after. Runs inside the desktop session (session-run.sh).

Usage: kwin_window.py TITLE
"""
import json
import os
import sys

from gi.repository import Gio, GLib

PLUGIN = "tt-harness-window"
ANSWER_PATH = "/tt/Harness/Window"
ANSWER_INTERFACE = "tt.Harness.Window"
ANSWER_XML = f"""<node><interface name="{ANSWER_INTERFACE}">
  <method name="Answer"><arg type="s" direction="in" name="found"/></method>
</interface></node>"""
WAIT_MS = 5000


def script(title, reply_to):
    """The KWin script for *title*, answering to the connection *reply_to*.
    Both go in through json.dumps, so a title is never read as code."""
    return f"""(function () {{
    var found = [];
    try {{
        (workspace.stackingOrder || []).forEach(function (w) {{
            if (w.caption === {json.dumps(title)}) {{
                found.push({{minimized: w.minimized, active: w === workspace.activeWindow}});
            }}
        }});
    }} catch (e) {{
        found = {{error: String(e)}};
    }}
    callDBus({json.dumps(reply_to)}, "{ANSWER_PATH}", "{ANSWER_INTERFACE}", "Answer",
             JSON.stringify(found));
}})();
"""


def scripting(bus, method, args=None):
    """Call KWin's Scripting interface; its answer unpacked."""
    reply = bus.call_sync("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", method, args,
                          None, Gio.DBusCallFlags.NONE, WAIT_MS, None)
    return reply.unpack() if reply else ()


def ask(title):
    """KWin's answer for *title*, as the text the script sent; or an error."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    answers = []

    def on_call(_bus, _sender, _path, _interface, _method, parameters, invocation):
        answers.append(parameters.unpack()[0])
        invocation.return_value(None)

    interface = Gio.DBusNodeInfo.new_for_xml(ANSWER_XML).interfaces[0]
    register = getattr(bus, "register_object_with_closures2", bus.register_object)
    register(ANSWER_PATH, interface, on_call, None, None)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{PLUGIN}.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write(script(title, bus.get_unique_name()))
    try:
        scripting(bus, "unloadScript", GLib.Variant("(s)", (PLUGIN,)))   # one left behind
        number = scripting(bus, "loadScript", GLib.Variant("(ss)", (path, PLUGIN)))[0]
        if number < 0:
            return json.dumps({"error": f"KWin refused the script ({number})"})
        scripting(bus, "start")
        context = GLib.MainContext.default()
        expired = []
        GLib.timeout_add(WAIT_MS, lambda: expired.append(True))
        while not answers and not expired:
            context.iteration(True)
        return answers[0] if answers else json.dumps({"error": "KWin didn't answer"})
    finally:
        scripting(bus, "unloadScript", GLib.Variant("(s)", (PLUGIN,)))
        os.remove(path)


if __name__ == "__main__":
    try:
        print(ask(sys.argv[1]))
    except GLib.Error as error:
        print(json.dumps({"error": error.message}))
