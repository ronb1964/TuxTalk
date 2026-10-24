"""What every tab of Preferences shares: the window it belongs to."""


class Tab:
    """One tab of Preferences. It builds its page (build) and handles its own
    settings, reading and changing them through the window (*win*, a
    prefs.PreferencesWindow), which saves and applies them for every tab."""

    def __init__(self, win):
        self.win = win

    @property
    def config(self):
        """The settings as Preferences has them now, applied or not."""
        return self.win.config

    @property
    def window(self):
        """The Preferences window, for the dialogs a tab opens over it."""
        return self.win.window

    def update_config(self, key, value):
        """Change a setting; Apply or OK saves it."""
        self.win.update_config(key, value)
