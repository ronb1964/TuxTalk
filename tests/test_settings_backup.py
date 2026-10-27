"""Settings backup: a file to move to another computer, and automatic copies
to undo a mistake (settings_backup.py)."""
import datetime
import json
import os
import zipfile

import pytest

from tuxtalk import config, settings_backup as sb, stats


CONFIG_TEXT = '# TalkType config\nhotkey = "F8"\nmodel = "parakeet-v3"\ndevice = "vulkan"\n'
COMMANDS_TEXT = '[commands]\n"my email" = "ron@example.com"\n'


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Settings files in a temporary folder, every graphics device working."""
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "cfg" / "config.toml"))
    monkeypatch.setattr(config, "CUSTOM_COMMANDS_PATH", str(tmp_path / "cfg" / "custom_commands.toml"))
    monkeypatch.setattr(stats, "stats_path", lambda: str(tmp_path / "data" / "stats.json"))
    monkeypatch.setattr(sb, "_device_works_here", lambda device: True)
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "config.toml").write_text(CONFIG_TEXT)
    (tmp_path / "cfg" / "custom_commands.toml").write_text(COMMANDS_TEXT)
    return tmp_path


def read(path):
    with open(path) as f:
        return f.read()


def set_stats(days):
    stats._write(days)


def day(words):
    return {"dictations": 1, "words": words, "seconds": 1.0}


# --- the backup file ---------------------------------------------------------

def test_a_saved_backup_loads_back(home):
    set_stats({"2026-10-01": day(50)})
    dest = home / "TalkType-settings.zip"
    sb.write_backup(str(dest))

    # Everything changes after the backup...
    with open(config.CONFIG_PATH, "w") as f:
        f.write('hotkey = "F9"\n')
    with open(config.CUSTOM_COMMANDS_PATH, "w") as f:
        f.write("[commands]\n")

    backup = sb.read_backup(str(dest))
    assert backup.version
    sb.restore(backup)

    # ...and loading it brings it all back.
    assert read(config.CONFIG_PATH) == CONFIG_TEXT
    assert read(config.CUSTOM_COMMANDS_PATH) == COMMANDS_TEXT


def test_the_backup_is_a_plain_zip_with_the_settings_inside(home):
    dest = home / "b.zip"
    sb.write_backup(str(dest))
    with zipfile.ZipFile(dest) as z:
        names = set(z.namelist())
    assert {"config.toml", "custom_commands.toml", sb.MANIFEST} <= names


def test_a_backup_without_commands_still_works(home):
    os.remove(config.CUSTOM_COMMANDS_PATH)
    dest = home / "b.zip"
    sb.write_backup(str(dest))
    sb.restore(sb.read_backup(str(dest)))
    assert read(config.CONFIG_PATH) == CONFIG_TEXT


@pytest.mark.parametrize("make", [
    lambda p: p.write_text("not a zip at all"),
    lambda p: zipfile.ZipFile(p, "w").writestr("photo.jpg", b"x"),           # someone else's zip
    lambda p: _zip(p, {sb.MANIFEST: json.dumps({"app": "TalkType"})}),      # no settings inside
    lambda p: _zip(p, {sb.MANIFEST: json.dumps({"app": "TalkType"}),
                       "config.toml": 'hotkey = "F8'}),                      # damaged settings
])
def test_a_wrong_or_broken_file_is_turned_away(home, make):
    bad = home / "bad.zip"
    make(bad)
    with pytest.raises(sb.BackupError):
        sb.read_backup(str(bad))
    assert read(config.CONFIG_PATH) == CONFIG_TEXT      # nothing was touched


def _zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)


# --- a different computer ----------------------------------------------------

def test_a_device_this_computer_lacks_keeps_this_computers_device(home, monkeypatch):
    dest = home / "b.zip"
    with open(config.CONFIG_PATH, "w") as f:
        f.write(CONFIG_TEXT.replace('"vulkan"', '"cuda"'))
    sb.write_backup(str(dest))

    # The new computer: no NVIDIA card, currently on the processor.
    with open(config.CONFIG_PATH, "w") as f:
        f.write('hotkey = "F12"\ndevice = "cpu"\n')
    monkeypatch.setattr(sb, "_device_works_here", lambda device: device != "cuda")

    notes = sb.restore(sb.read_backup(str(dest)))
    text = read(config.CONFIG_PATH)
    assert 'device = "cpu"' in text and 'hotkey = "F8"' in text
    assert any("CUDA" in n for n in notes)


def test_a_device_that_works_here_comes_across(home):
    dest = home / "b.zip"
    sb.write_backup(str(dest))
    with open(config.CONFIG_PATH, "w") as f:
        f.write('device = "cpu"\n')
    assert sb.restore(sb.read_backup(str(dest))) == []
    assert 'device = "vulkan"' in read(config.CONFIG_PATH)


# --- stats -------------------------------------------------------------------

def test_loading_a_backup_merges_stats_and_keeps_the_higher_count(home):
    set_stats({"2026-10-01": day(50), "2026-10-02": day(10)})
    dest = home / "b.zip"
    sb.write_backup(str(dest))
    # Meanwhile this computer dictated more on the 2nd, and on a new day.
    set_stats({"2026-10-02": day(80), "2026-10-03": day(5)})

    sb.restore(sb.read_backup(str(dest)))
    days = stats.load()
    assert days["2026-10-01"]["words"] == 50     # from the backup
    assert days["2026-10-02"]["words"] == 80     # the higher one
    assert days["2026-10-03"]["words"] == 5      # kept


def test_restoring_an_automatic_copy_leaves_stats_alone(home):
    set_stats({"2026-10-01": day(50)})
    sb.snapshot_if_changed()
    set_stats({"2026-10-01": day(70)})
    sb.restore(sb.read_backup(sb.list_snapshots()[0].path))
    assert stats.load()["2026-10-01"]["words"] == 70


# --- automatic copies --------------------------------------------------------

def test_a_copy_is_taken_only_when_the_settings_changed(home):
    assert sb.snapshot_if_changed() is not None
    assert sb.snapshot_if_changed() is None             # same settings
    with open(config.CONFIG_PATH, "a") as f:
        f.write('language = "en"\n')
    assert sb.snapshot_if_changed() is not None
    assert len(sb.list_snapshots()) == 2


def test_only_the_last_ten_copies_are_kept(home, monkeypatch):
    clock = [datetime.datetime(2026, 10, 1, 8, 0, 0)]
    monkeypatch.setattr(sb, "_now", lambda: clock[0])
    for i in range(13):
        with open(config.CONFIG_PATH, "a") as f:
            f.write(f"# change {i}\n")
        sb.snapshot_if_changed()
        clock[0] += datetime.timedelta(minutes=1)
    copies = sb.list_snapshots()
    assert len(copies) == sb.KEEP == 10
    assert copies[0].when > copies[-1].when          # newest first
    assert "# change 12" in sb.read_backup(copies[0].path).files["config.toml"].decode()


def test_restoring_saves_the_current_settings_first_so_it_can_be_undone(home):
    sb.snapshot_if_changed()
    old = sb.list_snapshots()[0]
    with open(config.CONFIG_PATH, "w") as f:
        f.write('hotkey = "F9"\n')

    sb.restore(sb.read_backup(old.path))

    assert read(config.CONFIG_PATH) == CONFIG_TEXT
    newest = sb.list_snapshots()[0]
    assert sb.read_backup(newest.path).files["config.toml"] == b'hotkey = "F9"\n'


def test_no_settings_yet_means_no_copy(home):
    os.remove(config.CONFIG_PATH)
    assert sb.snapshot_if_changed() is None
    assert sb.list_snapshots() == []


def test_a_broken_copy_in_the_folder_is_skipped_not_fatal(home):
    sb.snapshot_if_changed()
    folder = sb.snapshots_dir()
    with open(os.path.join(folder, "settings-20261001-080000.zip"), "w") as f:
        f.write("junk")
    with open(os.path.join(folder, "notes.txt"), "w") as f:
        f.write("not a copy")
    assert len(sb.list_snapshots()) == 2           # listed by name; read on restore
    with open(config.CONFIG_PATH, "a") as f:
        f.write("# changed\n")
    assert sb.snapshot_if_changed() is not None    # the junk one didn't stop it


def test_copies_are_named_the_way_people_say_dates():
    now = datetime.datetime(2026, 10, 11, 9, 0)
    assert sb.when_label(datetime.datetime(2026, 10, 11, 8, 43), now, twenty_four=False) == "Today 8:43 AM"
    assert sb.when_label(datetime.datetime(2026, 10, 10, 21, 50), now, twenty_four=False) == "Yesterday 9:50 PM"
    assert sb.when_label(datetime.datetime(2026, 10, 2, 7, 5), now, twenty_four=False) == "Oct 2, 2026, 7:05 AM"


def test_last_saved_is_noted_and_doesnt_show_up_as_a_copy(home):
    assert sb.last_saved() is None
    sb.write_backup(str(home / "b.zip"))
    assert sb.last_saved() is not None
    assert sb.list_snapshots() == []


def test_the_backup_folder_is_remembered(home):
    folder = home / "Documents" / "TalkType backups"
    folder.mkdir(parents=True)
    assert sb.backup_folder() is None
    sb.set_backup_folder(str(folder))
    assert sb.backup_folder() == str(folder)
    assert sb.list_snapshots() == []        # not mistaken for a copy


def test_a_backup_folder_that_was_deleted_falls_back_to_none(home):
    folder = home / "gone"
    folder.mkdir()
    sb.set_backup_folder(str(folder))
    folder.rmdir()
    assert sb.backup_folder() is None


# --- one list: automatic copies and the user's own backup files --------------

def _backup_file(folder, name, when, monkeypatch):
    monkeypatch.setattr(sb, "_now", lambda: when)
    sb.write_backup(str(folder / name))


def test_the_list_holds_copies_and_backup_files_newest_first(home, monkeypatch):
    folder = home / "TalkType backups"
    folder.mkdir()
    sb.set_backup_folder(str(folder))
    monkeypatch.setattr(sb, "_now", lambda: datetime.datetime(2026, 10, 11, 8, 58))
    sb.snapshot_if_changed()
    _backup_file(folder, "a.zip", datetime.datetime(2026, 10, 11, 9, 15), monkeypatch)
    _backup_file(folder, "b.zip", datetime.datetime(2026, 10, 10, 20, 0), monkeypatch)
    (folder / "holiday-photos.zip").write_bytes(b"not ours")      # someone else's zip
    (folder / "notes.txt").write_text("hi")

    points = sb.restore_points()
    assert [(p.when.hour, p.kind) for p in points] == [(9, "file"), (8, "auto"), (20, "file")]


def test_without_a_backup_folder_the_list_is_the_copies(home):
    sb.snapshot_if_changed()
    assert [p.kind for p in sb.restore_points()] == ["auto"]


def test_the_list_is_kept_short(home, monkeypatch):
    folder = home / "f"
    folder.mkdir()
    sb.set_backup_folder(str(folder))
    for i in range(20):
        _backup_file(folder, f"{i}.zip", datetime.datetime(2026, 10, 1, 8, i), monkeypatch)
    points = sb.restore_points()
    assert len(points) == sb.LIST_MAX
    assert points[0].when.minute == 19                  # the newest ones


def test_suggested_file_names_differ_within_a_day(monkeypatch):
    monkeypatch.setattr(sb, "_now", lambda: datetime.datetime(2026, 10, 11, 9, 15))
    first = sb.suggested_name()
    monkeypatch.setattr(sb, "_now", lambda: datetime.datetime(2026, 10, 11, 9, 41))
    assert first == "TuxTalk-settings-2026-10-11-0915.zip" != sb.suggested_name()


def test_times_follow_a_24_hour_clock_where_people_use_one():
    now = datetime.datetime(2026, 10, 11, 23, 0)
    when = datetime.datetime(2026, 10, 11, 21, 50)
    assert sb.when_label(when, now, twenty_four=True) == "Today 21:50"
    assert sb.when_label(when, now, twenty_four=False) == "Today 9:50 PM"
    assert sb.when_label(datetime.datetime(2026, 10, 2, 7, 5), now, twenty_four=True) == "Oct 2, 2026, 07:05"


@pytest.mark.parametrize("fmt, expected", [
    ("%r", False),          # en_US
    ("%I:%M:%S %p", False),
    ("%T", True),           # de_DE, en_GB, fr_FR
    ("%H:%M:%S", True),
])
def test_the_clock_is_read_from_the_region_setting(monkeypatch, fmt, expected):
    import locale
    monkeypatch.setattr(locale, "nl_langinfo", lambda item: fmt)
    monkeypatch.setattr(locale, "setlocale", lambda *a: "")
    assert sb._uses_24_hour_clock() is expected


# --- backups made before the rename (TalkType, up to 0.14.x) -----------------

def _as_talktype_backup(path):
    """Rewrite a fresh backup's manifest the way TalkType wrote it."""
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = json.loads(files[sb.MANIFEST])
    manifest["app"] = "TalkType"
    files[sb.MANIFEST] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)


def test_a_backup_saved_by_talktype_still_loads(home):
    dest = home / "TalkType-settings-2026-10-11-1200.zip"
    sb.write_backup(str(dest))
    _as_talktype_backup(dest)
    with open(config.CONFIG_PATH, "w") as f:
        f.write('hotkey = "F9"\n')
    sb.restore(sb.read_backup(str(dest)))
    assert read(config.CONFIG_PATH) == CONFIG_TEXT


def test_talktype_backups_are_listed_with_the_others(home, monkeypatch):
    folder = home / "backups"
    folder.mkdir()
    monkeypatch.setattr(sb, "backup_folder", lambda: str(folder))
    dest = folder / "TalkType-settings-2026-10-11-1200.zip"
    sb.write_backup(str(dest))
    _as_talktype_backup(dest)
    assert str(dest) in [p.path for p in sb.restore_points()]


def test_your_words_travel_with_the_backup(home):
    """custom_words.txt (Preferences > Snippets > Your Words) is in the backup
    and comes back with it. A backup from before Your Words leaves them be."""
    from tuxtalk import custom_words
    custom_words.save(["TuxTalk", "BambuStudio"])
    dest = home / "b.zip"
    sb.write_backup(str(dest))
    custom_words.save(["Other"])
    sb.restore(sb.read_backup(str(dest)))
    assert custom_words.load() == ["TuxTalk", "BambuStudio"]

    _zip(home / "old.zip", {sb.MANIFEST: json.dumps({"app": "TalkType"}),
                            "config.toml": CONFIG_TEXT})
    sb.restore(sb.read_backup(str(home / "old.zip")))
    assert custom_words.load() == ["TuxTalk", "BambuStudio"]


# --- damaged files and failed writes -----------------------------------------

def _corrupt_member(path, name):
    """Overwrite the start of *name*'s compressed data with an invalid
    deflate block, which zipfile reports as zlib.error, not BadZipFile."""
    with zipfile.ZipFile(path) as z:
        info = z.getinfo(name)
    with open(path, "r+b") as f:
        f.seek(info.header_offset + 26)
        name_len = int.from_bytes(f.read(2), "little")
        extra_len = int.from_bytes(f.read(2), "little")
        f.seek(info.header_offset + 30 + name_len + extra_len)
        f.write(b"\xff\xff\xff\xff")


def test_damaged_compressed_data_is_turned_away_with_a_message(home):
    dest = home / "b.zip"
    sb.write_backup(str(dest))
    _corrupt_member(dest, "config.toml")
    with pytest.raises(sb.BackupError):
        sb.read_backup(str(dest))


def test_a_huge_member_is_turned_away_before_it_is_read(home, monkeypatch):
    dest = home / "b.zip"
    sb.write_backup(str(dest))
    monkeypatch.setattr(sb, "_MAX_MEMBER_BYTES", 10)
    with pytest.raises(sb.BackupError):
        sb.read_backup(str(dest))


def test_a_damaged_stats_file_is_left_alone_by_a_restore(home):
    """Loading a backup must not replace an unreadable (maybe recoverable)
    stats file with only the backup's days."""
    set_stats({"2026-10-01": day(50)})
    dest = home / "b.zip"
    sb.write_backup(str(dest))
    damaged = "{ not json"
    with open(stats.stats_path(), "w") as f:
        f.write(damaged)

    notes = sb.restore(sb.read_backup(str(dest)))

    assert read(stats.stats_path()) == damaged
    assert any("stats" in n for n in notes)
    assert read(config.CONFIG_PATH) == CONFIG_TEXT      # the settings still came across


def test_a_failed_stats_write_does_not_suggest_resetting_stats(home, monkeypatch):
    """A full disk while saving the merged stats leaves the stats file fine,
    so the note must not tell the user to reset it."""
    set_stats({"2026-10-01": day(50)})
    dest = home / "b.zip"
    sb.write_backup(str(dest))

    def full_disk(theirs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(stats, "merge_days", full_disk)

    notes = sb.restore(sb.read_backup(str(dest)))

    assert any("couldn't be saved" in n for n in notes)
    assert not any("Reset Stats" in n for n in notes)
    assert read(config.CONFIG_PATH) == CONFIG_TEXT      # the settings still came across


def test_nothing_is_restored_when_the_undo_copy_cannot_be_saved(home, monkeypatch):
    """If the copy that lets a restore be undone can't be saved, the settings
    are not replaced."""
    dest = home / "b.zip"
    sb.write_backup(str(dest))
    backup = sb.read_backup(str(dest))
    with open(config.CONFIG_PATH, "w") as f:
        f.write('hotkey = "F9"\n')

    def full_disk(path, files):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(sb, "_write_zip", full_disk)

    with pytest.raises(sb.BackupError):
        sb.restore(backup)
    assert read(config.CONFIG_PATH) == 'hotkey = "F9"\n'
    assert sb.snapshot_if_changed() is None             # startup copy still never raises



def test_a_restore_that_fails_partway_puts_the_settings_back(home, monkeypatch):
    """A full disk on the second file: the settings were the backup's and the
    commands the old ones, a mix nobody chose."""
    dest = home / "b.zip"
    sb.write_backup(str(dest))
    backup = sb.read_backup(str(dest))
    with open(config.CONFIG_PATH, "w") as f:
        f.write('hotkey = "F9"\n')
    write = config.write_text_atomic

    def full_on_the_commands(path, text, **kw):
        if path == config.CUSTOM_COMMANDS_PATH and text != COMMANDS_TEXT:
            raise OSError(28, "No space left on device")
        write(path, text, **kw)
    monkeypatch.setattr(config, "write_text_atomic", full_on_the_commands)
    backup.files["custom_commands.toml"] = b"# the backup's commands\n"
    with pytest.raises(sb.BackupError, match="as they were"):
        sb.restore(backup)
    assert read(config.CONFIG_PATH) == 'hotkey = "F9"\n'
    assert read(config.CUSTOM_COMMANDS_PATH) == COMMANDS_TEXT

@pytest.mark.parametrize("problem", [sb.BackupError("This isn't a TuxTalk backup."),
                                     OSError(28, "No space left on device")])
def test_preferences_shows_a_refused_restore_as_a_message(prefs_window, monkeypatch, problem):
    """Preferences stays open and says why; nothing restarts."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from gtk_text import tree_text
    shown = []

    def refuse(path):
        raise problem
    monkeypatch.setattr(sb, "restore", refuse)
    monkeypatch.setattr(Gtk.Dialog, "run", lambda d: shown.append(tree_text(d)))
    window = prefs_window()
    monkeypatch.setattr(window, "restart_service", lambda: pytest.fail("restarted"))
    window.updates_tab._restore_backup("/backups/tuxtalk-settings.zip")
    assert len(shown) == 1
    assert "The settings weren't restored" in shown[0] and str(problem) in shown[0]
    assert window.window.get_visible()


def test_saving_a_backup_makes_its_folder_the_backup_folder(home, monkeypatch):
    """The folder a backup is saved to becomes the backup folder, where the
    next one goes and where Earlier settings looks; the Backup folder row can
    still set it outright."""
    from types import SimpleNamespace
    from tuxtalk.prefs_updates import UpdatesTab
    folder = home / "USB stick"
    folder.mkdir()
    shown = []
    monkeypatch.setattr("tuxtalk.prefs_updates.show_message", lambda *a, **k: shown.append(a[2]))
    window = SimpleNamespace(
        window=None, _pick_file=lambda title, save_name=None: str(folder / "tuxtalk-backup"),
        _show_last_backup=lambda: None, _fill_restore_list=lambda: None,
        _show_backup_folder=lambda: None)
    UpdatesTab._on_save_backup_clicked(window, None)
    assert shown == ["Backup saved"]
    assert sb.backup_folder() == str(folder)
    assert [p.kind for p in sb.restore_points()] == ["file"]


def test_the_backup_folder_can_be_chosen_outright(home, monkeypatch):
    """Ron, 2026-10-18: removing the Backup folder row left no way to pick
    the location except by saving a backup there."""
    from types import SimpleNamespace
    from tuxtalk.prefs_updates import UpdatesTab
    folder = home / "Backups"
    folder.mkdir()
    refreshed = []
    monkeypatch.setattr("tuxtalk.prefs_updates.show_message", lambda *a, **k: pytest.fail("a message"))
    window = SimpleNamespace(
        _pick_file=lambda title, save_name=None, folder_only=False: str(folder) if folder_only else None,
        window=None,
        _show_backup_folder=lambda: refreshed.append("folder"),
        _fill_restore_list=lambda: refreshed.append("list"))
    UpdatesTab._on_change_backup_folder_clicked(window, None)
    assert sb.backup_folder() == str(folder)
    assert refreshed == ["folder", "list"]
