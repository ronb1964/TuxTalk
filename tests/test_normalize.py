"""Spoken punctuation and commands turned into text (normalize_text), and
the same words left alone where they are ordinary English."""
import pytest

from tuxtalk.normalize import normalize_text

def test_basic_punct():
    s = "thank you comma this is great exclamation point"
    assert normalize_text(s) == "Thank you, this is great!"

def test_space_before_bang_removed():
    s = "thank you very much !"
    assert normalize_text(s) == "Thank you very much!"

def test_quotes_and_comma():
    # "open quote ... close quote" preserves inner text as literal
    # (punctuation commands are NOT processed inside quotes), in smart
    # quotes, capitalised and punctuated like any other quote.
    s = "open quote hello comma world close quote exclamation point"
    assert normalize_text(s) == "\u201cHello comma world!\u201d"


@pytest.mark.parametrize("said, written", [
    ("he said open quote hello close quote", "He said \u201chello.\u201d"),
    ("open quote hello close quote he said", "\u201cHello\u201d he said."),
    ("she called it open quote the beast close quote and laughed",
     "She called it \u201cthe beast\u201d and laughed."),
])
def test_quoted_words_get_smart_quotes_and_sentence_capitals(said, written):
    assert normalize_text(said) == written


@pytest.mark.parametrize("said, written", [
    ("he said quote hello quote", "He said \u201chello.\u201d"),
    ("he said quote hi quote and she said quote bye quote",
     "He said \u201chi\u201d and she said \u201cbye.\u201d"),
])
def test_a_bare_quote_opens_and_then_closes_with_no_spaces_inside(said, written):
    """Not 'He said " hello ".': each "quote" pairs with the next one."""
    assert normalize_text(said) == written

def test_ellipsis_and_caps():
    s = "this is fine dot dot dot next sentence"
    # auto-period adds a period at the end of the line
    assert normalize_text(s) == "This is fine\u2026 Next sentence."

def test_newlines_tabs():
    s = "first line newline press tab indented second line period"
    # \u00a7SHIFT_ENTER\u00a7 markers are converted to keypresses by app.py
    # Auto-period adds periods at line ends
    assert normalize_text(s) == "First line.\u00a7SHIFT_ENTER\u00a7\tIndented second line."

def test_return_commands():
    """"return" and "line break" each start a new line."""
    # Test "return" - returns \u00a7SHIFT_ENTER\u00a7 markers, auto-adds periods.
    # Like "new line", it takes the spaces around it, so the next line
    # doesn't start with one.
    s = "first line return second line"
    assert normalize_text(s) == "First line.\u00a7SHIFT_ENTER\u00a7Second line."

    # Test "line break"
    s = "first line line break second line"
    assert normalize_text(s) == "First line.\u00a7SHIFT_ENTER\u00a7Second line."

    # Test "new paragraph" for double line break
    # The regex for "new paragraph" eats surrounding whitespace/punctuation,
    # so no space appears after the second marker.
    s = "first paragraph new paragraph second paragraph"
    assert normalize_text(s) == "First paragraph.\u00a7SHIFT_ENTER\u00a7\u00a7SHIFT_ENTER\u00a7Second paragraph."

    # Test "paragraph break"
    s = "first paragraph paragraph break second paragraph"
    assert normalize_text(s) == "First paragraph.\u00a7SHIFT_ENTER\u00a7\u00a7SHIFT_ENTER\u00a7Second paragraph."


# =====================================================================
# Context-aware protection: command words used as English nouns must
# NOT be replaced with punctuation, or "period of time" becomes
# "period. Of time".
# =====================================================================

def test_period_of_time_preserved():
    """'period of [noun]' is a fixed noun phrase, not a sentence terminator."""
    s = "we waited a fairly long period of time"
    assert normalize_text(s) == "We waited a fairly long period of time."

def test_period_after_adjective_preserved():
    """'[adjective] period' is noun usage."""
    s = "during a brief period"
    assert normalize_text(s) == "During a brief period."

def test_period_after_article_preserved():
    """'the/this/that period' is noun usage."""
    s = "the period was difficult"
    assert normalize_text(s) == "The period was difficult."

def test_period_still_works_as_command():
    """End-of-clause 'period' still acts as sentence terminator."""
    s = "I am going home period new sentence here"
    assert normalize_text(s) == "I am going home. New sentence here."

def test_in_return_preserved():
    """'in return' is a fixed phrase, not a line break command."""
    s = "he gave me nothing in return"
    assert normalize_text(s) == "He gave me nothing in return."

def test_tax_return_preserved():
    """'tax return' should not become 'tax [line break]'."""
    s = "I filed my tax return yesterday"
    assert normalize_text(s) == "I filed my tax return yesterday."

def test_his_return_preserved():
    """Possessive + return is noun usage."""
    s = "we celebrated his return home"
    assert normalize_text(s) == "We celebrated his return home."

def test_dash_of_salt_preserved():
    """'dash of [word]' is recipe/measurement noun usage."""
    s = "add a dash of salt to the recipe"
    assert normalize_text(s) == "Add a dash of salt to the recipe."

def test_great_quote_preserved():
    """'[adjective] quote' is noun usage."""
    s = "she said a great quote about life"
    assert normalize_text(s) == "She said a great quote about life."


@pytest.mark.parametrize("said, written", [
    ("he told me quote we won't quote", "He told me \u201cwe won't.\u201d"),
    ("he told me quote we won\u2019t quote", "He told me \u201cwe won\u2019t.\u201d"),
    ("she said quote never quote", "She said \u201cnever.\u201d"),
    ("she said quote never again quote", "She said \u201cnever again.\u201d"),
    ("she said quote me too quote", "She said \u201cme too.\u201d"),
    ("she said quote of the people quote", "She said \u201cof the people.\u201d"),
    ("please quote hello quote", "Please \u201chello.\u201d"),
    ("he said quote it was fine quote", "He said \u201cit was fine.\u201d"),
])
def test_a_quotation_pairs_whatever_words_are_next_to_quote(said, written):
    """Words that could make "quote" the verb or the noun ("please", "won't",
    "me") are often inside a quotation too: two quotes are still a pair."""
    assert normalize_text(said) == written


def test_comma_operator_preserved():
    """'comma operator' is a programming term, not a punctuation command."""
    s = "the comma operator in C is useful"
    assert normalize_text(s) == "The comma operator in C is useful."

def test_comma_still_works_between_clauses():
    """Bare 'comma' between phrases still produces a comma."""
    s = "first item comma second item comma third item"
    assert normalize_text(s) == "First item, second item, third item."


# =====================================================================
# Standalone-"i" capitalization. Whisper transcribes the pronoun "I"
# as lowercase when it appears mid-sentence; cap_first only handles
# the first letter of a line, so mid-sentence occurrences slip through.
# =====================================================================

def test_standalone_i_capitalized_midsentence():
    """Lowercase 'i' as a standalone pronoun mid-sentence becomes 'I'."""
    s = "today i went to the store"
    assert normalize_text(s) == "Today I went to the store."

def test_multiple_standalone_i_capitalized():
    """All standalone 'i' occurrences in one sentence get capitalized."""
    s = "i think i can do it"
    assert normalize_text(s) == "I think I can do it."

def test_i_contraction_capitalized_midsentence():
    """'i'll', 'i'm', 'i've', 'i'd' mid-sentence get capitalized."""
    s = "tomorrow i'll go and i'm sure i've earned it"
    assert normalize_text(s) == "Tomorrow I'll go and I'm sure I've earned it."

def test_words_containing_i_not_touched():
    """Words like 'in', 'it', 'iPad' must NOT be altered."""
    s = "the iPad is in it"
    # cap_first capitalizes the leading 'T' of "the"; "iPad", "in", "it" untouched
    assert normalize_text(s) == "The iPad is in it."

def test_existing_uppercase_I_unchanged():
    """An already-capitalized 'I' is left alone (no double processing)."""
    s = "I already said I would"
    assert normalize_text(s) == "I already said I would."


# =====================================================================
# Numbers, abbreviations, times, dashes and command words that
# normalization must not corrupt.
# =====================================================================

def test_decimals_and_prices_preserved():
    """Periods inside numbers must not get split: '3.5' must not become '3. 5'."""
    assert normalize_text("version 3.5 costs $19.99") == "Version 3.5 costs $19.99."


def test_single_letter_abbreviations_preserved():
    """'U.S.' / 'e.g.' must not be split into 'U. S.' / 'e. G.'"""
    assert normalize_text("the U.S. economy grew") == "The U.S. economy grew."
    assert normalize_text("this is useful e.g. when testing") == "This is useful e.g. when testing."


def test_am_prefixed_words_not_time_formatted():
    """Number + word starting with 'am'/'pm' must not be treated as a time."""
    assert normalize_text("there are 5 among us") == "There are 5 among us."
    assert normalize_text("I ate 3 amazing tacos") == "I ate 3 amazing tacos."


def test_time_formatting_still_works():
    """Real times keep getting normalized alongside the rule above."""
    assert "11:30 PM" in normalize_text("meet me at 11. 30 p. m. sharp")
    assert "5 PM" in normalize_text("the store closes at 5 p. m. tonight")


def test_em_dash_command_works():
    """'em dash' is handled before the 'dash' rule, which would leave 'em-'."""
    assert normalize_text("please use an em dash here") == "Please use an — here."


def test_dash_command_still_works():
    """Plain 'dash' still produces a tight hyphen."""
    assert normalize_text("five dash three") == "Five-three."


def test_return_verb_not_treated_as_command():
    """Common verb uses of 'return' must not insert a line break."""
    out = normalize_text("I want to return this item tomorrow")
    assert "§SHIFT_ENTER§" not in out
    assert "return this item" in out

    out = normalize_text("she will return next week")
    assert "§SHIFT_ENTER§" not in out
    assert "will return" in out

    out = normalize_text("please return it to the store")
    assert "§SHIFT_ENTER§" not in out


@pytest.mark.parametrize("said, typed", [
    ("every tab shows", "Every tab shows."),
    ("the tabs in Claude Desktop", "The tabs in Claude Desktop."),
    ("open a new tab in the browser", "Open a new tab in the browser."),
    ("tab hello world", "Tab hello world."),
    ("first tab second tab third", "First tab second tab third."),
    # Talking about the key, not pressing it.
    ("press the tab key to indent", "Press the tab key to indent."),
    ("press tab key to indent", "Press tab key to indent."),
])
def test_tab_on_its_own_is_typed_as_the_word(said, typed):
    assert normalize_text(said) == typed


def test_press_tab_at_the_start_indents_the_line():
    assert normalize_text("press tab hello world") == "\tHello world."
    assert normalize_text("press tab press tab hello") == "\t\tHello."


def test_literal_press_tab_types_the_words():
    assert normalize_text("literal press tab to continue") == "Press tab to continue."


# --- append_auto_punct: auto-period/space placement around line-break markers ---

from tuxtalk.normalize import append_auto_punct

_M = "§SHIFT_ENTER§"


def test_auto_punct_plain_text():
    assert append_auto_punct("Hello world", True, True) == "Hello world. "
    assert append_auto_punct("Hello world.", True, True) == "Hello world. "
    assert append_auto_punct("Hello", False, False) == "Hello"


def test_auto_punct_no_orphan_period_after_trailing_break():
    """'hello new line' must not leave an orphan '. ' after the line break."""
    assert append_auto_punct(f"Hello.{_M}", True, True) == f"Hello.{_M}"


def test_auto_punct_period_goes_before_trailing_break():
    assert append_auto_punct(f"Hello{_M}", True, True) == f"Hello.{_M}"
    assert append_auto_punct(f"Hello{_M}{_M}", True, True) == f"Hello.{_M}{_M}"


def test_auto_punct_only_markers_untouched():
    assert append_auto_punct(_M, True, True) == _M


# --- protection lists must not swallow the command ---

def test_return_command_after_pronoun_still_works():
    """'thank you return' is a very common dictation ending — must break line."""
    assert normalize_text("thank you return") == f"Thank you.{_M}"


def test_return_command_before_sentence_starter_still_works():
    """Utterance-initial 'return' followed by a normal sentence must break + capitalize."""
    out = normalize_text("return this is a test")
    assert _M in out
    assert "This is a test." in out


@pytest.mark.parametrize("said, typed", [
    ("name press tab address", "Name\taddress."),
    # The engine's commas and periods around the command go with it, and the
    # next field doesn't start with a space.
    ("Name, press tab, address.", "Name\taddress."),
    ("Name. Press Tab. Address.", "Name.\tAddress."),
    ("first press tab second press tab third", "First\tsecond\tthird."),
])
def test_press_tab_between_fields_types_a_tab(said, typed):
    assert normalize_text(said) == typed


def test_line_starting_with_protected_word_is_capitalized():
    """A protected word at line start must still get sentence capitalization."""
    assert normalize_text("return to sender was my favorite song") == "Return to sender was my favorite song."


# --- cap_first: don't capitalize past the start of the line -----------------
# The capitalization pass must not scan forward to the first letter it can
# find, or a line opening with a number or symbol gets its *second* word
# capitalized.

def test_line_starting_with_a_year_does_not_capitalize_the_next_word():
    assert normalize_text("2024 was a good year") == "2024 was a good year."


def test_line_starting_with_a_price_does_not_capitalize_the_next_word():
    assert normalize_text("$50 is too much") == "$50 is too much."


def test_line_starting_with_a_count_does_not_capitalize_the_next_word():
    assert normalize_text("15 minutes later he left") == "15 minutes later he left."


def test_email_address_at_line_start_is_not_capitalized():
    assert normalize_text("rbrand@example.com is my email") == "rbrand@example.com is my email."


def test_url_at_line_start_is_not_capitalized():
    assert normalize_text("github.com/ronb1964 has the code") == "github.com/ronb1964 has the code."


def test_opening_quote_is_still_skipped_when_capitalizing():
    assert normalize_text('"hello there" she said') == '"Hello there" she said.'


def test_ordinary_sentences_are_still_capitalized():
    assert normalize_text("the meeting went well") == "The meeting went well."


# --- a.m./p.m. must not swallow the sentence period -------------------------
# "a.m." ends in a period that can also be the full stop. Rewriting it to "AM"
# must keep that period, or two sentences merge into one.

def test_am_at_end_of_text_keeps_its_period():
    assert normalize_text("It starts at 5 p.m.") == "It starts at 5 PM."


def test_am_mid_text_does_not_gain_a_period_even_before_a_capital():
    """Deliberate limitation, documented rather than guessed at.

    "9 a.m. We should be early" and "8 a.m. Tuesday" are grammatically
    identical, and a following capital cannot tell them apart because Whisper
    capitalizes proper nouns as well as sentence starts. Adding a period here
    would split ordinary sentences in two, which is worse than leaving one out: a
    wrong split corrupts text the user must notice and repair, while a missing
    full stop merely reads a little short.
    """
    assert normalize_text("The meeting is at 9 a.m. We should be early.") == \
        "The meeting is at 9 AM We should be early."


def test_am_with_minutes_mid_text_does_not_gain_a_period():
    assert normalize_text("Dinner is at 7:30 p.m. Bring a dish.") == \
        "Dinner is at 7:30 PM Bring a dish."


def test_am_mid_sentence_does_not_gain_a_period():
    assert normalize_text("Call me at 9 a.m. tomorrow if you can.") == \
        "Call me at 9 AM tomorrow if you can."


def test_am_followed_by_a_lowercase_word_does_not_gain_a_period():
    assert normalize_text("I woke at 6 a.m. and went running.") == \
        "I woke at 6 AM and went running."


# --- a.m./p.m. must not split a sentence at a proper noun -------------------
# Whisper capitalizes proper nouns as well as sentence starts, so "uppercase
# word follows" is not evidence that the abbreviation's period was also a full
# stop. "8 a.m. Tuesday" is an ordinary thing to dictate.

def test_am_before_a_weekday_does_not_gain_a_period():
    assert normalize_text("we start at 8 a.m. Tuesday") == "We start at 8 AM Tuesday."


def test_pm_before_a_weekday_does_not_gain_a_period():
    assert normalize_text("the deadline is 5 p.m. Friday") == "The deadline is 5 PM Friday."


def test_am_before_a_month_does_not_gain_a_period():
    assert normalize_text("the 8 a.m. January review") == "The 8 AM January review."


def test_pm_before_a_timezone_does_not_gain_a_period():
    assert normalize_text("call me at 5 p.m. EST") == "Call me at 5 PM EST."


def test_am_mid_sentence_before_a_capitalized_word_does_not_gain_a_period():
    assert normalize_text("the 9 a.m. Monday meeting is cancelled") == \
        "The 9 AM Monday meeting is cancelled."


# --- filenames must not be split into two sentences -------------------------

def test_a_filename_is_not_split_into_two_sentences():
    """The pass that puts a space after a sentence period protects decimals
    ("19.99") and single-letter abbreviations ("e.g."), and a separate guard
    protects domains ("example.com"). Filenames need the same, or "report.pdf"
    becomes "report. Pdf" — the extension capitalized as a new sentence."""
    assert normalize_text("the file is report.pdf") == "The file is report.pdf."


def test_common_file_extensions_survive():
    for name in ("notes.txt", "photo.jpeg", "archive.tar", "script.py",
                 "sheet.xlsx", "song.mp3", "readme.md"):
        out = normalize_text(f"open {name} now")
        assert name in out, f"{name} was mangled into {out!r}"


def test_a_domain_still_survives():
    """Domains stay whole alongside the filename rule."""
    assert "example.com" in normalize_text("go to example.com for info")


def test_a_real_sentence_boundary_is_still_spaced():
    """The guard must not stop genuine missing-space repairs."""
    assert normalize_text("this is one.this is two") == "This is one. This is two."


# --- i.e. is not the pronoun "I" --------------------------------------------

def test_ie_is_not_capitalized_as_the_pronoun():
    """The standalone-"i" rule uses \\bi\\b, and the dot in "i.e." is a word
    boundary — so "i.e." must be kept from becoming "I.e." mid-sentence."""
    assert normalize_text("use it, i.e. the new one") == "Use it, i.e. the new one."


def test_the_pronoun_i_is_still_capitalized():
    assert normalize_text("then i went home") == "Then I went home."


def test_i_at_the_end_of_a_sentence_is_still_capitalized():
    assert normalize_text("that was me and i") == "That was me and I."


# --- a line already ending in punctuation gets no extra period --------------

def test_a_line_ending_in_a_colon_gets_no_period():
    """A dictated colon or semicolon counts as "already punctuated" too, so
    it doesn't collect a period: "the plan:." """
    assert normalize_text("here is the plan colon") == "Here is the plan:"


def test_a_line_ending_in_a_semicolon_gets_no_period():
    assert normalize_text("first do this semicolon") == "First do this;"


# --- times must not capitalize the word that follows -------------------------

def test_a_spaced_am_does_not_capitalize_the_next_word():
    """Whisper writes "9 a. m.", which time formatting collapses to "9 AM".
    The capitalization pass must not treat the "m." as a sentence ending and
    upper-case the following word."""
    assert normalize_text("meet at 9 a. m. tomorrow") == "Meet at 9 AM tomorrow."


def test_a_spaced_pm_does_not_capitalize_the_next_word():
    assert normalize_text("it was 5 p. m. and dark") == "It was 5 PM and dark."


def test_a_time_ending_the_sentence_keeps_its_period():
    """Pinned behaviour: the abbreviation's dot doubles as the full stop."""
    assert normalize_text("we start at 8 a. m.") == "We start at 8 AM."


# --- append_auto_punct edge cases ------------------------------------------


def test_auto_punct_leaves_a_trailing_colon_alone():
    assert append_auto_punct("here is the plan:", True, False) == "here is the plan:"


def test_auto_punct_leaves_a_trailing_semicolon_alone():
    assert append_auto_punct("first do this;", True, False) == "first do this;"


def test_auto_punct_does_not_invent_a_period_from_silence():
    """A whitespace-only utterance must not become a lone "." injected into
    the document — a stray full stop from a recording of nothing."""
    assert append_auto_punct("   ", True, False).strip() == ""


def test_auto_punct_still_adds_a_period_to_a_normal_sentence():
    assert append_auto_punct("this is a sentence", True, False) == "this is a sentence."


# --- the auto-period preference must actually be honoured -------------------

def test_normalize_can_be_told_not_to_add_a_period():
    """_prepare_text calls normalize_text BEFORE append_auto_punct, so
    normalize_text must honour the "Ensure period at end of sentences"
    preference itself, or the period is already there by the time the
    preference is consulted."""
    assert normalize_text("this is a sentence", auto_period=False) == "This is a sentence"


def test_normalize_adds_a_period_by_default():
    assert normalize_text("this is a sentence") == "This is a sentence."


def test_auto_period_off_still_normalizes_everything_else():
    """Turning the period off must not turn off capitalization or spacing."""
    out = normalize_text("hello comma world", auto_period=False)
    assert out == "Hello, world"


# The email rule turns "name at domain.tld" into name@domain.tld. Country
# endings like .no .it .in .my .us are also English words that start sentences,
# so "Look at this. My car broke." must not become "Look@this.My car broke."
# A space around the dot may only count for endings that can't be words.
@pytest.mark.parametrize("said", [
    "Meet me at three. No wait, four o'clock.",
    "Look at this. My car broke.",
    "We met at church. It was nice.",
    "I'll be at home. In the morning I leave.",
    "We arrived at noon. Us and the kids.",
    "Stop at the bank. Es is closed.",
])
def test_sentence_after_at_is_not_an_email(said):
    assert "@" not in normalize_text(said)


@pytest.mark.parametrize("said, expected", [
    ("email john at gmail.com", "Email john@gmail.com."),
    ("email john at gmail. com", "Email john@gmail.com."),
    ("ron at example . org please", "ron@example.org please."),
    ("send it to ron at example.de", "Send it to ron@example.de."),
    ("Write to sales at acme.co today", "Write to sales@acme.co today."),
])
def test_spoken_email_addresses_still_join(said, expected):
    assert normalize_text(said) == expected


# The space-after-the-dot fix for email endings must not run on all text, or a
# sentence starting with "Net", "Co" or "De" gets glued to the one before it.
@pytest.mark.parametrize("said", [
    "That's it. Net profit was up.",
    "I left early. Co-workers stayed.",
])
def test_tld_space_fix_only_touches_email_addresses(said):
    out = normalize_text(said)
    assert ". " in out and ".net" not in out.lower() and ".co" not in out.lower()


def test_tld_space_fix_still_joins_a_split_address():
    assert normalize_text("write to john@gmail. Com today") == "Write to john@gmail.com today."
    assert normalize_text("mail a@b. co. uk now") == "Mail a@b.co.uk now."
    assert normalize_text("mail a@b. Co. Uk now") == "Mail a@b.co.uk now."
    assert normalize_text("write to john@gmail. Net today") == "Write to john@gmail.net today."


# The space-fix must not take every country ending (it, in, my, no, us) across
# a spaced dot, or the sentence after an address is glued into it:
# "john@gmail.com. It was great" -> "john@gmail.com.it was great".
@pytest.mark.parametrize("said, expected", [
    ("send it to john@gmail.com. it was great",
     "Send it to john@gmail.com. It was great."),
    ("reach me at bob@work.com. my phone is off",
     "Reach me at bob@work.com. My phone is off."),
    ("email john@gmail.com period No thanks", "Email john@gmail.com. No thanks."),
    ("write to anna@example.de. it is ok", "Write to anna@example.de. It is ok."),
    ("email bob at work dot com. no rush", "Email bob@work.com. No rush."),
    ("my email is john at gmail dot com period in the morning",
     "My email is john@gmail.com. In the morning."),
    # "Net" and "Gov" are endings too, but a finished address doesn't take
    # a second capitalized one.
    ("email bob@work.com. Net profit was up",
     "Email bob@work.com. Net profit was up."),
    ("email bob at work dot com. gov contracts too",
     "Email bob@work.com. Gov contracts too."),
])
def test_sentence_after_an_email_address_stays_a_sentence(said, expected):
    assert normalize_text(said) == expected


def test_missed_space_before_a_capital_is_still_a_sentence_break():
    """Endings are lowercase; "it.It" is a transcriber's missed space."""
    assert normalize_text("I like it.It was good") == "I like it. It was good."
    assert normalize_text("meet me at three.No wait") == "Meet me at three. No wait."


@pytest.mark.parametrize("said", [
    "look at github.com/ron",
    "we arrived at example.com",
    "the docs are at example.org",
    "I'm at example.net all day",
])
def test_an_ordinary_word_before_at_is_not_an_email_name(said):
    """In a spoken address the word before "at" is a mailbox name, never
    "look", "arrived" or "are"."""
    assert "@" not in normalize_text(said)


def test_mailbox_names_that_are_words_still_work():
    assert normalize_text("write to sales at acme.com") == "Write to sales@acme.com."
    assert normalize_text("email contact at example.org") == "Email contact@example.org."


# Parakeet writes a spoken "dot" in an address as the word about one time in
# three ("john at gmail dot com"), the rest as "gmail.com". Said the same way,
# it should come out the same way.
@pytest.mark.parametrize("said, expected", [
    ("Email me at john at gmail dot com.", "Email me at john@gmail.com."),
    ("Email me at John at Gmail dot com.", "Email me at john@gmail.com."),
    ("look at github dot com", "Look at github.com."),
    ("go to www dot example dot org for the details", "Go to www.example.org for the details."),
    ("write to sales at acme dot net today", "Write to sales@acme.net today."),
    ("the docs are at docs dot python dot org", "The docs are at docs.python.org."),
    ("visit example dot com for more", "Visit example.com for more."),
    ("my email is ron at example dot com if you need it", "My email is ron@example.com if you need it."),
])
def test_a_spoken_dot_in_an_address_becomes_a_dot(said, expected):
    assert normalize_text(said) == expected


@pytest.mark.parametrize("said", [
    "the dot com bubble burst in 2000",
    "it was a dot com company",
    "put a dot on the map",
    "connect the dots",
    "click the red dot",
    "dot com",
    "I remember the dot com days",
    "dot dot dot",
    "it was a big dot com company",
    "the early dot com days were wild",
    "we survived the old dot com crash",
])
def test_an_ordinary_dot_stays_a_word(said):
    out = normalize_text(said)
    assert ".com" not in out and ".on" not in out
    assert "dot" in out.lower() or out == "…" or out.startswith("…")


# Every dot in an address is protected, not only the one before the ending, so
# an address with more parts isn't split like a sentence ("www. Example.org").
@pytest.mark.parametrize("said, expected", [
    ("go to www.example.org for the details", "Go to www.example.org for the details."),
    ("the docs are at docs.python.org", "The docs are at docs.python.org."),
    ("email me at bob at mail.example.com", "Email me at bob@mail.example.com."),
    ("see news.bbc.co.uk", "See news.bbc.co.uk."),
])
def test_an_address_with_more_parts_stays_whole(said, expected):
    assert normalize_text(said) == expected


def test_a_missed_space_is_still_a_sentence_break():
    assert normalize_text("I left early.then I came back") == "I left early. Then I came back."


# Parakeet sometimes ends "New line" with a colon, not a period; the colon goes
# with the command rather than starting the new line (": Go to ...").
@pytest.mark.parametrize("said", [
    "New line: Go to the shop.",
    "New line; go to the shop.",
    "New paragraph: Go to the shop.",
])
def test_punctuation_after_a_line_break_command_goes_with_it(said):
    out = normalize_text(said)
    assert out.replace("§SHIFT_ENTER§", "") == "Go to the shop."


# Parakeet capitalizes a name ("John at gmail.com"), but an address is written
# in lower case, not "John@gmail.com".
@pytest.mark.parametrize("said, expected", [
    ("Send the plans to John at gmail.com.", "Send the plans to john@gmail.com."),
    ("Email me at John at Gmail dot com.", "Email me at john@gmail.com."),
    ("write to Sales at Acme. com today", "Write to sales@acme.com today."),
])
def test_a_spoken_address_is_lower_case(said, expected):
    assert normalize_text(said) == expected


# --- hour and minutes run together ("641 p.m.") --------------------------------
# Parakeet writes the same spoken time different ways: "6.41 p.m." one time,
# "641 p.m." the next. Both must become 6:41 PM.

@pytest.mark.parametrize("heard, typed", [
    ("So your 641 p.m. draft is what we sent.", "So your 6:41 PM draft is what we sent."),
    ("meet at 1130 a.m. sharp", "Meet at 11:30 AM sharp."),
    ("lunch at 115 p.m. today", "Lunch at 1:15 PM today."),
    ("it starts at 1015 am", "It starts at 10:15 AM."),
])
def test_run_together_times_get_their_colon(heard, typed):
    assert normalize_text(heard).rstrip() == typed


@pytest.mark.parametrize("heard, number", [
    ("we sold 2000 pm units", "2000"),         # not an hour and minutes
    ("the 1365 a.m. broadcast", "1365"),
    ("about 675 p.m. readings", "675"),       # 75 minutes is no time
])
def test_numbers_that_cannot_be_times_are_left_alone(heard, number):
    out = normalize_text(heard)
    assert number in out and ":" not in out


# A comma the user says before a line break is kept, so a letter greeting said
# as "dear john comma new line" keeps its comma, and no period appears with the
# auto-period preference off.
@pytest.mark.parametrize("said, expected", [
    ("dear john comma new line thanks for the update",
     f"Dear john,{_M}Thanks for the update."),
    ("hi sarah comma new paragraph how are you",
     f"Hi sarah,{_M}{_M}How are you."),
])
def test_comma_before_a_line_break_is_kept(said, expected):
    assert normalize_text(said) == expected


# A comma the engine writes before a spoken "new line" is a pause, not the
# user's comma, so it goes and the line gets its full stop, as with paragraphs.
@pytest.mark.parametrize("said, expected", [
    ("Dear John, new line thanks", f"Dear John.{_M}Thanks."),
    ("I finished the van, new line, next I'll do the bed.",
     f"I finished the van.{_M}Next I'll do the bed."),
    ("Buy milk, newline, eggs, newline, bread.",
     f"Buy milk.{_M}Eggs.{_M}Bread."),
    ("first line, line break, second", f"First line.{_M}Second."),
    ("Okay, new line.", f"Okay.{_M}"),
])
def test_engine_comma_before_a_line_break_goes(said, expected):
    assert normalize_text(said) == expected


def test_engine_comma_before_a_final_line_break_with_auto_period_off():
    assert normalize_text("Okay, new line.", auto_period=False) == f"Okay{_M}"


def test_comma_before_a_final_line_break_gets_no_period():
    out = normalize_text("dear john comma new line")
    assert out == f"Dear john,{_M}"
    assert append_auto_punct(out, True, True) == f"Dear john,{_M}"


def test_trailing_comma_is_kept_with_auto_period_off():
    assert normalize_text("it was hot comma", auto_period=False) == "It was hot,"


def test_trailing_comma_at_the_end_still_becomes_a_period():
    assert normalize_text("it was hot comma") == "It was hot."


# A full stop or question mark followed by a closing quote or bracket already
# ends the line, so it gets no second one: "hello close quote" must not end as
# 'Hello.”. '.
@pytest.mark.parametrize("said, expected", [
    ("hello close quote", "Hello.” "),
    ("why question mark close quote", "Why?” "),
    ("hi exclamation mark close quote", "Hi!” "),
    ("the end period close quote", "The end.” "),
    ("see note period close parenthesis", "See note.) "),
])
def test_no_second_period_after_a_closing_quote_or_bracket(said, expected):
    assert append_auto_punct(normalize_text(said), True, True) == expected


def test_a_closing_bracket_without_punctuation_still_gets_a_period():
    assert normalize_text("see the note close parenthesis") == "See the note)."


# Only a spoken dash joins words: a dash the engine writes as a pause keeps its
# spaces (not "going-you know-to"), and a tab next to a spoken dash is kept.
def test_an_engine_dash_between_words_keeps_its_spaces():
    assert (normalize_text("I was going - you know - to the store")
            == "I was going - you know - to the store.")


def test_a_tab_before_a_spoken_dash_is_kept():
    assert normalize_text("first press tab dash second") == "First\t-second."


@pytest.mark.parametrize("said, expected", [
    ("well dash known author", "Well-known author."),
    ("e hyphen mail", "E-mail."),
    ("the plan colon dash next", "The plan: next."),
])
def test_a_spoken_dash_still_joins_the_words(said, expected):
    assert normalize_text(said) == expected


# The engine often ends the sentence itself before a spoken mark: the spoken
# mark replaces its period instead of landing after it as ". ?".
@pytest.mark.parametrize("said,typed", [
    ("If I like 'em, I'll send 'em. Question mark.", "If I like 'em, I'll send 'em?"),
    ("Off of GitHub for TuxTalk. Question mark.", "Off of GitHub for TuxTalk?"),
    ("Is it done. Exclamation point.", "Is it done!"),
    ("Is it done. Exclamation mark. Then more.", "Is it done! Then more."),
    ("Is it done? Question mark.", "Is it done?"),
    ("is it done question mark", "Is it done?"),
])
def test_a_spoken_mark_replaces_the_engines_period(said, typed):
    assert normalize_text(said) == typed


def test_an_abbreviation_keeps_its_period_before_a_spoken_mark():
    assert normalize_text("Is it the U.S. question mark").startswith("Is it the U.S.")


# "ended with a question mark": with an article the mark is talked about,
# not dictated (it typed "ended with a?").
@pytest.mark.parametrize("said, typed", [
    ("should it have ended with a question mark", "Should it have ended with a question mark."),
    ("end it with an exclamation point", "End it with an exclamation point."),
    ("add a comma after hello", "Add a comma after hello."),
])
def test_a_mark_talked_about_stays_words(said, typed):
    assert normalize_text(said) == typed


@pytest.mark.parametrize("said, typed", [
    ("is that right question mark", "Is that right?"),
    ("that was great exclamation point", "That was great!"),
    ("hello comma world", "Hello, world."),
])
def test_a_dictated_mark_is_still_typed(said, typed):
    assert normalize_text(said) == typed


# A capital letter and a number name one thing (a key, a grid square, a seat),
# so the number is a digit whatever the numbers setting does.
@pytest.mark.parametrize("said, typed", [
    ("when I let go of F eight", "When I let go of F8."),
    ("press F twelve", "Press F12."),
    ("tap f one", "Tap F1."),                      # Whisper writes "f"
    ("go to B five A seven", "Go to B5 A7."),      # A beside another pair
    ("A seven B five", "A7 B5."),
    ("vitamin B twelve", "Vitamin B12."),
    ("I sat in row E nine", "I sat in row E9."),
    ("I have F8 already", "I have F8 already."),
])
def test_a_letter_and_number_are_written_together(said, typed):
    assert normalize_text(said) == typed


@pytest.mark.parametrize("said, typed", [
    ("A five minute walk", "A five minute walk."),    # A and I are words
    ("I two think so", "I two think so."),
    ("out of eight people", "Out of eight people."),  # "of" ends in f
    ("if eight people come", "If eight people come."),
    ("the grade was an F. Eight of us passed", "The grade was an F. Eight of us passed."),
])
def test_words_next_to_a_number_are_left_alone(said, typed):
    assert normalize_text(said) == typed


def test_a_carriage_return_talked_about_stays_words():
    """"it inserted a carriage return" typed "Inserted a carriage" and a
    line break."""
    assert normalize_text("it inserted a carriage return") == "It inserted a carriage return."
    assert normalize_text("thank you return") != "Thank you return."   # the command still works


@pytest.mark.parametrize("said, typed", [
    ("name soft break address", "Name   address."),
    ("one, soft line, two", "One   two."),
    ("name soft break", "Name."),           # nothing after it: no spaces to keep
])
def test_soft_break_is_three_spaces(said, typed):
    """Help and Voice Commands promise three spaces; the space tidy made
    them one."""
    assert normalize_text(said) == typed
