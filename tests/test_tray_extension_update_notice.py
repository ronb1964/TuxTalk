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


def _daily_check_finds(monkeypatch, app, extension):
    """Run the daily check against a GitHub answer of *app* and *extension*
    updates; what it opened: "window" (Update Available) and "notice"."""
    import threading
    from tuxtalk import config, update_checker
    queued, opened = threading.Event(), []
    monkeypatch.setattr(config, "load_config",
                        lambda: config.Settings(auto_check_updates=True, last_update_check=""))
    monkeypatch.setattr(config, "record_update_check", lambda ts: None)
    monkeypatch.setattr(update_checker, "check_for_updates", lambda: {
        "success": True, "update_available": app, "extension_update": extension})
    monkeypatch.setattr(update_ui, "extension_update_notice", lambda: opened.append("notice"))
    calls = []

    def idle_add(fn, *args):
        calls.append((fn, args))
        if len(calls) == 1 + app + extension:      # the time recorded, then what it opens
            queued.set()
    monkeypatch.setattr(update_ui.GLib, "idle_add", idle_add)
    update_ui.auto_check_for_updates(lambda result: opened.append("window"))
    assert queued.wait(5)
    for fn, args in calls:                          # GTK's loop, here
        fn(*args)
    return opened


def test_an_extension_update_found_with_an_app_update_is_announced_too(monkeypatch):
    """An update from an older release brings both: the Update Available
    window offers only the app, so the extension's notice still comes."""
    assert _daily_check_finds(monkeypatch, app=True, extension=True) == ["window", "notice"]


def test_an_extension_update_alone_is_only_a_notice(monkeypatch):
    assert _daily_check_finds(monkeypatch, app=False, extension=True) == ["notice"]
