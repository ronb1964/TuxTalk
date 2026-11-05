"""Take a screenshot by voice (screenshot.py). A recorder stands in for every
program run and the portal is a fake on the test's bus: no test takes a real
screenshot."""
import logging
import subprocess

import pytest

from tuxtalk import screenshot


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
                                                         actions_on, monkeypatch):
    monkeypatch.setattr(screenshot.os.path, "exists", lambda _p: False)
    assert screenshot.take(False) is True
    assert actions_on == ["Screenshot saved"]
    assert tools.commands == []


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
