"""Dictations pasted on GNOME: the extension sets the clipboard inside GNOME
Shell (clipboard.copy_in_shell), so no wl-copy window flashes at each paste.
The extension runs under Node with stand-ins for GNOME Shell (gnome_extension_js)."""
import re

from gnome_extension_js import run
from tuxtalk import app_focus

ENABLED = """
const {extension} = await t.enable();
const served = t.shell.exported.find(item =>
    item.path === '/io/github/ronb1964/TuxTalk/Extension');
"""


def test_the_extension_serves_set_clipboard_as_tuxtalk_calls_it():
    """A different signature on either side and every paste falls back to wl-copy."""
    xml, _logs = run(ENABLED + "t.report(served.xml);")
    assert f'<interface name="{app_focus.EXTENSION_INTERFACE}">' in xml
    method = re.search(r'<method name="SetClipboard">(.*?)</method>', xml, re.S).group(1)
    assert re.findall(r'type="(\w)" direction="(\w+)"', method) == [("s", "in"), ("b", "in")]


def test_the_dictation_goes_on_the_clipboard_or_the_primary_selection():
    (clipboard, primary), _logs = run(ENABLED + """
        served.object.SetClipboard('hello there', false);
        served.object.SetClipboard('for the terminal', true);
        t.report([t.shell.clipboard, t.shell.primary]);
    """)
    assert (clipboard, primary) == ("hello there", "for the terminal")
