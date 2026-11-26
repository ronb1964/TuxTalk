"""Help's words live in src/tuxtalk/help/*.txt, one file a page, so Help can
grow without help_text.py growing. These keep the files and the code that
shows them in step: no page lost or left over, and no placeholder in a page
that Help can't fill in."""
import string
from pathlib import Path

import pytest

from tuxtalk import help_outline, help_text

FOLDER = Path(help_text.__file__).parent / "help"


def _named():
    """Every page the code shows: the tabs, and the Commands tab's examples."""
    return {name for _title, name in help_text._TABS} | {"commands_examples"}


def test_every_tab_has_its_page_and_every_page_is_shown():
    files = {path.stem for path in FOLDER.glob("*.txt")}
    assert files == _named(), (
        f"pages without a file: {sorted(_named() - files)}; "
        f"files no tab shows: {sorted(files - _named())}")


@pytest.mark.parametrize("flatpak", [False, True])
@pytest.mark.parametrize("name", sorted(_named()))
def test_a_page_uses_only_placeholders_help_fills_in(name, flatpak, monkeypatch):
    """A {name} Help doesn't know, or a lone { or } in a page (write {{ and }}
    for a brace), would leave the whole tab unable to open."""
    if flatpak:
        monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    else:
        monkeypatch.delenv("FLATPAK_ID", raising=False)
    known = set(help_text._values())
    used = {field for _text, field, _spec, _conv in string.Formatter().parse(help_text.page(name))
            if field is not None}
    assert used <= known, f"help/{name}.txt uses {sorted(used - known)}"


@pytest.mark.parametrize("name", sorted(_named() - {"commands_examples"}))
def test_each_page_starts_with_its_title(name):
    """help_outline makes the page's big title from its first "# " line."""
    assert help_text.page(name).startswith("# ")


def test_a_page_read_from_its_file_keeps_no_extra_line_at_the_end():
    """Editors end a file with a line break; the page itself doesn't."""
    assert not help_text.page("tips").endswith("\n")


@pytest.mark.parametrize("name", sorted(_named()))
def test_an_examples_line_ends_its_paragraph(name):
    """A line of text right under {examples_...} would join the example's
    last bullet on screen (help_outline flows a line on into the one before),
    and read as part of what's typed."""
    lines = help_text.page(name).split("\n")
    for number, line in enumerate(lines[:-1]):
        after = lines[number + 1]
        if line.startswith("{examples_"):
            assert (not after.strip() or after.startswith(("#", "{examples_"))
                    or help_outline._ITEM.match(after)), f"help/{name}.txt line {number + 2}"


def test_the_commands_examples_page_is_filled_in_like_every_page(monkeypatch):
    """The test above checks it against every placeholder, so Help must fill
    them all in: a {hold} there would otherwise keep Help from opening."""
    monkeypatch.setattr(help_text, "page", lambda _name: "Hold <b>{hold}</b>:\n{examples_commands}")
    assert help_text.commands_examples().startswith("Hold <b>")
