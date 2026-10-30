"""Runs the GNOME extension's code under Node, with stand-ins for GNOME Shell
(tests/gnome_shell), so its tests check what it does rather than how its
source reads: a guard broken in effect but kept in form fails here.

A test hands run() JavaScript that drives the extension through gnome.mjs's
functions (imported as `t`) and ends with t.report(value); run() returns that
value and what the extension logged."""
import json
import pathlib
import re
import shutil
import subprocess
import textwrap

import pytest

STAND_INS = pathlib.Path(__file__).resolve().parent / "gnome_shell"


def _node():
    """Node's path; the test is skipped without a Node that has module hooks."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't installed: the GNOME extension's tests run its code under Node")
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    found = re.match(r"v(\d+)\.(\d+)", version)
    if not found or (int(found[1]), int(found[2])) < (20, 6):
        pytest.skip(f"Node {version} has no module hooks; the extension's tests need 20.6 or newer")
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
