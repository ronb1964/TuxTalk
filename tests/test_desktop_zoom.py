"""The desktop's own zoom for the mouse grid (desktop_zoom.py), against
stand-ins for KWin's D-Bus calls and for GSettings: nothing here zooms the
real desktop or writes the real dconf."""
import json
import logging
import os
import threading
from types import SimpleNamespace

import dbus
import pytest

from tuxtalk import config, desktop_detect, desktop_zoom, kwin_desktop
from tuxtalk.mouse_grid_model import Rect

KDE_STEPS_TO_3X = 1.2 ** 6
FULL_HD = Rect(0, 0, 1920, 1080)


class FakeKWin:
    """KWin as kwin_desktop._call reaches it: whether the Zoom effect is
    loaded, and every shortcut invoked. *failing* (a shortcut, and how many
    of it go through first) makes the rest of that shortcut fail as an
    unanswered D-Bus call does."""

    def __init__(self):
        self.zoom_loaded = True
        self.shortcuts = []
        self.failing = None
        self.calls = 0

    def call(self, path, interface, method, signature, args, service, timeout):
        self.calls += 1
        if method == "isEffectLoaded":
            assert (path, interface, args) == ("/Effects", "org.kde.kwin.Effects", ("zoom",))
            return self.zoom_loaded
        assert (path, method) == ("/component/kwin", "invokeShortcut")
        if self.failing and args[0] == self.failing[0] \
                and self.shortcuts.count(args[0]) >= self.failing[1]:
            raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.NoReply")
        self.shortcuts.append(args[0])
        return None


class FakeGSettings:
    """GSettings as desktop_zoom uses it: each installed schema a dict of its
    keys, a key in *locked* refusing writes as a locked-down key does."""

    def __init__(self):
        self.schemas = {}
        self.locked = set()

    def add_magnifier(self, root, enabled=False, factor=2.0, tracking="proportional",
                      position="full-screen", lens=False):
        self.schemas[f"{root}.applications"] = {"screen-magnifier-enabled": enabled}
        self.schemas[f"{root}.magnifier"] = {"mag-factor": factor, "mouse-tracking": tracking,
                                             "screen-position": position, "lens-mode": lens}

    def magnifier(self, root):
        """(enabled, mag-factor) as the desktop has them now."""
        return (self.schemas[f"{root}.applications"]["screen-magnifier-enabled"],
                self.schemas[f"{root}.magnifier"]["mag-factor"])

    def gio(self):
        store = self

        class Settings:
            def __init__(self, schema):
                self._schema, self._keys = schema, store.schemas[schema]

            @classmethod
            def new(cls, schema):
                return cls(schema)

            @staticmethod
            def sync():
                pass

            def get_boolean(self, key):
                return self._keys[key]

            def get_double(self, key):
                return self._keys[key]

            def get_string(self, key):
                return self._keys[key]

            def _set(self, key, value):
                if key in store.locked:
                    return False
                self._keys[key] = value
                return True

            set_boolean = set_double = _set

        def lookup(schema, _recursive):
            if schema not in store.schemas:
                return None
            return SimpleNamespace(has_key=lambda key: key in store.schemas[schema])

        source = SimpleNamespace(lookup=lookup)
        return SimpleNamespace(Settings=Settings,
                               SettingsSchemaSource=SimpleNamespace(get_default=lambda: source))


@pytest.fixture(autouse=True)
def _own_data_and_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "get_data_dir", lambda: str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture
def desktop(monkeypatch):
    """Sets which desktop TuxTalk believes it runs on."""
    def on(name):
        monkeypatch.setattr(desktop_detect, "get_desktop_environment", lambda: name)
    return on


@pytest.fixture
def kwin(monkeypatch, desktop):
    fake = FakeKWin()
    monkeypatch.setattr(kwin_desktop, "_call", fake.call)
    desktop("kde")
    return fake


@pytest.fixture
def gsettings(monkeypatch):
    fake = FakeGSettings()
    monkeypatch.setattr(desktop_zoom, "Gio", fake.gio())
    return fake


def _kwinrc(text):
    folder = os.environ["XDG_CONFIG_HOME"]
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "kwinrc"), "w", encoding="utf-8") as f:
        f.write(text)


def _restore_file(desktop="kde"):
    return os.path.join(config.get_data_dir(), f"zoom-to-restore-{desktop}.json")


def _left_over(data, desktop=None):
    """Leaves *data* in a restore file: *desktop*'s, or the one it names."""
    os.makedirs(config.get_data_dir(), exist_ok=True)
    with open(_restore_file(desktop or data["desktop"]), "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


# Where the pointer waits so the chosen part shows centred


def test_kde_parks_at_the_corner_for_the_top_left_part():
    view = desktop_zoom.View(KDE_STEPS_TO_3X, "proportional", False)
    x, y = desktop_zoom.parking_spot(Rect(0, 0, 640, 360), FULL_HD, FULL_HD, view)
    assert x == pytest.approx(0, abs=1) and y == pytest.approx(0, abs=1)


def test_gnome_padding_parks_where_the_spike_measured():
    view = desktop_zoom.View(3.0, "proportional", True)
    screen = Rect(0, 0, 1920, 1200)
    x, y = desktop_zoom.parking_spot(Rect(0, 0, 640, 400), screen, screen, view)
    assert x == pytest.approx(106.7, abs=0.5) and y == pytest.approx(100.0, abs=0.5)
    # Cinnamon on 1920x1080, the same model.
    x, y = desktop_zoom.parking_spot(Rect(0, 0, 640, 360), FULL_HD, FULL_HD, view)
    assert x == pytest.approx(97.1, abs=0.5) and y == pytest.approx(90.0, abs=0.5)


def test_centered_parks_on_the_parts_centre_on_one_monitor():
    for tracking in ("centered", "other"):
        view = desktop_zoom.View(3.0, tracking, False)
        spot = desktop_zoom.parking_spot(Rect(640, 360, 640, 360), FULL_HD, FULL_HD, view)
        assert spot == (960, 540)


def test_the_spot_stays_on_the_parts_monitor():
    canvas = Rect(0, 0, 3840, 1080)
    right = Rect(1920, 0, 1920, 1080)
    part = Rect(3200, 0, 640, 360)
    for view in (desktop_zoom.View(KDE_STEPS_TO_3X, "proportional", False),
                 desktop_zoom.View(3.0, "proportional", True),
                 desktop_zoom.View(3.0, "centered", False)):
        x, y = desktop_zoom.parking_spot(part, right, canvas, view)
        assert 1920 <= x <= 3839 and 0 <= y <= 1079


def _shown_at(point, roi, canvas_middle, zoom):
    """Where a view centred on *roi* shows *point*: W / 2 + z * (p - roi)."""
    return canvas_middle + zoom * (point - roi)


def test_centered_on_two_monitors_shows_the_part_on_its_own_monitors_centre():
    """With two screens side by side the view centres on the pointer over
    the whole desktop: parked on the part's own centre, the right screen's
    middle cell would show on the seam between the screens."""
    canvas = Rect(0, 0, 3840, 1080)
    right = Rect(1920, 0, 1920, 1080)
    part = Rect(2560, 360, 640, 360)            # the right screen's cell 14
    view = desktop_zoom.View(3.0, "centered", False)
    x, y = desktop_zoom.parking_spot(part, right, canvas, view)
    assert _shown_at(part.center[0], x, 1920, 3.0) == pytest.approx(right.center[0])
    assert _shown_at(part.center[1], y, 540, 3.0) == pytest.approx(right.center[1])
    assert right.x <= x < right.x + right.width


# KDE: KWin's Zoom effect, stepped by its own shortcuts


@pytest.mark.parametrize("step, steps", [(1.2, 6), (2.0, 2)])
def test_kde_zoom_in_and_back_take_the_same_number_of_steps(kwin, step, steps):
    _kwinrc(f"[Effect-zoom]\nZoomFactor={step}\n")
    zoom = desktop_zoom.find()
    assert zoom.name == "KDE zoom"
    assert zoom.view == desktop_zoom.View(pytest.approx(step ** steps), "proportional", False)

    assert zoom.zoom_in()
    assert kwin.shortcuts == ["view_zoom_in"] * steps
    with open(_restore_file(), encoding="utf-8") as f:
        assert json.load(f) == {"desktop": "kde", "steps": steps}

    zoom.restore()
    assert kwin.shortcuts == ["view_zoom_in"] * steps + ["view_zoom_out"] * steps
    assert not os.path.exists(_restore_file())


def test_kde_follows_the_users_mouse_tracking(kwin):
    _kwinrc("[Effect-zoom]\nMouseTracking=1\n")
    assert desktop_zoom.find().view.tracking == "centered"
    _kwinrc("[Effect-zoom]\nMouseTracking=2\n")
    assert desktop_zoom.find().view.tracking == "other"


@pytest.mark.parametrize("value", ["2.0", "push", "9"])
def test_kde_mouse_tracking_it_cant_read_isnt_taken_for_proportional(kwin, value):
    _kwinrc(f"[Effect-zoom]\nMouseTracking={value}\n")
    assert desktop_zoom.find().view.tracking == "other"


def test_kde_without_the_zoom_effect_gives_no_magnifier(kwin):
    kwin.zoom_loaded = False
    assert desktop_zoom.find() is None
    assert kwin.shortcuts == []


def test_kwin_not_answering_gives_no_magnifier(kwin, monkeypatch, caplog):
    def no_answer(*_args):
        raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.ServiceUnknown")
    monkeypatch.setattr(kwin_desktop, "_call", no_answer)
    assert desktop_zoom.find() is None
    assert "Desktop zoom" in caplog.text


# GNOME and Cinnamon: the magnifier's two settings, put back as they were


@pytest.mark.parametrize("name, root, label", [
    ("gnome", "org.gnome.desktop.a11y", "GNOME magnifier"),
    ("cinnamon", "org.cinnamon.desktop.a11y", "Cinnamon magnifier")])
@pytest.mark.parametrize("enabled", [False, True])
def test_gnome_magnifier_is_put_back_exactly(desktop, gsettings, name, root, label, enabled):
    desktop(name)
    gsettings.add_magnifier(root, enabled=enabled, factor=2.0)
    zoom = desktop_zoom.find()
    assert zoom.name == label
    assert zoom.view == desktop_zoom.View(3.0, "proportional", True)

    assert zoom.zoom_in()
    assert gsettings.magnifier(root) == (True, 3.0)
    zoom.restore()
    assert gsettings.magnifier(root) == (enabled, 2.0)
    assert not os.path.exists(_restore_file(name))


def test_a_second_zoom_in_keeps_what_to_put_back(desktop, gsettings):
    desktop("gnome")
    gsettings.add_magnifier("org.gnome.desktop.a11y", enabled=False, factor=2.0)
    zoom = desktop_zoom.find()
    assert zoom.zoom_in() and zoom.zoom_in()
    zoom.restore()
    assert gsettings.magnifier("org.gnome.desktop.a11y") == (False, 2.0)


@pytest.mark.parametrize("setup", [{"lens": True}, {"position": "top-half"}])
def test_a_lens_or_half_screen_magnifier_is_not_used(desktop, gsettings, setup):
    desktop("gnome")
    gsettings.add_magnifier("org.gnome.desktop.a11y", **setup)
    assert desktop_zoom.find() is None


def test_a_desktop_without_the_magnifier_schemas_gives_none(desktop, gsettings):
    desktop("cinnamon")
    assert desktop_zoom.find() is None


def test_restore_twice_changes_nothing_the_second_time_on_kde(kwin):
    zoom = desktop_zoom.find()
    zoom.zoom_in()
    zoom.restore()
    zoom.restore()
    assert kwin.shortcuts.count("view_zoom_out") == 6


def test_restore_twice_changes_nothing_the_second_time_on_gnome(desktop, gsettings):
    desktop("gnome")
    gsettings.add_magnifier("org.gnome.desktop.a11y", enabled=False, factor=2.0)
    zoom = desktop_zoom.find()
    zoom.zoom_in()
    zoom.restore()
    gsettings.schemas["org.gnome.desktop.a11y.magnifier"]["mag-factor"] = 4.0  # the user's own
    zoom.restore()
    assert gsettings.magnifier("org.gnome.desktop.a11y") == (False, 4.0)


def test_a_failing_zoom_call_leaves_nothing_changed_and_logs_on_kde(kwin, caplog):
    kwin.failing = ("view_zoom_in", 3)
    assert not desktop_zoom.find().zoom_in()
    # The three steps that went in were taken back out again.
    assert kwin.shortcuts == ["view_zoom_in"] * 3 + ["view_zoom_out"] * 3
    assert not os.path.exists(_restore_file())
    assert "Desktop zoom" in caplog.text


def test_a_failing_zoom_call_leaves_nothing_changed_and_logs_on_gnome(desktop, gsettings,
                                                                      caplog):
    desktop("gnome")
    gsettings.add_magnifier("org.gnome.desktop.a11y", enabled=False, factor=2.0)
    gsettings.locked.add("screen-magnifier-enabled")
    assert not desktop_zoom.find().zoom_in()
    assert gsettings.magnifier("org.gnome.desktop.a11y") == (False, 2.0)
    assert not os.path.exists(_restore_file("gnome"))
    assert "Desktop zoom" in caplog.text


def test_a_failed_put_back_keeps_what_is_still_owed(kwin, caplog):
    zoom = desktop_zoom.find()
    zoom.zoom_in()
    kwin.failing = ("view_zoom_out", 2)
    zoom.restore()
    assert "Desktop zoom" in caplog.text
    with open(_restore_file(), encoding="utf-8") as f:
        assert json.load(f) == {"desktop": "kde", "steps": 4}

    kwin.failing = None
    zoom.restore()
    assert kwin.shortcuts.count("view_zoom_out") == 6
    assert not os.path.exists(_restore_file())


def test_restore_says_whether_the_zoom_is_back(kwin):
    zoom = desktop_zoom.find()
    zoom.zoom_in()
    kwin.failing = ("view_zoom_out", 2)
    assert zoom.restore() is False
    kwin.failing = None
    assert zoom.restore() is True
    assert zoom.restore() is True           # nothing owed: nothing to do


def test_restore_says_whether_the_gnome_magnifier_is_back(desktop, gsettings):
    desktop("gnome")
    gsettings.add_magnifier("org.gnome.desktop.a11y", enabled=False, factor=2.0)
    zoom = desktop_zoom.find()
    zoom.zoom_in()
    gsettings.locked.add("screen-magnifier-enabled")
    assert zoom.restore() is False
    gsettings.locked.clear()
    assert zoom.restore() is True


def test_the_next_grid_puts_a_failed_put_back_back_first_on_kde(kwin, caplog):
    first = desktop_zoom.find()
    first.zoom_in()
    kwin.failing = ("view_zoom_out", 2)
    first.restore()                 # four out-steps still owed

    # KWin failing again after two more: the next grid doesn't zoom over
    # the two still owed, and the file says two.
    kwin.shortcuts.clear()
    assert not desktop_zoom.find().zoom_in()
    assert kwin.shortcuts == ["view_zoom_out"] * 2
    with open(_restore_file(), encoding="utf-8") as f:
        assert json.load(f) == {"desktop": "kde", "steps": 2}
    assert "not zooming" in caplog.text

    # KWin answering again: the two go out first, then the new zoom goes in.
    kwin.failing = None
    kwin.shortcuts.clear()
    second = desktop_zoom.find()
    assert second.zoom_in()
    assert kwin.shortcuts == ["view_zoom_out"] * 2 + ["view_zoom_in"] * 6
    second.restore()
    assert not os.path.exists(_restore_file())


def test_the_same_magnifier_puts_back_what_it_owes_before_zooming_again(kwin):
    zoom = desktop_zoom.find()
    zoom.zoom_in()
    kwin.failing = ("view_zoom_out", 2)
    zoom.restore()
    kwin.failing = None
    kwin.shortcuts.clear()
    assert zoom.zoom_in()
    assert kwin.shortcuts == ["view_zoom_out"] * 4 + ["view_zoom_in"] * 6


def test_the_next_grid_never_takes_a_half_put_back_magnifier_for_the_users(desktop, gsettings):
    desktop("gnome")
    root = "org.gnome.desktop.a11y"
    gsettings.add_magnifier(root, enabled=False, factor=2.0)
    first = desktop_zoom.find()
    first.zoom_in()
    gsettings.locked.add("screen-magnifier-enabled")
    first.restore()                 # refused: still on at 3x

    assert not desktop_zoom.find().zoom_in()
    with open(_restore_file("gnome"), encoding="utf-8") as f:
        assert json.load(f) == {"desktop": "gnome", "enabled": False, "mag_factor": 2.0}

    gsettings.locked.clear()
    second = desktop_zoom.find()
    assert second.zoom_in()
    second.restore()
    assert gsettings.magnifier(root) == (False, 2.0)


# A zoom left on by a service that stopped


def test_a_zoom_left_on_is_put_back_at_start(kwin, gsettings, caplog):
    caplog.set_level(logging.INFO)
    gsettings.add_magnifier("org.gnome.desktop.a11y", enabled=True, factor=3.0)
    _left_over({"desktop": "gnome", "enabled": False, "mag_factor": 2.0})
    desktop_zoom.restore_left_over()
    assert gsettings.magnifier("org.gnome.desktop.a11y") == (False, 2.0)
    assert not os.path.exists(_restore_file("gnome"))
    assert "Desktop zoom: put back a zoom TuxTalk left on" in caplog.text

    _left_over({"desktop": "kde", "steps": 6})
    desktop_zoom.restore_left_over()
    assert kwin.shortcuts == ["view_zoom_out"] * 6
    assert not os.path.exists(_restore_file())


def test_a_left_over_kde_zoom_half_put_back_keeps_only_what_is_still_owed(kwin):
    """Two of six out-steps go through, then KWin stops answering: the next
    start owes four, not six, or it takes two steps of the user's own zoom."""
    _left_over({"desktop": "kde", "steps": 6})
    kwin.failing = ("view_zoom_out", 2)
    desktop_zoom.restore_left_over()
    with open(_restore_file(), encoding="utf-8") as f:
        assert json.load(f) == {"desktop": "kde", "steps": 4}

    kwin.failing = None
    desktop_zoom.restore_left_over()
    assert kwin.shortcuts == ["view_zoom_out"] * 6
    assert not os.path.exists(_restore_file())


@pytest.mark.parametrize("desktop_file, damaged", [
    ("kde", "{not json"), ("kde", '{"desktop": "kde", "steps": "six"}'),
    ("kde", '{"desktop": "kde", "steps": true}'), ("kde", '{"desktop": "beos"}'),
    ("kde", '["kde"]'), ("kde", '{"desktop": "gnome", "enabled": false, "mag_factor": 2}'),
    ("gnome", '{"desktop": "gnome", "enabled": "no", "mag_factor": 2}')])
def test_a_damaged_restore_file_is_deleted_with_a_warning(kwin, gsettings, caplog,
                                                         desktop_file, damaged):
    gsettings.add_magnifier("org.gnome.desktop.a11y")
    _left_over(damaged, desktop_file)
    desktop_zoom.restore_left_over()
    assert not os.path.exists(_restore_file(desktop_file))
    assert kwin.shortcuts == []
    assert "WARNING" in caplog.text and "Desktop zoom" in caplog.text


def test_a_kde_zoom_left_on_waits_for_kde(kwin, desktop, gsettings, caplog):
    caplog.set_level(logging.INFO)
    desktop("gnome")
    _left_over({"desktop": "kde", "steps": 6})
    desktop_zoom.restore_left_over()
    assert kwin.calls == 0
    assert os.path.exists(_restore_file())
    assert "put back at a start on KDE" in caplog.text
    assert "WARNING" not in caplog.text


def test_a_gnome_zoom_leaves_a_kde_zoom_waiting_alone(kwin, desktop, gsettings, caplog):
    caplog.set_level(logging.INFO)
    _left_over({"desktop": "kde", "steps": 6})
    desktop("gnome")
    root = "org.gnome.desktop.a11y"
    gsettings.add_magnifier(root, enabled=False, factor=2.0)
    desktop_zoom.restore_left_over()          # the service's start: one info line

    zoom = desktop_zoom.find()
    assert zoom.zoom_in()
    assert gsettings.magnifier(root) == (True, 3.0)
    zoom.restore()
    assert gsettings.magnifier(root) == (False, 2.0)
    assert not os.path.exists(_restore_file("gnome"))
    with open(_restore_file("kde"), encoding="utf-8") as f:
        assert json.load(f) == {"desktop": "kde", "steps": 6}
    assert kwin.calls == 0
    assert caplog.text.count("put back at a start on KDE") == 1
    assert "WARNING" not in caplog.text


def test_a_restore_file_that_cant_be_read_is_kept(kwin, caplog):
    os.makedirs(_restore_file())    # opening it fails, as an unreadable file does
    desktop_zoom.restore_left_over()
    assert os.path.isdir(_restore_file())
    assert "couldn't read the restore file" in caplog.text
    assert kwin.calls == 0


def test_a_zoom_step_kwin_cant_use_gives_no_magnifier(kwin):
    # 1.05 would take 23 steps each way, each a D-Bus call.
    for step in ("nan", "inf", "1.0", "0.5", "1.05", "1.001"):
        _kwinrc(f"[Effect-zoom]\nZoomFactor={step}\n")
        assert desktop_zoom.find() is None


def test_the_service_puts_back_a_zoom_left_on_before_listening(service_main):
    started = service_main()
    assert started.index("puts back a zoom left on") < started.index("listens for hotkeys")


def test_nothing_left_over_does_nothing(kwin):
    desktop_zoom.restore_left_over()
    assert kwin.calls == 0


@pytest.mark.parametrize("name", ["cosmic", "xfce"])
def test_cosmic_and_xfce_have_no_magnifier(kwin, desktop, name):
    desktop(name)
    assert desktop_zoom.find() is None
    assert kwin.calls == 0



def test_the_next_grids_zoom_waits_for_the_last_ones_put_back(kwin, monkeypatch):
    """A put-back still stepping out on another thread (a slow KWin) and the
    next grid's zoom share the restore file: the zoom waits, so it neither
    steps the same zoom out again (taking the user's own) nor loses the file."""
    first = desktop_zoom.find()
    first.zoom_in()
    kwin.shortcuts.clear()
    slow, going = threading.Event(), threading.Event()
    answer = kwin.call

    def slow_call(*args):
        if args[4][0] == "view_zoom_out":
            going.set()
            slow.wait(5)
        return answer(*args)
    monkeypatch.setattr(kwin_desktop, "_call", slow_call)

    putting_back = threading.Thread(target=first.restore)
    putting_back.start()
    going.wait(5)
    second = desktop_zoom.find()
    zooming = threading.Thread(target=second.zoom_in)
    zooming.start()
    zooming.join(0.2)
    assert zooming.is_alive()                   # waiting for the put-back
    slow.set()
    putting_back.join(5)
    zooming.join(5)
    assert kwin.shortcuts == ["view_zoom_out"] * 6 + ["view_zoom_in"] * 6
    with open(_restore_file(), encoding="utf-8") as f:
        assert json.load(f) == {"desktop": "kde", "steps": 6}
