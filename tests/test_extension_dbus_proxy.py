"""The GNOME extension's D-Bus proxy and menus.

String checks, since the extension can't run here:

* The proxy is built asynchronously, since building it in enable() blocks
  GNOME Shell on D-Bus round trips at every login and unlock, and
  _dbusAvailable is set only when TuxTalk is really running.
* A service stopped mid-recording turns the panel mic off.
* Recent Dictations clears itself inside the reply, so reopening the menu
  before the first reply arrives can't list every entry twice.
"""

import pathlib
import re

EXT = pathlib.Path(__file__).resolve().parent.parent / "gnome-extension" / "talktype@ronb1964.github.io"
JS = (EXT / "extension.js").read_text()


def _method(name):
    start = JS.index(f"    {name}(")
    end = JS.index("\n    }\n", start)
    return JS[start:end]


def test_the_proxy_is_built_asynchronously_without_auto_start():
    connect = _method("_connectDBus")
    assert "new TuxTalkProxy(" in connect
    assert "(proxy, error) =>" in connect, "no callback: GJS would init it synchronously"
    assert "DO_NOT_AUTO_START" in connect and "DO_NOT_LOAD_PROPERTIES" in connect


def test_only_watch_name_marks_tuxtalk_available():
    assert JS.count("this._dbusAvailable = true") == 1
    assert "this._dbusAvailable = true" in _method("_watchDBusService")


def test_it_starts_hidden_and_retries_the_proxy_when_tuxtalk_appears():
    init = _method("_init")
    assert "this.hide();" in init and "if (!this._dbusAvailable)" not in init
    assert "this._connectDBus()" in _method("_watchDBusService")


def test_destroy_cancels_a_proxy_still_being_set_up():
    assert "this._cancellable.cancel()" in _method("destroy")


def test_menu_actions_never_call_a_missing_proxy():
    """Before the proxy is ready, a click must not throw a TypeError."""
    unguarded = re.findall(r"this\._proxy\.\w+Remote\(", JS)
    guarded_by_if = {"SetFocusedWindowRemote", "SetFocusedWindowClassRemote",
                     "SetFocusedWindowGeometryRemote",
                     "GetStatusRemote", "GetRecentDictationsRemote", "GetPresetsRemote"}
    for call in unguarded:
        name = call[len("this._proxy."):-1]
        assert name in guarded_by_if, f"{name} is called without checking the proxy"


def test_a_stopped_service_turns_the_recording_icon_off():
    block = JS[JS.index("connectSignal('ServiceStateChanged'"):]
    block = block[:block.index("}));")]
    assert "this._isRecording = false" in block


def test_recent_dictations_clears_inside_the_reply_and_ignores_stale_ones():
    refresh = _method("_refreshHistoryMenu")
    reply = refresh[refresh.index("GetRecentDictationsRemote"):]
    assert "menu.removeAll()" in reply
    assert "request !== this._historyRequest" in reply


def test_unused_signals_are_not_declared():
    assert "TranscriptionComplete" not in JS and "ErrorOccurred" not in JS


def test_no_reply_touches_the_extension_after_it_is_disabled():
    """The screen lock disables the extension: a GetStatus reply arriving
    after that grabbed F8 again, with nothing listening, and the others drew
    on the destroyed menu ("already disposed" in the journal). Each reply's
    first check is that it isn't destroyed."""
    assert "this._destroyed = true;" in _method("destroy")
    replies = re.findall(r"(\w+)Remote\((?:[^()]*?, )?\((?:result|res), (?:error|err)\) => \{\n"
                         r"(.*?)\n", JS)
    names = {name for name, _first in replies}
    assert {"GetStatus", "GetPresets", "GetRecentDictations", "GetPasteLastHotkey"} <= names
    for name, _first in replies:
        body = JS[JS.index(f"{name}Remote("):]
        first_if = body[body.index("if ("):body.index("\n", body.index("if ("))]
        assert "this._destroyed" in first_if, f"{name}'s reply doesn't check first"


def test_notifications_say_what_happened():
    """Not just "TuxTalk", with the news in the body."""
    assert not re.search(r"Main\.notify\(['\"`]TuxTalk['\"`],", JS)


def test_a_status_update_never_turns_dictation_on_or_off():
    """From GNOME 47, setToggleState emits 'toggled' as a click does: without
    the guard, every status update started or stopped the service."""
    switch = JS[JS.index("connect('toggled'"):]
    assert "if (this._updatingToggle)" in switch[:switch.index("StartServiceRemote")]
    update = _method("_updateMenu")
    set_at = update.index("setToggleState")
    assert update.rindex("this._updatingToggle = true", 0, set_at) < set_at
    assert "this._updatingToggle = false" in update[set_at:]


def test_every_menu_item_calls_a_method_the_interface_declares():
    """_addRemoteItem builds the method's name at run time, so grep can't
    find a mistyped one."""
    for method in re.findall(r"_addRemoteItem\('[^']*', '(\w+)'", JS):
        assert f'<method name="{method}"' in JS, method
