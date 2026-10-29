"""Every typelib the app imports UNGUARDED must be verified by the build.

The AppImage copies GObject-Introspection typelibs with two `cp ... || true`
calls, because only one of the two source directories exists on a given base
image. That is fine — but the build must then assert the *outcome*, or a
missing typelib sails through and the app dies on launch with a GI error the
user cannot act on.

The distinction that matters is guarded vs unguarded:

    gi.require_version('GdkPixbuf', '2.0')   # module level → MUST be bundled
    from gi.repository import GdkPixbuf      # splash_screen.py, first run

    try:                                      # optional → must NOT be required
        gi.require_version('Notify', '0.7')   # notify.py, sets _ready
        from gi.repository import Notify
    except Exception:
        ...

A typelib imported only inside a try/except is optional: the code degrades
without it, and listing it in REQUIRED_TYPELIBS would hard-fail every build
for a feature meant to be optional.

Guardedness is detected from the AST rather than a hand-maintained exempt list,
so a new optional dependency is classified correctly without editing this file.
"""

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "container-build.sh"
SRC = ROOT / "src/tuxtalk"


def _require_version_calls(tree):
    """Yield (namespace, version, guarded) for each gi.require_version() call."""
    guarded_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for child in ast.walk(node):
                guarded_nodes.add(id(child))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "require_version"):
            continue
        if len(node.args) < 2:
            continue
        ns, ver = node.args[0], node.args[1]
        if not (isinstance(ns, ast.Constant) and isinstance(ver, ast.Constant)):
            continue
        yield str(ns.value), str(ver.value), id(node) in guarded_nodes


def _classify_source():
    """Return (unguarded, guarded) sets of typelib filenames used by the app."""
    unguarded, guarded = set(), set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for ns, ver, is_guarded in _require_version_calls(tree):
            (guarded if is_guarded else unguarded).add(f"{ns}-{ver}.typelib")
    # A namespace imported unguarded anywhere is a hard requirement, even if
    # some other module also imports it defensively.
    return unguarded, guarded - unguarded


def _verified_by_build():
    text = BUILD.read_text()
    parts = text.split("REQUIRED_TYPELIBS=(", 1)
    assert len(parts) > 1, "container-build.sh no longer defines REQUIRED_TYPELIBS"
    return set(re.findall(r'"([^"]+\.typelib)"', parts[1].split(")", 1)[0]))


def test_build_verifies_every_unguarded_typelib():
    unguarded, _ = _classify_source()
    missing = unguarded - _verified_by_build()
    assert not missing, (
        f"src/tuxtalk imports {sorted(missing)} without a try/except, but "
        f"container-build.sh does not verify they were bundled. The AppImage "
        f"would build successfully and then fail to start. Add them to "
        f"REQUIRED_TYPELIBS in container-build.sh."
    )


def test_build_does_not_require_optional_typelibs():
    """Requiring an optional typelib hard-fails builds for a feature meant to be absent."""
    _, guarded = _classify_source()
    wrongly_required = guarded & _verified_by_build()
    assert not wrongly_required, (
        f"container-build.sh requires {sorted(wrongly_required)}, but the source "
        f"imports them behind try/except — they are optional and are not bundled "
        f"in the AppImage. Requiring them fails every build."
    )


def test_build_does_not_require_typelibs_nothing_uses():
    """A stale entry hard-fails the build for no reason."""
    unguarded, _ = _classify_source()
    stale = _verified_by_build() - unguarded
    assert not stale, (
        f"container-build.sh requires {sorted(stale)}, but no source file "
        f"imports them unguarded. Either the dependency was dropped or the "
        f"list is out of date."
    )
