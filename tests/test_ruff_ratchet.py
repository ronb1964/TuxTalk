"""Ruff's bugbear and simplify findings in src/tuxtalk may only go away.

pyproject.toml turns the two rule families on and lists, file by file, the
findings that were already there in its per-file-ignores. CI's ruff step holds
new code to the rules; these tests make the list a ratchet: an entry whose
finding is gone has to be struck, so the rule holds for that file from then on.

Rules change between ruff versions, so the tests run only with the version
pyproject.toml pins for CI (poetry install --with dev installs it).
"""
import json
import pathlib
import re
import subprocess
import sys

import pytest
import toml

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = toml.loads((ROOT / "pyproject.toml").read_text())
LISTED = {(path, code)
          for path, codes in PYPROJECT["tool"]["ruff"]["lint"]["per-file-ignores"].items()
          for code in codes}


def _ruff(*args):
    return subprocess.run([sys.executable, "-m", "ruff", *args], cwd=ROOT,
                          capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def findings():
    """(file, rule) pairs ruff reports in src/tuxtalk with no file let off."""
    pytest.importorskip("ruff")
    pinned = PYPROJECT["tool"]["poetry"]["group"]["dev"]["dependencies"]["ruff"]
    installed = re.search(r"[\d.]+", _ruff("--version").stdout).group()
    if installed != pinned:
        pytest.skip(f"ruff {installed} is installed; the ratchet is measured with {pinned}")
    result = _ruff("check", "src/tuxtalk", "--no-cache", "--exit-zero", "--output-format",
                   "json", "--config", "lint.per-file-ignores = {}")
    assert result.returncode == 0, result.stderr
    return {(pathlib.Path(f["filename"]).relative_to(ROOT).as_posix(), f["code"])
            for f in json.loads(result.stdout)}


def test_every_listed_finding_is_still_there(findings):
    gone = sorted(LISTED - findings)
    assert not gone, (
        "fixed, so strike them from [tool.ruff.lint.per-file-ignores] in pyproject.toml:\n  "
        + "\n  ".join(f"{path}: {code}" for path, code in gone))


def test_no_finding_outside_the_list(findings):
    new = sorted(findings - LISTED)
    assert not new, (
        "new bugbear or simplify findings; fix them (`ruff check src/tuxtalk` says where):\n  "
        + "\n  ".join(f"{path}: {code}" for path, code in new))
