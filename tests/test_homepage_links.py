"""TuxTalk's homepage is https://tuxtalk.app/ wherever a package or the app
names one; the code stays linked on GitHub as its own entry."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = "https://tuxtalk.app/"


def test_software_centers_show_the_website_and_the_code():
    appdata = (ROOT / "io.github.ronb1964.TuxTalk.appdata.xml").read_text()
    assert f'<url type="homepage">{SITE}</url>' in appdata
    assert '<url type="vcs-browser">https://github.com/ronb1964/TuxTalk</url>' in appdata


def test_deb_and_rpm_name_the_website():
    for script in ("build-deb.sh", "build-rpm.sh"):
        assert re.search(r'--url "https://tuxtalk\.app/"', (ROOT / script).read_text()), script


def test_about_window_links_the_website_before_github(about_window):
    from gi.repository import Gtk
    from gtk_text import widgets
    links = [(w.get_uri(), w.get_label()) for w in widgets(about_window)
             if isinstance(w, Gtk.LinkButton)]
    assert links[:2] == [(SITE, "Website"),
                         ("https://github.com/ronb1964/TuxTalk", "GitHub Repository")]
