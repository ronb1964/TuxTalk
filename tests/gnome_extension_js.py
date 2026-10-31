"""Runs the GNOME extension's code under Node, with stand-ins for GNOME Shell
(tests/gnome_shell), so its tests check what it does rather than how its
source reads: a guard broken in effect but kept in form fails here. KDE's
focus script (kwin_focus.py) runs the same way, against tests/kwin.

A test hands run() JavaScript that drives the extension through gnome.mjs's
functions (imported as `t`) and ends with t.report(value); run() returns that
value and what the extension logged."""
import functools
import json
import os
import pathlib
import re
import shutil
import subprocess
import textwrap

import pytest

STAND_INS = pathlib.Path(__file__).resolve().parent / "gnome_shell"
KWIN_STAND_INS = pathlib.Path(__file__).resolve().parent / "kwin" / "workspace.js"


@functools.cache
def _node_or_why_not():
    """(Node's path, None), or (None, why these tests can't run)."""
    node = shutil.which("node")
    if node is None:
        return None, "Node isn't installed: the GNOME extension's tests run its code under Node"
    version = subprocess.run([node, "--version"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
    found = re.match(r"v(\d+)\.(\d+)", version)
    if not found or (int(found[1]), int(found[2])) < (20, 6):
        return None, f"Node {version} has no module hooks; the extension's tests need 20.6 or newer"
    return node, None


def _node():
    """Node's path. Without a Node that has module hooks the test is skipped
    here, and fails in CI, where a skip would hide that the extension went
    untested."""
    node, why_not = _node_or_why_not()
    if node is None:
        if os.environ.get("CI"):
            pytest.fail(why_not)
        pytest.skip(why_not)
    return node


def run(scenario):
    """Run *scenario* (JavaScript, `t` being gnome.mjs) against a freshly
    loaded extension; (the value it reported, the [level, text] it logged)."""
    script = (f"import * as t from {json.dumps((STAND_INS / 'gnome.mjs').as_uri())};\n"
              + textwrap.dedent(scenario))
    done = subprocess.run(
        [_node(), "--import", (STAND_INS / "loader.mjs").as_uri(),
         "--input-type=module", "--eval", script],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    reported = json.loads(done.stdout.strip().splitlines()[-1])
    return reported["value"], reported["logs"]


def run_kwin_script(script, scenario):
    """Run *scenario* (JavaScript using kwin/workspace.js, which calls load()
    to load the KWin script *script*); the value it gave report()."""
    source = "\n".join([KWIN_STAND_INS.read_text(),
                        f"const KWIN_SCRIPT = {json.dumps(script)};",
                        textwrap.dedent(scenario)])
    done = subprocess.run([_node(), "--eval", source], capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])
