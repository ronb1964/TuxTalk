"""Period at the end (Preferences → Advanced → Fine-tuning): Always, As
heard, or Never, one dropdown over auto_period and no_final_period. With
Never a dictation's last full stop is left off (dictation_text
.without_final_period), one said is typed, and a Type at pause recording's
last mark is left off too."""
import pytest

from tuxtalk.dictation_text import without_final_period
from tuxtalk.normalize import LINE_BREAK
from tuxtalk.prefs_advanced import AdvancedTab, period_choice


@pytest.mark.parametrize(("typed", "heard", "left"), [
    ("Send it tomorrow. ", "Send it tomorrow.", "Send it tomorrow "),
    ("prof1. ", "prof1.", "prof1 "),
    ("I'll be there at 5. ", "I'll be there at 5.", "I'll be there at 5 "),
    ("Is it ready? ", "Is it ready?", "Is it ready? "),
    ("Hello. See you soon. ", "Hello. See you soon.", "Hello. See you soon "),
    ("\u201cHello.\u201d ", "Hello.", "\u201cHello\u201d "),
    ("John Smith." + LINE_BREAK, "John Smith. New line.", "John Smith" + LINE_BREAK),
    ("Wait... ", "Wait...", "Wait... "),
    ("We met in the U.S. ", "We met in the U.S.", "We met in the U.S. "),
    ("and so on etc. ", "and so on etc.", "and so on etc. "),
    ("See you tomorrow. ", "see you tomorrow period", "See you tomorrow. "),
    ("Hello. ", "Hello fullstop", "Hello. "),
])
def test_never_leaves_off_the_last_full_stop_only(typed, heard, left):
    """The speech model's full stop goes, inside a closing quote or before
    a line break said last too. Question marks, the full stops between
    sentences, an ellipsis and an abbreviation's dot stay, and so does a
    full stop the user said."""
    assert without_final_period(typed, heard, "en") == left


def test_a_german_full_stop_said_is_kept():
    assert without_final_period("Bis morgen. ", "Bis morgen Punkt.", "de") == "Bis morgen. "


def test_old_settings_keep_their_meaning_in_the_dropdown():
    """The checkbox was auto_period alone: ticked is Always, unticked As heard."""
    assert period_choice({}) == "always"
    assert period_choice({"auto_period": False}) == "as_heard"
    assert period_choice({"auto_period": False, "no_final_period": True}) == "never"


def test_the_dropdown_sets_both_settings():
    changes = []

    class Stub:
        config = {"auto_period": True}
        _period_changed = AdvancedTab._period_changed

        def update_config(self, key, value):
            changes.append((key, value))

    row = AdvancedTab._period_row(Stub())
    label, combo = row.get_children()
    assert combo.get_active_id() == "always"
    combo.set_active_id("never")
    assert changes == [("auto_period", False), ("no_final_period", True)]
    changes.clear()
    combo.set_active_id("as_heard")
    assert changes == [("auto_period", False), ("no_final_period", False)]
