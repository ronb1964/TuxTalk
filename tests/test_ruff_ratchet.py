"""Ruff's bugbear and simplify findings in src/tuxtalk may only go down.

pyproject.toml turns the two rule families on, and its per-file-ignores let
off the rules each file broke when they came in, so `ruff check` passes. A
per-file ignore lets off every finding of that rule in the file, a new one
too, so the number each file had is kept in ruff_baseline.json and these
tests count again with nothing let off: a file with more findings of a rule
than its baseline fails, and so does one with fewer until the baseline is
lowered (and the per-file ignore struck when it reaches none). The baseline
is edited by hand, only ever downwards, never raised without the maintainer's OK.

Rules change between ruff versions, so the count is made with the version
poetry.lock pins, which CI installs. Elsewhere another version skips; in CI
it fails, so the ratchet can't quietly stop running there.
"""
import collections
import json
import os
import pathlib
import subprocess
import sys

import pytest
import toml

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = json.loads(pathlib.Path(__file__).with_name("ruff_baseline.json").read_text())
IGNORES = toml.loads((ROOT / "pyproject.toml").read_text())["tool"]["ruff"]["lint"][
    "per-file-ignores"]


def _pinned_ruff():
    lock = toml.loads((ROOT / "poetry.lock").read_text())
    return next(p["version"] for p in lock["package"] if p["name"] == "ruff")


def _ruff(*args):
    return subprocess.run([sys.executable, "-m", "ruff", *args], cwd=ROOT,
                          capture_output=True, text=True, check=False)


def _not_measurable(why):
    if os.environ.get("CI"):
        pytest.fail(f"{why}; CI must count with the pinned ruff")
    pytest.skip(why)


@pytest.fixture(scope="module")
def findings():
    """How many times each file breaks each rule, with no file let off."""
    try:
        import ruff  # noqa: F401  (only to know it's installed)
    except ImportError:
        _not_measurable("ruff isn't installed")
    installed = _ruff("--version").stdout.split()[-1]
    if installed != _pinned_ruff():
        _not_measurable(f"ruff {installed} is installed; poetry.lock pins {_pinned_ruff()}")
    result = _ruff("check", "src/tuxtalk", "--no-cache", "--exit-zero", "--output-format",
                   "json", "--config", "lint.per-file-ignores = {}")
    assert result.returncode == 0, result.stderr
    return collections.Counter(
        (pathlib.Path(f["filename"]).relative_to(ROOT).as_posix(), f["code"])
        for f in json.loads(result.stdout))


def test_no_file_breaks_a_rule_more_often_than_its_baseline(findings):
    over = sorted(f"{path}: {code} {count} (baseline {BASELINE.get(path, {}).get(code, 0)})"
                  for (path, code), count in findings.items()
                  if count > BASELINE.get(path, {}).get(code, 0))
    assert not over, ("new bugbear or simplify findings; fix them "
                      "(ruff check src/tuxtalk --config 'lint.per-file-ignores = {}'):\n  "
                      + "\n  ".join(over))


def test_a_lower_count_lowers_the_baseline(findings):
    under = sorted(f"{path}: {code} {findings[path, code]} (baseline {count})"
                   for path, codes in BASELINE.items() for code, count in codes.items()
                   if findings[path, code] < count)
    assert not under, ("fewer findings: lower these in tests/ruff_baseline.json, and strike "
                       "the ones at 0 from it and from pyproject's per-file-ignores:\n  "
                       + "\n  ".join(under))


def test_the_per_file_ignores_let_off_exactly_the_rules_in_the_baseline():
    ignored = {path: sorted(codes) for path, codes in IGNORES.items()}
    assert ignored == {path: sorted(codes) for path, codes in BASELINE.items()}
