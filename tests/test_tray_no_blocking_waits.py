"""One-time tray paths that must not freeze the menu by waiting on the GTK
main loop.

* Replacing a pre-rename service can wait up to 3 s for it to exit.
* Quit and Restart Service wait for the service off it (test_quit_restart.py).
* After onboarding, KDE's menu cache rebuild (kbuildsycoca) can take up to 15 s.
"""

from tuxtalk import tray as tray_mod
from tuxtalk import tray_service


class FakeGLib:
    def __init__(self):
        self.timers = []

    def timeout_add(self, _ms, fn, *args):
        self.timers.append(lambda: fn(*args))


def _run_timers(loop):
    """Every timer, those they add included; how many ran."""
    ran = 0
    while loop.timers:
        loop.timers.pop(0)()
        ran += 1
    return ran


def _tray(**attrs):
    """A tray with only what the start-up check uses."""
    t = tray_mod.DictationTray.__new__(tray_mod.DictationTray)
    t.onboarding_in_progress = False
    t._service_gen = 0
    t._restart_pending = False
    t.__dict__.update(attrs)
    return t


def test_waiting_for_the_old_service_does_not_sleep(monkeypatch):
    loop = FakeGLib()
    monkeypatch.setattr(tray_service, "GLib", loop)
    monkeypatch.setattr("time.sleep", lambda s: (_ for _ in ()).throw(AssertionError("slept")))
    old = [[111], [111], []]          # still there for two checks, then gone
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids",
                        lambda: old[0] if len(old) == 1 else old.pop(0))
    stops = []
    monkeypatch.setattr("tuxtalk.service_launcher.stop_dictation_service", lambda: stops.append(1))
    started = []
    t = _tray(is_service_running=lambda: False, start_service=lambda _: started.append(1))

    t._auto_start_service(1000)
    _run_timers(loop)
    assert stops == [1] and started == [1]


def test_an_old_service_that_wont_go_is_given_three_seconds(monkeypatch):
    loop = FakeGLib()
    monkeypatch.setattr(tray_service, "GLib", loop)
    old = {"pids": [111]}
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids", lambda: old["pids"])
    monkeypatch.setattr("tuxtalk.service_launcher.stop_dictation_service", lambda: None)
    started = []
    t = _tray(is_service_running=lambda: False, start_service=lambda _: started.append(1))
    t._auto_start_service(1000)
    for _ in range(31):                             # the first look, then 30 of 100 ms
        loop.timers.pop(0)()
    assert started == [1] and loop.timers == []


def test_a_service_running_at_start_that_goes_is_replaced(monkeypatch):
    """A service with no tray, quitting as the tray starts (Dictation off
    from the GNOME extension): adopted, it went, and dictation stayed off."""
    loop = FakeGLib()
    monkeypatch.setattr(tray_service, "GLib", loop)
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids", lambda: [])
    running = iter([True, True, True, False])
    started = []
    t = _tray(is_service_running=lambda: next(running), start_service=lambda _: started.append(1))
    t._auto_start_service(1000)
    _run_timers(loop)
    assert started == [1]


def test_a_running_service_is_adopted_after_a_while(monkeypatch):
    from tuxtalk.service_launcher import QUIT_WAIT_S
    loop = FakeGLib()
    monkeypatch.setattr(tray_service, "GLib", loop)
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids", lambda: [])
    started = []
    t = _tray(is_service_running=lambda: True, start_service=lambda _: started.append(1))
    t._auto_start_service(1000)
    assert _run_timers(loop) == 1 + QUIT_WAIT_S and started == []


def test_dictation_turned_off_before_the_start_up_check_stays_off(monkeypatch):
    loop = FakeGLib()
    monkeypatch.setattr(tray_service, "GLib", loop)
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids", lambda: [])
    started = []
    t = _tray(is_service_running=lambda: False, start_service=lambda _: started.append(1))
    t._auto_start_service(1000)
    t._drop_pending_launches()                      # Dictation off from the extension
    _run_timers(loop)
    assert started == []


def test_dictation_turned_off_while_the_old_service_goes_stays_off(monkeypatch):
    """Turned off while the check waited for the pre-0.15.0 service to go,
    the next look found no service and started one."""
    loop = FakeGLib()
    monkeypatch.setattr(tray_service, "GLib", loop)
    old = {"pids": [111]}
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids", lambda: old["pids"])
    monkeypatch.setattr("tuxtalk.service_launcher.stop_dictation_service", lambda: None)
    started = []
    t = _tray(is_service_running=lambda: False, start_service=lambda _: started.append(1))
    t._auto_start_service(1000)
    loop.timers.pop(0)()                            # the first look
    loop.timers.pop(0)()                            # still waiting for it
    t._drop_pending_launches()                      # the user turns Dictation off
    old["pids"] = []
    _run_timers(loop)
    assert started == []


def test_the_kde_cache_rebuild_falls_back_to_kbuildsycoca5(monkeypatch):
    """Plasma 5 has only kbuildsycoca5; Plasma 6 gets one rebuild, not two."""
    import types
    from tuxtalk import menu_cache
    ran = []

    def fake_run(argv, **_):
        ran.append(argv[0])
        if argv[0] == "kbuildsycoca6":
            raise FileNotFoundError(argv[0])
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(menu_cache.subprocess, "run", fake_run)
    menu_cache.refresh_menu_cache()
    assert ran == ["update-desktop-database", "kbuildsycoca6", "kbuildsycoca5"]
    ran.clear()
    monkeypatch.setattr(menu_cache.subprocess, "run",
                        lambda argv, **_: ran.append(argv[0]) or types.SimpleNamespace(returncode=0))
    menu_cache.refresh_menu_cache()
    assert ran == ["update-desktop-database", "kbuildsycoca6"]


def test_the_kde_cache_rebuild_runs_off_the_main_loop(tray_main, monkeypatch):
    """After first-run setup the rebuild runs on its own thread, so the menu
    never waits for it."""
    import threading
    ran = []
    done = threading.Event()
    monkeypatch.setattr(tray_mod.menu_cache, "refresh_menu_cache",
                        lambda: ran.append(threading.current_thread()) or done.set())
    tray_main(first_run=True).run_timers()
    assert done.wait(5)
    assert ran[0] is not threading.main_thread()
