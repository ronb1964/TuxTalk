"""The commit check (.githooks/docs_in_commit.py): a commit that changes the
app must say what it did to the texts users read. 0.23 and 0.25 shipped
with Help, the README or the Voice Commands list out of date because those
were left for a check before the build."""
import importlib.util
import os
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / ".githooks"
_spec = importlib.util.spec_from_file_location("docs_in_commit", HOOKS / "docs_in_commit.py")
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

APP = "src/tuxtalk/pipeline.py"


def test_a_change_to_the_app_without_a_docs_line_is_refused():
    assert "no Docs: line" in check.problem("Fix the tone word\n", [APP])


@pytest.mark.parametrize("staged", [
    ["gnome-extension/talktype@ronb1964.github.io/extension.js"],
    ["src/tuxtalk/fonts/tuxtalk-emoji.txt", APP],
])
def test_the_gnome_extension_and_every_app_file_count(staged):
    assert check.problem("Change\n", staged)


def test_naming_the_updated_texts_lets_it_in():
    staged = [APP, "CHANGELOG.md", "src/tuxtalk/help/writing.txt", "README.md"]
    assert check.problem("Tone word\n\nDocs: CHANGELOG.md, writing.txt, README.md\n",
                         staged) == ""


def test_a_text_named_but_not_in_the_commit_is_refused():
    """Saying "help_text.py" without changing it is the promise this replaces."""
    said = check.problem("Tone word\n\nDocs: CHANGELOG.md, help_text.py\n",
                         [APP, "CHANGELOG.md"])
    assert "help_text.py" in said and "doesn't change" in said


def test_a_change_users_notice_needs_the_changelog():
    said = check.problem("Tone word\n\nDocs: help_text.py\n",
                         [APP, "src/tuxtalk/help_text.py"])
    assert "CHANGELOG.md" in said


@pytest.mark.parametrize("line, ok", [
    ("Docs: none, a refactor with the same behaviour", True),
    ("docs: None - only comments changed here", True),
    ("Docs: none", False),
    ("Docs: none, refactor", False),
])
def test_none_needs_a_reason(line, ok):
    assert (check.problem(f"Split a function\n\n{line}\n", [APP]) == "") is ok


def test_a_commit_of_only_texts_tests_or_scripts_needs_no_line():
    for staged in (["README.md", "docs/index.html", "src/tuxtalk/help/writing.txt"],
                   ["tests/test_tone_word.py"], ["build-release.sh", "CLAUDE.md"]):
        assert check.problem("Wording\n", staged) == ""


def test_git_s_own_comment_lines_dont_count():
    message = "Tone word\n# Docs: CHANGELOG.md\n"
    assert "no Docs: line" in check.problem(message, [APP, "CHANGELOG.md"])


def test_the_hook_runs_the_check():
    hook = HOOKS / "commit-msg"
    assert os.access(hook, os.X_OK)
    assert "docs_in_commit.py" in hook.read_text()
