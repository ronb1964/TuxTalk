"""Preferences must not hide a half-configured permission state.

The Typing Setup section decides between "configured, just restart" (fix button
disabled) and "not configured" (fix button enabled, so clicking it runs the
usermod/udev setup). That decision must consider BOTH halves of setup:

    udev rule present      -> /dev/uinput write (typing)
    in 'input' group       -> /dev/input read  (hotkeys)

Keying it on the udev rule alone gets the Fedora case wrong: rule present, user
never added to 'input', so the section would claim "configured - please
restart" and DISABLE the only button that would add them.
"""


class _FakeLabel:
    def __init__(self):
        self.markup = None

    def set_markup(self, m):
        self.markup = m

    def set_text(self, t):
        self.markup = t


class _FakeButton:
    def __init__(self):
        self.sensitive = None
        self.label = None

    def set_sensitive(self, v):
        self.sensitive = v

    def set_label(self, v):
        self.label = v


def _prefs_with_perms(monkeypatch, *, all_ok, udev, in_group, hotkey_ok=False):
    from tuxtalk import prefs as prefs_mod
    from tuxtalk import uinput_helper as uh

    monkeypatch.setattr(uh, "check_all_device_permissions",
                        lambda: (all_ok, "reason"))
    monkeypatch.setattr(uh, "check_udev_rule_exists", lambda: udev)
    monkeypatch.setattr(uh, "check_user_in_input_group", lambda: in_group)
    monkeypatch.setattr(uh, "check_input_read_permission",
                        lambda: (hotkey_ok, "reason"))

    prefs = prefs_mod.PreferencesWindow.__new__(prefs_mod.PreferencesWindow)
    prefs.typing_status_label = _FakeLabel()
    prefs.fix_typing_button = _FakeButton()
    return prefs


def test_fedora_bug_fix_button_stays_enabled_when_group_missing(monkeypatch):
    # udev rule present, but NOT in the input group, access still failing.
    prefs = _prefs_with_perms(monkeypatch, all_ok=False, udev=True, in_group=False)
    prefs._show_typing_status()
    assert prefs.fix_typing_button.sensitive is True, (
        "must let the user run setup — they were never added to 'input'"
    )
    assert "dictation key won't work" in prefs.typing_status_label.markup


def test_both_configured_but_stale_says_restart_and_disables(monkeypatch):
    # Rule present AND in group (system db), but this session is stale.
    prefs = _prefs_with_perms(monkeypatch, all_ok=False, udev=True, in_group=True)
    prefs._show_typing_status()
    assert prefs.fix_typing_button.sensitive is False
    assert "restart" in prefs.typing_status_label.markup.lower()


def test_full_access_reports_ready(monkeypatch):
    prefs = _prefs_with_perms(monkeypatch, all_ok=True, udev=True, in_group=True)
    assert prefs._show_typing_status() is True      # System check: all set
    assert prefs.fix_typing_button.sensitive is False
    assert "✓" in prefs.typing_status_label.markup


def test_nothing_configured_enables_fix(monkeypatch):
    prefs = _prefs_with_perms(monkeypatch, all_ok=False, udev=False, in_group=False)
    assert prefs._show_typing_status() is False     # System check opens on it
    assert prefs.fix_typing_button.sensitive is True


def test_unreadable_keyboard_says_the_dictation_key_wont_work(monkeypatch):
    # Without /dev/input no hotkey is seen; offering a clipboard fallback
    # would promise something that can't happen.
    prefs = _prefs_with_perms(monkeypatch, all_ok=False, udev=False, in_group=False)
    prefs._show_typing_status()
    markup = prefs.typing_status_label.markup
    assert "dictation key won't work" in markup
    assert "restart" in markup
    assert "clipboard" not in markup.lower()


def test_readable_keyboard_without_uinput_says_it_cant_type(monkeypatch):
    prefs = _prefs_with_perms(monkeypatch, all_ok=False, udev=False, in_group=True,
                              hotkey_ok=True)
    prefs._show_typing_status()
    markup = prefs.typing_status_label.markup
    assert "can't type" in markup and "restart" in markup
    assert "clipboard" not in markup.lower()
    assert prefs.fix_typing_button.sensitive is True
