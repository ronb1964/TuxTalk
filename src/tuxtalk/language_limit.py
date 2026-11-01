"""Auto-detect, limited to the languages the user speaks.

Whisper's auto-detect picks from 99 languages, and on a short or accented
dictation it can pick the wrong one: Spanish heard as French, a one-word
English "Okay" heard as Welsh, and the words then come out in that language.
Someone who only ever speaks English and Spanish can tick those two
(Preferences → General → Change model, device or language → Language →
Several languages), and the detection is then held to them.

Whisper scores every language before it writes a word, so the limit costs
nothing while it guesses right: the dictation goes ahead as detected. Only
when its first choice is off the list is it run again, told to use the most
likely language that is on it.

Parakeet has no language setting at all (it decides internally, among its 25
languages), so the list applies to the Whisper models only. Preferences says
so next to the list.

The setting is stored as text, "en,es", like every other setting in
config.toml; "" means any language.
"""
from .logger import setup_logger
from .recommend import LANGUAGES

logger = setup_logger(__name__)

# The languages the pickers offer, and so the only ones a list may hold.
KNOWN = frozenset(code for code, _name in LANGUAGES)


def parse(setting):
    """The language codes in *setting* ("en, es"), known ones only, in order,
    without repeats. () for "" or a list with nothing usable in it."""
    codes = []
    for part in (setting or "").split(","):
        code = part.strip().lower()
        if code in KNOWN and code not in codes:
            codes.append(code)
    return tuple(codes)


def to_setting(codes):
    """The config text for *codes*: "en,es", in the pickers' order."""
    chosen = set(codes)
    return ",".join(code for code, _name in LANGUAGES if code in chosen)


def describe(codes, limit=3):
    """How Preferences shows a list: "Any language", "English, Spanish",
    or "English, Spanish, French and 2 more"."""
    from .recommend import language_name
    if not codes:
        return "Any language"
    names = [language_name(c) for c in codes]
    if len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])} and {len(names) - limit} more"


def forced_language(codes):
    """With exactly one language on the list there is nothing to detect:
    that language is used, the same as choosing it by hand. None otherwise."""
    return codes[0] if len(codes) == 1 else None


def best_allowed(probabilities, codes):
    """The language on *codes* that Whisper scored highest, from its
    {code: probability} scores. A language missing from the scores counts
    as 0, so the first one on the list wins a tie at nothing."""
    return max(codes, key=lambda c: (probabilities.get(c, 0.0), -codes.index(c)))


def redirect(detected, probabilities, codes):
    """The language to run Whisper again with, or None to keep its own pick.

    None when there is no list, or when *detected* is on it."""
    if not codes or detected in codes:
        return None
    return best_allowed(probabilities, codes)


def language_and_limit(language, codes):
    """(language, limit_to) for one transcription, dictation or a file: a
    language chosen by hand is used as it is; otherwise a list of one is
    that language, and a longer list holds auto-detect to it."""
    if not language:
        language = forced_language(codes)
    return (language, ()) if language else (None, tuple(codes))


def whisper_held_to(transcribe, audio, language, limit_to, **options):
    """faster-whisper's (segments, info) for *audio*, run again in the
    likeliest language on *limit_to* when it detected one that isn't.
    *transcribe* is the model's transcribe. It detects the language before
    it decodes a word (segments is a generator), so starting again costs
    only that detection, and only when its pick is off the list."""
    segments, info = transcribe(audio, language=language, **options)
    if not limit_to:
        return segments, info
    better = redirect(info.language, dict(info.all_language_probs or ()), limit_to)
    if better:
        logger.info(f"Language: Whisper heard {info.language}, which isn't on your list; "
                    f"using {better}")
        segments, info = transcribe(audio, language=better, **options)
    return segments, info
