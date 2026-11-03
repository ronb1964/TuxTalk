"""Where TuxTalk's own icon file is, for the windows that show it (the
splash screen and About) and the start-at-login entry: inside the running
AppImage, beside the source, where a .deb or .rpm put it, or where first run
installed it for the user."""
import os

from .config import APP_ID

_SCALABLE = f"share/icons/hicolor/scalable/apps/{APP_ID}.svg"


def user_icon_file():
    """Where first run puts the icon for the user (desktop_launcher's
    install_app_icon), at its largest size."""
    return os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps/tuxtalk.png")


def icon_files():
    """Where the icon may be, best first; an SVG renders more cleanly at
    256 px than a PNG does. All absolute: which icon shows mustn't depend on
    the folder TuxTalk was started from."""
    paths = []
    appdir = os.environ.get('APPDIR')       # a running AppImage, with a PNG at its root
    if appdir:
        paths += [os.path.join(appdir, f"{APP_ID}.png"),
                  os.path.join(appdir, f"{APP_ID}.svg"),
                  os.path.join(appdir, "usr", _SCALABLE)]
    # The project's own icons/, beside src/, when running from the source.
    project_icons = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "icons")
    paths += [
        os.path.join(project_icons, "OFFICIAL_ICON_DO_NOT_CHANGE.svg"),
        f"/app/{_SCALABLE}",
        # System install locations (.deb / .rpm): the hicolor SVG, then the
        # pixmaps PNG. A packaged install has no $APPDIR and no project
        # icons/ folder.
        f"/usr/{_SCALABLE}",
        f"/usr/local/{_SCALABLE}",
        f"/usr/share/icons/hicolor/256x256/apps/{APP_ID}.png",
        f"/usr/share/pixmaps/{APP_ID}.png",
        # The PNG that ships inside the package: the fallback for installs
        # with no project folder around them (a pip install into a venv).
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", f"{APP_ID}.png"),
        user_icon_file(),
    ]
    return paths


def icon_file():
    """The first icon file there is, or None."""
    return next((path for path in icon_files() if os.path.isfile(path)), None)
