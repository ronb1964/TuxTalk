"""Tests for the tray telling the GNOME extension about committed changes.

The tray process owns the D-Bus name the extension listens on. Change signals
emitted anywhere else never reach the extension, and its menu would sit on a
stale model indefinitely while the GTK tray shows the new one.
"""

import pytest

from tuxtalk import tray


class FakeDBusService:
    def __init__(self, fail=False):
        self.model_changes = []
        self._fail = fail

    def emit_model_changed(self, model_name):
        if self._fail:
            raise RuntimeError("bus went away")
        self.model_changes.append(model_name)


class FakeTray:
    """Just enough of the tray to exercise the emit helper."""

    _emit_model_changed = tray.DictationTray._emit_model_changed

    def __init__(self, dbus_service=None):
        self.dbus_service = dbus_service


def test_model_change_is_announced_to_the_extension():
    svc = FakeDBusService()

    FakeTray(svc)._emit_model_changed("large-v3")

    assert svc.model_changes == ["large-v3"]


def test_no_dbus_service_is_not_an_error():
    """The tray runs fine on desktops with no extension attached."""
    FakeTray(None)._emit_model_changed("base")  # must not raise


def test_a_failing_bus_does_not_break_the_preset_change():
    """Announcing is best-effort — the config was already saved."""
    svc = FakeDBusService(fail=True)

    FakeTray(svc)._emit_model_changed("small")  # must not raise


# --- extension packaging ----------------------------------------------------

def test_extension_version_is_bumped_whenever_extension_js_changes():
    """The in-app updater compares only this integer.

    Without a bump, every GNOME user who has installed the extension is told
    "up to date" forever and never receives the newer menu. So when anything
    in the extension differs from the last release, its version must be
    higher than that release's.
    """
    import json
    import pathlib
    import subprocess

    root = pathlib.Path(__file__).resolve().parent.parent
    ext = "gnome-extension/talktype@ronb1964.github.io"

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)

    tag = git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD").stdout.strip()
    if not tag:
        pytest.skip("no release tag in this checkout (CI fetches them with fetch-depth 0)")
    changed = git("diff", "--quiet", tag, "--", ext, f":(exclude){ext}/metadata.json").returncode
    if changed == 0:
        return
    released = json.loads(git("show", f"{tag}:{ext}/metadata.json").stdout)["version"]
    version = json.loads((root / ext / "metadata.json").read_text())["version"]
    assert version > released, (
        f"The extension changed since {tag} but its version is still {version}. "
        f"Bump it, or GNOME users will never be offered the update.")
