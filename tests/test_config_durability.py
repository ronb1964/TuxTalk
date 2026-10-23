"""Tests for settings surviving crashes, full disks and bad values.

The settings file holds the hotkey. If it is lost or reset, dictation stops
working entirely — and because "setup already done" lives in a *different*
file, onboarding does not re-run to fix it. The user is left with a tray icon
that does nothing and no explanation.
"""

import os

import pytest

from tuxtalk import config


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    """Point the config module at a throwaway file."""
    path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))
    monkeypatch.setattr(config, "_config_cache", None)
    monkeypatch.setattr(config, "_config_mtime", 0.0)
    # Per-process latches: real processes start fresh, tests must too.
    monkeypatch.setattr(config, "_repair_attempted", False)
    monkeypatch.setattr(config, "_config_read_failed", False)
    for latch in ("_error_printed", "_invalid_reported", "_read_error_printed"):
        if hasattr(config.load_config, latch):
            delattr(config.load_config, latch)
    return path


# --- atomic writes ---------------------------------------------------------

def test_save_never_leaves_a_truncated_file(config_path, monkeypatch):
    """A full disk mid-write must not leave the file empty, which would lose
    every setting including the hotkey."""
    settings = config.Settings()
    settings.model = "large-v3"
    config.save_config(settings)
    original = config_path.read_text()
    assert "large-v3" in original

    # Fail the write before it can be renamed into place, as a full disk would.
    def no_space(*args, **kwargs):
        raise OSError(28, "No space left on device")

    settings.model = "tiny"
    with monkeypatch.context() as m, pytest.raises(OSError):
        m.setattr(config.os, "fsync", no_space)
        config.save_config(settings)

    assert config_path.read_text() == original, "the previous settings were destroyed"
    leftovers = [p.name for p in config_path.parent.iterdir() if ".tmp" in p.name]
    assert not leftovers, f"temp file left behind after a failed save: {leftovers}"


def test_save_keeps_a_backup_of_the_previous_settings(config_path):
    first = config.Settings()
    first.model = "base"
    config.save_config(first)

    second = config.Settings()
    second.model = "large-v3"
    config.save_config(second)

    backup = config_path.with_suffix(config_path.suffix + ".bak")
    assert backup.exists(), "no backup written"
    assert "base" in backup.read_text()


def test_saved_settings_round_trip(config_path):
    settings = config.Settings()
    settings.model = "small"
    settings.auto_period = False
    settings.auto_timeout_minutes = 9
    config.save_config(settings)

    loaded = config.load_config()

    assert loaded.model == "small"
    assert loaded.auto_period is False
    assert loaded.auto_timeout_minutes == 9


# --- corrupt config recovery ------------------------------------------------

def test_a_corrupt_config_is_moved_aside_not_silently_reset(config_path):
    good = config.Settings()
    good.model = "small"
    config.save_config(good)

    config_path.write_text("this is not valid toml {{{")
    config._config_cache = None
    config._config_mtime = 0.0

    config.load_config()

    salvaged = list(config_path.parent.glob("config.toml.corrupt-*"))
    assert salvaged, "the damaged file was discarded without a copy"


def test_a_corrupt_config_is_restored_from_backup(config_path):
    # Deliberately not the default model, or this would pass vacuously.
    good = config.Settings()
    good.model = "tiny"
    config.save_config(good)
    # A second save moves the first into .bak
    second = config.Settings()
    second.model = "base"
    config.save_config(second)

    config_path.write_text("garbage {{{")
    config._config_cache = None
    config._config_mtime = 0.0

    loaded = config.load_config()

    assert loaded.model == "tiny", "did not fall back to the last good settings"


def test_an_empty_config_is_treated_as_damaged(config_path):
    """Zero bytes parses fine as TOML and would silently give all-defaults,
    including a blank hotkey, which kills dictation with no message."""
    good = config.Settings()
    good.model = "tiny"
    config.save_config(good)
    second = config.Settings()
    second.model = "base"
    config.save_config(second)

    config_path.write_text("")
    config._config_cache = None
    config._config_mtime = 0.0

    loaded = config.load_config()

    assert loaded.model == "tiny"


def test_a_missing_config_is_not_treated_as_damaged(config_path):
    """First run has no file at all. That is normal, not corruption."""
    loaded = config.load_config()

    assert loaded.model == config.Settings().model
    assert not list(config_path.parent.glob("config.toml.corrupt-*"))


# --- validation must not kill the caller ------------------------------------

def test_invalid_settings_raise_instead_of_exiting():
    """The tray runs validate_config() once per second, so exiting on one bad
    value would make the tray icon vanish with no dialog and no way to tell why."""
    settings = config.Settings()
    settings.injection_mode = "nonsense"

    with pytest.raises(config.ConfigError):
        config.validate_config(settings)


def test_the_error_names_the_setting_that_is_wrong():
    settings = config.Settings()
    settings.injection_mode = "nonsense"

    with pytest.raises(config.ConfigError) as caught:
        config.validate_config(settings)

    assert "injection_mode" in str(caught.value)


def test_valid_settings_validate_cleanly():
    config.validate_config(config.Settings())  # must not raise


def test_loading_a_config_with_one_bad_value_falls_back_for_that_key(config_path):
    """The tray must keep running. A bad value falls back to its default rather
    than taking the whole process down."""
    config_path.write_text('model = "small"\ninjection_mode = "nonsense"\n')
    config._config_cache = None
    config._config_mtime = 0.0

    loaded = config.load_config()

    assert loaded.injection_mode == config.Settings().injection_mode
    assert loaded.model == "small", "unrelated settings were discarded"


def test_a_removed_setting_in_an_old_config_is_dropped_quietly(config_path):
    """indicator_backing lost its control in 0.16 and its setting after 0.17:
    a config (or backup) that still has it loads as before, and the next save
    leaves it out."""
    config_path.write_text('model = "small"\nindicator_backing = "strong"\n')
    loaded = config.load_config()
    assert loaded.model == "small"
    config.save_config(loaded)
    assert "indicator_backing" not in config_path.read_text()
    assert config.load_config().model == "small"


# --- custom commands are settings too ---------------------------------------

@pytest.fixture
def commands_path(tmp_path, monkeypatch):
    path = tmp_path / "custom_commands.toml"
    monkeypatch.setattr(config, "CUSTOM_COMMANDS_PATH", str(path))
    monkeypatch.setattr(config, "_commands_read_failed", False, raising=False)
    return path


def test_a_damaged_commands_file_is_not_replaced_with_an_empty_one(commands_path):
    """load_custom_commands() returns {} when the file cannot be parsed, and
    Preferences writes its list back on every Apply/OK, so saving must refuse
    to turn one damaged byte into zero commands on disk."""
    commands_path.write_text('[commands]\n"talk type" = "TalkType"\nbroken {{{\n')
    damaged = commands_path.read_text()

    loaded = config.load_custom_commands()
    assert loaded == {}          # cannot read it — as designed

    with pytest.raises(config.ConfigNotLoadedError):
        config.save_custom_commands(loaded)

    assert commands_path.read_text() == damaged, "damaged file was overwritten"


def test_commands_can_still_be_saved_after_the_file_is_repaired(commands_path):
    """The refusal must clear itself once the file reads cleanly again."""
    commands_path.write_text('[commands]\nbroken {{{\n')
    assert config.load_custom_commands() == {}

    commands_path.write_text('[commands]\n"talk type" = "TalkType"\n')
    assert config.load_custom_commands() == {"talk type": "TalkType"}

    config.save_custom_commands({"talk type": "TalkType", "new": "thing"})
    assert config.load_custom_commands() == {"talk type": "TalkType", "new": "thing"}


def test_deliberately_deleting_every_command_still_works(commands_path):
    """Clearing the list is a legitimate user action when the file reads fine."""
    config.save_custom_commands({"talk type": "TalkType"})
    config.save_custom_commands({})
    assert config.load_custom_commands() == {}


def test_saving_commands_on_a_fresh_install_is_never_blocked(commands_path):
    assert not commands_path.exists()
    assert config.load_custom_commands() == {}
    config.save_custom_commands({"talk type": "TalkType"})
    assert config.load_custom_commands() == {"talk type": "TalkType"}


def test_custom_commands_round_trip(commands_path):
    commands = {"talk type": "TalkType", "why do tool": "ydotool"}

    config.save_custom_commands(commands)

    assert config.load_custom_commands() == commands


def test_a_quote_in_a_spoken_phrase_does_not_destroy_every_command(commands_path):
    """The phrase is escaped on write like the replacement, so one command
    containing a double quote can't make the whole file unparseable and stop
    every custom command at once."""
    commands = {
        'say "hello"': "greeting",
        "talk type": "TalkType",
    }

    config.save_custom_commands(commands)
    loaded = config.load_custom_commands()

    assert loaded == commands


def test_a_backslash_in_a_phrase_survives(commands_path):
    commands = {"back slash": r"C:\Users\Ron", "normal": "fine"}

    config.save_custom_commands(commands)

    assert config.load_custom_commands() == commands


def test_custom_commands_are_written_atomically(commands_path, monkeypatch):
    config.save_custom_commands({"first": "one"})
    original = commands_path.read_text()

    def no_space(*args, **kwargs):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as m, pytest.raises(OSError):
        m.setattr(config.os, "fsync", no_space)
        config.save_custom_commands({"second": "two"})

    assert commands_path.read_text() == original, "custom commands were destroyed"


# --- recovery must actually repair, not just paper over ---------------------

def test_recovered_settings_are_written_back_to_disk(config_path):
    """Recovery writes the repaired settings back; otherwise every process
    start (tray, service, prefs) would quarantine the file again and show the
    user the same alarming warning forever."""
    good = config.Settings()
    good.model = "tiny"
    config.save_config(good)
    second = config.Settings()
    second.model = "base"
    config.save_config(second)

    config_path.write_text("garbage {{{")
    config._config_cache = None
    config._config_mtime = 0.0

    config.load_config()

    on_disk = config_path.read_text()
    assert "garbage" not in on_disk, "the damaged file was left in place"
    assert "tiny" in on_disk, "the recovered settings were not persisted"


def test_a_damaged_config_is_quarantined_once_not_every_launch(config_path, monkeypatch):
    good = config.Settings()
    good.model = "tiny"
    config.save_config(good)
    second = config.Settings()
    second.model = "base"
    config.save_config(second)

    config_path.write_text("garbage {{{")

    # Distinct timestamps per launch — otherwise three quarantines inside the
    # same second collapse to one filename and the test proves nothing.
    stamps = iter(["20260101-000001", "20260101-000002", "20260101-000003"])
    real_strftime = config.time.strftime
    monkeypatch.setattr(config.time, "strftime",
                        lambda fmt, *a: next(stamps, "20260101-000009")
                        if fmt == "%Y%m%d-%H%M%S" else real_strftime(fmt, *a))

    for _ in range(3):  # three process starts
        config._config_cache = None
        config._config_mtime = 0.0
        loaded = config.load_config()

    assert loaded.model == "tiny"
    quarantined = list(config_path.parent.glob("config.toml.corrupt-*"))
    assert len(quarantined) == 1, f"re-quarantined on every launch: {len(quarantined)} copies"


def test_repairing_a_damaged_config_does_not_destroy_the_good_backup(config_path):
    """The backup is the only remaining copy of the user's real settings at
    that moment — overwriting it with the damaged file removes the safety net
    right when it is needed."""
    good = config.Settings()
    good.model = "tiny"
    config.save_config(good)
    second = config.Settings()
    second.model = "base"
    config.save_config(second)

    config_path.write_text("garbage {{{")
    config._config_cache = None
    config._config_mtime = 0.0
    config.load_config()

    backup = config_path.with_suffix(config_path.suffix + ".bak")
    contents = backup.read_text()
    assert "garbage" not in contents, "the good backup was overwritten with the damaged file"
    assert "tiny" in contents


def test_a_later_save_still_refreshes_the_backup_normally(config_path):
    """Repair must not permanently freeze the backup."""
    first = config.Settings()
    first.model = "tiny"
    config.save_config(first)

    config_path.write_text("garbage {{{")
    config._config_cache = None
    config._config_mtime = 0.0
    config.load_config()

    later = config.Settings()
    later.model = "medium"
    config.save_config(later)

    backup = config_path.with_suffix(config_path.suffix + ".bak")
    assert "garbage" not in backup.read_text()


def test_repair_failure_does_not_crash_the_app(config_path, monkeypatch):
    """A read-only config directory must not take the tray down."""
    good = config.Settings()
    good.model = "tiny"
    config.save_config(good)
    second = config.Settings()
    second.model = "base"
    config.save_config(second)
    config_path.write_text("garbage {{{")
    config._config_cache = None
    config._config_mtime = 0.0

    def refuse(*args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(config, "write_text_atomic", refuse)

    loaded = config.load_config()  # must not raise

    assert loaded.model == "tiny", "in-memory recovery should still work"


# --- concurrent writers ------------------------------------------------------

def test_concurrent_saves_do_not_collide(tmp_path):
    """Three TuxTalk processes run at once (tray, dictation service and
    Preferences) and all of them save settings.

    Each needs its own temp file, or they truncate each other's half-written
    file and then fail to rename it, raising FileNotFoundError out of save_config.
    """
    import threading

    path = str(tmp_path / "config.toml")
    errors = []

    def writer(tag):
        try:
            for _ in range(60):
                config.write_text_atomic(path, f'# {tag}\n' + f'model = "{tag}"\n' * 40)
        except Exception as e:
            errors.append(f"{tag}: {type(e).__name__}: {e}")

    threads = [threading.Thread(target=writer, args=(t,)) for t in ("alpha", "bravo", "charlie")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent saves raised: {errors[:3]}"

    # Whoever won, the file must hold exactly one complete version.
    written = [ln.split('"')[1] for ln in open(path).read().splitlines() if "model" in ln]
    assert written, "file ended up empty"
    assert len(set(written)) == 1, f"file interleaves several writers: {set(written)}"
    assert len(written) == 40, f"file is truncated: {len(written)} of 40 lines"


def test_no_temp_files_are_left_behind(tmp_path):
    path = str(tmp_path / "config.toml")
    config.write_text_atomic(path, "model = \"tiny\"\n")

    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert not leftovers, f"temp files left behind: {leftovers}"


# --- repair must never destroy what it could not preserve -------------------

def test_an_unreadable_config_is_never_overwritten(config_path):
    """A config the app cannot READ is not a damaged config.

    load_config catches any exception, so a permission error must not be
    taken for corruption. The quarantine copy would fail for the same reason,
    yet os.replace (which needs write permission on the DIRECTORY, not the
    file) would still replace a valid config with no copy kept.
    """
    good = config.Settings()
    good.model = "large-v3"
    good.device = "cuda"
    good.hotkey = "F8"
    config.save_config(good)
    original = config_path.read_text()
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    if backup.exists():
        backup.unlink()

    os.chmod(config_path, 0o000)
    config._config_cache = None
    config._config_mtime = 0.0
    try:
        config.load_config()  # must not raise
    finally:
        os.chmod(config_path, 0o600)

    assert config_path.read_text() == original, "an unreadable but valid config was destroyed"


def test_repair_is_skipped_when_the_damaged_file_cannot_be_quarantined(config_path, monkeypatch):
    """If the only copy of the user's content cannot be preserved, leave it."""
    good = config.Settings()
    good.model = "tiny"
    config.save_config(good)
    second = config.Settings()
    second.model = "base"
    config.save_config(second)
    config_path.write_text("garbage {{{")

    def cannot_copy(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(config.shutil, "copy2", cannot_copy)
    config._config_cache = None
    config._config_mtime = 0.0
    config.load_config()

    assert config_path.read_text() == "garbage {{{", \
        "overwrote the damaged file without keeping a copy of it"


# --- the repair must produce valid TOML -------------------------------------

def test_a_setting_containing_a_quote_survives_a_save(config_path):
    """_toml_value escapes quotes, so a mic named 'Blue Yeti "Nano" USB' still
    produces a file that parses."""
    s = config.Settings()
    s.mic = 'Blue Yeti "Nano" USB'
    config.save_config(s)

    config._config_cache = None
    config._config_mtime = 0.0
    assert config.load_config().mic == 'Blue Yeti "Nano" USB'


def test_a_setting_containing_a_backslash_survives_a_save(config_path):
    s = config.Settings()
    s.mic = r"Mic\Device"
    config.save_config(s)

    config._config_cache = None
    config._config_mtime = 0.0
    assert config.load_config().mic == r"Mic\Device"


def test_repair_never_leaves_the_file_unparseable(config_path):
    """An unparseable repair would be re-detected as damage on the next poll
    and rewritten again: one quarantine file and one disk write every second,
    forever."""
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    backup.write_text('# TalkType config\nmodel = "small"\nmic = "Blue Yeti \\"Nano\\" USB"\nhotkey = "F8"\n')
    config_path.write_text("garbage {{{")

    writes = []
    real_write = config.write_text_atomic
    original = config.write_text_atomic

    def counting(*args, **kwargs):
        writes.append(1)
        return original(*args, **kwargs)

    config.write_text_atomic = counting
    try:
        for _ in range(25):
            config._config_cache = None
            config._config_mtime = 0.0
            config.load_config()
    finally:
        config.write_text_atomic = real_write

    assert len(writes) <= 1, f"repair rewrote the file {len(writes)} times — livelock"
    config._load_toml_file(str(config_path))  # must parse; raises otherwise


def test_repair_happens_once_even_without_a_cache_reset(config_path):
    """The tray's real call pattern: one long-lived process polling every second."""
    good = config.Settings()
    good.model = "tiny"
    config.save_config(good)
    second = config.Settings()
    second.model = "base"
    config.save_config(second)
    config_path.write_text("garbage {{{")
    config._config_cache = None
    config._config_mtime = 0.0

    writes = []
    original = config.write_text_atomic
    config.write_text_atomic = lambda *a, **k: (writes.append(1), original(*a, **k))[1]
    try:
        for _ in range(20):
            config.load_config()
    finally:
        config.write_text_atomic = original

    assert len(writes) == 1, f"repaired {len(writes)} times in one process"


# --- an unreadable (but intact) config must never be overwritten -----------

def test_a_transient_read_error_does_not_pin_the_process_to_defaults(config_path,
                                                                     monkeypatch):
    """A one-off read failure must not poison the cache.

    If the defaults were cached under the real file's mtime after an EACCES,
    every later load_config() would return them from cache and the good file
    on disk would never be read again for the life of the process.
    """
    settings = config.Settings()
    settings.model = "large-v3"
    config.save_config(settings)
    config._config_cache = None
    config._config_mtime = 0.0

    real = config._load_toml_file
    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(config, "_load_toml_file", flaky)

    first = config.load_config()
    assert first.model == config.Settings.model   # defaults for this call only

    second = config.load_config()
    assert second.model == "large-v3", "the good file on disk was never re-read"


def test_an_unreadable_config_is_not_overwritten_with_defaults(config_path,
                                                               monkeypatch):
    """The tray's update check calls save_config(load_config()) five seconds
    after launch. If the config could not be READ, load_config returns
    defaults, and saving them must not destroy a file whose contents are good.
    """
    settings = config.Settings()
    settings.model = "large-v3"
    settings.hotkey = "F8"
    config.save_config(settings)
    good = config_path.read_text()
    config._config_cache = None
    config._config_mtime = 0.0

    def unreadable(path):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(config, "_load_toml_file", unreadable)

    loaded = config.load_config()
    assert loaded.model == config.Settings.model  # defaults, as designed

    # The tray writes that back; this must not destroy the file.
    with pytest.raises(config.ConfigNotLoadedError):
        config.save_config(loaded)

    assert config_path.read_text() == good, "unreadable config was overwritten"


def test_saving_still_works_once_the_config_can_be_read_again(config_path,
                                                              monkeypatch):
    """The refusal must clear itself — fixing the permissions and carrying
    on must not leave the app permanently unable to save."""
    settings = config.Settings()
    settings.model = "large-v3"
    config.save_config(settings)
    config._config_cache = None
    config._config_mtime = 0.0

    real = config._load_toml_file
    broken = {"yes": True}

    def flaky(path):
        if broken["yes"]:
            raise OSError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(config, "_load_toml_file", flaky)
    config.load_config()

    broken["yes"] = False
    recovered = config.load_config()
    assert recovered.model == "large-v3"

    recovered.model = "medium"
    config.save_config(recovered)          # must not raise
    assert "medium" in config_path.read_text()


def test_saving_a_fresh_install_is_never_blocked(config_path):
    """No file on disk yet is not a read failure — first save must work."""
    assert not config_path.exists()
    config._config_cache = None
    config._config_mtime = 0.0
    loaded = config.load_config()
    loaded.model = "medium"
    config.save_config(loaded)
    assert "medium" in config_path.read_text()


# --- control characters, wrong types, the shared cache --------------------

@pytest.mark.parametrize("value", [
    "a\r\nb", "\x1b[0m", "del\x7f", "nul\x00c", "tab\there", "back\bspace\f",
    "emoji 🎤 ok", 'mixed "q" \\ \r\n',
])
def test_any_string_round_trips_as_valid_toml(value):
    """A raw \\r or other control character makes tomllib reject the whole
    file, so one pasted Windows line break would break every custom command.
    tomllib arrived in Python 3.11; CI runs 3.10 like the AppImage, so there
    the strict parser this guards against isn't present to test."""
    tomllib = pytest.importorskip("tomllib")
    assert tomllib.loads(f"k = {config._toml_string(value)}")["k"] == value


def test_a_setting_with_a_carriage_return_survives_a_save(config_path):
    settings = config.Settings()
    settings.mic = "USB\r\nMic"
    config.save_config(settings)
    config._config_cache = None
    assert config.load_config().mic == "USB\r\nMic"
    assert not list(config_path.parent.glob("config.toml.corrupt-*"))


def test_a_wrong_typed_value_costs_only_that_setting(config_path):
    """key_delay = "fast" must not make the whole file count as damaged,
    quarantined and rolled back to the .bak copy."""
    config_path.write_text('model = "small"\nhotkey = "F8"\nkey_delay = "fast"\n')
    loaded = config.load_config()
    assert loaded.model == "small"
    assert loaded.hotkey == "F8"
    assert loaded.key_delay == config.Settings().key_delay
    assert not list(config_path.parent.glob("config.toml.corrupt-*"))


def test_an_infinite_number_in_a_whole_number_setting_costs_only_that_setting(config_path):
    """int(inf) raises OverflowError rather than ValueError."""
    config_path.write_text('model = "small"\nkey_delay = inf\n')
    loaded = config.load_config()
    assert loaded.model == "small"
    assert loaded.key_delay == config.Settings().key_delay
    assert not list(config_path.parent.glob("config.toml.corrupt-*"))


def test_a_typing_delay_from_ydotool_days_does_not_slow_the_new_keyboard(config_path):
    """typing_delay was ydotool's wait, and its fast path for Electron apps
    ignored it. Carried over, a hand-set 25 made typing into Claude Desktop
    take 3 seconds a sentence instead of a blink."""
    for old in (12, 25):
        config_path.write_text(f'model = "small"\ntyping_delay = {old}\n')
        assert config.load_config().key_delay == config.Settings().key_delay
    config.save_config(config.load_config())
    assert "typing_delay" not in config_path.read_text()


def test_a_key_delay_set_by_hand_stays(config_path):
    config_path.write_text('model = "small"\nkey_delay = 12\n')
    assert config.load_config().key_delay == 12


def test_a_key_delay_out_of_range_goes_back_to_the_default(config_path):
    """500 ms a key would make a sentence take a minute to type; 0 overflows
    the kernel's buffer and loses letters."""
    for wrong in (0, 500):
        config_path.write_text(f'model = "small"\nkey_delay = {wrong}\n')
        loaded = config.load_config()
        assert loaded.key_delay == config.Settings().key_delay and loaded.model == "small"


def test_recovery_from_backup_skips_only_the_bad_field(config_path):
    config_path.write_text("this is = = not toml")
    (config_path.parent / "config.toml.bak").write_text(
        'hotkey = "F7"\nkey_delay = "fast"\nmodel = "small"\n')
    loaded = config.load_config()
    assert loaded.hotkey == "F7"
    assert loaded.model == "small"
    assert loaded.key_delay == config.Settings().key_delay


def test_changing_a_loaded_object_without_saving_changes_nothing_else(config_path):
    """load_config() gives each caller its own copy, so a change that was
    never saved doesn't show up everywhere or get written by the next
    unrelated save."""
    config.save_config(config.Settings(model="small"))
    first = config.load_config()
    first.model = "large-v3"
    assert config.load_config().model == "small"
    assert config.load_config() is not config.load_config()
