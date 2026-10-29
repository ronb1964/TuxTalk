"""GNOME's Alt+F8 / Alt+F7 must always come back.

The hotkey-test dialog clears org.gnome.desktop.wm.keybindings begin-resize
and begin-move while it is open. gsettings keeps whatever is written across
reboots, so an exception, a crash, a kill or a logout before the restore would
leave the user's window keybindings disabled for good, with nothing pointing at
TuxTalk. The originals go to a backup file first, and the next start puts them
back if the restore never happened.
"""

import json

import pytest

from tuxtalk import gnome_keybindings as G


class FakeGsettings:
    """Stands in for the gsettings command line."""

    def __init__(self, values, fail_keys=()):
        self.values = dict(values)
        self.fail_keys = set(fail_keys)
        self.sets = []

    def run(self, cmd, **kwargs):
        _, action, schema, key, value = cmd
        assert action == "set" and schema == G.SCHEMA
        self.sets.append((key, value))
        if key in self.fail_keys:
            raise OSError("transient")
        self.values[key] = value
        return type("R", (), {"returncode": 0})()

    def check_output(self, cmd, **kwargs):
        _, action, schema, key = cmd
        assert action == "get"
        return self.values[key] + "\n"


@pytest.fixture
def gnome(monkeypatch, tmp_path):
    fake = FakeGsettings({"begin-resize": "['<Alt>F8']", "begin-move": "['<Alt>F7']"})
    monkeypatch.setattr(G.subprocess, "run", fake.run)
    monkeypatch.setattr(G.subprocess, "check_output", fake.check_output)
    monkeypatch.setattr(G, "_backup_path", lambda: str(tmp_path / G.BACKUP_NAME))
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    fake.backup = tmp_path / G.BACKUP_NAME
    return fake


def test_the_originals_are_saved_before_they_are_cleared(gnome):
    saved = G.disable_for_hotkey_test()
    assert saved == {"begin-resize": "['<Alt>F8']", "begin-move": "['<Alt>F7']"}
    assert json.loads(gnome.backup.read_text()) == saved
    assert gnome.values == {"begin-resize": "[]", "begin-move": "[]"}


def test_restore_puts_both_back_and_removes_the_backup(gnome):
    saved = G.disable_for_hotkey_test()
    G.restore(saved)
    assert gnome.values == {"begin-resize": "['<Alt>F8']", "begin-move": "['<Alt>F7']"}
    assert not gnome.backup.exists()


def test_a_killed_test_is_repaired_at_the_next_start(gnome):
    G.disable_for_hotkey_test()          # ...and the process dies here
    G.restore_left_over()
    assert gnome.values == {"begin-resize": "['<Alt>F8']", "begin-move": "['<Alt>F7']"}
    assert not gnome.backup.exists()


def test_a_second_test_after_a_crash_keeps_the_real_values(gnome):
    """Reading the bindings again would only find the cleared ones."""
    G.disable_for_hotkey_test()          # crash, then onboarding runs again
    saved = G.disable_for_hotkey_test()
    assert saved["begin-resize"] == "['<Alt>F8']"


def test_a_restore_that_failed_again_keeps_the_real_values(gnome):
    """A left-over backup that can't be put back yet: reading the bindings
    then finds them cleared, and saving that over the backup lost Alt+F8 and
    Alt+F7 for good. The test leaves them alone instead."""
    G.disable_for_hotkey_test()                 # a test that never gave them back
    gnome.fail_keys = {"begin-move"}             # and the next restore fails
    assert G.disable_for_hotkey_test() is None
    assert json.loads(gnome.backup.read_text()) == {
        "begin-resize": "['<Alt>F8']", "begin-move": "['<Alt>F7']"}
    gnome.fail_keys = set()
    assert G.restore_left_over()
    assert gnome.values == {"begin-resize": "['<Alt>F8']", "begin-move": "['<Alt>F7']"}


def test_restore_still_tries_the_second_binding_if_the_first_fails(gnome):
    saved = G.disable_for_hotkey_test()
    gnome.fail_keys = {"begin-resize"}
    G.restore(saved)                     # must not raise
    assert gnome.values["begin-move"] == "['<Alt>F7']"
    assert gnome.backup.exists()         # retried at the next start


def test_restore_skips_values_that_were_never_captured(gnome):
    G.restore(None)
    assert gnome.sets == []


def test_other_desktops_are_left_alone(gnome, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert G.disable_for_hotkey_test() is None
    assert gnome.sets == [] and not gnome.backup.exists()


def test_nothing_is_cleared_when_the_backup_cannot_be_written(gnome, monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(G, "_backup_path", lambda: str(blocker / G.BACKUP_NAME))
    assert G.disable_for_hotkey_test() is None
    assert gnome.sets == []


def test_the_hotkey_test_gives_the_keys_back_after_an_error(monkeypatch):
    """GNOME's Alt+F8 and Alt+F7 are cleared while the first-run hotkey test
    is open; an error in the test must still put them back, or they stay dead
    after a reboot (gsettings persists)."""
    import types
    from tuxtalk import config, service_launcher
    from tuxtalk import hotkey_test_dialog as hkd
    restored = []
    monkeypatch.setattr(config, "load_config", config.Settings)
    monkeypatch.setattr(G, "disable_for_hotkey_test", lambda: {"saved": "Alt+F8"})
    monkeypatch.setattr(G, "restore", restored.append)
    monkeypatch.setattr(service_launcher, "stop_dictation_service", lambda **k: None)
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [])
    monkeypatch.setattr(hkd, "time", types.SimpleNamespace(sleep=lambda s: None))

    def broken(*a):
        raise OSError("keyboard went away")
    monkeypatch.setattr(hkd, "open_key_devices", broken)
    with pytest.raises(OSError):
        hkd.show_hotkey_test_dialog()
    assert restored == [{"saved": "Alt+F8"}]


def test_the_tray_repairs_left_over_bindings_at_start(tray_main):
    assert "restores GNOME keybindings" in tray_main()


def test_the_backup_is_written_whole_or_not_at_all(gnome, monkeypatch):
    """Cut off halfway (a full disk), a backup that can't be read back kept
    every later hotkey test from clearing the bindings."""
    def full_disk(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr("tuxtalk.config.write_text_atomic", full_disk)
    assert G.disable_for_hotkey_test() is None
    assert not gnome.backup.exists()
    assert gnome.values == {"begin-resize": "['<Alt>F8']", "begin-move": "['<Alt>F7']"}


def test_a_key_gnome_refuses_is_logged_with_its_reason(monkeypatch):
    said = []
    monkeypatch.setattr(G.logger, "warning", said.append)
    monkeypatch.setattr(G.subprocess, "run", lambda cmd, **k: type(
        "R", (), {"returncode": 1, "stderr": "No such key “begin-resize”\n"})())
    assert G._set("begin-resize", "[]") is False
    assert said == ["GNOME didn't take begin-resize: No such key “begin-resize”"]
