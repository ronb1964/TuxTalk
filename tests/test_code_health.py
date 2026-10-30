"""TuxTalk's code stays small, simple, documented and honest about its errors.

The 2026-10-12 audit noted three huge files and nothing came of it: the
reviews read only each change, and a change shows the lines it adds, never
how long the file has grown. By 0.18 app.py was 5,900 lines. These tests
measure all of src/tuxtalk instead, subfolders included (CLAUDE.md, "Small
files and functions" and "Errors handled where they can happen").

New code meets the limits. What was over them when the rule came in is
listed in code_health_baseline.json, finding by finding, and may only go
down. A function or finding that moves to another file keeps its entry, so a
file can be split by moving code. After an improvement, lower the baseline:

    CODE_HEALTH_UPDATE=1 PYTHONPATH=src:/usr/lib64/python3.14/site-packages:\\
        /usr/lib/python3.14/site-packages .venv/bin/python -m pytest tests/test_code_health.py

It never raises a number. Raising one by hand needs Ron's OK first.
"""
import ast
import io
import json
import os
import pathlib
import re
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "tuxtalk"
BASELINE = pathlib.Path(__file__).with_name("code_health_baseline.json")

FILE_LINES = 1500
FUNCTION_LINES = 100
FUNCTION_DECISIONS = 15     # decision points, close to McCabe complexity
LINE_LENGTH = 100           # pyproject.toml's ruff line-length
DOCSTRING_FROM = 15         # lines: a function or class this long says what it's for

_DECISIONS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler,
              ast.comprehension, ast.match_case)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_BROAD = {"Exception", "BaseException"}
# A level that is kept: logger.py writes INFO and up, so debug() records nothing.
_KEPT_LEVELS = {"info", "warning", "error", "exception", "critical"}
_LOGGERS = re.compile(r"(^|_)(log|logger|logging)$", re.I)
# Who asked and when is history, for the commit message. A date inside a link
# is a reference, not history, so links are left out.
_LINK = re.compile(r"\w+://\S+")
_DATED = re.compile(r"\b20\d\d-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b|\bRon\b")


def _inside(node):
    """Child nodes of *node*, not entering functions, classes or lambdas
    defined in it: their code runs elsewhere."""
    todo = list(ast.iter_child_nodes(node))
    while todo:
        child = todo.pop()
        yield child
        if not isinstance(child, _SCOPES):
            todo.extend(ast.iter_child_nodes(child))


def _decisions(func):
    count = 1
    for node in _inside(func):
        if isinstance(node, _DECISIONS):
            count += 1 + (len(node.ifs) if isinstance(node, ast.comprehension) else 0)
        elif isinstance(node, ast.BoolOp):
            count += len(node.values) - 1
    return count


def _scopes(tree):
    """[(qualified name, node)] for every function and class, nested ones
    included; a name used twice in one scope gets #2, #3..."""
    found, seen = [], set()

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name, n = f"{prefix}{child.name}", 2
                while name in seen:
                    name, n = f"{prefix}{child.name}#{n}", n + 1
                seen.add(name)
                found.append((name, child))
                visit(child, f"{name}.")
            else:
                visit(child, prefix)
    visit(tree, "")
    return found


def _is_broad(caught):
    if caught is None:
        return True
    if isinstance(caught, ast.Tuple):
        return any(_is_broad(c) for c in caught.elts)
    name = caught.attr if isinstance(caught, ast.Attribute) else getattr(caught, "id", "")
    return name in _BROAD


def _logs_or_raises(handler):
    for node in _inside(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _KEPT_LEVELS):
            owner = node.func.value
            owner = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", "")
            if _LOGGERS.search(owner):
                return True
    return False


def measure_file(text):
    """(lines, {function: {"lines", "decisions"}}, {symbol::rule: count}) for
    one file's source. A finding's symbol is the function or class it is in,
    or <module>."""
    tree = ast.parse(text)
    scopes = _scopes(tree)
    spans = sorted(((n.lineno, n.end_lineno, q) for q, n in scopes), key=lambda s: s[1] - s[0])

    def symbol(line):
        return next((q for start, end, q in spans if start <= line <= end), "<module>")

    findings = {}

    def add(line, rule, sym=None):
        key = f"{sym or symbol(line)}::{rule}"
        findings[key] = findings.get(key, 0) + 1

    for number, line in enumerate(text.splitlines(), 1):
        if len(line) > LINE_LENGTH:
            add(number, "long_line")
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _is_broad(node.type) \
                and not _logs_or_raises(node):
            add(node.lineno, "swallowed_error")
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT and _DATED.search(_LINK.sub("", tok.string)):
            add(tok.start[0], "dated_comment")
    if ast.get_docstring(tree) is None:
        add(1, "undocumented", "<module>")
    functions = {}
    for name, node in scopes:
        length = node.end_lineno - node.lineno + 1
        if length >= DOCSTRING_FROM and ast.get_docstring(node) is None:
            add(node.lineno, "undocumented", name)
        if not isinstance(node, ast.ClassDef):
            functions[name] = {"lines": length, "decisions": _decisions(node)}
    return len(text.splitlines()), functions, findings


def measure(src=SRC):
    """{"files": {path: lines}, "functions": {path::name: {...}},
    "findings": {path::symbol::rule: count}}, paths relative to *src*."""
    files, functions, findings = {}, {}, {}
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src).as_posix()
        lines, funcs, found = measure_file(path.read_text(encoding="utf-8"))
        files[rel] = lines
        functions.update({f"{rel}::{name}": m for name, m in funcs.items()})
        findings.update({f"{rel}::{key}": n for key, n in found.items()})
    return {"files": files, "functions": functions, "findings": findings}


FUNCTION_LIMITS = {"lines": FUNCTION_LINES, "decisions": FUNCTION_DECISIONS}


def _rest(key):
    """A key without its file: what stays the same when code moves. A file's
    own key keeps its name, so prefs.py moved into a folder is the same file."""
    path, _, rest = key.partition("::")
    return rest or path.rsplit("/", 1)[-1]


def _moves(baseline_keys, now_keys):
    """{new key: old key} for entries whose code moved to another file: the
    old key is gone, and exactly one new key has the same rest."""
    gone = [k for k in baseline_keys if k not in now_keys]
    new = [k for k in now_keys if k not in baseline_keys]
    moved = {}
    for old in gone:
        same = [k for k in new if _rest(k) == _rest(old)]
        if len(same) == 1:
            moved[same[0]] = old
    return moved


def _allowance(kind, key, baseline, now):
    entries = baseline[kind]
    if key in entries:
        return entries[key]
    old = _moves(entries, now[kind]).get(key)
    return entries.get(old)


def problems(now, baseline):
    """What is over its limit, worded for the person who has to fix it."""
    found = []
    for path, lines in now["files"].items():
        limit = _allowance("files", path, baseline, now) or FILE_LINES
        if lines > limit:
            found.append(f"{path} is {lines} lines, at most {limit} allowed: split it")
    for name, measured in now["functions"].items():
        allowed = {**FUNCTION_LIMITS, **(_allowance("functions", name, baseline, now) or {})}
        for what, value in measured.items():
            if value > allowed[what]:
                found.append(f"{name} has {value} {what}, at most {allowed[what]} allowed: "
                             "split the function")
    for key, count in now["findings"].items():
        allowed = _allowance("findings", key, baseline, now) or 0
        if count > allowed:
            found.append(f"{key}: {count}, at most {allowed} allowed")
    return found


def lowered(baseline, now):
    """The baseline with every number that went down lowered, entries that
    moved re-keyed to where the code is now, and numbers within the limit
    dropped. Never raises a number or adds an entry."""
    out = {"files": {}, "functions": {}, "findings": {}}
    for kind in out:
        moved = {old: new for new, old in _moves(baseline[kind], now[kind]).items()}
        for key, allowed in baseline[kind].items():
            where = key if key in now[kind] else moved.get(key)
            if where is None:
                continue                            # the code is gone
            measured = now[kind][where]
            if kind == "functions":
                kept = {m: min(v, measured[m]) for m, v in allowed.items()
                        if min(v, measured[m]) > FUNCTION_LIMITS[m]}
            else:
                kept = min(allowed, measured)
                if kept <= (FILE_LINES if kind == "files" else 0):
                    kept = None
            if kept:
                out[kind][where] = kept
    return out


def _baseline():
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_nothing_in_tuxtalk_is_bigger_or_messier_than_allowed():
    now, baseline = measure(), _baseline()
    if os.environ.get("CODE_HEALTH_UPDATE") == "1":
        BASELINE.write_text(json.dumps(lowered(baseline, now), indent=1, sort_keys=True) + "\n")
        baseline = _baseline()
    found = problems(now, baseline)
    assert not found, "\n".join(found) + (
        "\nCLAUDE.md, \"Small files and functions\": new code meets the limits, "
        "and old code's numbers may only go down.")


def test_the_baseline_keeps_up_with_every_improvement():
    """Once code gets better its number is lowered, so it can't quietly slip
    back: the ratchet only turns one way."""
    assert lowered(_baseline(), measure()) == _baseline(), (
        "Something improved or moved: lower the baseline with the command in "
        "this file's docstring and commit code_health_baseline.json")


def test_no_test_file_is_longer_than_a_source_file_may_be():
    """tests/ keeps to the file limit too. Its other measures (about 290
    long lines and 240 missing docstrings) aren't held to yet: CODE_HEALTH.md,
    task 1."""
    files = measure(ROOT / "tests")["files"]
    assert {path: n for path, n in files.items() if n > FILE_LINES} == {}


# --- the measuring itself ------------------------------------------------------------

def _findings(source):
    return measure_file('"""A module."""\n' + source)[2]


def test_an_error_caught_and_dropped_is_a_finding():
    assert _findings("try:\n    x()\nexcept Exception:\n    pass\n") == \
        {"<module>::swallowed_error": 1}


def test_an_error_logged_where_it_is_kept_is_fine():
    assert _findings("try:\n    x()\nexcept Exception as e:\n    logger.warning(e)\n") == {}


def test_a_debug_message_does_not_count_as_keeping_the_error():
    """logger.py keeps INFO and up: a debug line records nothing."""
    assert _findings("try:\n    x()\nexcept Exception:\n    logger.debug('x')\n") == \
        {"<module>::swallowed_error": 1}


def test_a_broad_except_hidden_in_a_tuple_is_still_broad():
    assert _findings("try:\n    x()\nexcept (ValueError, Exception):\n    pass\n") == \
        {"<module>::swallowed_error": 1}


def test_a_raise_in_a_function_defined_in_the_handler_does_not_count():
    source = "try:\n    x()\nexcept Exception:\n    def later():\n        raise\n"
    assert _findings(source) == {"<module>::swallowed_error": 1}


def test_a_narrow_except_is_left_to_the_reviewers():
    assert _findings("try:\n    x()\nexcept OSError:\n    pass\n") == {}


def test_a_date_in_a_link_is_not_history():
    assert _findings("# see https://example.org/2026-10-23/spec\n") == {}
    assert _findings("# Ron, 2026-10-18: the box was confusing\n") == \
        {"<module>::dated_comment": 1}


def test_a_long_function_without_a_docstring_is_a_finding_under_its_name():
    body = "".join(f"    x{i} = {i}\n" for i in range(DOCSTRING_FROM))
    assert _findings(f"def build():\n{body}") == {"build::undocumented": 1}


def test_a_subfolder_is_measured(tmp_path):
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "prefs.py").write_text('"""Prefs."""\n')
    assert measure(tmp_path)["files"] == {"ui/prefs.py": 1}


def test_code_moved_to_another_file_keeps_its_allowance():
    baseline = {"files": {"app.py": 5000}, "functions": {"app.py::deliver": {"lines": 300}},
                "findings": {"app.py::deliver::swallowed_error": 2}}
    now = {"files": {"app.py": 4000, "delivery.py": 900},
           "functions": {"delivery.py::deliver": {"lines": 300, "decisions": 9}},
           "findings": {"delivery.py::deliver::swallowed_error": 2}}
    assert problems(now, baseline) == []
    assert lowered(baseline, now) == {
        "files": {"app.py": 4000}, "functions": {"delivery.py::deliver": {"lines": 300}},
        "findings": {"delivery.py::deliver::swallowed_error": 2}}


def test_the_ratchet_never_raises_a_number():
    baseline = {"files": {"app.py": 5000}, "functions": {}, "findings": {}}
    now = {"files": {"app.py": 5200}, "functions": {}, "findings": {}}
    assert lowered(baseline, now) == baseline
    assert problems(now, baseline) == ["app.py is 5200 lines, at most 5000 allowed: split it"]
