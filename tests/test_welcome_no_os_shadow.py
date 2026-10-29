"""First-run setup must use the module-level `os` and `GLib`.

A local `import os` anywhere inside show_welcome_and_install makes `os`
function-local for the WHOLE function, so the earlier
`os.environ.get("FLATPAK_ID")` (the hotkey-test skip) throws UnboundLocalError,
which silently aborts onboarding right after "Let's Go!" and leaves the tray
icon slashed with nothing else happening.

If `os` ever becomes a local in that function again, this fails.
"""
from tuxtalk import first_run_setup


def test_show_welcome_and_install_uses_module_level_os():
    code = first_run_setup.show_welcome_and_install.__code__
    assert "os" not in code.co_varnames, (
        "os is function-local in show_welcome_and_install — a stray `import os` "
        "will crash the earlier os.environ use with UnboundLocalError and abort "
        "onboarding after 'Let's Go!'. Use the module-level os."
    )


def test_the_kde_help_uses_module_level_os_and_glib():
    """The KDE Flatpak help reads FLATPAK_ID, then sets a GLib timer after
    its imports: a local `import os` or `from gi.repository import GLib`
    further down would leave the name unassigned where it's first used."""
    code = first_run_setup._offer_kde_notification_help.__code__
    assert not {"os", "GLib"} & set(code.co_varnames + code.co_cellvars)
