"""One-time tray paths that must not freeze the menu by waiting on the GTK
main loop.

* Replacing a pre-rename service can wait up to 3 s for it to exit.
* After onboarding, KDE's menu cache rebuild (kbuildsycoca) can take up to 15 s.
"""
import types

from tuxtalk import tray as tray_mod


class FakeGLib:
    def __init__(self):
        self.timers = []

    def timeout_add(self, _ms, fn, *args):
        self.timers.append(lambda: fn(*args))


def test_waiting_for_the_old_service_does_not_sleep(monkeypatch):
    loop = FakeGLib()
    monkeypatch.setattr(tray_mod, "GLib", loop)
    monkeypatch.setattr(tray_mod.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("slept")))
    old = [[111], [111], []]          # still there for two checks, then gone
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids",
                        lambda: old[0] if len(old) == 1 else old.pop(0))
    stops = []
    monkeypatch.setattr("tuxtalk.service_launcher.stop_dictation_service", lambda: stops.append(1))
    started = []
    t = types.SimpleNamespace(onboarding_in_progress=False,
                              is_service_running=lambda: False,
                              start_service=lambda _: started.append(1))
    t._auto_start_service = lambda w=0, watching=False: tray_mod.DictationTray._auto_start_service(
        t, w, watching)

    assert t._auto_start_service() is False
    while loop.timers:
        loop.timers.pop(0)()
    assert stops == [1] and started == [1]


def test_the_wait_gives_up_once_a_service_could_have_quit(monkeypatch):
    loop = FakeGLib()
    monkeypatch.setattr(tray_mod, "GLib", loop)
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids", lambda: [111])
    monkeypatch.setattr("tuxtalk.service_launcher.stop_dictation_service", lambda: None)
    started = []
    t = types.SimpleNamespace(onboarding_in_progress=False,
                              is_service_running=lambda: True,
                              start_service=lambda _: started.append(1))
    t._auto_start_service = lambda w=0, watching=False: tray_mod.DictationTray._auto_start_service(
        t, w, watching)
    t._auto_start_service()
    checks = 0
    while loop.timers:
        loop.timers.pop(0)()
        checks += 1
    from tuxtalk.service_launcher import QUIT_WITHIN_S
    assert checks == QUIT_WITHIN_S * 10 and started == []


def test_a_service_that_quits_while_the_tray_starts_is_replaced(monkeypatch):
    """An update's restart: the old tray stopped the service as it quit,
    and the service typed what it held first. The new tray saw it running,
    skipped starting one, and dictation stayed off once it ended."""
    loop = FakeGLib()
    monkeypatch.setattr(tray_mod, "GLib", loop)
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids", lambda: [])
    running = iter([True, True, True, False])
    started = []
    t = types.SimpleNamespace(onboarding_in_progress=False,
                              is_service_running=lambda: next(running),
                              start_service=lambda _: started.append(1))
    t._auto_start_service = lambda w=0, watching=False: tray_mod.DictationTray._auto_start_service(
        t, w, watching)
    t._auto_start_service()
    while loop.timers:
        loop.timers.pop(0)()
    assert started == [1]


def test_the_kde_cache_rebuild_falls_back_to_kbuildsycoca5(monkeypatch):
    ran = []

    def fake_run(argv, **_):
        ran.append(argv[0])
        if argv[0] == "kbuildsycoca6":
            raise FileNotFoundError(argv[0])

    monkeypatch.setattr(tray_mod.subprocess, "run", fake_run)
    tray_mod._refresh_kde_menu_cache()
    assert ran == ["kbuildsycoca6", "kbuildsycoca5"]


def test_the_kde_cache_rebuild_runs_off_the_main_loop(tray_main, monkeypatch):
    """After first-run setup the rebuild runs on its own thread, so the menu
    never waits for it."""
    import threading
    ran = []
    done = threading.Event()
    monkeypatch.setattr(tray_mod, "_refresh_kde_menu_cache",
                        lambda: ran.append(threading.current_thread()) or done.set())
    tray_main(first_run=True).run_timers()
    assert done.wait(5)
    assert ran[0] is not threading.main_thread()


def test_dictation_turned_off_while_the_tray_watches_stays_off(monkeypatch):
    """Turned off during the watch at start, the next look found no service
    and started one."""
    loop = FakeGLib()
    monkeypatch.setattr(tray_mod, "GLib", loop)
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_service_pids", lambda: [])
    monkeypatch.setattr("tuxtalk.service_launcher.stop_dictation_service", lambda: None)
    running = {"now": True}
    started = []
    t = types.SimpleNamespace(onboarding_in_progress=False,
                              is_service_running=lambda: running["now"],
                              start_service=lambda _: started.append(1))
    t._auto_start_service = lambda w=0, watching=False: tray_mod.DictationTray._auto_start_service(
        t, w, watching)
    t._auto_start_service()
    loop.timers.pop(0)()                            # still watching
    tray_mod.DictationTray._kill_service(t)         # the user turns Dictation off
    running["now"] = False
    while loop.timers:
        loop.timers.pop(0)()
    assert started == []


def test_quitting_waits_for_the_service_to_end(monkeypatch):
    """From an AppImage the service's files go when the tray ends."""
    from tuxtalk import service_launcher
    pids = iter([[42], [42], []])
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: next(pids))
    monkeypatch.setattr(service_launcher.time, "sleep", lambda s: None)
    assert service_launcher.wait_until_stopped(5) is True
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [42])
    assert service_launcher.wait_until_stopped(0) is False
