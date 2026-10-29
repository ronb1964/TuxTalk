"""The GNOME extension must declare the shell versions users are running.

GNOME Shell refuses to load an extension whose metadata does not list the
running major version. For TuxTalk that is worse than losing the panel menu:
tray.py's _check_tray_visibility() sees the extension as absent and activates
the GTK AyatanaAppIndicator tray instead — which stock GNOME has no host for.
The user ends up with no icon at all, and no way to reach Preferences or Quit.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
METADATA_PATH = (
    REPO_ROOT / "gnome-extension" / "talktype@ronb1964.github.io" / "metadata.json"
)

# The oldest shell TuxTalk supports, and the newest that has a release date.
# Bump NEWEST when the next GNOME ships; the test then fails until metadata
# declares it, which is the reminder this file exists to provide.
OLDEST_SUPPORTED_SHELL = 45
NEWEST_RELEASED_SHELL = 51


def _metadata():
    return json.loads(METADATA_PATH.read_text())


def test_every_released_gnome_shell_is_declared():
    """A gap here means those users get no panel icon and no GTK fallback."""
    declared = {int(v) for v in _metadata()["shell-version"]}
    expected = set(range(OLDEST_SUPPORTED_SHELL, NEWEST_RELEASED_SHELL + 1))

    assert expected <= declared, (
        f"metadata.json does not declare GNOME "
        f"{sorted(expected - declared)}; the extension will not load there."
    )


def test_extension_version_is_bumped_past_the_last_shipped_build():
    """The in-app updater compares this integer only.

    Widening shell-version without bumping "version" means existing users are
    never offered the fixed extension, so the fix reaches nobody.
    """
    # v0.18.2 shipped version 25. Any metadata change that users must receive
    # has to advance this.
    LAST_SHIPPED_EXTENSION_VERSION = 25

    assert _metadata()["version"] > LAST_SHIPPED_EXTENSION_VERSION


def test_shell_versions_are_strings_in_ascending_order():
    """GNOME requires strings; ordering keeps the list readable as it grows."""
    raw = _metadata()["shell-version"]

    assert all(isinstance(v, str) for v in raw)
    assert [int(v) for v in raw] == sorted(int(v) for v in raw)
