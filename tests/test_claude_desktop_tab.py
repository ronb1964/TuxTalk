"""Claude Desktop must be recognised by the app id it actually reports.

A real Tab there moves the focus from the message box to the Send button,
and the next space typed sends the message half-written, so a tab goes in
as four spaces (window_classes.tab_may_send). An app id missing from its
list means that guard never fires.
"""

from tuxtalk import window_classes

# Both spellings are live: which one a user's build reports depends on how
# their Claude Desktop was packaged.
OBSERVED_CLAUDE_DESKTOP_APP_IDS = ["claude-desktop", "com.anthropic.Claude"]


def test_every_observed_claude_desktop_app_id_gets_spaces_for_a_tab():
    for app_id in OBSERVED_CLAUDE_DESKTOP_APP_IDS:
        assert window_classes.tab_may_send(app_id), app_id


def test_claude_desktop_in_other_capitals_is_the_same_app():
    assert window_classes.tab_may_send("Claude-Desktop")
    assert window_classes.tab_may_send("COM.ANTHROPIC.CLAUDE")


def test_other_apps_keep_their_real_tab():
    """In a form, Tab goes to the next box: the guard stays narrow."""
    for other in ("org.gnome.Ptyxis", "konsole", "firefox", "", None):
        assert not window_classes.tab_may_send(other)
