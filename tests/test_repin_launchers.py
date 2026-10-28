"""Pinned dock and favourite entries follow the rename (0.15.0).

The migration renames the menu entry (talktype.desktop -> tuxtalk.desktop).
A dock that pinned the old name then shows a blank icon on KDE, and GNOME
silently drops it from the favourites. So the pinned lists are rewritten too,
once.
"""
from tuxtalk import rename_migration as rm


def test_pinned_entries_are_renamed_in_place():
    items = ["applications:systemsettings.desktop", "applications:talktype-dev.desktop",
             "preferred://browser"]
    new, changed = rm.renamed_launchers(items)
    assert new == ["applications:systemsettings.desktop", "applications:tuxtalk-dev.desktop",
                   "preferred://browser"]
    assert changed


def test_every_old_launcher_name_is_covered():
    for old, new in (("talktype.desktop", "tuxtalk.desktop"),
                     ("io.github.ronb1964.TalkType.desktop", "io.github.ronb1964.TuxTalk.desktop"),
                     ("talktype-dev.desktop", "tuxtalk-dev.desktop")):
        assert rm.renamed_launchers([old])[0] == [new]                       # GNOME favourites
        assert rm.renamed_launchers([f"applications:{old}"])[0] == [f"applications:{new}"]  # KDE


def test_a_list_already_pinning_tuxtalk_does_not_get_it_twice():
    new, changed = rm.renamed_launchers(["tuxtalk.desktop", "talktype.desktop"])
    assert new == ["tuxtalk.desktop"] and changed


def test_nothing_to_rename_changes_nothing():
    assert rm.renamed_launchers(["firefox.desktop"]) == (["firefox.desktop"], False)


def test_repin_runs_once_and_uses_both_desktops(tmp_path):
    marker = tmp_path / "launchers-renamed"
    calls = []
    favs = {"v": ["org.gnome.Nautilus.desktop", "talktype.desktop"]}

    def plasma(script):
        calls.append(script)
        return "1"
    lines = rm.repin_launchers(str(marker), run_plasma_script=plasma,
                               gnome_favorites=(lambda: favs["v"], lambda v: favs.update(v=v)),
                               app_dirs=_apps(tmp_path, "tuxtalk.desktop", "tuxtalk-dev.desktop"))
    assert favs["v"] == ["org.gnome.Nautilus.desktop", "tuxtalk.desktop"]
    assert "applications:talktype-dev.desktop" in calls[0]     # the KDE script carries the map
    assert marker.exists() and lines
    assert rm.repin_launchers(str(marker), run_plasma_script=plasma) == []   # once only
    assert len(calls) == 1


def test_a_failure_leaves_it_to_try_again_next_start(tmp_path):
    marker = tmp_path / "launchers-renamed"

    def broken(script):
        raise RuntimeError("plasmashell not running yet")
    rm.repin_launchers(str(marker), run_plasma_script=broken, gnome_favorites=None, app_dirs=[])
    assert not marker.exists()


def test_favourites_gnome_wont_let_change_are_tried_again_next_start(tmp_path):
    """Locked favourites: Gio's set_strv says False and changes nothing. That
    was taken as done, so the old TalkType pin stayed for good."""
    marker = tmp_path / "launchers-renamed"
    favs = ["talktype.desktop"]
    lines = rm.repin_launchers(str(marker), run_plasma_script=None,
                               gnome_favorites=(lambda: favs, lambda v: False),
                               app_dirs=_apps(tmp_path, "tuxtalk.desktop"))
    assert not marker.exists()
    assert any("could not update the favourites" in line for line in lines)


def test_tray_repins_on_start(tray_main):
    started = tray_main()
    assert started.index("adopts the old install") < started.index("repins launchers")


def _apps(tmp_path, *names):
    """An applications folder holding the given .desktop files."""
    apps = tmp_path / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    for name in names:
        (apps / name).write_text("[Desktop Entry]\n")
    return [str(apps)]


def test_a_pin_is_left_alone_when_the_new_entry_is_not_installed(tmp_path):
    """GNOME drops a favourite whose .desktop file doesn't exist, so an old
    Flatpak's pin must not be renamed to a TuxTalk Flatpak that isn't there."""
    marker = tmp_path / "launchers-renamed"
    favs = {"v": ["org.gnome.Nautilus.desktop", "io.github.ronb1964.TalkType.desktop",
                  "talktype.desktop"]}
    scripts = []

    def plasma(script):
        scripts.append(script)
        return "0 1"
    rm.repin_launchers(str(marker), run_plasma_script=plasma,
                       gnome_favorites=(lambda: favs["v"], lambda v: favs.update(v=v)),
                       app_dirs=_apps(tmp_path, "tuxtalk.desktop"))
    assert favs["v"] == ["org.gnome.Nautilus.desktop", "io.github.ronb1964.TalkType.desktop",
                         "tuxtalk.desktop"]
    assert "io.github.ronb1964.TuxTalk.desktop" not in scripts[0].split("\n")[0]   # nor on KDE
    assert marker.read_text().split() == ["waiting", "talktype.desktop"]


def test_a_waiting_pin_does_not_rerun_every_start(tmp_path):
    """Until a new entry is installed, a start doesn't ask plasmashell again:
    that call blocks the tray, and the TuxTalk Flatpak may never arrive."""
    marker = tmp_path / "launchers-renamed"
    favs = {"v": ["io.github.ronb1964.TalkType.desktop"]}
    scripts = []

    def plasma(script):
        scripts.append(script)
        return "0 1"
    apps = _apps(tmp_path, "tuxtalk.desktop")
    gnome = (lambda: favs["v"], lambda v: favs.update(v=v))
    rm.repin_launchers(str(marker), run_plasma_script=plasma, gnome_favorites=gnome,
                       app_dirs=apps)
    assert rm.repin_launchers(str(marker), run_plasma_script=plasma, gnome_favorites=gnome,
                              app_dirs=apps) == []
    assert len(scripts) == 1

    _apps(tmp_path, "io.github.ronb1964.TuxTalk.desktop")      # the Flatpak arrives
    rm.repin_launchers(str(marker), run_plasma_script=lambda s: scripts.append(s) or "1 0",
                       gnome_favorites=gnome, app_dirs=apps)
    assert len(scripts) == 2
    assert favs["v"] == ["io.github.ronb1964.TuxTalk.desktop"]
    assert marker.read_text() == "done\n"
    assert rm.repin_launchers(str(marker), run_plasma_script=plasma, gnome_favorites=gnome,
                              app_dirs=apps) == []
    assert len(scripts) == 2


def test_an_old_done_marker_still_means_done(tmp_path):
    marker = tmp_path / "launchers-renamed"
    marker.write_text("done\n")

    def plasma(script):
        raise AssertionError("must not run")
    assert rm.repin_launchers(str(marker), run_plasma_script=plasma, gnome_favorites=None,
                              app_dirs=_apps(tmp_path, "tuxtalk.desktop")) == []


def test_the_pin_follows_once_the_new_entry_is_installed(tmp_path):
    marker = tmp_path / "launchers-renamed"
    favs = {"v": ["io.github.ronb1964.TalkType.desktop"]}
    rm.repin_launchers(str(marker), run_plasma_script=None,
                       gnome_favorites=(lambda: favs["v"], lambda v: favs.update(v=v)),
                       app_dirs=_apps(tmp_path, "io.github.ronb1964.TuxTalk.desktop"))
    assert favs["v"] == ["io.github.ronb1964.TuxTalk.desktop"]
    assert marker.exists()


def test_application_dirs_cover_xdg_and_flatpak_exports(tmp_path):
    env = {"HOME": str(tmp_path), "XDG_DATA_DIRS": "/opt/x/share:/usr/share"}
    dirs = rm.application_dirs(env)
    assert f"{tmp_path}/.local/share/applications" in dirs
    assert "/opt/x/share/applications" in dirs and "/usr/share/applications" in dirs
    assert f"{tmp_path}/.local/share/flatpak/exports/share/applications" in dirs
    assert "/var/lib/flatpak/exports/share/applications" in dirs
    env = {"HOME": str(tmp_path), "XDG_DATA_HOME": "/d"}
    dirs = rm.application_dirs(env)
    assert "/d/applications" in dirs and "/usr/share/applications" in dirs
