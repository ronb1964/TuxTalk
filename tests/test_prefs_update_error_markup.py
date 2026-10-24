"""An update error containing & or < must still show in the status line.

Unescaped, set_markup() makes Pango reject the text, and the label goes blank
exactly when the user needs the error.
"""
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Pango", "1.0")
from gi.repository import Pango  # noqa: E402

from tuxtalk.prefs_updates import UpdatesTab  # noqa: E402


def test_a_check_error_with_markup_characters_stays_readable():
    shown = []
    d = SimpleNamespace(update_status_label=SimpleNamespace(set_markup=shown.append))
    button = SimpleNamespace(set_sensitive=lambda v: None)
    error = "[Errno 13] Permission denied: '/home/x/R&D/<AppImages>'"

    UpdatesTab._handle_update_result(d, {"success": False, "error": error}, button)

    ok, _attrs, text, _accel = Pango.parse_markup(shown[0], -1, "\0")
    assert ok
    assert error in text
