"""Open apps by voice: "open Firefox" with Firefox already open brings its
window forward instead of starting a second one (app_focus.py), on KDE,
GNOME and other X11 desktops; anywhere else, or when that fails, TuxTalk
starts the app.

The desktops are stand-ins (desktop_fakes.py): nothing here touches the real one.
"""
from desktop_fakes import FakeShell, set_desktop
from tuxtalk import app_focus
from tuxtalk.app_finder import AppEntry


def _entry(app_id, name, program, wm_class=""):
    return AppEntry(app_id, name, name, "", program, "", wm_class)


FIREFOX = _entry("firefox.desktop", "Firefox", "firefox")
KONSOLE = _entry("org.kde.konsole.desktop", "Konsole", "konsole")
IDLE = _entry("idle3.desktop", "IDLE", "python3")
FIREFOX_FLATPAK = AppEntry("org.mozilla.firefox.desktop", "Firefox", "Firefox", "", "firefox",
                           "flatpak", "firefox")
# Entries whose program is a launcher with windows of its own.
PORTAL = _entry("Portal 2.desktop", "Portal 2", "steam")
YOUTUBE_MUSIC = _entry("chrome-cinhimbnkkaeohfgghhklpknlkffjgod-Default.desktop",
                       "YouTube Music", "google-chrome")
YOUTUBE_MUSIC_NAMED = _entry(YOUTUBE_MUSIC.app_id, "YouTube Music", "google-chrome",
                             "crx_cinhimbnkkaeohfgghhklpknlkffjgod")



# --- the names a window is known by -------------------------------------------------

def test_a_window_is_known_by_its_menu_entry_and_its_program():
    """On Wayland KWin names the window after the menu entry
    ("org.kde.konsole"); on X11 its class is the program's ("konsole")."""
    assert app_focus.window_names(KONSOLE) == {"org.kde.konsole", "konsole"}
    assert app_focus.window_names(FIREFOX) == {"firefox"}


def test_an_interpreter_never_names_the_window():
    """Every Python app's window could be called "python3": opening IDLE
    must not bring some other Python program forward."""
    assert app_focus.window_names(IDLE) == {"idle3"}


def test_a_launcher_never_names_the_window():
    """A Steam game's entry runs steam, a Chrome web app's runs
    google-chrome: their windows are the launcher's, not the game's."""
    assert app_focus.window_names(PORTAL) == {"portal 2"}
    assert "google-chrome" not in app_focus.window_names(YOUTUBE_MUSIC)


def test_the_class_the_entry_names_counts():
    """StartupWMClass: what the app's windows are really called."""
    assert "crx_cinhimbnkkaeohfgghhklpknlkffjgod" in app_focus.window_names(
        YOUTUBE_MUSIC_NAMED)


# --- KDE ------------------------------------------------------------------------------



def test_kde_brings_the_apps_window_forward(kde, fake_kwin):
    fake_kwin.windows = ['makeWindow("org.kde.konsole", "{k}", 0, 0, 800, 600)',
                         'makeWindow("firefox", "{f}", 0, 0, 800, 600)']
    assert app_focus.focus_running(FIREFOX) is True
    assert fake_kwin.activated == "{f}"
    assert '"firefox"' in fake_kwin.scripts[-1]


def test_kde_picks_the_window_used_last(kde, fake_kwin):
    """Two Konsole windows: the one higher in the stack is the one the user
    had last, as clicking Konsole in the task manager would show."""
    fake_kwin.windows = ['makeWindow("org.kde.konsole", "{old}", 0, 0, 800, 600)',
                         'makeWindow("org.kde.konsole", "{new}", 0, 0, 800, 600)',
                         'makeWindow("firefox", "{f}", 0, 0, 800, 600)']
    assert app_focus.focus_running(KONSOLE) is True
    assert fake_kwin.activated == "{new}"


def test_kde_knows_a_window_by_its_menu_entry(kde, fake_kwin):
    """A Flatpak's window: its class is its app id, which KWin also gives
    as the menu entry it belongs to."""
    flatpak = _entry("org.mozilla.firefox.desktop", "Firefox", "firefox")
    fake_kwin.windows = ['Object.assign(makeWindow("org.mozilla.firefox", "{f}", 0, 0, 9, 9),'
                         ' {desktopFileName: "org.mozilla.firefox"})']
    assert app_focus.focus_running(flatpak) is True
    assert fake_kwin.activated == "{f}"


def test_kde_never_takes_another_copys_window_for_the_one_asked_for(kde, fake_kwin):
    """Both Firefoxes installed, the distro's open: "open Firefox Flatpak"
    starts the Flatpak, though both have the class "firefox"."""
    fake_kwin.windows = ['Object.assign(makeWindow("firefox", "{d}", 0, 0, 9, 9),'
                         ' {desktopFileName: "firefox"})']
    assert app_focus.focus_running(FIREFOX_FLATPAK, exact=True) is False
    assert fake_kwin.activated is None
    fake_kwin.windows.append('Object.assign(makeWindow("firefox", "{f}", 0, 0, 9, 9),'
                             ' {desktopFileName: "org.mozilla.firefox"})')
    assert app_focus.focus_running(FIREFOX_FLATPAK, exact=True) is True
    assert fake_kwin.activated == "{f}"


def test_kde_shows_a_minimized_window(kde, fake_kwin):
    fake_kwin.windows = ['Object.assign(makeWindow("firefox", "{f}", 0, 0, 9, 9),'
                         ' {minimized: true})']
    assert app_focus.focus_running(FIREFOX) is True
    assert fake_kwin.minimized == [False]


def test_kde_never_brings_forward_a_popup_of_the_app(kde, fake_kwin):
    """A menu or tooltip of Firefox has its class too, but isn't a window
    to switch to: with only that, Firefox is started."""
    fake_kwin.windows = ['Object.assign(makeWindow("firefox", "{tip}", 0, 0, 9, 9),'
                         ' {normalWindow: false})']
    assert app_focus.focus_running(FIREFOX) is False
    assert fake_kwin.activated is None


def test_kde_never_takes_the_launchers_window_for_the_app(kde, fake_kwin):
    """Steam's library or Chrome's own window open: "open Portal 2" and
    "open YouTube Music" start the game and the web app, not show Steam and
    Chrome."""
    fake_kwin.windows = ['makeWindow("steam", "{s}", 0, 0, 800, 600)',
                         'makeWindow("google-chrome", "{c}", 0, 0, 800, 600)']
    assert app_focus.focus_running(PORTAL) is False
    assert app_focus.focus_running(YOUTUBE_MUSIC) is False
    assert app_focus.focus_running(YOUTUBE_MUSIC_NAMED) is False
    assert fake_kwin.activated is None


def test_kde_finds_a_web_app_by_the_class_its_entry_names(kde, fake_kwin):
    """An X11 window of a Chrome web app has the class StartupWMClass says,
    above Chrome's own window."""
    fake_kwin.windows = ['makeWindow("google-chrome", "{c}", 0, 0, 800, 600)',
                         'makeWindow("crx_cinhimbnkkaeohfgghhklpknlkffjgod", "{w}", 0, 0, 9, 9)',
                         'makeWindow("google-chrome", "{c2}", 0, 0, 800, 600)']
    assert app_focus.focus_running(YOUTUBE_MUSIC_NAMED) is True
    assert fake_kwin.activated == "{w}"


def test_kde_without_the_apps_window_says_launch(kde, fake_kwin):
    fake_kwin.windows = ['makeWindow("org.kde.konsole", "{k}", 0, 0, 800, 600)']
    assert app_focus.focus_running(FIREFOX) is False
    assert fake_kwin.activated is None


def test_a_kwin_that_never_answers_means_launch(kde, fake_kwin):
    fake_kwin.silent = True
    assert app_focus.focus_running(FIREFOX, timeout=0.05) is False


def test_a_refused_script_means_launch(kde, fake_kwin):
    """loadScript says -1 when a script of that name is still loaded."""
    fake_kwin.load_result = -1
    assert app_focus.focus_running(FIREFOX) is False
    assert "start" not in fake_kwin.methods


def test_the_script_is_taken_out_of_kwin_afterwards(kde, fake_kwin):
    """One left loaded would be started again by the next script's start(),
    bringing Firefox forward a second time; one from a run that stopped
    halfway goes before loading."""
    fake_kwin.windows = ['makeWindow("firefox", "{f}", 0, 0, 800, 600)']
    app_focus.focus_running(FIREFOX)
    assert fake_kwin.methods == ["unloadScript", "loadScript", "start", "unloadScript"]


def test_an_answer_from_an_earlier_script_never_counts(kde, fake_kwin):
    """Each attempt listens on a connection of its own: a slow script's late
    "yes" goes to a name that's gone, not to the next attempt."""
    fake_kwin.windows = ['makeWindow("firefox", "{f}", 0, 0, 800, 600)']
    app_focus.focus_running(FIREFOX)
    first = fake_kwin.scripts[-1]
    fake_kwin.silent = True
    app_focus.focus_running(FIREFOX, timeout=0.05)
    assert fake_kwin.scripts[-1] != first


# --- GNOME ------------------------------------------------------------------------------


def test_gnome_asks_the_extension(gnome, fake_extension):
    assert app_focus.focus_running(FIREFOX) is True
    assert fake_extension.called == ["firefox.desktop"]


def test_gnome_without_the_apps_window_says_launch(gnome, fake_extension):
    fake_extension.answer = False
    assert app_focus.focus_running(FIREFOX) is False


def test_gnome_without_the_extension_means_launch(gnome):
    """The extension turned off, or never installed (Ubuntu's GNOME with
    the tray instead): nobody to ask, so Firefox is started."""
    shell = FakeShell(extension=False)
    try:
        assert app_focus.focus_running(FIREFOX) is False
    finally:
        shell.stop()


def test_gnome_shell_not_on_the_bus_means_launch(gnome):
    assert app_focus.focus_running(FIREFOX) is False


# --- X11 --------------------------------------------------------------------------------


def test_x11_activates_through_net_active_window(window_manager):
    """Source indication 2, a taskbar's: window managers that keep other
    programs from stealing the focus still obey it."""
    firefox = window_manager.window("Navigator", "firefox")
    window_manager.stack([window_manager.window("xfce4-terminal", "Xfce4-terminal"), firefox])
    assert app_focus.focus_running(FIREFOX) is True
    assert window_manager.activations() == [(firefox, 2)]


def test_x11_picks_the_window_used_last(window_manager):
    older = window_manager.window("Navigator", "firefox")
    newer = window_manager.window("Navigator", "firefox")
    window_manager.stack([older, newer, window_manager.window("mousepad", "Mousepad")])
    assert app_focus.focus_running(FIREFOX) is True
    assert window_manager.activations() == [(newer, 2)]


def test_x11_matches_the_class_whatever_its_case(window_manager):
    """Older Firefox and many apps capitalise their class ("Firefox")."""
    firefox = window_manager.window("Navigator", "Firefox")
    window_manager.stack([firefox])
    assert app_focus.focus_running(FIREFOX) is True
    assert window_manager.activations() == [(firefox, 2)]


def test_x11_never_takes_the_launchers_window_for_the_app(window_manager):
    window_manager.stack([window_manager.window("Steam", "steam"),
                          window_manager.window("google-chrome", "Google-chrome")])
    assert app_focus.focus_running(PORTAL) is False
    assert app_focus.focus_running(YOUTUBE_MUSIC) is False
    assert app_focus.focus_running(YOUTUBE_MUSIC_NAMED) is False
    assert window_manager.activations() == []


def test_x11_finds_a_web_app_by_the_class_its_entry_names(window_manager):
    web_app = window_manager.window("crx_cinhimbnkkaeohfgghhklpknlkffjgod",
                                    "Google-chrome")
    web_app_class = window_manager.window("google-chrome",
                                          "crx_cinhimbnkkaeohfgghhklpknlkffjgod")
    window_manager.stack([web_app_class, web_app])
    assert app_focus.focus_running(YOUTUBE_MUSIC_NAMED) is True
    assert window_manager.activations() == [(web_app_class, 2)]


def test_x11_knows_a_gtk_window_by_its_application_id(window_manager):
    """Mint's own apps (xed, GNOME Terminal) have reverse-DNS menu entries
    and a class named after the program, which the entry doesn't name: GTK
    puts the entry's id on the window."""
    xed = _entry("org.x.editor.desktop", "Text Editor", "xed")
    terminal = _entry("org.gnome.Terminal.desktop", "Terminal", "gnome-terminal")
    editor = window_manager.window("xed", "Xed", _GTK_APPLICATION_ID="org.x.editor")
    shell = window_manager.window("gnome-terminal-server", "Gnome-terminal",
                                  _GTK_APPLICATION_ID="org.gnome.Terminal")
    window_manager.stack([editor, shell])
    assert app_focus.focus_running(xed) is True
    assert app_focus.focus_running(terminal) is True
    assert window_manager.activations() == [(editor, 2), (shell, 2)]


def test_x11_knows_a_kde_window_by_its_desktop_file(window_manager):
    entry = _entry("org.kde.filelight.desktop", "Filelight", "filelight-launcher")
    window = window_manager.window("filelight", "filelight",
                                   _KDE_NET_WM_DESKTOP_FILE="org.kde.filelight")
    window_manager.stack([window])
    assert app_focus.focus_running(entry) is True
    assert window_manager.activations() == [(window, 2)]


def test_x11_never_takes_another_copys_window_for_the_one_asked_for(window_manager):
    """Firefox doesn't name its menu entry on the window: with two copies
    installed, which one a "firefox" window is can't be told, so the one
    asked for is started."""
    window_manager.stack([window_manager.window("Navigator", "firefox")])
    assert app_focus.focus_running(FIREFOX_FLATPAK, exact=True) is False
    assert app_focus.focus_running(FIREFOX, exact=True) is False
    assert window_manager.activations() == []


def test_x11_without_the_apps_window_says_launch(window_manager):
    window_manager.stack([window_manager.window("mousepad", "Mousepad")])
    assert app_focus.focus_running(FIREFOX) is False
    assert window_manager.activations() == []


def test_x11_without_a_window_manager_list_says_launch(window_manager):
    window_manager.window("Navigator", "firefox")
    assert app_focus.focus_running(FIREFOX) is False


def test_no_x_server_means_launch(monkeypatch):
    set_desktop(monkeypatch, "XFCE", "x11")
    monkeypatch.setenv("DISPLAY", ":9999")
    assert app_focus.focus_running(FIREFOX) is False


# --- everywhere else ----------------------------------------------------------------------

def test_other_desktops_say_launch(cosmic, nobody_is_asked):
    """COSMIC, Sway, Hyprland: no way to bring a window forward is known, so
    the app is started; most then show their window."""
    assert app_focus.focus_running(FIREFOX) is False


def test_wayland_on_another_desktop_never_asks_xwayland(monkeypatch, nobody_is_asked):
    """XWayland knows only the X11 apps, and can't move the focus to them
    under a Wayland compositor."""
    set_desktop(monkeypatch, "sway", "wayland")
    monkeypatch.setenv("DISPLAY", ":0")
    assert app_focus.focus_running(FIREFOX) is False


def test_an_error_means_launch_and_is_logged(gnome, monkeypatch, caplog):
    def broken(*_args, **_kwargs):
        raise RuntimeError("the bus went away")
    monkeypatch.setattr(app_focus, "call_extension", broken)
    with caplog.at_level("INFO"):
        assert app_focus.focus_running(FIREFOX) is False
    assert "the bus went away" in caplog.text
