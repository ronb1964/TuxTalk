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
    Fedora-family systems, so its notices never appeared (2026-10-18)."""
    import sys
    import types
    calls = []

    class Proxy:
        def Notify(self, *args, **kwargs):
            calls.append((args, kwargs))

    class Bus:
        def get_object(self, name, path):
            calls.append((name, path))
            return Proxy()

    fake_dbus = types.SimpleNamespace(
        SessionBus=Bus, UInt32=int, Int32=int,
        Array=lambda items, signature: list(items),
        Dictionary=lambda items, signature: dict(items))
    monkeypatch.setitem(sys.modules, "dbus", fake_dbus)
    import importlib
    before = dict(vars(notify))              # conftest's held-back _send among them
    real = importlib.reload(notify)          # the real _send, not the held-back one
    try:
        real._send("TuxTalk", "Heard only silence")
    finally:
        # The module as it was, so notify.notify is again the function
        # the service calls and _send is held back, whatever runs next.
        vars(notify).update(before)
    assert calls[0] == ("org.freedesktop.Notifications", "/org/freedesktop/Notifications")
    args, kwargs = calls[1]
    assert args[3:5] == ("TuxTalk", "Heard only silence")
    assert kwargs["dbus_interface"] == "org.freedesktop.Notifications"


def test_no_notification_service_is_not_an_error(monkeypatch):
    def broken(title, body):
        raise RuntimeError("org.freedesktop.Notifications was not provided")
    monkeypatch.setattr(notify, "_send", broken)
    notify.notify("TuxTalk", "nobody to show it")   # no error
