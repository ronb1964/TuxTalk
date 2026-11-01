"""Type at pause (TuxTalk Labs), each phrase typed at the pause after it.
CLAUDE.md Step 8: "Type at pause, a question over pauses".
"""
import time

from harness import Outcome, check, heard_in

SECTION = "Type at pause"
WHOLE_PASS = "Heard whole, the sentence said over pauses is a question"
PAUSE_S = 1.6       # well past the 0.8 s that ends a phrase


def _say_with_pauses(run, phrase_ids):
    """Hold F8, say *phrase_ids* with a pause between each, let go; (typed, log)."""
    run.sink_ready(fresh=True)
    mark = run.log_mark()
    run.vm.key_down("f8")
    try:
        time.sleep(0.4)
        for i, phrase_id in enumerate(phrase_ids):
            if i:
                time.sleep(PAUSE_S)
            run.play(phrase_id)
        time.sleep(0.3)
    finally:
        run.vm.key_up("f8")
    typed = run.wait_dictation(mark)
    return typed or "", run.log_since(mark)


def _with_type_at_pause(run, phrase_ids):
    run.set_config(chunked_dictation=True)
    try:
        return _say_with_pauses(run, phrase_ids)
    finally:
        run.set_config(chunked_dictation=False)


@check(SECTION, "pause_question", "A question said over two pauses ends with a question mark")
def pause_question(run):
    typed, log = _with_type_at_pause(run, ["pause_question_1", "pause_question_2",
                                           "pause_question_3"])
    if log.count("Chunk (middle)") < 2:
        return Outcome("fail", "Type at pause didn't cut the dictation at the pauses",
                       typed=typed)
    if typed.rstrip().endswith("?"):
        detail = ("Ends with ?; log: “" + WHOLE_PASS + "”" if WHOLE_PASS in log
                  else "Ends with ? (the last phrase alone already had it)")
        return Outcome("pass", detail, typed=typed.rstrip())
    return Outcome("fail", "the question ended with a period", typed=typed.rstrip(),
                   heard=heard_in(log))


@check(SECTION, "pause_statement", "A statement said over a pause keeps its period")
def pause_statement(run):
    typed, log = _with_type_at_pause(run, ["pause_statement_1", "pause_statement_2"])
    if log.count("Chunk (middle)") < 1:
        return Outcome("fail", "Type at pause didn't cut the dictation at the pause",
                       typed=typed)
    if typed.rstrip().endswith(".") and WHOLE_PASS not in log:
        return Outcome("pass", "Ends with a period", typed=typed.rstrip())
    return Outcome("fail", "the statement didn't end with a period", typed=typed.rstrip(),
                   heard=heard_in(log))
