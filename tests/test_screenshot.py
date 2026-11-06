"""Take a screenshot by voice (screenshot.py). A recorder stands in for every
program run and the portal is a fake on the test's bus: no test takes a real
screenshot."""
import logging
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from tuxtalk import screen_message, screenshot, ui_style


class Tools:
    """Records each command run. *installed* are the programs found; a
    command for one of them writes its output file unless *writes* is off."""

    def __init__(self, installed):
        self.installed = installed
        self.commands = []
        self.writes = True
        self.exit_code = 0

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.installed else None

    def __call__(self, cmd, **kw):
        assert kw.get("timeout") and not kw.get("shell")
        self.commands.append(list(cmd))
        if self.writes and self.exit_code == 0:
            with open(cmd[-1], "wb") as f:
                f.write(b"png")
        return subprocess.CompletedProcess(cmd, self.exit_code, b"", b"")


def install(monkeypatch, *names):
    tools = Tools(names)
    monkeypatch.setattr(screenshot.subprocess, "run", tools)
    monkeypatch.setattr(screenshot.shutil, "which", tools.which)
    return tools


@pytest.fixture
def tools(monkeypatch):
    return install(monkeypatch, "spectacle", "gnome-screenshot", "xfce4-screenshooter")


@pytest.fixture
def no_tools(monkeypatch):
    return install(monkeypatch)


@pytest.fixture
def home(monkeypatch, tmp_path):
    """HOME and the Pictures folder are tmp_path's, so nothing lands in Ron's."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(screenshot.GLib, "get_user_special_dir",
                        lambda _which: str(tmp_path / "Pictures"))
    return tmp_path


def test_kde_saves_with_spectacle_in_the_background(kde, tools, home, actions_on):
    assert screenshot.take(False) is True
    command = tools.commands[0]
    assert command[:5] == ["spectacle", "--background", "--fullscreen", "--nonotify",
                           "--output"]
    assert command[5].startswith(str(home / "Pictures" / "Screenshots" / "Screenshot_"))
    assert actions_on == ["Screenshot saved in ~/Pictures/Screenshots"]


def test_gnome_asks_the_portal_and_says_where_it_saved(gnome, fake_portal, tools, home,
                                                       actions_on):
    assert screenshot.take(False) is True
    assert fake_portal.calls[0]["interactive"] is False
    assert (home / "Pictures" / "Screenshot from the portal.png").exists()
    assert actions_on == ["Screenshot saved in ~/Pictures"]
    assert tools.commands == []


@pytest.mark.parametrize("own_tmpdir", [False, True])
def test_a_portal_shot_left_in_a_temporary_folder_is_kept_with_the_others(
        cosmic, fake_portal, tools, home, actions_on, monkeypatch, own_tmpdir):
    """COSMIC's portal answers with a file in /tmp: TuxTalk said "Screenshot
    saved in //tmp" (Pop!_OS VM), a folder emptied at the next start. With
    TuxTalk's own TMPDIR elsewhere (pam_tmpdir), /tmp still counts: the
    portal's folder here is pytest's, which is in /tmp."""
    monkeypatch.setattr(screenshot.tempfile, "gettempdir",
                        lambda: str(home / "tmp") if own_tmpdir else str(fake_portal.temporary))
    fake_portal.saved = "temporary"
    assert screenshot.take(False) is True
    kept = list((home / "Pictures" / "Screenshots").glob("Screenshot_*.png"))
    assert [shot.read_bytes() for shot in kept] == [b"png"]
    assert not any(fake_portal.temporary.iterdir())
    assert actions_on == ["Screenshot saved in ~/Pictures/Screenshots"]
    assert tools.commands == []


@pytest.mark.parametrize("where", ["pictures", "documents"])
def test_a_portal_shot_in_a_kept_folder_stays_where_it_is(gnome, fake_portal, tools, home,
                                                          actions_on, monkeypatch, where):
    """A Pictures folder that is itself under /tmp keeps its files; and the
    document portal's folder (in XDG_RUNTIME_DIR) shows a file kept
    elsewhere, so moving from it would take that file away."""
    root = str(fake_portal.temporary)
    if where == "pictures":
        monkeypatch.setattr(screenshot.GLib, "get_user_special_dir", lambda _which: root)
    else:
        monkeypatch.setenv("XDG_RUNTIME_DIR", root)
    monkeypatch.setattr(screenshot.tempfile, "gettempdir",
                        lambda: root if where == "pictures" else "/nonexistent-tmp")
    fake_portal.saved = "temporary"
    assert screenshot.take(False) is True
    assert [p.name for p in fake_portal.temporary.iterdir()] == ["Screenshot from the portal.png"]


def test_a_link_left_in_a_temporary_folder_is_never_moved(cosmic, fake_portal, tools, home,
                                                          actions_on):
    """In /tmp another user could plant a link where the portal's file was
    to be; moving it would bring that user's file in among the pictures."""
    fake_portal.saved = "link"
    screenshot.take(False)
    assert not list((home / "Pictures").glob("**/*.png"))
    assert (fake_portal.temporary / "Screenshot from the portal.png").is_symlink()
    assert (fake_portal.temporary / "someone else's.png").read_bytes() == b"theirs"


def test_a_pictures_folder_reached_through_a_link_is_named_as_the_user_knows_it(
        gnome, fake_portal, tools, home, actions_on, tmp_path_factory):
    """The note said the link's target ("/mnt/data/Pictures"), not ~/Pictures."""
    elsewhere = tmp_path_factory.mktemp("data-drive")
    (home / "Pictures").symlink_to(elsewhere)
    assert screenshot.take(False) is True
    assert actions_on == ["Screenshot saved in ~/Pictures"]


def test_a_portal_answer_with_a_malformed_address_is_unconfirmed_and_logs_no_path(
        gnome, fake_portal, tools, home, actions_on, caplog):
    """GLib's error for a malformed address quotes it, and with it the path."""
    fake_portal.saved = "malformed"
    with caplog.at_level(logging.DEBUG, logger="tuxtalk"):
        assert screenshot.take(False) is False
    assert actions_on == ["Screenshot not confirmed"]
    assert "private-name" not in caplog.text
    assert tools.commands == []


def test_a_cancelled_portal_saves_nothing_and_tries_no_tool(gnome, fake_portal, tools,
                                                            home, actions_on):
    fake_portal.code = 1
    assert screenshot.take(False) is False
    assert actions_on == ["Screenshot cancelled"]
    assert tools.commands == []


def test_a_portal_that_failed_falls_back_to_the_desktops_tool(gnome, fake_portal, tools,
                                                              home, actions_on):
    fake_portal.code = 2
    assert screenshot.take(False) is True
    assert tools.commands[0][:2] == ["gnome-screenshot", "-f"]


def test_without_a_portal_the_desktops_tool_is_used(x11_session, tools, home, actions_on):
    assert screenshot.take(False) is True
    assert tools.commands[0][:2] == ["gnome-screenshot", "-f"]
    assert actions_on == ["Screenshot saved in ~/Pictures/Screenshots"]


def test_xfce_screenshooter_is_the_second_choice(x11_session, monkeypatch, home):
    tools = install(monkeypatch, "xfce4-screenshooter")
    assert screenshot.take(False) is True
    assert tools.commands[0][:3] == ["xfce4-screenshooter", "-f", "-s"]


def test_with_no_way_to_take_one_says_so(cosmic, no_tools, home, actions_on):
    assert screenshot.take(False) is False
    assert actions_on == ["Couldn't take a screenshot"]


def test_spectacle_that_wrote_no_file_isnt_called_saved(kde, tools, home, actions_on):
    tools.writes = False
    assert screenshot.take(False) is False
    assert actions_on == ["Couldn't take a screenshot"]


def test_a_failed_spectacle_is_not_followed_by_the_portal(kde, fake_portal, tools, home,
                                                          actions_on):
    tools.exit_code = 1
    assert screenshot.take(False) is False
    assert actions_on == ["Couldn't take a screenshot"]
    assert fake_portal.calls == []
    assert len(tools.commands) == 1


def test_a_spectacle_that_opened_a_window_gets_no_second_attempt(kde, fake_portal, tools,
                                                                 home, actions_on):
    tools.writes = False
    assert screenshot.take(False) is False
    assert actions_on == ["Couldn't take a screenshot"]
    assert fake_portal.calls == [] and len(tools.commands) == 1


def test_kde_without_spectacle_asks_the_portal(kde, fake_portal, no_tools, home, actions_on):
    assert screenshot.take(False) is True
    assert len(fake_portal.calls) == 1


def test_a_portal_that_never_answers_is_not_followed_by_a_tool(gnome, fake_portal, tools,
                                                               home, actions_on, monkeypatch):
    monkeypatch.setattr(screenshot, "PORTAL_WAIT_S", 0.2)
    fake_portal.code = None
    assert screenshot.take(False) is False
    assert tools.commands == []
    assert actions_on == ["Screenshot not confirmed"]


def test_a_portal_save_it_cant_see_is_still_called_saved(gnome, fake_portal, tools, home,
                                                         actions_on):
    fake_portal.saved = "unseen"
    assert screenshot.take(False) is True
    assert actions_on == ["Screenshot saved"]
    assert tools.commands == []


def test_a_portal_done_without_a_file_is_unconfirmed_and_no_tool_runs(gnome, fake_portal,
                                                                      tools, home, actions_on):
    """It may have saved one all the same: a second tool would take another."""
    fake_portal.saved = "nothing"
    assert screenshot.take(False) is False
    assert actions_on == ["Screenshot not confirmed"]
    assert tools.commands == []


def test_a_portal_done_with_a_file_name_it_cant_read_is_unconfirmed(gnome, fake_portal, tools,
                                                                    home, actions_on):
    """The portal said done: a picture may be saved, so no tool takes another."""
    fake_portal.saved = "unreadable"
    assert screenshot.take(False) is False
    assert actions_on == ["Screenshot not confirmed"]
    assert tools.commands == []


def test_a_portal_call_that_times_out_is_unconfirmed_and_no_tool_runs(gnome, fake_portal,
                                                                      tools, home, actions_on,
                                                                      monkeypatch):
    monkeypatch.setattr(screenshot, "PORTAL_CALL_TIMEOUT_MS", 200)
    fake_portal.replies = False
    assert screenshot.take(False) is False
    assert len(fake_portal.calls) == 1
    assert actions_on == ["Screenshot not confirmed"]
    assert tools.commands == []


def test_the_portal_is_asked_without_making_a_folder(gnome, fake_portal, tools, home,
                                                     actions_on):
    """The portal saves where it likes: an empty Screenshots folder would be litter."""
    screenshot.take(False)
    assert not (home / "Pictures" / "Screenshots").exists()


@pytest.fixture
def same_second(monkeypatch):
    """Every screenshot is taken in the same second."""
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 5, 14, 30, 0)
    monkeypatch.setattr(screenshot, "datetime", Clock)


def test_two_screenshots_in_one_second_are_both_kept(kde, tools, home, actions_on,
                                                     same_second):
    assert screenshot.take(False) is True and screenshot.take(False) is True
    first, second = (command[-1] for command in tools.commands)
    assert first != second
    assert Path(first).read_bytes() == Path(second).read_bytes() == b"png"


def test_an_older_file_with_the_name_is_not_taken_for_this_screenshot(kde, tools, home,
                                                                      actions_on, same_second):
    older = home / "Pictures" / "Screenshots" / "Screenshot_20261105_143000.png"
    older.parent.mkdir(parents=True)
    older.write_bytes(b"older")
    tools.writes = False
    assert screenshot.take(False) is False
    assert actions_on == ["Couldn't take a screenshot"]
    assert older.read_bytes() == b"older"


def test_an_empty_file_is_not_a_screenshot(kde, tools, home, actions_on, monkeypatch):
    def empty(cmd, **kw):
        tools.commands.append(list(cmd))
        open(cmd[-1], "wb").close()
        return subprocess.CompletedProcess(cmd, 0, b"", b"")
    monkeypatch.setattr(screenshot.subprocess, "run", empty)
    assert screenshot.take(False) is False
    assert actions_on == ["Couldn't take a screenshot"]


def test_only_the_first_installed_tool_is_tried(x11_session, monkeypatch, home):
    tools = install(monkeypatch, "gnome-screenshot", "xfce4-screenshooter")
    tools.writes = False
    assert screenshot.take(False) is False
    assert len(tools.commands) == 1


def test_the_path_never_reaches_the_log(kde, tools, home, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        screenshot.take(False)
    assert "take a screenshot (saved" in caplog.text
    assert str(home) not in caplog.text


def test_the_folder_never_reaches_the_log_where_the_note_cant_float(monkeypatch, caplog):
    """Without an X display the label isn't shown, and the log says so
    without the label, which can name a folder."""
    monkeypatch.setattr(ui_style, "is_x11", lambda _display: False)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert screen_message._show_now("Screenshot saved in /media/ron/Pictures", None) is False
    assert "no X display" in caplog.text
    assert "/media/ron" not in caplog.text


def test_a_timeout_never_puts_the_path_in_the_log(kde, home, monkeypatch, caplog):
    def too_slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 10)
    install(monkeypatch, "spectacle")
    monkeypatch.setattr(screenshot.subprocess, "run", too_slow)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert screenshot.take(False) is False
    assert "spectacle" in caplog.text
    assert str(home) not in caplog.text


def test_the_folder_is_the_desktops_pictures_folder(home):
    assert screenshot.folder() == str(home / "Pictures" / "Screenshots")


def test_without_a_pictures_folder_it_is_home_pictures(home, monkeypatch):
    monkeypatch.setattr(screenshot.GLib, "get_user_special_dir", lambda _which: None)
    assert screenshot.folder() == str(home / "Pictures" / "Screenshots")


def test_the_name_is_spectacles_own(home):
    path = screenshot.new_path()
    assert path.startswith(str(home / "Pictures" / "Screenshots" / "Screenshot_"))
    assert path.endswith(".png")
    assert (home / "Pictures" / "Screenshots").is_dir()


def test_take_a_screenshot_said_alone_types_nothing(actions_on, chat, say, kde, tools, home):
    say("Take a screenshot.")
    assert chat[2] == []
    assert tools.commands[0][0] == "spectacle"
    assert actions_on == ["Screenshot saved in ~/Pictures/Screenshots"]
