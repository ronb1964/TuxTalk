"""Open apps by voice (TuxTalk Labs): which installed app a spoken name means."""
import pytest

from tuxtalk.app_commands import parse
from tuxtalk import app_finder
from tuxtalk.app_finder import AppEntry, find, install_kind, program_name


def entry(app_id, name, generic="", keywords=(), program="", install=""):
    return AppEntry(app_id, name, name, generic, tuple(keywords), program, install)


@pytest.fixture
def apps():
    return [
        entry("firefox.desktop", "Firefox", "Web Browser", ("web", "internet"), "firefox"),
        entry("org.mozilla.firefox.desktop", "Firefox", "Web Browser", ("web",), "",
              "flatpak"),
        entry("org.kde.konsole.desktop", "Konsole", "Terminal", ("shell", "terminal"),
              "konsole"),
        entry("kitty.desktop", "kitty", "Terminal emulator", ("terminal",), "kitty"),
        entry("org.kde.kwrite.desktop", "KWrite", "Text Editor", (), "kwrite"),
        entry("org.kde.kate.desktop", "Kate", "Text Editor", (), "kate"),
        entry("eu.betterbird.Betterbird.desktop", "Betterbird", "Email Client",
              ("mail",), "betterbird"),
        entry("libreoffice-writer.desktop", "LibreOffice Writer", "Word Processor", (),
              "libreoffice"),
        entry("libreoffice-calc.desktop", "LibreOffice Calc", "Spreadsheet", (),
              "libreoffice"),
        entry("org.kde.kcalc.desktop", "KCalc", "Calculator", ("calculator",), "kcalc"),
    ]


def ids(found):
    return [e.app_id for e in found.entries]


def test_exact_and_split_names(apps):
    assert ids(find(parse("open firefox"), apps, {})) == ["firefox.desktop"]
    assert ids(find(parse("open fire fox"), apps, {})) == ["firefox.desktop"]
    assert ids(find(parse("open k write"), apps, {})) == ["org.kde.kwrite.desktop"]


def test_respellings_close_enough(apps):
    assert ids(find(parse("open console"), apps, {})) == ["org.kde.konsole.desktop"]
    assert ids(find(parse("open better bird"), apps, {})) == [
        "eu.betterbird.Betterbird.desktop"]
    assert ids(find(parse("open fierfox"), apps, {})) == ["firefox.desktop"]


def test_generic_names_and_word_starts(apps):
    assert ids(find(parse("open the calculator"), apps, {})) == ["org.kde.kcalc.desktop"]
    assert ids(find(parse("open libre writer"), apps, {})) == ["libreoffice-writer.desktop"]
    assert ids(find(parse("open calc"), apps, {})) == ["libreoffice-calc.desktop"]


def test_the_name_wins_over_a_program_shared_by_other_apps(apps):
    # Both LibreOffice entries run "libreoffice"; the program name only counts
    # when no app is called that.
    assert find(parse("open libreoffice"), apps, {}).kind == "tie"
    start_center = apps + [entry("libreoffice-startcenter.desktop", "LibreOffice")]
    assert ids(find(parse("open libreoffice"), start_center, {})) == [
        "libreoffice-startcenter.desktop"]


def test_same_app_twice_is_not_a_tie(apps):
    assert ids(find(parse("open firefox"), apps, {})) == ["firefox.desktop"]
    assert ids(find(parse("open firefox flatpak"), apps, {})) == [
        "org.mozilla.firefox.desktop"]


def test_a_missing_copy_falls_back_to_the_app_that_is_there(apps):
    assert ids(find(parse("open firefox snap"), apps, {})) == ["firefox.desktop"]


def test_default_words_go_to_the_default(apps):
    assert ids(find(parse("open the browser"), apps, {"browser": "firefox.desktop"})) == [
        "firefox.desktop"]
    assert ids(find(parse("open terminal"), apps, {"terminal": "org.kde.konsole.desktop"})) == [
        "org.kde.konsole.desktop"]
    assert ids(find(parse("open email"), apps, {"email": "eu.betterbird.Betterbird.desktop"})) == [
        "eu.betterbird.Betterbird.desktop"]


def test_a_default_that_is_not_installed_is_ignored(apps):
    found = find(parse("open terminal"), apps, {"terminal": "gone.desktop"})
    assert found.kind == "tie"


def test_two_different_apps_tie_without_a_default(apps):
    found = find(parse("open terminal"), apps, {})
    assert found.kind == "tie"
    assert sorted(ids(found)) == ["kitty.desktop", "org.kde.konsole.desktop"]


def test_nothing_close_is_none(apps):
    assert find(parse("open fierfox zz"), apps, {}).kind == "none"
    assert find(parse("open the door"), apps, {}).kind == "none"
    assert find(parse("open source is great"), apps, {}).kind == "none"


def test_odd_entries_never_crash_or_win():
    odd = [AppEntry("x.desktop", "", "", "", (), "", ""),
           AppEntry("y.desktop", "Tool 2.0 & Co", "", "", (), "tool", "")]
    assert find(parse("open tool"), odd, {}).kind in ("one", "none")
    assert find(parse("open tool"), [], {}).kind == "none"


def test_program_name_of_a_menu_entry_command():
    assert program_name("/usr/bin/firefox %u") == "firefox"
    assert program_name("env BAMF_DESKTOP_FILE_HINT=x /snap/bin/firefox %u") == "firefox"
    assert program_name("/usr/bin/flatpak run --branch=stable --command=firefox "
                        "org.mozilla.firefox @@u %u @@") == "firefox"
    assert program_name("") == ""
    assert program_name("'unclosed") == ""


def test_install_kind_from_the_command():
    assert install_kind("flatpak run org.mozilla.firefox") == "flatpak"
    assert install_kind("env X=1 /snap/bin/firefox %u") == "snap"
    assert install_kind("/home/ron/Apps/Tool-1.2.AppImage --no-sandbox") == "appimage"
    assert install_kind("/usr/bin/firefox %u") == ""


class FakeMenuApp:
    def __init__(self, app_id, command, categories=""):
        self.app_id, self.command, self.categories = app_id, command, categories

    def get_id(self):
        return self.app_id

    def get_commandline(self):
        return self.command

    def get_categories(self):
        return self.categories


def test_kde_terminal_setting_is_read(tmp_path, monkeypatch):
    kdeglobals = tmp_path / "kdeglobals"
    kdeglobals.write_text("[General]\nTerminalApplication=kitty\n")
    monkeypatch.setattr(app_finder, "_KDEGLOBALS", str(kdeglobals))
    assert app_finder._kde_terminal() == "kitty"


def test_a_broken_or_missing_kdeglobals_means_no_setting(tmp_path, monkeypatch):
    kdeglobals = tmp_path / "kdeglobals"
    kdeglobals.write_bytes(b"[General]\nTerminalApplication=kon\xffsole\n")
    monkeypatch.setattr(app_finder, "_KDEGLOBALS", str(kdeglobals))
    assert app_finder._kde_terminal().startswith("kon")
    kdeglobals.write_text("no section header\n")
    assert app_finder._kde_terminal() == ""
    monkeypatch.setattr(app_finder, "_KDEGLOBALS", str(tmp_path / "missing"))
    assert app_finder._kde_terminal() == ""


def test_terminal_is_kdes_choice_else_the_only_emulator():
    konsole = FakeMenuApp("konsole.desktop", "konsole", "System;TerminalEmulator;")
    kitty = FakeMenuApp("kitty.desktop", "/usr/bin/kitty", "System;TerminalEmulator;")
    other = FakeMenuApp("kate.desktop", "kate", "Utility;")
    assert app_finder._terminal_id([konsole, kitty, other], "kitty") == "kitty.desktop"
    assert app_finder._terminal_id([konsole, other], "") == "konsole.desktop"
    assert app_finder._terminal_id([konsole, kitty], "") is None


def test_the_class_an_entry_names_for_its_windows_is_read(tmp_path, monkeypatch):
    """StartupWMClass tells a Chrome web app's windows from Chrome's own
    (app_focus.window_names). Exec runs `true`: GLib turns away an entry
    whose program isn't installed."""
    from gi.repository import Gio
    path = tmp_path / "chrome-abc-Default.desktop"
    path.write_text("[Desktop Entry]\nType=Application\nName=YouTube Music\n"
                    "Exec=true --app-id=abc\nStartupWMClass=crx_abc\n")
    plain = tmp_path / "plain.desktop"
    plain.write_text("[Desktop Entry]\nType=Application\nName=Plain\nExec=true\n")
    monkeypatch.setattr(app_finder, "_desktop_apps", lambda: [
        Gio.DesktopAppInfo.new_from_filename(str(path)),
        Gio.DesktopAppInfo.new_from_filename(str(plain))])
    assert [e.wm_class for e in app_finder.installed_apps()] == ["crx_abc", ""]
