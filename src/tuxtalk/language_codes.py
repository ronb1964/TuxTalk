"""Language codes as locales and settings write them, cut to the bare
language: "de_DE.UTF-8", "de-CH" and "de" are all German to the text rules,
and the locale the desktop's text is in."""
import os


def locale_language(value):
    """"de_DE.UTF-8" or "de-CH" -> "de"; None for "C", "POSIX" or anything
    unreadable."""
    value = (value or "").strip()
    if not value or value == "POSIX" or value.split(".")[0] == "C":
        return None
    code = value.split(".")[0].split("@")[0].replace("-", "_").split("_")[0].lower()
    return code if code.isalpha() and 2 <= len(code) <= 3 else None


def display_locale(env=None) -> str:
    """The locale the desktop's text is in, region and all where it's known
    ("de_CH.UTF-8"; "de" from LANGUAGE), or "" for the C locale or none.

    The first of LC_ALL, LC_MESSAGES and LANG that is set, unless LANGUAGE's
    first readable entry names another language or a region of its own, as
    in gettext (which ignores LANGUAGE when the locale is C). KDE sets
    LANGUAGE when the display language differs from the formats locale, and
    sets the bare "de" for a Swiss German user, which mustn't hide the de_CH
    locale."""
    env = os.environ if env is None else env
    locale = next((env[v] for v in ("LC_ALL", "LC_MESSAGES", "LANG") if env.get(v)), "")
    language = locale_language(locale)
    if not language:
        return ""               # C or POSIX wins over everything, LANGUAGE included
    first = next((e for e in (env.get("LANGUAGE") or "").split(":")
                  if locale_language(e)), None)
    if first and (locale_language(first) != language or "_" in first):
        return first
    return locale


def swiss_german_locale(env=None) -> bool:
    """Whether the system is set to Swiss German (de_CH, or gsw for Swiss
    German itself), where ß is never written."""
    return display_locale(env).replace("-", "_").startswith(("de_CH", "gsw"))
