"""Refuse a commit that changes the app without saying what it did to the
texts users read: Help, the README, the CHANGELOG, the website, the Voice
Commands list. Releases 0.23 and 0.25 shipped with those out of date
because they were left for a check before the build; this makes them part
of each change.

A commit that touches src/tuxtalk or the GNOME extension needs a line in
its message:

    Docs: CHANGELOG.md, help_text.py, README.md

naming the user-facing files it updated, each of them in the commit and
CHANGELOG.md always among them; or, for a change no user can notice (a
refactor, a test, a comment):

    Docs: none, <why: at least three words>

Merges are let through: what they bring in was checked when it was made.
Run by .githooks/commit-msg with the message file's path.
"""
import fnmatch
import re
import subprocess
import sys

# Paths that are the app itself: a change here can change what users see.
CODE = ("src/tuxtalk/*", "gnome-extension/*")
# What users read, wherever it lives. Changing only these needs no Docs line.
DOCS = ("CHANGELOG.md", "README.md", "docs/*.html", "io.github.ronb1964.TuxTalk.appdata.xml",
        "src/tuxtalk/help/*.txt", "src/tuxtalk/help_text.py", "src/tuxtalk/help_pages.py",
        "src/tuxtalk/commands_reference.py")
_DOCS_LINE = re.compile(r"^docs:\s*(.*)$", re.IGNORECASE | re.MULTILINE)


def _matches(path, patterns):
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def problem(message: str, staged: list) -> str:
    """Why a commit with *message* and the *staged* paths may not go in, or
    "" when it may."""
    code = [p for p in staged if _matches(p, CODE) and not _matches(p, DOCS)]
    if not code:
        return ""
    text = "\n".join(line for line in message.splitlines() if not line.startswith("#"))
    found = _DOCS_LINE.search(text)
    if found is None:
        return ("This commit changes the app (" + ", ".join(code[:3])
                + (", ..." if len(code) > 3 else "") + ") but its message has no Docs: line.\n"
                "Update what users read (Help, README, CHANGELOG, website, the Voice\n"
                "Commands list) in this same commit and name them: Docs: CHANGELOG.md, ...\n"
                "If no user can notice the change: Docs: none, <why>")
    said = found.group(1).strip()
    if said.lower().startswith("none"):
        why = said[4:].strip(" ,:;-")
        return "" if len(why.split()) >= 3 else (
            "Docs: none needs a reason of at least three words, e.g.\n"
            "Docs: none, a refactor with the same behaviour")
    named = [name for name in re.split(r"[,\s]+", said) if name]
    missing = [name for name in named
               if not any(p == name or p.endswith("/" + name) for p in staged)]
    if missing:
        return ("Docs: names " + ", ".join(missing) + ", which this commit doesn't change.\n"
                "Stage the updated file, or name only what the commit holds.")
    if "CHANGELOG.md" not in staged:
        return ("A change users can notice goes in CHANGELOG.md's [Unreleased] section:\n"
                "update it in this commit and name it in the Docs: line.")
    return ""


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          check=True).stdout


def main(message_path):
    if subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                      capture_output=True).returncode == 0:
        return 0
    staged = _git("diff", "--cached", "--name-only").split()
    with open(message_path, encoding="utf-8") as f:
        why_not = problem(f.read(), staged)
    if why_not:
        print("Commit refused (.githooks/docs_in_commit.py):\n" + why_not, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
