"""Claude Desktop must be recognised by the app id it actually reports.

Synthetic Ctrl+V is silently dropped by Electron/Chromium apps on Wayland, so
delivery.inject_text types into those apps instead. The routing is one check,
window_classes.pastes_broken, which makes its list the whole feature: an app id
missing from it means the override never fires and the user gets a paste that
quietly does nothing.

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
    """The check delivery.inject_text makes, for each id seen in the log."""
    for app_id in OBSERVED_CLAUDE_DESKTOP_APP_IDS:
        assert window_classes.pastes_broken(app_id), (
            f"Claude Desktop reports {app_id!r}; without it in the set the "
            f"Electron paste override cannot fire and dictation silently "
            f"pastes nothing."
        )


def test_claude_desktop_in_other_capitals_is_still_typed_into():
    """A build that reports its name in other capitals is the same app; a
    case-sensitive match gave it a paste that silently did nothing."""
    assert window_classes.pastes_broken("Claude-Desktop")
    assert window_classes.pastes_broken("COM.ANTHROPIC.CLAUDE")


def test_an_unrelated_app_still_uses_the_normal_paste_path():
    """The override must stay narrow — it is a workaround, not the default."""
    assert not window_classes.pastes_broken("org.gnome.Ptyxis")
    assert not window_classes.pastes_broken("konsole")
    assert not window_classes.pastes_broken("")
    assert not window_classes.pastes_broken(None)
