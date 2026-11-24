"""German apps, windows, keys, sound and mouse commands, said as English.

With voice commands in German, a whole dictation such as "Firefox
schließen", "zu Desktop zwei wechseln" or "Rechtsklick fünf" is turned into
the English command it means ("close firefox", "switch to desktop 2",
"right click 5"), and command_phrases reads that as it reads English: one
parser decides what every command does, whatever language it was said in.
An app's name passes through as it was said.

Most German commands put the verb last ("Firefox öffnen"); one said first
("öffne Firefox") is read with the verb moved to the end, so each rule is
written once. Like the English phrases, only a whole dictation counts:
anything else gives None and is typed.
"""
import re

from . import spoken_numbers
from .undo import GERMAN_NUMBER_WORDS
from .voice_navigation import german_verb_last, normalize

# A verb said first, as the infinitive it is said last as.
_VERBS_FIRST = {
    "öffne": "öffnen", "öffnen": "öffnen", "starte": "starten", "start": "starten",
    "schließe": "schließen", "schließ": "schließen", "wechsle": "wechseln",
    "wechsel": "wechseln", "wechsele": "wechseln", "minimiere": "minimieren",
    "maximiere": "maximieren", "lade": "laden", "speichere": "speichern",
    "speicher": "speichern", "kopiere": "kopieren", "kopier": "kopieren",
    "sperre": "sperren", "sperr": "sperren", "mache": "machen", "mach": "machen",
    "zeige": "anzeigen", "zeig": "anzeigen", "drücke": "drücken", "drück": "drücken",
    "geh": "gehen", "gehe": "gehen", "scrolle": "scrollen", "scroll": "scrollen",
    "pausiere": "pausieren", "spiele": "abspielen", "spiel": "abspielen",
    "zentriere": "zentrieren",
}

_PLACES = {"links": "left", "rechts": "right", "oben": "top", "unten": "bottom"}
_DIRECTIONS = {"links": "left", "rechts": "right", "hoch": "up", "rauf": "up", "oben": "up",
               "runter": "down", "unten": "down"}
_CLICKS = {"klick": "click", "klicken": "click", "linksklick": "left click",
           "rechtsklick": "right click", "doppelklick": "double click",
           "mittelklick": "middle click"}
# The app kinds app_finder knows by their English word.
_APP_KINDS = {"e mail": "email", "email": "email", "mail": "email", "dateien": "files",
              "dateimanager": "files", "rechner": "calculator", "taschenrechner": "calculator"}
# A German article before an app's name, as the English one: app_finder
# lets a loose match ("Datei" for Dateien) count only for a bare name, so
# "eine Datei öffnen" is typed, as "open a file" is.
_ARTICLES = {"den": "the", "die": "the", "das": "the", "dem": "the", "ein": "a", "eine": "a",
             "einen": "a"}

# German phrase -> English phrase, for commands that take no name or number.
_FIXED = {
    **dict.fromkeys(("neuer tab", "neuen tab", "neuen tab öffnen", "neuer tab öffnen"),
                    "new tab"),
    **dict.fromkeys(("tab schließen", "den tab schließen", "diesen tab schließen"), "close tab"),
    **dict.fromkeys(("nächster tab", "nächsten tab"), "next tab"),
    **dict.fromkeys(("vorheriger tab", "vorherigen tab"), "previous tab"),
    **dict.fromkeys(("tab wieder öffnen", "geschlossenen tab öffnen",
                     "geschlossenen tab wieder öffnen"), "reopen tab"),
    **dict.fromkeys(("eine seite zurück", "eine seite zurück gehen"), "go back a page"),
    **dict.fromkeys(("eine seite vor", "eine seite vorwärts", "eine seite weiter",
                     "eine seite vor gehen"), "go forward a page"),
    **dict.fromkeys(("seite neu laden", "die seite neu laden", "seite aktualisieren"),
                    "reload the page"),
    **dict.fromkeys(("seite hoch", "seite rauf", "bild hoch", "bild auf"), "page up"),
    **dict.fromkeys(("seite runter", "bild runter", "bild ab"), "page down"),
    **dict.fromkeys(("nach ganz oben", "nach ganz oben gehen"), "go to the top"),
    **dict.fromkeys(("nach ganz unten", "nach ganz unten gehen"), "go to the bottom"),
    **dict.fromkeys(("auf der seite suchen", "in der seite suchen"), "find on page"),
    **dict.fromkeys(("datei speichern", "die datei speichern"), "save the file"),
    "alles markieren": "select all",
    **dict.fromkeys(("auswahl kopieren", "die auswahl kopieren", "markierung kopieren"),
                    "copy the selection"),
    **dict.fromkeys(("das einfügen", "zwischenablage einfügen"), "paste it"),
    **dict.fromkeys(("escape drücken", "esc drücken", "escape taste drücken",
                     "esc taste drücken"), "press escape"),
    **dict.fromkeys(("fenster schließen", "das fenster schließen", "dieses fenster schließen"),
                    "close window"),
    **dict.fromkeys(("fenster minimieren", "das fenster minimieren"), "minimize window"),
    **dict.fromkeys(("fenster maximieren", "das fenster maximieren"), "maximize window"),
    **dict.fromkeys(("fenster wiederherstellen", "das fenster wiederherstellen"),
                    "restore window"),
    **dict.fromkeys(("fenster zentrieren", "das fenster zentrieren"), "center window"),
    **dict.fromkeys(("fenster auf den anderen bildschirm",
                     "fenster auf den anderen bildschirm verschieben"),
                    "move window to the other screen"),
    **dict.fromkeys(("desktop anzeigen", "den desktop anzeigen", "schreibtisch anzeigen"),
                    "show desktop"),
    "alle fenster anzeigen": "show all windows",
    **dict.fromkeys(("nächster desktop", "nächsten desktop"), "next desktop"),
    **dict.fromkeys(("vorheriger desktop", "vorherigen desktop"), "previous desktop"),
    **dict.fromkeys(("lauter", "lautstärke hoch", "lautstärke lauter", "lautstärke rauf"),
                    "volume up"),
    **dict.fromkeys(("leiser", "lautstärke runter", "lautstärke leiser"), "volume down"),
    **dict.fromkeys(("ton aus", "stumm", "stumm schalten", "ton stumm schalten"),
                    "mute the sound"),
    **dict.fromkeys(("ton an", "ton ein"), "unmute the sound"),
    **dict.fromkeys(("musik pausieren", "die musik pausieren", "musik anhalten",
                     "musik stoppen"), "pause music"),
    **dict.fromkeys(("musik abspielen", "musik fortsetzen", "musik starten"), "play music"),
    **dict.fromkeys(("nächster titel", "nächstes lied", "nächster song"), "next track"),
    **dict.fromkeys(("vorheriger titel", "vorheriges lied", "vorheriger song"),
                    "previous track"),
    **dict.fromkeys(("bildschirm sperren", "den bildschirm sperren", "computer sperren"),
                    "lock the screen"),
    **dict.fromkeys(("bildschirmfoto machen", "ein bildschirmfoto machen",
                     "screenshot machen", "einen screenshot machen"), "take a screenshot"),
    **dict.fromkeys(("bildschirmfoto von diesem fenster", "screenshot von diesem fenster",
                     "bildschirmfoto von diesem fenster machen"), "screenshot this window"),
    **dict.fromkeys(("was kann ich sagen", "sprachbefehle anzeigen"), "what can i say"),
    **dict.fromkeys(("mausraster", "mausraster anzeigen", "raster anzeigen", "mausgitter"),
                    "mouse grid"),
    "abbrechen": "cancel",
    **dict.fromkeys(("raster schließen", "raster ausblenden"), "close grid"),
    **dict.fromkeys(("hierhin bewegen", "maus hierhin", "maus hierhin bewegen",
                     "zeiger hierhin bewegen", "mauszeiger hierhin",
                     "mauszeiger hierhin bewegen"), "move here"),
}


def _number(words: str) -> str | None:
    """*words* ("fünf", "zwölf", "25") as digits, or None."""
    if words.isdigit():
        return words
    if words in GERMAN_NUMBER_WORDS:
        return str(GERMAN_NUMBER_WORDS[words])
    digits = spoken_numbers.to_digits(words, "de")
    return digits if digits.isascii() and digits.isdigit() else None


def _app(words: str) -> str:
    """An app's name as said, an app kind in English, after its article in
    English."""
    first, _, rest = words.partition(" ")
    article, name = (_ARTICLES[first], rest) if first in _ARTICLES and rest else ("", words)
    name = _APP_KINDS.get(name, name)
    return f"{article} {name}" if article else name


def _with_number(template: str):
    """A rule's builder that puts the number its first group says in *template*."""
    def build(m):
        number = _number(m[1]) if m[1] else ""
        return None if number is None else template.format(number).strip()
    return build


# German phrase -> English phrase, for commands with a number, a direction or
# a name in them; the first that matches all of the words counts.
_RULES = [
    (r"(?:(?!nach )(.+?) )?(?:nach )?(unten|runter|oben|hoch) scrollen",
     lambda m: _scroll(m[2], m[1])),
    (r"fenster nach (?:(oben|unten) )?(links|rechts|oben|unten)",
     lambda m: "snap " + " ".join(_PLACES[w] for w in (m[1], m[2]) if w)),
    (r"zu desktop (.+) wechseln", _with_number("switch to desktop {}")),
    (r"fenster auf desktop (.+?)(?: verschieben)?", _with_number("move window to desktop {}")),
    (r"lautstärke (?:hoch|lauter|rauf) (.+)", _with_number("volume up {}")),
    (r"lautstärke (?:runter|leiser) (.+)", _with_number("volume down {}")),
    (r"(?:nummer )?(.+)", _with_number("{}")),
    (rf"({'|'.join(_CLICKS)})(?: (?:auf )?(?:nummer )?(.+))?",
     lambda m: _click(m[1], m[2])),
    (r"(links|rechts|hoch|rauf|runter)(?: (.+))?", lambda m: _nudge(m[1], m[2])),
    (r"alle (.+) schließen", lambda m: f"close all {_app(m[1])}"),
    (r"zu (.+) wechseln", lambda m: f"switch to {_app(m[1])}"),
    (r"(.+) (schließen|minimieren|maximieren|wiederherstellen)",
     lambda m: f"{_WINDOW_VERBS[m[2]]} {_app(m[1])}"),
    (r"(?:weiteren|weiteres|noch (?:ein|einen|eine)) (.+) (?:öffnen|starten|aufrufen)",
     lambda m: f"open another {_app(m[1])}"),
    (r"(?:neues|weiteres) (.+?)[ ]?fenster öffnen", lambda m: f"open another {_app(m[1])}"),
    (r"(.+) (öffnen|aufrufen|starten)",
     lambda m: f"{'start' if m[2] == 'starten' else 'open'} {_app(m[1])}"),
]
_WINDOW_VERBS = {"schließen": "close", "minimieren": "minimize", "maximieren": "maximize",
                 "wiederherstellen": "restore"}


def _swiss(words: dict) -> dict:
    """*words* ({said: meant}) with each said word's Swiss spelling too, ss
    for ß ("schliessen"). Only the command's own words: an app's name is
    found as it was said."""
    return {**words, **{said.replace("ß", "ss"): meant for said, meant in words.items()}}


_FIXED = _swiss(_FIXED)
_VERBS_FIRST = _swiss(_VERBS_FIRST)
_WINDOW_VERBS = _swiss(_WINDOW_VERBS)
_COMPILED = [(re.compile(pattern.replace("ß", "(?:ß|ss)")), build) for pattern, build in _RULES]
# A web address, read from the words as said: its dots, slashes and a
# path's capitals stay ("geh zu example.com/Reset"). "Punkt" is its dot.
_GO_TO = re.compile(
    r"^\s*(?:bitte\s+)?(?:geh(?:e)?\s+zu\s+(?P<first>.+?)|zu\s+(?P<last>.+?)\s+gehen)"
    r"(?:\s+bitte)?[\s.!?]*$", re.IGNORECASE)
_SCHEME = re.compile(r"((?i:https?://)?)(.*)", re.DOTALL)
# A web search, read from the words as said so their capitals stay. Not
# "suche nach ...", which starts everyday sentences ("Suche nach einer
# Wohnung in Berlin.").
_SEARCH = re.compile(
    r"^\s*(?:websuche|im\s+(?:web|internet)\s+suchen|suche\s+im\s+(?:web|internet))"
    r"\s*[,:]?(?:\s+nach)?\s+(?P<words>.+?)[\s.!?]*$", re.IGNORECASE)


def _scroll(direction: str, count: str | None) -> str | None:
    number = _number(count) if count else ""
    if number is None:
        return None
    return f"scroll {_DIRECTIONS[direction]} {number}".strip()


def _click(word: str, cell: str | None) -> str | None:
    number = _number(cell) if cell else ""
    return None if number is None else f"{_CLICKS[word]} {number}".strip()


def _nudge(direction: str, steps: str | None) -> str | None:
    number = _number(steps) if steps else ""
    return None if number is None else f"{_DIRECTIONS[direction]} {number}".strip()


def to_english(text: str) -> str | None:
    """The English command the German dictation *text* is, or None when it
    is no German command ("geh zu youtube Punkt com" is "go to youtube dot
    com")."""
    if m := _SEARCH.match(text):
        return f"web search {m.group('words')}"
    if m := _GO_TO.match(text):
        # A spoken "Punkt" is a dot in the host only: a path keeps its words.
        scheme, rest = _SCHEME.match(m["first"] or m["last"]).groups()
        host, slash, path = rest.partition("/")
        return ("go to " + scheme + re.sub(r"(?i)\s*\bpunkt\b\s*", " dot ", host)
                + slash + path)
    said = german_verb_last(normalize(text), _VERBS_FIRST)
    if not said:
        return None
    if english := _FIXED.get(said):
        return english
    for pattern, build in _COMPILED:
        if m := pattern.fullmatch(said):
            english = build(m)
            if english:
                return english
    return None
