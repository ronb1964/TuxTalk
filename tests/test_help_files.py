"""Help's words live in src/tuxtalk/help/*.txt, one file a page, so Help can
grow without help_text.py growing. These keep the files and the code that
shows them in step: no page lost or left over, and no placeholder in a page
that Help can't fill in."""
import string
from pathlib import Path

import pytest

from tuxtalk import help_text

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
