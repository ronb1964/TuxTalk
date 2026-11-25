"""German voice-editing instructions done in code, said as English.

With voice commands in German, an instruction such as "alles in
Großbuchstaben", "die Kommas entfernen" or "das löschen" is read as the
English one it means ("make this all caps", "remove the commas", "delete
this"), and voice_edit_rules does it exactly, as it does the English. Only
for matching: the AI, when the edit needs it, still gets the words as said.

Like german_commands, most instructions put the verb last ("die Kommas
entfernen"); one said first ("entferne die Kommas") is read with the verb
moved to the end, so each rule is written once. Only a whole instruction
counts: anything else gives None and goes to the AI, as any instruction the
rules don't know does.
"""
import re

from .voice_navigation import german_verb_last

# A verb said first, as the infinitive it is said last as.
_VERBS_FIRST = {
    "mach": "machen", "mache": "machen", "schreib": "schreiben", "schreibe": "schreiben",
    "setz": "setzen", "setze": "setzen", "entfern": "entfernen", "entferne": "entfernen",
    "lösch": "löschen", "lösche": "löschen", "streich": "streichen", "streiche": "streichen",
    "nummerier": "nummerieren", "nummeriere": "nummerieren", "formatier": "formatieren",
    "formatiere": "formatieren", "unterstreich": "unterstreichen",
    "unterstreiche": "unterstreichen",
}

# What the instruction is about, if it says: "das", "alles", "den Text".
_TARGET = (r"(?:(?:das|dies|es|alles|das alles|den text|die auswahl|die zeilen|jede zeile"
           r"|alle zeilen|daraus|den ganzen text) )?")
# How a change of form ends, if it does: "in Großbuchstaben (schreiben)".
_MAKE = r"(?: (?:machen|schreiben|setzen|umwandeln|formatieren))?"
_TAKE_OUT = r"(?:entfernen|löschen|weg machen|wegmachen|rausnehmen|herausnehmen|streichen)"
_ALL = r"(?:(?:alle|die|sämtliche) )?"

# The marks "die Kommas entfernen" takes out, as voice_edit_rules names them.
_MARKS = {
    "kommas": "commas", "kommata": "commas", "beistriche": "commas", "punkte": "periods",
    "fragezeichen": "question marks", "ausrufezeichen": "exclamation points",
    "semikolons": "semicolons", "semikola": "semicolons", "strichpunkte": "semicolons",
    "doppelpunkte": "colons", "anführungszeichen": "quotes", "gänsefüßchen": "quotes",
    "apostrophe": "apostrophes", "bindestriche": "hyphens", "gedankenstriche": "dashes",
    "striche": "dashes", "klammern": "brackets", "satzzeichen": "punctuation",
}
_MARKS.update({said.replace("ß", "ss"): meant for said, meant in _MARKS.items()})
_SHORTCUTS = {"fett": "bold", "kursiv": "italic", "unterstrichen": "underlined"}
_SHORTCUT = rf"(?:in )?(?:{'|'.join(_SHORTCUTS)})"

# German instruction (verb last) -> English instruction; the first whose
# pattern matches all of the words counts.
_RULES = [
    (rf"{_TARGET}(?:in |zu |als )?(?:großbuchstaben|versalien){_MAKE}",
     lambda m: "make this all caps"),
    (rf"{_TARGET}(?:(?:in |zu |als )?kleinbuchstaben{_MAKE}|klein ?schreiben)",
     lambda m: "make this lower case"),
    (r"(?:jedes wort|alle wörter|jeden anfangsbuchstaben|alle anfangsbuchstaben)"
     r" groß(?: ?schreiben| machen)?", lambda m: "make this title case"),
    (rf"{_ALL}({'|'.join(_MARKS)}) {_TAKE_OUT}", lambda m: f"remove the {_MARKS[m[1]]}"),
    (rf"{_ALL}(?:aufzählungszeichen|spiegelstriche|nummerierung|nummern) {_TAKE_OUT}",
     lambda m: "remove the bullets"),
    (rf"(?:das wort|den begriff|den ausdruck|die wörter) (.+) {_TAKE_OUT}",
     lambda m: f"remove the word {m[1]}"),
    (rf"{_TARGET}(?:als |zu |in )?(?:eine[nr]? )?nummerierte[nr]? liste(?: daraus)?{_MAKE}"
     rf"|{_TARGET}nummerieren", lambda m: "make this a numbered list"),
    (rf"{_TARGET}(?:als |zu |in )?(?:eine[nr]? )?(?:aufzählung|stichpunkte|stichpunktliste"
     rf"|liste mit (?:aufzählungszeichen|spiegelstrichen)|liste)(?: daraus)?{_MAKE}",
     lambda m: "make this a bulleted list"),
    (rf"{_TARGET}(?:einrücken|einzug vergrößern)", lambda m: "indent this"),
    (rf"{_TARGET}ausrücken|(?:den )?einzug (?:verkleinern|entfernen)"
     rf"|(?:die )?einrückung (?:entfernen|aufheben)", lambda m: "unindent this"),
    # The verb said first, its particle last: "rück das ein".
    (r"rücke? (?:das |es |alles )?(ein|aus)",
     lambda m: "indent this" if m[1] == "ein" else "unindent this"),
    # As an instruction "alles" is the selection ("alles löschen" said as a
    # dictation of its own, without the editing key, clears the box: undo).
    (rf"(?:das|dies|es|alles|das alles|die auswahl|den text) {_TAKE_OUT}",
     lambda m: "delete this"),
    (rf"{_TARGET}({_SHORTCUT}(?: und {_SHORTCUT})*){_MAKE}",
     lambda m: "make this " + " and ".join(_SHORTCUTS[w] for w in m[1].split()
                                           if w in _SHORTCUTS)),
    (rf"{_TARGET}unterstreichen", lambda m: "underline this"),
]


# Swiss spelling, ss for ß, matched too ("Grossbuchstaben").
_COMPILED = [(re.compile(pattern.replace("ß", "(?:ß|ss)")), build) for pattern, build in _RULES]


def to_english(said: str) -> str | None:
    """The English instruction the German one *said* (lower case, single
    spaces: voice_edit_rules._norm) is, or None when it is none of the
    edits done in code."""
    said = german_verb_last(said, _VERBS_FIRST)
    for pattern, build in _COMPILED:
        if m := pattern.fullmatch(said):
            return build(m)
    return None
