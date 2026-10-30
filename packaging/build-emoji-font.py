#!/usr/bin/env python3
"""Build src/tuxtalk/fonts/tuxtalk-emoji.ttf: the emoji TuxTalk's windows
show, cut out of Google's Noto Color Emoji.

TuxTalk loads it in every process (ui_style.prepare_windows), so its emoji
show on a computer without an emoji font too. Without one they were boxes
with the character's code in them (issue #19). Only the emoji TuxTalk uses
go in, so the font stays around 50 KB.

Run it after adding an emoji to a TuxTalk screen; tests/test_emoji_font.py
fails until you do. It needs fontTools and the network:

    python3 packaging/build-emoji-font.py
"""
import ast
import hashlib
import pathlib
import sys
import tempfile
import unicodedata
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "tuxtalk"
FONT = SRC / "fonts" / "tuxtalk-emoji.ttf"
# What's in it, one emoji a line, for tests/test_emoji_font.py (which can't
# count on fontTools being installed to read the font itself).
CONTENTS = FONT.with_suffix(".txt")

# The colour bitmap (CBDT) Noto Color Emoji, at a fixed commit. Not the
# COLRv1 one: that draws in colour only with cairo 1.18 and later.
NOTO_COMMIT = "1ffdd21391dd1f25c081fa93a9dea0c7c029442b"
NOTO_URL = (f"https://raw.githubusercontent.com/googlefonts/noto-emoji/{NOTO_COMMIT}"
            "/2D/fonts/NotoColorEmoji.ttf")
NOTO_SHA256 = "15671215ab769fdc7162a045d56fd7d7e477c51b04e6b3c761d914d8fdd6cc44"

# Symbols in TuxTalk's strings that every text font draws (DejaVu Sans,
# Noto Sans), and the CJK punctuation TuxTalk types rather than shows. The
# emoji font isn't needed for these, and Noto Color Emoji doesn't have them.
TEXT_SYMBOLS = frozenset("–—‘’‚“”„′″•…€←→−⊘▁■▸▼●✓✗、。！，；？")

# Joining marks that go with emoji, kept in the font with them.
_JOINERS = "‍️"


def symbols_in_source():
    """Every symbol (not a letter, mark or space) in the string literals of
    TuxTalk's code: what its windows can show. Docstrings don't count."""
    found = set()
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                           ast.AsyncFunctionDef))
                      and ast.get_docstring(node, clean=False) is not None}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings):
                found.update(ch for ch in node.value
                             if ord(ch) >= 0x2000 and ch not in _JOINERS
                             and unicodedata.category(ch)[0] not in "LMZC")
    return found


def emoji_needed():
    """The symbols the emoji font has to carry."""
    return symbols_in_source() - TEXT_SYMBOLS


def main():
    from fontTools import subset
    from fontTools.ttLib import TTFont

    with tempfile.TemporaryDirectory() as tmp:
        full = pathlib.Path(tmp) / "NotoColorEmoji.ttf"
        print(f"Downloading Noto Color Emoji ({NOTO_COMMIT[:12]})")
        urllib.request.urlretrieve(NOTO_URL, full)
        if hashlib.sha256(full.read_bytes()).hexdigest() != NOTO_SHA256:
            sys.exit("The downloaded font doesn't match NOTO_SHA256: not using it.")
        wanted = emoji_needed()
        missing = sorted(ch for ch in wanted if ord(ch) not in TTFont(full).getBestCmap())
        if missing:
            sys.exit("Noto Color Emoji has no " + ", ".join(f"{ch} (U+{ord(ch):04X})" for ch in missing)
                     + ". If every text font draws it, add it to TEXT_SYMBOLS.")
        options = subset.Options()
        options.layout_features = ["*"]
        options.hinting = False
        font = TTFont(full)
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=[ord(ch) for ch in wanted | set(_JOINERS)])
        subsetter.subset(font)
        FONT.parent.mkdir(exist_ok=True)
        font.save(FONT)
    CONTENTS.write_text("".join(f"{ch}\n" for ch in sorted(wanted)), encoding="utf-8")
    print(f"Wrote {FONT.relative_to(ROOT)}: {len(wanted)} emoji, {FONT.stat().st_size // 1024} KB")
    print("".join(sorted(wanted)))


if __name__ == "__main__":
    main()
