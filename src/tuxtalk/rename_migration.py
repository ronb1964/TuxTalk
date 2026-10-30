"""Move a TalkType install's folders to their TuxTalk names (0.15.0 rename).

TalkType became TuxTalk because "TALKTYPE" is another company's registered
trademark for dictation software. Users should notice nothing but the new name.

Runs first in every entry point (tray, dictation service, Preferences), before
anything reads a path. Each old folder is renamed to its new name and replaced
by a symlink, so an old TalkType copy that is still installed keeps working on
the same settings and models. Nothing is ever deleted, copied or overwritten:
- both names exist   -> leave both, log it, use the new one
- other disk (EXDEV) -> point the NEW name at the old folder instead of moving
"""
import errno
import os

from .logger import setup_logger

logger = setup_logger(__name__)

# Files TuxTalk creates on its own the moment it starts (the logger writes its
# log before anything else). A new folder holding only these is not "in use".
_AUTO_CREATED = {"tuxtalk.log", "tuxtalk.log.old"}


def _only_auto_created(folder):
    """True if *folder* holds nothing but files TuxTalk made by itself on start."""
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    return all(n in _AUTO_CREATED and os.path.isfile(os.path.join(folder, n)) for n in names)


# Left in the new settings folder when this start converted an old TalkType
# install; the tray then shows "TalkType is now TuxTalk" once and removes it.
# Tied to the conversion, not to a version, so someone who skips releases
# still sees it on whichever TuxTalk version first finds their settings.
NOTICE_FILE = ".rename-notice-pending"


def notice_pending(config_dir):
    """True if the "TalkType is now TuxTalk" notice is still to be shown."""
    return os.path.isfile(os.path.join(config_dir, NOTICE_FILE))


def notice_shown(config_dir):
    """Record that the rename notice has been shown."""
    try:
        os.remove(os.path.join(config_dir, NOTICE_FILE))
    except OSError:
        pass


def _ask_for_notice(folder):
    try:
        with open(os.path.join(folder, NOTICE_FILE), "w"):
            pass
    except OSError:
        pass                              # a missed message never stops startup


def PAIRS(dev=False):
    """(old, new) folder paths relative to the home folder."""
    s = "-dev" if dev else ""
    return [(f".config/talktype{s}", f".config/tuxtalk{s}"),
            (f".local/share/TalkType{s}", f".local/share/TuxTalk{s}")]


def migrate(home=None, dev=False):
    """Move the old folders; return one line per thing done, for the log.

    Never raises: a failure becomes a line, and startup carries on.
    """
    home = home or os.path.expanduser("~")
    done = []
    for old_rel, new_rel in PAIRS(dev):
        old, new = os.path.join(home, old_rel), os.path.join(home, new_rel)
        # The settings folder is what makes it someone's install.
        settings = old_rel.startswith(".config/")
        if os.path.islink(old):
            # Either our own link from an earlier run (old -> new), or the
            # user's: e.g. the models moved to a bigger disk with a symlink.
            # Follow the user's link, never skip it, or TuxTalk starts empty.
            if os.path.isdir(old) and not os.path.lexists(new):
                try:
                    os.rename(old, new)       # moves the link itself
                    os.symlink(new, old)
                    done.append(f"{old_rel} is a link to {os.readlink(new)}; "
                                f"{new_rel} now leads there too")
                    if settings:
                        _ask_for_notice(new)
                except OSError as e:
                    done.append(f"could not follow the link {old_rel}: {e}")
            continue
        if not os.path.isdir(old):
            continue                      # nothing old
        if (os.path.isdir(new) and not os.path.islink(new) and _only_auto_created(new)):
            # Fold the fresh log into the old folder, so the move below keeps it.
            for n in os.listdir(new):
                if not os.path.exists(os.path.join(old, n)):
                    os.rename(os.path.join(new, n), os.path.join(old, n))
            try:
                os.rmdir(new)
            except OSError:
                pass
        if os.path.islink(new) and os.path.realpath(new) == os.path.realpath(old):
            continue                      # the other-disk link from an earlier run
        if os.path.lexists(new):
            done.append(f"{old_rel} and {new_rel} both exist; "
                        f"using {new_rel}, left {old_rel} alone")
            continue
        try:
            os.makedirs(os.path.dirname(new), exist_ok=True)
            os.rename(old, new)
            os.symlink(new, old)
            done.append(f"moved {old_rel} to {new_rel}")
            if settings:
                _ask_for_notice(new)
        except OSError as e:
            # EXDEV: another disk; EBUSY: the old path is a mount point.
            if e.errno not in (errno.EXDEV, errno.EBUSY):
                done.append(f"could not move {old_rel}: {e}")
                continue
            # A rename can't cross disks, and copying gigabytes of models at
            # startup is not acceptable. Let the new name lead to the old folder.
            try:
                os.symlink(old, new)
                done.append(f"{old_rel} can't be moved ({e.strerror}); {new_rel} now points to it")
                if settings:
                    _ask_for_notice(new)
            except OSError as e2:
                done.append(f"could not link {new_rel}: {e2}")
    return done


def run_at_startup(environ=None, home=None):
    """The package-import entry point. Off in the test suite
    (TUXTALK_NO_MIGRATE) and in the Flatpak, whose folders live in its own
    sandbox and never had the old names on the host."""
    environ = os.environ if environ is None else environ
    if environ.get("TUXTALK_NO_MIGRATE") or environ.get("FLATPAK_ID"):
        return []
    try:
        return migrate(home=home, dev=environ.get("DEV_MODE") == "1")
    except Exception as e:      # never stop the app from starting
        logger.warning(f"Rename to TuxTalk: moving the folders failed: {e}")
        return []


# Where an old TalkType AppImage path now lives, best match first: the AUR
# package moved /opt/talktype/TalkType.AppImage to /opt/tuxtalk/TuxTalk.AppImage.
def _new_appimage_paths(old_path):
    if old_path == "/opt/talktype/TalkType.AppImage":
        yield "/opt/tuxtalk/TuxTalk.AppImage"
    if old_path.endswith("/TalkType.AppImage"):
        yield old_path[: -len("TalkType.AppImage")] + "TuxTalk.AppImage"


def _rewrite_exec(line):
    """Point Exec at the TuxTalk AppImage only where that file exists; an
    entry that would start nothing is worse than one with the old name."""
    cmd = line[len("Exec="):].rstrip("\n")
    target, _, rest = cmd.partition(" ")
    for new in _new_appimage_paths(target):
        if os.path.exists(new):
            return "Exec=" + new + ((" " + rest) if rest else "") + "\n"
    return line


def _rewrite_entry(text, dev):
    """A TalkType .desktop entry with TuxTalk's name, AppImage and description.
    Everything else (launch-at-login on or off above all) is kept as it was."""
    from .autostart import AUTOSTART_COMMENT, AUTOSTART_COMMENT_DEV, _OLD_COMMENTS
    out = []
    for line in text.splitlines(keepends=True):
        if line.startswith("Name=TalkType"):
            line = "Name=TuxTalk" + line[len("Name=TalkType"):]
        elif line.startswith("Exec="):
            line = _rewrite_exec(line)
        elif line.rstrip("\n") in (f"Comment={c}" for c in _OLD_COMMENTS):
            line = f"Comment={AUTOSTART_COMMENT_DEV if dev else AUTOSTART_COMMENT}\n"
        out.append(line)
    return "".join(out)


def _install_tuxtalk_icon():
    """TuxTalk's icon installed for the menu ("tuxtalk"), or None if it
    couldn't be: then an entry keeps the old icon rather than a blank one."""
    from .desktop_launcher import install_app_icon
    return install_app_icon()


def _refresh_renamed_entries(home):
    """Finish entries an earlier start already renamed: point Exec at
    TuxTalk.AppImage once that file exists, and give them TuxTalk's icon.
    A first start from a download folder renames the entries but not the
    AppImage, so the real update arriving later must still repoint them."""
    done, icon = [], ...
    for folder in (".config/autostart", ".local/share/applications"):
        path = os.path.join(home, folder, "tuxtalk.desktop")
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                text = f.read()
            out = []
            for line in text.splitlines(keepends=True):
                if line.startswith("Exec="):
                    line = _rewrite_exec(line)
                elif line.rstrip("\n") == "Icon=talktype":
                    if icon is ...:
                        icon = _install_tuxtalk_icon()
                    if icon:
                        line = f"Icon={icon}\n"
                out.append(line)
            new_text = "".join(out)
            if new_text != text:
                tmp = path + ".tmp"
                with open(tmp, "w") as f:
                    f.write(new_text)
                os.replace(tmp, path)
                done.append(f"updated {folder}/tuxtalk.desktop for the TuxTalk AppImage and icon")
        except OSError as e:
            done.append(f"could not update {folder}/tuxtalk.desktop: {e}")
    return done


def adopt_old_install(home=None, dev=False, running_appimage=None):
    """Move an installed TalkType's AppImage, menu launcher and launch-at-login
    entry to TuxTalk's names. Called by the tray on start; returns log lines.

    A 0.14.x updater saves the new version as ~/AppImages/TalkType.AppImage and
    restarts it, so this is how an updated install ends up fully renamed. The
    AppImage is renamed (safe while it runs) and a symlink keeps the old path
    working; an existing TuxTalk.AppImage is never overwritten, and only the
    AppImage that is actually running is renamed (*running_appimage*, the
    APPIMAGE variable): a downloaded TuxTalk run beside an installed older
    TalkType must not turn that old file into the installed TuxTalk. The .desktop
    entries are rewritten under the new name and the old ones removed, so the
    menu never lists both and login never starts both.
    """
    home = home or os.path.expanduser("~")
    if running_appimage is None:
        running_appimage = os.environ.get("APPIMAGE", "")
    done = []
    if not dev:
        old = os.path.join(home, "AppImages", "TalkType.AppImage")
        new = os.path.join(home, "AppImages", "TuxTalk.AppImage")
        is_running = bool(running_appimage) and os.path.exists(old) and \
            os.path.realpath(running_appimage) == os.path.realpath(old)
        if (is_running and os.path.isfile(old) and not os.path.islink(old)
                and not os.path.lexists(new)):
            try:
                os.rename(old, new)
                os.symlink(new, old)
                done.append("renamed ~/AppImages/TalkType.AppImage to TuxTalk.AppImage")
            except OSError as e:
                done.append(f"could not rename the AppImage: {e}")
    suffix = "-dev" if dev else ""
    for folder in (".config/autostart", ".local/share/applications"):
        old = os.path.join(home, folder, f"talktype{suffix}.desktop")
        new = os.path.join(home, folder, f"tuxtalk{suffix}.desktop")
        if not os.path.isfile(old):
            continue
        try:
            if not os.path.exists(new):
                with open(old) as f:
                    text = _rewrite_entry(f.read(), dev)
                tmp = new + ".tmp"
                with open(tmp, "w") as f:
                    f.write(text)
                os.replace(tmp, new)
                done.append(f"moved {folder}/talktype{suffix}.desktop to tuxtalk{suffix}.desktop")
            else:
                done.append(f"removed {folder}/talktype{suffix}.desktop "
                            f"(tuxtalk{suffix}.desktop exists)")
            os.remove(old)
        except OSError as e:
            done.append(f"could not move {folder}/talktype{suffix}.desktop: {e}")
    if not dev:
        done += _refresh_renamed_entries(home)
    return done


# ---------------------------------------------------------------------------
# Pinned dock and favourite entries
# ---------------------------------------------------------------------------
# The menu entries were renamed (adopt_old_install above; the .deb/.rpm and
# AUR packages install the new names). A dock that pinned the old name shows a
# blank, generic icon on KDE and GNOME silently drops it from the favourites,
# so those lists are rewritten.

LAUNCHER_RENAMES = {
    "talktype.desktop": "tuxtalk.desktop",
    "talktype-dev.desktop": "tuxtalk-dev.desktop",
    "io.github.ronb1964.TalkType.desktop": "io.github.ronb1964.TuxTalk.desktop",
}


def application_dirs(environ=None):
    """The folders a desktop looks in for .desktop files: XDG_DATA_HOME and
    XDG_DATA_DIRS, plus the Flatpak exports in case XDG_DATA_DIRS lacks them."""
    env = os.environ if environ is None else environ
    home = env.get("HOME") or os.path.expanduser("~")
    data_home = env.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    data_dirs = (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs = []
    for base in [data_home, *data_dirs,
                 os.path.join(home, ".local", "share", "flatpak", "exports", "share"),
                 "/var/lib/flatpak/exports/share"]:
        path = os.path.join(base, "applications")
        if base and path not in dirs:
            dirs.append(path)
    return dirs


def installed_renames(app_dirs=None):
    """The renames whose new .desktop file exists. A desktop drops a pin it
    can't find a file for, so an old pin stays as it is until then."""
    dirs = application_dirs() if app_dirs is None else app_dirs
    return {old: new for old, new in LAUNCHER_RENAMES.items()
            if any(os.path.isfile(os.path.join(d, new)) for d in dirs)}


def _renamed(item, renames=LAUNCHER_RENAMES):
    """'talktype.desktop' or 'applications:talktype.desktop' under its new name."""
    prefix, _, rest = item.rpartition(":")
    if rest in renames:
        return (prefix + ":" if prefix else "") + renames[rest]
    return item


def waiting_launchers(items, renames):
    """Old pinned entries left alone because their new entry isn't installed."""
    return [item for item in items
            if item.rpartition(":")[2] in LAUNCHER_RENAMES
            and item.rpartition(":")[2] not in renames]


def renamed_launchers(items, renames=LAUNCHER_RENAMES):
    """(new list, changed) for a pinned list. A renamed entry already pinned
    under its new name is dropped rather than listed twice."""
    items = list(items)
    out = []
    for item in items:
        new = _renamed(item, renames)
        if new != item and (new in out or new in items):
            continue
        out.append(new)
    return out, out != items


def _plasma_script(renames=LAUNCHER_RENAMES):
    """Plasma desktop script: rename the pinned launchers in every task manager.
    It prints how many it changed and how many old pins it left waiting."""
    import json
    old = [f"applications:{a}" for a in LAUNCHER_RENAMES]
    renames = {f"applications:{a}": f"applications:{b}" for a, b in renames.items()}
    head = f"var renames = {json.dumps(renames)};\nvar old = {json.dumps(old)};\n"
    return (head + r"""
var changed = 0, waiting = 0;
function fix(w) {
    if (w.type.indexOf("taskmanager") < 0 && w.type.indexOf("icontasks") < 0) return;
    w.currentConfigGroup = ["General"];
    var l = w.readConfig("launchers", []);
    if (typeof l === "string") l = l.length ? l.split(",") : [];
    var out = [], did = false;
    for (var i = 0; i < l.length; i++) {
        var n = renames[l[i]] || l[i];
        if (n === l[i] && old.indexOf(l[i]) >= 0) waiting++;
        if (n !== l[i]) {
            did = true;
            if (out.indexOf(n) >= 0 || l.indexOf(n) >= 0) continue;
        }
        out.push(n);
    }
    if (did) { w.writeConfig("launchers", out); changed++; }
}
panels().forEach(function (p) { p.widgets().forEach(fix); });
desktops().forEach(function (d) { d.widgets().forEach(fix); });
print(changed + " " + waiting);
""")


def _default_plasma_runner():
    """Runs a Plasma script on a KDE session; None anywhere else."""
    if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        return None

    def run(script):
        import dbus
        # A private connection: this runs before the tray sets the GLib main
        # loop, and the shared one opened now would lack it, so exporting the
        # tray's D-Bus service on it would fail for the whole session.
        bus = dbus.SessionBus(private=True)
        try:
            shell = bus.get_object("org.kde.plasmashell", "/PlasmaShell")
            return str(shell.evaluateScript(script, dbus_interface="org.kde.PlasmaShell"))
        finally:
            bus.close()
    return run


def _default_gnome_favorites():
    """(get, set) for GNOME's favourite apps; None when GNOME isn't there."""
    from gi.repository import Gio
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.gnome.shell", True) is None:
        return None
    settings = Gio.Settings.new("org.gnome.shell")
    return (lambda: list(settings.get_strv("favorite-apps")),
            lambda value: settings.set_strv("favorite-apps", value))


_DEFAULT = object()


def _marker_waiting_on(marker_path):
    """The renames that were installed when the marker was written with an old
    pin still waiting, or None when the marker means everything is done."""
    try:
        with open(marker_path) as f:
            lines = f.read().split()
    except OSError:
        return None
    if not lines or lines[0] != "waiting":
        return None
    return set(lines[1:])


def _repin_gnome(gnome_favorites, renames, done):
    """repin_launchers' GNOME part: rename the old pins in the favourites,
    noting what happened in *done*. Returns (failed, waiting)."""
    get, put = gnome_favorites
    try:
        new, changed = renamed_launchers(get(), renames)
        if changed:
            # Gio says False, changing nothing, where the key is locked.
            if put(new) is False:
                raise OSError("GNOME didn't take the change (locked favourites?)")
            done.append("GNOME: renamed TuxTalk in the favourites")
        if waiting_launchers(new, renames):
            done.append("GNOME: left an old favourite alone; the new entry isn't installed")
            return False, True
    except Exception as e:
        # Logged here, not also in *done*: it's tried at every start, and
        # one line a start is enough.
        logger.info(f"GNOME favourites not updated yet: {e}")
        return True, False
    return False, False


def repin_launchers(marker_path, run_plasma_script=_DEFAULT, gnome_favorites=_DEFAULT,
                    app_dirs=None):
    """Rename pinned TalkType launchers on the KDE dock and in GNOME's
    favourites, once. The marker file records success; after a failure (say
    plasmashell wasn't up yet at login) the next start tries again. While an
    old pin waits for its new entry, the marker lists the entries installed
    then, and only a newly installed one makes a start try again."""
    renames = None
    if os.path.exists(marker_path):
        waiting_on = _marker_waiting_on(marker_path)
        if waiting_on is None:
            return []
        renames = installed_renames(app_dirs)
        if set(renames) <= waiting_on:
            return []
    if run_plasma_script is _DEFAULT:
        run_plasma_script = _default_plasma_runner()
    if gnome_favorites is _DEFAULT:
        gnome_favorites = _default_gnome_favorites()
    if renames is None:
        renames = installed_renames(app_dirs)
    done, failed, waiting = [], False, False
    if run_plasma_script:
        try:
            counts = str(run_plasma_script(_plasma_script(renames))).split()
            n = counts[0] if counts else "0"
            left = int(counts[1]) if len(counts) > 1 else 0
            if n != "0":
                done.append(f"KDE: renamed pinned launchers in {n} task manager(s)")
            if left:
                waiting = True
                done.append(f"KDE: left {left} old pin(s) alone; the new entry isn't installed")
        except Exception as e:      # plasmashell may not be up yet: the next start tries again
            # Logged here, not also in *done*, as GNOME's failure is.
            logger.info(f"KDE pinned launchers not updated yet: {e}")
            failed = True
    if gnome_favorites:
        gnome_failed, gnome_waiting = _repin_gnome(gnome_favorites, renames, done)
        failed, waiting = failed or gnome_failed, waiting or gnome_waiting
    if not failed:
        content = "done\n"
        if waiting:
            content = "\n".join(["waiting", *sorted(renames)]) + "\n"
        try:
            os.makedirs(os.path.dirname(marker_path), exist_ok=True)
            with open(marker_path, "w") as f:
                f.write(content)
        except OSError:
            pass
    return done
