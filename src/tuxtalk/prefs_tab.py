"""What the tabs of Preferences share: the window they belong to, the amber
of the warnings, and whether the GNOME extension applies here."""

# The amber of TuxTalk's warnings (an unsafe setting, a backup never saved).
WARNING_AMBER = "#e6aa3c"


def _on_gnome():
    """Whether the GNOME Shell extension can apply on this desktop at all."""
    try:
        from .desktop_detect import is_gnome
        return is_gnome()
    except Exception:
        return True   # unsure: show the GNOME options rather than hide them


class Tab:
    """One tab of Preferences, or a window of settings a tab opens. It builds
    its page (build) and handles its own settings, reading and changing them
    through the window (*win*, a prefs.PreferencesWindow), which saves and
    applies them for every tab."""

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
