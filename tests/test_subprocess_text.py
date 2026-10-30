"""A program's output read as text can't stop TuxTalk over one odd byte.

subprocess decodes text=True output strictly, so a device, layout or file
name in another encoding raised UnicodeDecodeError out of checks that ran
at every recording start or before every typed chunk. Output that is only
parsed loosely, logged or shown is read with errors="replace"; output that
is written back later (GNOME's settings) is read strictly, inside a try
that catches the decoding error (ValueError) and keeps the old value.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "tuxtalk"
_CALLS = {"run", "check_output", "check_call", "Popen"}
_CATCHES_DECODING = {"ValueError", "UnicodeError", "UnicodeDecodeError", "Exception",
                     "BaseException"}


def _names(caught):
    if caught is None:
        return {"BaseException"}
    items = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    return {getattr(c, "attr", None) or getattr(c, "id", "") for c in items}


def _strict_text_calls(tree):
    """(line, inside a try that catches the decoding error) for each
    subprocess call that decodes text without errors=."""
    found = []

    def visit(node, guarded):
        if isinstance(node, ast.Try):
            catches = any(_names(h.type) & _CATCHES_DECODING for h in node.handlers)
            for child in node.body:
                visit(child, guarded or catches)
            for child in node.handlers + node.orelse + node.finalbody:
                visit(child, guarded)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            guarded = False         # a function's own body runs elsewhere
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", "")
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            text = any(isinstance(kw.get(k), ast.Constant) and kw[k].value is True
                       for k in ("text", "universal_newlines")) or "encoding" in kw
            if name in _CALLS and text and "errors" not in kw:
                found.append((node.lineno, guarded))
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(tree, False)
    return found


def test_text_read_from_a_program_says_what_an_odd_byte_does():
    unguarded = [f"{path.relative_to(SRC)}:{line}"
                 for path in sorted(SRC.rglob("*.py"))
                 for line, guarded in _strict_text_calls(ast.parse(path.read_text()))
                 if not guarded]
    assert unguarded == [], ('pass errors="replace", or catch ValueError around a strict read: '
                             + ", ".join(unguarded))


def test_the_check_sees_an_unguarded_call_and_a_guarded_one():
    source = ("import subprocess\n"
              "subprocess.run(['a'], text=True)\n"
              "subprocess.run(['b'], text=True, errors='replace')\n"
              "try:\n    subprocess.check_output(['c'], text=True)\n"
              "except (OSError, ValueError):\n    pass\n")
    assert _strict_text_calls(ast.parse(source)) == [(2, False), (5, True)]
