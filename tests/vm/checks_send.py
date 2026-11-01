"""Send each dictation automatically, Paste Last Dictation, and what TuxTalk
does to other sound while you dictate. CLAUDE.md Step 8: "Press enter",
"Paste Last Dictation", "Turn other sounds down", "pause it".
"""
import time

from harness import Outcome, check, heard_in
from vmlib import REMOTE

PLAYER_LOG = f"~/{REMOTE}/player.txt"


def focus_unknown(run):
    """Why TuxTalk can't tell which window has the focus here, or None.
    There an automatic Enter isn't sent and no countdown runs, by design:
    COSMIC names only the app, and GNOME says nothing without its extension."""
    if "cosmic" in run.vm.spec.tags:
        return "COSMIC names only the focused app, not its window"
    return run.window_blind()


def _no_countdown(run):
    """The Outcome for a countdown check where focus_unknown() applies."""
    return Outcome("skip", f"No countdown here, by design: {focus_unknown(run)}. "
                   "“Sends after the wait” checks that nothing is sent.")


def say_then_wait_for_countdown(run, phrase_id):
    """Dictate with the send countdown on; returns the log mark once the
    countdown has started, or None if it never did."""
    _t, _h, mark = run.say(phrase_id, wait=False)
    if run.wait_log(mark, "Sending in", "Held back the Enter", timeout=25) is None:
        return None
    return mark


@check("Send automatically", "send_plain", "Sends after the wait")
def send_plain(run):
    run.set_config(press_enter_after=True, press_enter_delay=2.0)
    run.sink_ready()
    if focus_unknown(run):
        return _not_sent_unknown(run)
    mark = say_then_wait_for_countdown(run, "send_plain")
    if mark is None:
        return Outcome("fail", "the countdown never started (no “Sending in” in the log)")
    time.sleep(0.4)
    shot = run.shot("send-countdown")
    early = run.sink_text()
    time.sleep(3.5)
    typed, log = run.sink_text(), run.log_since(mark)
    outcome = run.expect_typed("send_plain", "Testing the send countdown.\n", typed,
                               heard_in(log))
    outcome.screenshot = shot
    if outcome.status == "pass" and early.endswith("\n"):
        outcome.status, outcome.detail = "fail", "Enter came at once, not after the wait"
    elif outcome.status == "pass":
        outcome.detail = "Log: “Sending in 2s”, then Enter about two seconds later"
    return outcome


def _not_sent_unknown(run):
    """Dictate where the focused window isn't known: typed, then not sent,
    with a space after it for the next dictation."""
    _t, _h, mark = run.say("send_plain", wait=False)
    run.wait_log(mark, "Not sent", "Sending in", timeout=25)
    time.sleep(3.5)     # past the 2 s a countdown would have taken
    typed, log = run.sink_text(), run.log_since(mark)
    heard = heard_in(log)
    problems = []
    if typed.endswith("\n"):
        problems.append("it was sent")
    elif not typed.endswith(" "):
        problems.append("no space after the words for the next dictation")
    if "Not sent: which window has the focus isn't known" not in log:
        problems.append("no “Not sent: which window has the focus isn't known” in the log")
    detail = "; ".join(problems) or ("Typed and not sent, by design; log: “Not sent: which "
                                     "window has the focus isn't known”")
    return Outcome("fail" if problems else "pass", detail, typed=typed, heard=heard)


def _held(run, phrase_id, key, label):
    """Dictate, then tap *key* during the wait: nothing sent, no recording
    started, and the space left off before the Enter typed after all."""
    if focus_unknown(run):
        return _no_countdown(run)
    run.sink_ready()
    mark = say_then_wait_for_countdown(run, phrase_id)
    if mark is None:
        return Outcome("fail", "the countdown never started")
    time.sleep(0.3)
    recordings_before = run.log_since(mark).count("Recording stopped")
    run.vm.tap(key)
    time.sleep(0.6)
    shot = run.shot(f"send-held-{key}")
    time.sleep(3.5)
    typed, log = run.sink_text(), run.log_since(mark)
    problems = []
    if typed.endswith("\n"):
        problems.append("it was sent anyway")
    elif not typed.endswith(" "):
        problems.append("the space left off for the Enter wasn't typed, so the next dictation "
                        "sticks to the period")
    if "Held the send" not in log:
        problems.append("no “Held the send” in the log")
    if log.count("Recording stopped") != recordings_before:
        problems.append(f"tapping {label} started a recording")
    detail = "; ".join(problems) or f"Not sent; log: “Held the send”; {label} started no recording"
    return Outcome("fail" if problems else "pass", detail, typed=typed, screenshot=shot)


@check("Send automatically", "send_hold_f8", "Tap F8 during the wait holds the send")
def send_hold_f8(run):
    return _held(run, "send_hold", "f8", "F8")


@check("Send automatically", "send_hold_f9", "Tap F9 during the wait holds it too")
def send_hold_f9(run):
    return _held(run, "send_hold", "f9", "F9")


@check("Send automatically", "send_more", "Hold F8 during the wait adds more, then sends")
def send_more(run):
    if focus_unknown(run):
        return _no_countdown(run)
    run.sink_ready()
    mark = say_then_wait_for_countdown(run, "first_part")
    if mark is None:
        return Outcome("fail", "the countdown never started")
    time.sleep(0.8)     # about when a person decides to add more
    run.vm.key_down("f8")
    time.sleep(0.4)
    run.play("added_part")
    time.sleep(0.3)
    run.vm.key_up("f8")
    run.wait_log(mark, "Pressed Enter", timeout=25)
    time.sleep(1.5)
    typed = run.sink_text()
    return run.expect_typed("added_part", "First part. And this part was added.\n", typed,
                            heard_in(run.log_since(mark)))


@check("Send automatically", "send_and", "A sentence cut off on “and” isn't sent")
def send_and(run):
    run.sink_ready()
    mark = say_then_wait_for_countdown(run, "noon_and")
    if mark is None:
        return Outcome("fail", "nothing happened after the dictation")
    time.sleep(4)
    typed, log = run.sink_text(), run.log_since(mark)
    problems = []
    if typed.endswith("\n"):
        problems.append("it was sent")
    if 'Held back the Enter: the dictation ended on "and"' not in log:
        problems.append("no “Held back the Enter” in the log")
    detail = "; ".join(problems) or "Typed, not sent; log: “Held back the Enter … \"and\"”"
    return Outcome("fail" if problems else "pass", detail, typed=typed)


def switch_during_countdown(run, shot_name):
    """Dictate, switch windows during the send wait; the Outcome: nothing sent."""
    run.sink_ready("other", "TT Other", fresh=True)     # the window to switch to...
    run.sink_ready(fresh=True)                          # ...under the one dictated into
    mark = say_then_wait_for_countdown(run, "send_plain")
    if mark is None:
        return Outcome("fail", "the countdown never started")
    run.vm.tap("alt", "tab", hold=0.15)
    time.sleep(0.8)
    shot = run.shot(shot_name)
    time.sleep(3.5)
    first, other, log = run.sink_text(), run.sink_text("other"), run.log_since(mark)
    run.close_sink("other")
    problems = []
    if first.endswith("\n") or "\n" in other:
        problems.append("an Enter was pressed")
    if "Not sent: another window has the focus now" not in log:
        problems.append("no “Not sent: another window has the focus now” in the log")
    detail = "; ".join(problems) or ("Nothing sent; log: “Not sent: another window has the focus "
                                     "now”")
    return Outcome("fail" if problems else "pass", detail, typed=first, screenshot=shot)


@check("Send automatically", "send_switch", "Switching windows during the wait sends nothing")
def send_switch(run):
    blind = focus_unknown(run)
    outcome = Outcome("skip", blind) if blind else switch_during_countdown(run, "send-switch")
    run.set_config(press_enter_after=False)
    return outcome


@check("Sound and other windows", "paste_last", "Paste Last Dictation types it again elsewhere")
def paste_last(run):
    run.sink_ready(fresh=True)
    typed, heard, _log = run.say("paste_last")
    run.sink_ready("other", "TT Other", fresh=True)
    mark = run.log_mark()
    run.vm.tap("ctrl", "alt", "p")
    run.wait_log(mark, "Paste last dictation", timeout=10)
    time.sleep(3)
    pasted = run.sink_text("other")
    run.close_sink("other")
    log = run.log_since(mark)
    if typed and pasted.rstrip(" ") == typed.rstrip(" ") and "Paste last dictation" in log:
        return Outcome("pass", "The same text typed in the second window; log: “Paste last "
                       "dictation”",
                       typed=pasted)
    return Outcome("fail", f"first window: {typed!r}; second window: {pasted!r}",
                   expected=typed, typed=pasted, heard=heard)


@check("Sound and other windows", "duck_audio", "Turn other sounds down while dictating")
def duck_audio(run):
    run.set_config(duck_audio=True)
    run.sink_ready()
    run.vm.ssh(f"setsid -f timeout 25 pw-play ~/{REMOTE}/tone.wav > /dev/null 2>&1 < /dev/null")
    time.sleep(1.5)
    _t, _h, mark = run.say("basic", wait=False)
    time.sleep(0.2)
    log = run.wait_log(mark, "Put 1 sound(s) back", timeout=30) or run.log_since(mark)
    run.vm.ssh("pkill -x pw-play", check=False)
    run.set_config(duck_audio=False)
    down = "Turned 1 other sound(s) down" in log
    back = "Put 1 sound(s) back" in log
    if down and back:
        return Outcome("pass", "Log: “Turned 1 other sound(s) down”, then “Put 1 sound(s) back”")
    return Outcome("fail", f"turned down: {down}; put back: {back}")


def _player(run, status):
    """Dictate once with a pretend player in *status*; (its calls, the log)."""
    run.vm.ssh(f"rm -f {PLAYER_LOG}")
    run.vm.session_background("player", "python3", f"{run.vm.home}/{REMOTE}/fake_player.py",
                              f"{run.vm.home}/{REMOTE}/player.txt", status)
    time.sleep(2)
    run.sink_ready()
    _typed, _heard, log = run.say("basic")
    time.sleep(2)
    calls = run.vm.read(PLAYER_LOG).split()
    run.vm.ssh("kill $(pgrep -f '[f]ake_player.py')", check=False)
    return calls, log


@check("Sound and other windows", "pause_media", "Dictating pauses a playing video, then plays it")
def pause_media(run):
    calls, log = _player(run, "Playing")
    logged = "Paused ttplayer while dictating" in log and "Resumed ttplayer" in log
    if calls == ["Pause", "Play"] and logged:
        return Outcome("pass", "The player got Pause, then Play; log: “Paused ttplayer …”, "
                       "“Resumed ttplayer”")
    return Outcome("fail", f"the player got {calls or 'no calls'}")


@check("Sound and other windows", "paused_stays_paused",
       "A video that was already paused stays paused")
def paused_stays_paused(run):
    calls, _log = _player(run, "Paused")
    if not calls:
        return Outcome("pass", "The paused player got no calls")
    return Outcome("fail", f"the paused player got {calls}")
