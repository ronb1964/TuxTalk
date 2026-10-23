"""Everything a user reads says TuxTalk, not TalkType (0.15.0 rename).

TalkType is another company's registered trademark for dictation software.
Old names survive only where they are invisible or needed to find an old
install: the D-Bus names (kept so an old GNOME extension keeps working until
the next logout), file patterns that find old AppImages, the rename module,
and the deliberate "Formerly TalkType" in the About window.
"""
import ast
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "tuxtalk"
EXT = (pathlib.Path(__file__).resolve().parent.parent / "gnome-extension"
       / "talktype@ronb1964.github.io" / "extension.js")

# Substrings allowed anywhere in a string.
ALLOWED = (
    "Formerly TalkType",
    "TalkType-*.AppImage",              # glob patterns that find an old download
)
# Strings allowed only when they are the WHOLE string: the kept D-Bus names.
# (A message like "flatpak update io.github.ronb1964.TalkType" is not one.)
ALLOWED_WHOLE = {"io.github.ronb1964.TalkType", "/io/github/ronb1964/TalkType"}


# Whole strings that are the old name on purpose, per file.
ALLOWED_EXACT = {
    "settings_backup.py": {"TalkType"},     # backups saved before the rename still load
    # Files an install from before the rename left, which the uninstall removes.
    "uninstall.py": {"TalkType", "TalkType.AppImage"},
    # The ydotoold unit a TalkType wrote, which 0.18 removes.
    "uinput_helper.py": {"(TalkType keystroke injection)"},
    # The one-time notice after an old TalkType install is converted.
    "tray.py": {
        "TalkType is now TuxTalk",
        "Why the change: another company owns the name TalkType for dictation software.",
        # An old TalkType tray that would not quit: its menu still says TalkType.
        "An older TalkType is still running",
        "TuxTalk is the new name for TalkType, and only one of them can run at a "
        "time. Click the TalkType microphone icon in the system tray, choose Quit "
        "TalkType, then start TuxTalk again.",
        # An old TalkType Flatpak was stopped: its uninstall needs its real ID.
        "Stopped the old TalkType Flatpak",
        "It was running next to TuxTalk, its new name, so everything you said "
        "would be typed twice. You can uninstall it from your software center, or "
        "with: flatpak uninstall io.github.ronb1964.TalkType",
    },
}


def _strings(tree):
    """Every string constant except docstrings (those are for developers)."""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(
                    getattr(node.body[0], "value", None), ast.Constant):
                docs.add(id(node.body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def test_python_ui_text_says_tuxtalk():
    bad = []
    for f in sorted(SRC.rglob("*.py")):
        if f.name == "rename_migration.py":
            continue
        for line, s in _strings(ast.parse(f.read_text())):
            if s in ALLOWED_EXACT.get(f.name, ()) or s in ALLOWED_WHOLE:
                continue
            rest = s
            for ok in ALLOWED:
                rest = rest.replace(ok, "")
            # The old AUR package name, in a command users are told to run.
            if "TalkType" in rest or "talktype-appimage" in rest:
                bad.append(f"{f.name}:{line}: {s[:80]!r}")
    assert not bad, "\n".join(bad)


def test_extension_ui_text_says_tuxtalk():
    text = EXT.read_text()
    bad = []
    for m in re.finditer(r"(['\"`])((?:\\.|(?!\1).)*?)\1", text):
        s = m.group(2)
        if s in ALLOWED_WHOLE:
            continue
        rest = s
        for ok in ALLOWED:
            rest = rest.replace(ok, "")
        if "TalkType" in rest:
            bad.append(f"extension.js:{text[:m.start()].count(chr(10)) + 1}: {s[:80]!r}")
    assert not bad, "\n".join(bad)


def test_about_says_formerly_talktype(about_window):
    from gtk_text import shown_text
    assert "Formerly TalkType" in shown_text(about_window)


def test_issue_templates_say_tuxtalk_and_point_to_the_current_log():
    templates = SRC.parent.parent / ".github" / "ISSUE_TEMPLATE"
    bug = (templates / "bug_report.yml").read_text()
    assert "~/.config/tuxtalk/tuxtalk.log" in bug
    for f in templates.glob("*.yml"):
        text = f.read_text()
        # The repo URL waits for the GitHub rename, and 0.14.x users still
        # need their old log path.
        text = text.replace("github.com/ronb1964/TalkType", "")
        text = text.replace("~/.config/talktype/talktype.log", "")
        assert "TalkType" not in text and "talktype" not in text, f.name
