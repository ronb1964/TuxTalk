"""The daily check's extension-update notice points to where it installs.

Extension updates are installed from Preferences → Updates & Backup. "Check
for Updates" covers only the app, so pointing there would answer "You're up to
date".
"""
from tuxtalk import update_ui


def test_the_notice_points_to_updates_and_backup(monkeypatch):
    shown = []
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: shown.append((title, body)))
    assert update_ui.extension_update_notice() is False
    (title, body), = shown
    assert "Updates & Backup" in body and "Check for Updates" not in body
