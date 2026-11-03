"""The About dialog finds TuxTalk's icon.

No install has hicolor/128x128/apps/talktype.png or a src/tuxtalk/icons
folder, so looking only there would leave About without an icon.
"""
from tuxtalk import about_dialog, app_icon


def test_a_real_icon_file_is_used(monkeypatch, tmp_path):
    svg = tmp_path / "icon.svg"
    svg.write_text("<svg/>")
    monkeypatch.setattr(app_icon, "icon_files", lambda: [str(tmp_path / "gone.png"), str(svg)])
    assert about_dialog.about_icon_source(lambda name: False) == ("file", str(svg))


def test_the_appimage_bundled_svg_is_used(monkeypatch, tmp_path):
    (tmp_path / "io.github.ronb1964.TuxTalk.svg").write_text("<svg/>")
    monkeypatch.setenv("APPDIR", str(tmp_path))
    assert about_dialog.about_icon_source(lambda name: False) == (
        "file", str(tmp_path / "io.github.ronb1964.TuxTalk.svg"))


def test_the_icon_theme_is_the_fallback(monkeypatch):
    monkeypatch.setattr(app_icon, "icon_files", lambda: [])
    assert about_dialog.about_icon_source(lambda name: name == "tuxtalk") == ("icon", "tuxtalk")
    assert about_dialog.about_icon_source(lambda name: True) == (
        "icon", "io.github.ronb1964.TuxTalk")
    assert about_dialog.about_icon_source(lambda name: False) is None


def test_release_notes_go_into_their_own_about_window_only_while_it_is_open(monkeypatch):
    """The notes come from GitHub while About is open. They land in the window
    that asked for them, and in none once it's closed: its box is gone."""
    import types
    from tuxtalk import notes_text, update_checker
    shown, later = [], []
    real_show = notes_text.NotesView.show
    monkeypatch.setattr(notes_text.NotesView, "show",
                        lambda self, text: shown.append(text) or real_show(self, text))
    monkeypatch.setattr(about_dialog.GLib, "idle_add", lambda fn, *a: later.append((fn, a)))
    monkeypatch.setattr(about_dialog.threading, "Thread",
                        lambda target, daemon: types.SimpleNamespace(start=target))
    monkeypatch.setattr(update_checker, "fetch_release_by_tag", lambda tag: {"body": "New things"})

    def open_about():
        about_dialog.show_about_dialog()
        return about_dialog._open_dialog

    window = open_about()
    window.destroy()                    # closed before the notes came back
    for fn, args in later:
        fn(*args)
    assert shown == ["Loading release notes..."]

    later.clear()
    window = open_about()
    try:
        for fn, args in later:
            fn(*args)
        assert shown[-1] == "New things"
    finally:
        window.destroy()


def test_opening_about_again_brings_the_open_one_forward(about_window, monkeypatch):
    """Each click opened another About, and asked GitHub for the notes again."""
    from tuxtalk import raise_window, update_checker
    raised, asked = [], []
    monkeypatch.setattr(raise_window, "raise_window", raised.append)
    monkeypatch.setattr(update_checker, "fetch_release_by_tag", asked.append)
    about_dialog.show_about_dialog()
    assert raised == [about_window] and asked == []
    assert about_dialog._open_dialog is about_window
