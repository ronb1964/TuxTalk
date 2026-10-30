"""A program's output read as text can't stop TuxTalk over one odd byte.

subprocess decodes text output strictly, so a device, layout or file name
in another encoding raised UnicodeDecodeError out of checks that ran at
every recording start or before every typed chunk. Output that is only
parsed loosely, logged or shown is read with errors="replace"; output that
is written back later (GNOME's settings) is read strictly, inside a try
that catches the decoding error (ValueError) and doesn't raise it again.

What the check sees, module by module: calls of subprocess's run,
check_output, check_call and Popen, under any name an import or a
`run=subprocess.run` default gives them; text mode from text=,
universal_newlines= (anything but a literal False or None) or encoding=;
and .decode() of a call's .stdout or .stderr or of check_output() itself.
Such a call passes when it has errors= set to anything but "strict" or
None, or sits in a try whose handler catches ValueError (or wider) and
has no raise. A call with **options and no errors= is reported, since
the check can't see inside the dictionary.

What it can't see: output decoded later from a variable (stdout=PIPE, then
out.decode() elsewhere), a subprocess function passed around under a name
it wasn't defaulted or assigned to, an errors= value computed at run time
(taken as given), and a strict read guarded by its caller's try.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "tuxtalk"
_CALLS = {"run", "check_output", "check_call", "Popen"}
_CATCHES_DECODING = {"ValueError", "UnicodeError", "UnicodeDecodeError", "Exception",
                     "BaseException"}


def _subprocess_names(tree):
    """(names of the subprocess module, names of its functions) in *tree*."""
    modules, functions = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "subprocess"}
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            functions |= {a.asname or a.name for a in node.names if a.name in _CALLS}

    def is_function(value):
        return (isinstance(value, ast.Attribute) and value.attr in _CALLS
                and isinstance(value.value, ast.Name) and value.value.id in modules)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            pairs = [*zip(a.args[len(a.args) - len(a.defaults):], a.defaults, strict=True),
                     *zip(a.kwonlyargs, a.kw_defaults, strict=True)]
            functions |= {arg.arg for arg, d in pairs if d is not None and is_function(d)}
        elif isinstance(node, ast.Assign) and is_function(node.value):
            functions |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return modules, functions


def _is_subprocess_call(node, modules, functions):
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr in _CALLS and isinstance(f.value, ast.Name) and f.value.id in modules
    return isinstance(f, ast.Name) and f.id in functions


def _off(value):
    return isinstance(value, ast.Constant) and value.value in (False, None)


def _strict(node, modules, functions):
    """Whether *node* is a call that decodes a program's output strictly."""
    kw = {k.arg: k.value for k in node.keywords}
    if _is_subprocess_call(node, modules, functions):
        if "errors" in kw:
            return _off(kw["errors"]) or (isinstance(kw["errors"], ast.Constant)
                                          and kw["errors"].value == "strict")
        text = any(k in kw and not _off(kw[k]) for k in ("text", "universal_newlines"))
        return text or "encoding" in kw or None in kw      # None: **options
    f = node.func
    if not (isinstance(f, ast.Attribute) and f.attr == "decode"):
        return False
    if len(node.args) > 1 or "errors" in kw:
        return False
    source = f.value
    if isinstance(source, ast.Attribute) and source.attr in ("stdout", "stderr"):
        return True
    return isinstance(source, ast.Call) and _is_subprocess_call(source, modules, functions)


def _guards(handler):
    """Whether *handler* catches a decoding error and lets it go."""
    caught = handler.type
    items = [] if caught is None else (caught.elts if isinstance(caught, ast.Tuple) else [caught])
    names = {getattr(c, "attr", None) or getattr(c, "id", "") for c in items}
    catches = caught is None or bool(names & _CATCHES_DECODING)
    return catches and not any(isinstance(n, ast.Raise) for b in handler.body
                               for n in ast.walk(b))


def strict_unguarded(source):
    """Line numbers in *source* of strict decodes of a program's output that
    no enclosing try in the same function catches."""
    tree = ast.parse(source)
    modules, functions = _subprocess_names(tree)
    found = []

    def visit(node, guarded):
        if isinstance(node, ast.Try):
            safe = guarded or any(_guards(h) for h in node.handlers)
            for child in node.body:
                visit(child, safe)
            for child in node.handlers + node.orelse + node.finalbody:
                visit(child, guarded)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            guarded = False         # a function's body runs where it's called
        if isinstance(node, ast.Call) and not guarded and _strict(node, modules, functions):
            found.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(tree, False)
    return sorted(found)


def test_text_read_from_a_program_says_what_an_odd_byte_does():
    unguarded = [f"{path.relative_to(SRC)}:{line}" for path in sorted(SRC.rglob("*.py"))
                 for line in strict_unguarded(path.read_text())]
    assert unguarded == [], ('pass errors="replace", or catch ValueError around a strict read: '
                             + ", ".join(unguarded))


def test_the_check_finds_strict_reads_however_they_are_written():
    source = '''\
import subprocess
import subprocess as sp
from subprocess import check_output as co
import other

subprocess.run(['a'], text=True)
sp.run(['b'], universal_newlines=True)
co(['c'], encoding='utf-8')
subprocess.run(['d'], text=True, errors='strict')
subprocess.run(['e'], text=True, errors=None)
subprocess.run(['f'], text=mode)
subprocess.run(['g'], **options)
subprocess.run(['h'], capture_output=True).stdout.decode()
co(['i']).decode('utf-8')


def ask(run=subprocess.run):
    return run(['j'], text=True)
'''
    assert strict_unguarded(source) == [6, 7, 8, 9, 10, 11, 12, 13, 14, 18]


def test_the_check_lets_loose_reads_guarded_reads_and_other_runs_pass():
    source = '''\
import subprocess
import other

subprocess.run(['a'], text=True, errors='replace')
subprocess.run(['b'], text=False)
subprocess.run(['c'], capture_output=True).stdout.decode('utf-8', 'replace')
other.run(['d'], text=True)
asyncio.run(main())
try:
    subprocess.check_output(['e'], text=True)
except (OSError, ValueError):
    pass
try:
    subprocess.check_output(['f'], text=True)
except UnicodeDecodeError:
    raise
'''
    assert strict_unguarded(source) == [14]
