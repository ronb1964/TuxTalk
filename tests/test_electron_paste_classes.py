"""Claude Desktop must be recognised by the app id it actually reports.

Synthetic Ctrl+V is silently dropped by Electron/Chromium apps on Wayland, so
_inject_text types into those apps instead. The routing is a single membership
test against window_classes.ELECTRON_PASTE_BROKEN_CLASSES, which makes that set
the whole feature: an app id missing from it means the override never fires and
the user gets a paste that quietly does nothing.

Claude Desktop reports itself as 'claude-desktop' or as 'com.anthropic.Claude'
depending on how it was packaged, so the set must list both.
"""

from tuxtalk import window_classes


# The app ids Claude Desktop has been observed reporting, oldest first. Both
# spellings are live in the wild: which one a user's build reports depends on
# how their Claude Desktop was packaged.
OBSERVED_CLAUDE_DESKTOP_APP_IDS = [
    "claude-desktop",
    "com.anthropic.Claude",
]


def test_every_observed_claude_desktop_app_id_takes_the_fast_type_path():
    """The membership test _inject_text performs, for each id seen in the log."""
    for app_id in OBSERVED_CLAUDE_DESKTOP_APP_IDS:
        assert app_id in window_classes.ELECTRON_PASTE_BROKEN_CLASSES, (
            f"Claude Desktop reports {app_id!r}; without it in the set the "
            f"Electron paste override cannot fire and dictation silently "
            f"pastes nothing."
        )


def test_an_unrelated_app_still_uses_the_normal_paste_path():
    """The override must stay narrow — it is a workaround, not the default."""
    assert "org.gnome.Ptyxis" not in window_classes.ELECTRON_PASTE_BROKEN_CLASSES
    assert "konsole" not in window_classes.ELECTRON_PASTE_BROKEN_CLASSES
