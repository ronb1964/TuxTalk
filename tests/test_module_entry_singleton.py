"""`python -m tuxtalk.app` must run the whole service in ONE module object.

Running the dictation service as `python -m tuxtalk.app` executes app.py under
the name "__main__". But the input backends reach the app via
`from . import app` — module "tuxtalk.app" — which is a SEPARATE module object
with its own globals. main() assigns model, _custom_commands, dbus_service,
_key_delay_ms and friends as module globals; if main() runs in "__main__" while
the recording loop runs in "tuxtalk.app", every one of those globals is
invisible over there and transcription dies with `NameError: name 'model'`.

The loop is reached through EvdevInputBackend.start(), which does
`from . import app`, so it always runs in "tuxtalk.app". The `-m` entry must
therefore delegate to the package module's main(). Every install starts the
service that way (the tray launches `python3 -m tuxtalk.app`, the Flatpak
too), so this guard protects them all.
"""

import subprocess
import sys
import textwrap


def test_entry_helper_runs_the_package_modules_main(monkeypatch):
    """_run_service_entry() must invoke tuxtalk.app.main, so main() executes in
    the package module — the very object the input backends import."""
    from tuxtalk import app

    ran_in = {}
    monkeypatch.setattr(app, "main", lambda: ran_in.setdefault("module", app))

    app._run_service_entry()

    assert ran_in.get("module") is app, (
        "the module entry did not run the package module's main(); a "
        "`python -m tuxtalk.app` launch would split the app into two module "
        "objects and the recording loop would never see the `model` global."
    )


def test_dash_m_entry_delegates_to_package_module():
    """End to end: executing app.py as "__main__" (as `python -m` does) must
    call tuxtalk.app.main, not a second local main() defined in "__main__".

    The child pre-imports tuxtalk.app and replaces its main() with a probe that
    raises the moment it is called. The "__main__" guard delegates through
    `from tuxtalk.app import main`, so the probe fires and we confirm
    both that it ran and that it is the same module the backends import.
    """
    child = textwrap.dedent(
        """
        import runpy
        import sys

        import tuxtalk.app as pkg  # the canonical module the backends import

        class _Delegated(Exception):
            pass

        def _probe():
            # main() must be THIS package module's main (identity check),
            # never a duplicate defined in the "__main__" execution.
            raise _Delegated(sys.modules["tuxtalk.app"] is pkg)

        pkg.main = _probe

        try:
            runpy.run_module("tuxtalk.app", run_name="__main__", alter_sys=True)
        except _Delegated as exc:
            print("DELEGATED", bool(exc.args[0]))
        else:
            print("NOT_DELEGATED")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "DELEGATED True" in result.stdout, (
        "`python -m tuxtalk.app` did not delegate to tuxtalk.app.main "
        f"(stdout={result.stdout!r} stderr={result.stderr[-800:]!r})"
    )
