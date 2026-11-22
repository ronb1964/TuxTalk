"""Language codes as locales and settings write them, cut to the bare
language: "de_DE.UTF-8", "de-CH" and "de" are all German to the text rules."""


def locale_language(value):
    """"de_DE.UTF-8" or "de-CH" -> "de"; None for "C", "POSIX" or anything
    unreadable."""
    value = (value or "").strip()
    if not value or value == "POSIX" or value.split(".")[0] == "C":
        return None
    code = value.split(".")[0].split("@")[0].replace("-", "_").split("_")[0].lower()
    return code if code.isalpha() and 2 <= len(code) <= 3 else None
