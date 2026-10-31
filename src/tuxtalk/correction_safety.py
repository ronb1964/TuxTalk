"""Whether the local AI's fix of a self-correction may replace what was said.

The model is never trusted. Its answer is used only if edit_is_safe() says
so: every word must be one that was spoken, and the change must have one of
these shapes: words deleted (fillers, stutters, or the wrong half of a
correction, ending with the correction phrase), the corrected words put in
place of the wrong ones ("two boxes of screws, no wait, three boxes" ->
"three boxes of screws"), or a restated clause's new value swapped into the
first wording. Anything else (an answered question, a reworded sentence, a
dropped detail, the wrong half kept) is thrown away, and the sentence is
typed exactly as spoken.
"""
import re

from .cleanup import LEGIT_DOUBLES

# The fillers the AI is told it may delete (ai_cleanup's prompt), and no others: "hmm"
# carries meaning ("hmm, that sounds right"). A "like" it drops isn't always a
# filler either, but the prompt asks for it.
_FILLERS = {"um", "uh", "umm", "uhm", "er", "erm", "like"}
_FILLER_PHRASES = [("you", "know")]
_CUES = [("no", "wait"), ("wait", "no"), ("wait",), ("i", "mean"), ("sorry",), ("actually",),
         ("make", "that"), ("scratch", "that"), ("or", "rather"), ("rather",), ("no",)]
_CUES_LONGEST_FIRST = sorted(_CUES, key=len, reverse=True)
_CUE_WORDS = {word for cue in _CUES for word in cue}
# How many words a correction may throw away besides its correction phrase.
_MAX_REPLACED = 6
# Small words that may appear on both sides of a spliced correction without
# carrying a value: "the red paint for the van. Sorry, the blue paint".
LINKING_WORDS = {"the", "a", "an", "of", "for", "to", "in", "on", "at", "and", "or",
                 "with", "my", "your", "our", "his", "her", "their", "its", "this", "that"}

# Words a restated clause uses in place of ones already said: "send IT to Jim",
# "pick THEM up", "the red ONE".
STAND_INS = {"it", "them", "him", "her", "they", "one", "ones", "this", "that",
             "these", "those", "there"}
_MAX_SWAPPED = 3


# Numbers whole with their separators, so "2.5" changed to "2,5", "5/6" to
# "5-6" or "4:15" to "4/15" is a changed value, also when spaces stand around
# a slash, colon or dash ("3 - 4" into "3 / 4"; not a comma, which lists
# numbers as often as it groups digits); words in any script ("café",
# "Müller", "東京"); and the signs that carry a value as words of their own: a
# currency, a percent, and a minus or plus before a number (not a hyphen in
# "well-known").
_TOKEN = re.compile(r"\d+(?:[.,]\d+| *[/:–-] *\d+)*|[\w']+|[€$£¥%]|(?<!\w)[-−+](?=\d)")
# Words that turn a statement around. One left over from the wrong part of a
# correction, when the corrected words don't say it, reverses the sentence:
# "I will not, I mean I will, go" is not "I will not go".
_NEGATIONS = {"not", "never", "no", "nothing", "nobody", "none", "neither", "nor"}
# An answer that starts a new line or adds a tab inside a sentence types it:
# in a chat box a line break can send the message half-written.
_LAYOUT_CHARS = "\n\r\t"


def words(text):
    """The words and value signs of *text*, lower case, as the check compares them."""
    return [token.replace(" ", "") for token in _TOKEN.findall(text.lower().replace("’", "'"))]


def _deleted_runs(src, out):
    """Line *out* up as a subsequence of *src*: the runs of deleted words, or
    None if *out* contains any word that is not in *src* in that order."""
    runs, run, j = [], [], 0
    for i, w in enumerate(src):
        if j < len(out) and w == out[j]:
            if run:
                runs.append((i - len(run), run))
                run = []
            j += 1
        else:
            run.append(w)
    if run:
        runs.append((len(src) - len(run), run))
    return runs if j == len(out) else None


def _is_negation(word):
    return word in _NEGATIONS or word.endswith("n't")


def _without_trailing_cue_words(run):
    """*run* without the correction-phrase words at its end ("send it no")."""
    end = len(run)
    while end and run[end - 1] in _CUE_WORDS:
        end -= 1
    return run[:end]


def _without_fillers(run):
    """*run* (a list of words) with filler words and phrases taken out."""
    kept, i = [], 0
    while i < len(run):
        for phrase in _FILLER_PHRASES:
            if tuple(run[i:i + len(phrase)]) == phrase:
                i += len(phrase)
                break
        else:
            if run[i] not in _FILLERS:
                kept.append(run[i])
            i += 1
    return kept


def _deletion_allowed(run, src, start):
    """True if deleting *run* (at *start* in *src*) is fillers, a stutter or a correction."""
    core = _without_fillers(run)
    if not core:
        return True                                   # only fillers went
    n = len(core)
    before, after = src[max(0, start - n):start], src[start + len(run):start + len(run) + n]
    prev = src[start - 1] if start else None
    nxt = src[start + len(run)] if start + len(run) < len(src) else None
    one_word_repeated = len(set(core)) == 1 and core[0] in (prev, nxt)
    if (core in (before, after) or one_word_repeated) and not (
            len(set(core)) == 1 and core[0] in LEGIT_DOUBLES):
        return True                                   # a stutter
    # A correction drops the wrong part and then the correction phrase, so the
    # removed words must END with the phrase. Removing the phrase and what
    # FOLLOWS it would keep the wrong version ("5 minutes, actually 10" ->
    # "5 minutes"), which is exactly the mistake this check exists to stop.
    # The wrong part is also never much longer than the correction that
    # replaces it: "Order two boxes of screws. No, wait, three boxes" must not
    # become "Three boxes", which throws away "order", the point of it.
    following = len(src) - (start + len(run))
    # Strip every correction phrase from the end ("no wait, make that" is two).
    stripped = list(core)
    while True:
        for cue in _CUES_LONGEST_FIRST:
            if len(stripped) >= len(cue) and tuple(stripped[-len(cue):]) == cue:
                stripped = stripped[:-len(cue)]
                break
        else:
            break
    if len(stripped) == len(core):
        return False                                  # no correction phrase at the end
    wrong = len(stripped)
    if wrong == 0:
        return False    # only the phrase went, leaving both values: "5, actually 10" -> "5 10"
    return wrong <= _MAX_REPLACED and wrong <= following + 1


def _is_spliced_correction(src, out):
    """The other shape a correct fix takes, where the corrected words take the
    place of the wrong ones and the rest of the original phrase follows them:

        order two boxes of screws  no wait  three boxes
        start wrong-part           cue      right-part
     -> order three boxes of screws
        start right-part leftover-of-wrong-part

    Accepted only in exactly that shape: every word was spoken, the start and
    the right part are kept whole and in order, the leftover is the END of the
    wrong part, and at most _MAX_REPLACED words of the wrong part are dropped.
    Something must really be replaced, and nothing that reverses the sentence
    may survive from the wrong part: "do not send it, no wait, send it" must
    never become "do not send it".
    """
    for c_start in range(len(src)):
        for cue in _CUES_LONGEST_FIRST:
            if tuple(src[c_start:c_start + len(cue)]) != cue:
                continue
            after = src[c_start + len(cue):]
            for w_start in range(max(0, c_start - _MAX_REPLACED - 4), c_start):
                start, wrong = src[:w_start], src[w_start:c_start]
                # keep < len(wrong): something is replaced
                if any(_splices(start, wrong, keep, after, out) for keep in range(len(wrong))):
                    return True
            break   # longest cue at this position tried; move on
    return False


def _splices(start, wrong, keep, after, out):
    """True if *out* is *start*, then the start of *after* (the right part),
    then the last *keep* words of *wrong*, then the rest of *after*."""
    leftover = wrong[len(wrong) - keep:] if keep else []
    if len(wrong) - keep > _MAX_REPLACED:
        return False
    # The replaced words must be a value, not the phrase itself:
    # "three no wait four" -> "three four" is not "no" -> "four".
    replaced = _without_trailing_cue_words(wrong[:len(wrong) - keep])
    if not replaced:
        return False
    if any(_is_negation(w) and w not in after for w in leftover):
        return False
    for split in range(1, len(after) + 1):  # right part is not empty
        right, rest = after[:split], after[split:]
        # The correction replaces a like-sized piece ("two boxes" -> "three
        # boxes"), and what is carried over must be new to it. Without these,
        # "to 5 minutes, actually 10 minutes" could pass as "... 10 minutes 5
        # minutes", smuggling the wrong value back in.
        # A right part that is the replaced words themselves changes nothing
        # and keeps the wrong version; one that goes on from them is a
        # refinement: "call me Monday, sorry, Monday afternoon".
        if (right == replaced or abs((len(wrong) - keep) - len(right)) > 1
                or (set(leftover) & set(right)) - LINKING_WORDS):
            continue
        if out == start + right + leftover + rest:
            return True
    return False


def _is_restated_correction(src, out):
    """The shape a fix takes when the whole clause is said again: the model
    keeps the first wording and swaps in only the new value.

        send the report to bob  scratch that  send it to jim
        first wording           cue           restated
     -> send the report to jim

    Accepted only in exactly that shape: everything before the cue is kept
    except one piece of up to _MAX_SWAPPED words, which is replaced by a
    different piece of the restated clause that sits next to the same word
    ("to bob" / "to jim"), or at the start of both. Every other restated
    word was already said or stands in for something said ("it"), so no
    second change is dropped: "Call Bob, scratch that, email Jim" must not
    become "Email Bob".
    """
    for c_start in range(1, len(src)):
        for cue in _CUES_LONGEST_FIRST:
            if tuple(src[c_start:c_start + len(cue)]) != cue:
                continue
            first, restated = src[:c_start], src[c_start + len(cue):]
            # The answer fixes where the swap is: it keeps *before* and
            # *after* of the first wording, so only the split point varies.
            for w_start in range(len(first)):
                before = first[:w_start]
                for w_len in range(1, _MAX_SWAPPED + 1):
                    wrong, after = first[w_start:w_start + w_len], first[w_start + w_len:]
                    new = out[len(before):len(out) - len(after)]
                    if (len(wrong) < w_len or not 1 <= len(new) <= _MAX_SWAPPED
                            or new == wrong or out[:len(before)] != before
                            or out[len(out) - len(after):] != after
                            or (set(wrong) | set(new)) & _CUE_WORDS):
                        continue
                    for x_start in range(len(restated) - len(new) + 1):
                        if restated[x_start:x_start + len(new)] != new:
                            continue
                        # Same place: the same word before both, or both
                        # start their clause ("email ..." / "text ...").
                        if before:
                            placed = x_start > 0 and restated[x_start - 1] == before[-1]
                        else:
                            placed = x_start == 0
                        # Never the replaced words: "Call Bob and Sue, scratch
                        # that, call Jim and Bob" is not "Call Jim and Sue".
                        rest = restated[:x_start] + restated[x_start + len(new):]
                        if placed and set(rest) <= set(before + after) | STAND_INS:
                            return True
            break   # longest cue at this position tried; move on
    return False


def _keeps_a_dropped_negation(src, out):
    """True if *out* keeps a negation the correction took out: the words
    after a cue repeat the negated word without it ("don't send it, no wait,
    send it today"), so the answer must not say "don't send it today"."""
    for c_start in range(1, len(src)):
        for cue in _CUES_LONGEST_FIRST:
            if tuple(src[c_start:c_start + len(cue)]) != cue:
                continue
            first, after = src[:c_start], src[c_start + len(cue):]
            if any(_is_negation(w) and w in out and w not in after and first[i + 1] in after
                   for i, w in enumerate(first[:-1])):
                return True
            break   # longest cue at this position tried; move on
    return False


def edit_is_safe(said: str, edited: str) -> bool:
    """True only if *edited* is *said* with nothing but allowed edits."""
    if any(c in edited and c not in said for c in _LAYOUT_CHARS):
        return False
    src, out = words(said), words(edited)
    if not out or _keeps_a_dropped_negation(src, out):
        return False
    runs = _deleted_runs(src, out)
    if runs is not None and all(_deletion_allowed(run, src, start) for start, run in runs):
        return True
    # Compare without fillers for the spliced shapes; those may go anywhere.
    src, out = _without_fillers(src), _without_fillers(out)
    return _is_spliced_correction(src, out) or _is_restated_correction(src, out)
