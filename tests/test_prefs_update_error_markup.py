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


def test_versions_with_markup_characters_stay_readable(monkeypatch):
    """A tag such as "1.0&beta" went into the markup as it was, and the
    status label went blank."""
    from tuxtalk import installation, update_ui
    monkeypatch.setattr(installation, "get_install_type", lambda: "dev")
    monkeypatch.setattr(update_ui, "get_update_guidance",
                        lambda kind, version, url="": {"update_method": "none"})
    shown = []
    hidden = SimpleNamespace(show=lambda: None, hide=lambda: None)
    d = SimpleNamespace(update_status_label=SimpleNamespace(set_markup=shown.append),
                        download_btn=hidden, view_release_btn=hidden,
                        update_extension_btn=hidden)
    button = SimpleNamespace(set_sensitive=lambda v: None)

    UpdatesTab._handle_update_result(d, {
        "success": True, "update_available": True, "extension_update": True,
        "current_version": "0.19<rc>", "latest_version": "1.0&beta",
        "extension_current": "3<4", "extension_latest": "5&6"}, button)

    ok, _attrs, text, _accel = Pango.parse_markup(shown[-1], -1, "\0")
    assert ok
    assert "0.19<rc> → 1.0&beta" in text and "3<4 → 5&6" in text
