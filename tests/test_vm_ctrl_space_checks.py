"""The VM checks for Left Ctrl+Space are registered, can be selected, play
phrases that exist, and read the helper's report strictly: the pair and
nothing else, and never a pass on an empty file.
"""
import pytest
from conftest import import_vm_tool

# The harness reads its settings with tomllib, new in Python 3.11.
pytest.importorskip("tomllib", reason="the VM harness needs Python 3.11 or newer")
run = import_vm_tool("run")
checks = import_vm_tool("checks_hotkeys")
harness = import_vm_tool("harness")
voices = import_vm_tool("voices")

IDS = {"ctrl_space_helper_only_the_pair", "ctrl_space_hold", "ctrl_space_toggle",
       "ctrl_space_terminal", "ctrl_space_input_method_log", "ctrl_space_quiet_helper_update"}
HEARD_OK = ["HELLO 1 1.14 keyboards=1", "OK WATCH"]


def test_every_ctrl_space_check_is_registered_in_the_hotkeys_section():
    found = {c.id: c for c in run.CHECKS if c.id in IDS}
    assert set(found) == IDS
    assert {c.section for c in found.values()} == {checks.SECTION}


def test_the_hold_and_tap_checks_run_where_the_brief_says():
    on = {vm: {c.id for c in run.selected(run.VMS[vm], IDS)} for vm in run.VMS}
    assert "ctrl_space_hold" in on["kde"] and "ctrl_space_hold" in on["mint"]
    assert "ctrl_space_hold" in on["pop"] and "ctrl_space_hold" in on["ubuntu"]
    assert "ctrl_space_hold" not in on["debian"]
    assert run.selection_problem(["kde"], {"ctrl_space_toggle"}) is None


def test_the_phrase_the_checks_play_exists():
    assert "basic" in voices.load_phrases()


def test_say_holds_a_pair_of_keys_together_and_lets_both_go():
    events = []

    class Vm:
        def key_down(self, *keys):
            events.append(("down", keys))

        def key_up(self, *keys):
            events.append(("up", keys))

    guest = harness.Run.__new__(harness.Run)
    guest.vm = Vm()
    guest.log_mark = lambda: 0
    guest.play = lambda phrase: None
    guest.wait_dictation = lambda mark, sink: "x"
    guest.log_since = lambda mark: ""
    guest.say("basic", key=("ctrl", "spc"))
    guest.say("basic")
    assert events == [("down", ("ctrl", "spc")), ("up", ("ctrl", "spc")),
                      ("down", ("f8",)), ("up", ("f8",))]


def test_the_pair_alone_is_accepted():
    heard = HEARD_OK + checks.CTRL_TAP * 3 + checks.PAIR
    assert checks.helper_report_problem(heard, checks.CTRL_TAP * 3 + checks.PAIR) is None


def test_a_space_without_left_ctrl_is_a_problem():
    heard = HEARD_OK + checks.CTRL_TAP * 3 + ["DOWN 57 -"] + checks.PAIR
    assert "DOWN 57 -" in checks.helper_report_problem(
        heard, checks.CTRL_TAP * 3 + checks.PAIR)


def test_a_silent_listener_is_a_problem_not_a_pass():
    assert checks.helper_report_problem([], []) is not None
    assert checks.helper_report_problem(["HELLO 1 1.14 keyboards=1"], []) is not None
    assert checks.helper_report_problem(["HELLO 1 1.14 keyboards=1", "ERR bad spec"], []) \
        is not None


def test_a_listener_that_heard_nothing_after_the_watch_matches_an_empty_expectation():
    assert checks.helper_report_problem(HEARD_OK, []) is None


def test_the_helper_version_comes_from_its_hello():
    assert checks.helper_version(["HELLO 1 1.13 keyboards=2", "OK WATCH"]) == "1.13"
    assert checks.helper_version([]) is None
    assert checks.helper_version(["OK WATCH"]) is None
