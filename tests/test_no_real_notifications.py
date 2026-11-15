"""Running the tests never pops a notification on the developer's desktop.

Tests such as test_a_real_cuda_failure_still_falls_back_to_cpu go through the
real notification path, so conftest holds back notify._send, the one place a
notice leaves for the desktop, for the whole suite.
"""
from tuxtalk import notify, pipeline


def test_notifications_are_held_back_during_tests():
    assert notify._send.__name__ == "_held_back"


def test_the_service_notify_does_not_reach_the_desktop(monkeypatch):
    assert pipeline.notify_module is notify
    # Count the calls that reach the held-back _send(). A notify() that found
    # another way to the desktop would leave the count at zero.
    held_back = notify._send
    shown = []

    def counting_send(title, body):
        shown.append(title)
        return held_back(title, body)

    monkeypatch.setattr(notify, "_send", counting_send)
    notify.notify("TuxTalk", "should not appear")
    assert shown == ["TuxTalk"]


def test_a_notice_goes_to_the_desktops_notification_service(monkeypatch):
    """Over D-Bus, not libnotify: the AppImage couldn't load libnotify on
    Fedora-family systems, so its notices never appeared. With a time limit:
    a hung notification service would hold the hotkey loop otherwise."""
    from gi.repository import Gio
    calls = []

    class Connection:
        def call_sync(self, name, path, interface, method, args, reply_type, flags,
                      timeout_ms, cancellable):
            calls.append((name, interface, method, args.unpack(), flags, timeout_ms))

    monkeypatch.setattr(Gio, "bus_get_sync", lambda kind, cancellable: Connection())
    import importlib
    before = dict(vars(notify))              # conftest's held-back _send among them
    real = importlib.reload(notify)          # the real _send, not the held-back one
    try:
        real._send("TuxTalk", "Heard only silence")
    finally:
        # The module as it was, so notify.notify is again the function
        # the service calls and _send is held back, whatever runs next.
        vars(notify).update(before)
    [(name, interface, method, args, flags, timeout_ms)] = calls
    assert (name, interface, method) == ("org.freedesktop.Notifications",) * 2 + ("Notify",)
    assert args[3:5] == ("TuxTalk", "Heard only silence")
    assert 0 < timeout_ms <= 5000
    # A notification service that starts only when asked (mako, dunst) is started.
    assert not flags & Gio.DBusCallFlags.NO_AUTO_START


def test_no_notification_service_is_not_an_error(monkeypatch):
    def broken(title, body):
        raise RuntimeError("org.freedesktop.Notifications was not provided")
    monkeypatch.setattr(notify, "_send", broken)
    notify.notify("TuxTalk", "nobody to show it")   # no error
