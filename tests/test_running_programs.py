"""running_programs: whether this user runs a program, by its name."""
from tuxtalk import running_programs


def test_a_program_is_found_by_its_name(tmp_path):
    (tmp_path / "123").mkdir()
    (tmp_path / "123" / "comm").write_text("ibus-daemon\n")
    (tmp_path / "self").mkdir()
    assert running_programs.is_running("ibus-daemon", str(tmp_path))
    assert not running_programs.is_running("fcitx5", str(tmp_path))


def test_a_program_name_cut_mid_character_doesnt_stop_the_lookup(tmp_path):
    """/proc keeps 15 bytes of a name, which can end inside a character (a
    Wine program with a Japanese name): the lookup must go on past it."""
    (tmp_path / "77").mkdir()
    (tmp_path / "77" / "comm").write_bytes("Wメモ帳のテスト".encode()[:15] + b"\n")
    (tmp_path / "123").mkdir()
    (tmp_path / "123" / "comm").write_text("ibus-daemon\n")
    assert running_programs.is_running("ibus-daemon", str(tmp_path))
    assert not running_programs.is_running("fcitx5", str(tmp_path))


def test_programs_that_cant_be_listed_are_unknown_not_absent(tmp_path):
    """A clipboard history TuxTalk can't look for must count as there."""
    assert running_programs.is_running("copyq", str(tmp_path / "no-proc")) is None
