"""The AppImage runs Python 3.10; the dev setup runs 3.14.

tomllib only exists from 3.11, so a bare import crashes the AppImage at
startup while every test passes on the newer Pythons. Importing it is fine
inside a try with the toml package as the fallback, as config.py and prefs.py
do. The CI also runs on Ubuntu 22.04's Python 3.10.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "tuxtalk"


def test_no_module_imports_tomllib_without_a_fallback():
    # At the left margin = not inside a try with a 3.10 fallback (config.py
    # and prefs.py do it that way, indented under try:).
    offenders = [p.name for p in SRC.rglob("*.py")
                 if re.search(r"^(import tomllib|from tomllib)", p.read_text(), re.M)]
    assert not offenders, (
        f"{offenders} import tomllib, which Python 3.10 (the AppImage's) doesn't "
        f"have. Use config._parse_toml_text or config._load_toml_file.")


def test_no_test_file_imports_tomllib_without_a_fallback():
    """CI's Ubuntu 22.04 job collects the tests with Python 3.10 too: one
    bare import stops the whole run there. (tests/vm runs on the host.)"""
    offenders = [p.name for p in (ROOT / "tests").glob("*.py")
                 if re.search(r"^(import tomllib|from tomllib)", p.read_text(), re.M)]
    assert not offenders, (
        f"{offenders} import tomllib, which Python 3.10 doesn't have. Use "
        f"config._parse_toml_text, or import it in a try with toml as the fallback.")
