"""Enabling the GNOME extension must persist even when the live shell can't.

On Wayland, `gnome-extensions enable` fails for a just-installed extension
because the running shell hasn't discovered it yet. Stopping there would leave
the extension disabled, and a fresh GNOME install would reboot to no tray icon,
so the UUID is also written straight into org.gnome.shell enabled-extensions,
which the shell reads on its next start.
"""

import types


from tuxtalk import gnome_extensions as ge

UUID = "talktype@ronb1964.github.io"


def _fake_run(*, enable_rc, get_out, set_rc=0, get_rc=0, disabled_set_rc=0):
    """Build a subprocess.run stand-in keyed on which gsettings/CLI call it is.
    *get_out* and the state are enabled-extensions; disabled-extensions is
    empty unless a test sets state["disabled"], and *disabled_set_rc* other
    than 0 refuses changing it (a key an administrator locked)."""
    state = {"written": None, "set_calls": 0, "disabled": "@as []"}

    def run(argv, **kwargs):
        if argv[0] == "gnome-extensions" and argv[1] == "enable":
            return types.SimpleNamespace(returncode=enable_rc, stdout="", stderr="not found")
        if argv[:2] == ["gsettings", "get"] and argv[3] == "disabled-extensions":
            return types.SimpleNamespace(returncode=0, stdout=state["disabled"], stderr="")
        if argv[:2] == ["gsettings", "set"] and argv[3] == "disabled-extensions":
            if disabled_set_rc:
                return types.SimpleNamespace(returncode=disabled_set_rc, stdout="",
                                             stderr="The key is not writable")
            state["disabled"] = argv[4]
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:3] == ["gsettings", "get", "org.gnome.shell"]:
            if get_rc != 0:
                return types.SimpleNamespace(returncode=get_rc, stdout="", stderr="fail")
            out = state["written"] if state["written"] is not None else get_out
            return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
        if argv[:3] == ["gsettings", "set", "org.gnome.shell"]:
            state["set_calls"] += 1
            # A real gsettings set only changes the stored value on success.
            if set_rc == 0:
                state["written"] = argv[4]  # the repr'd list we wrote
            return types.SimpleNamespace(returncode=set_rc, stdout="", stderr="")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    return run, state


def test_reads_empty_typed_array(monkeypatch):
    def run(argv, **k):
        return types.SimpleNamespace(returncode=0, stdout="@as []\n", stderr="")
    monkeypatch.setattr(ge.subprocess, "run", run)
    assert ge._read_enabled_extensions() == []


def test_reads_populated_array(monkeypatch):
    def run(argv, **k):
        return types.SimpleNamespace(returncode=0, stdout="['a@x', 'b@y']\n", stderr="")
    monkeypatch.setattr(ge.subprocess, "run", run)
    assert ge._read_enabled_extensions() == ["a@x", "b@y"]


def test_enable_persists_via_gsettings_when_live_enable_fails(monkeypatch):
    # Wayland fresh install: CLI enable fails, list starts empty.
    run, state = _fake_run(enable_rc=1, get_out="@as []")
    monkeypatch.setattr(ge.subprocess, "run", run)

    assert ge.enable_extension(UUID) is True
    assert UUID in state["written"], "UUID must be written into enabled-extensions"


def test_enable_returns_true_when_live_enable_succeeds(monkeypatch):
    run, _ = _fake_run(enable_rc=0, get_out="@as []")
    monkeypatch.setattr(ge.subprocess, "run", run)
    assert ge.enable_extension(UUID) is True


def test_enable_does_not_duplicate_existing_entry(monkeypatch):
    run, state = _fake_run(enable_rc=1, get_out=f"['{UUID}']")
    monkeypatch.setattr(ge.subprocess, "run", run)

    assert ge.enable_extension(UUID) is True
    # Never wrote, because it was already present (no set call recorded).
    assert state["written"] is None


def test_does_not_clobber_extensions_when_read_fails(monkeypatch):
    """A failed `gsettings get` must NOT trigger a write — that would overwrite
    the user's entire enabled-extensions list with only TuxTalk's."""
    run, state = _fake_run(enable_rc=1, get_out="", get_rc=1)
    monkeypatch.setattr(ge.subprocess, "run", run)

    result = ge.enable_extension(UUID)
    assert state["set_calls"] == 0, "must not write enabled-extensions when the read failed"
    assert result is False, "neither CLI enable nor a safe gsettings write succeeded"


def test_enable_fails_when_neither_mechanism_works(monkeypatch):
    # CLI enable fails AND gsettings set fails -> genuinely could not enable.
    run, _ = _fake_run(enable_rc=1, get_out="@as []", set_rc=1)
    monkeypatch.setattr(ge.subprocess, "run", run)
    assert ge.enable_extension(UUID) is False


def test_is_enabled_falls_back_to_dbus_when_cli_absent(monkeypatch):
    # Inside the Flatpak the gnome-extensions CLI and the org.gnome.shell schema
    # are both absent (subprocess raises FileNotFoundError). Detection must then
    # ask GNOME Shell directly over D-Bus rather than give up and return False —
    # otherwise the GTK tray shows alongside the extension (the double tray icon).
    def no_cli(argv, **k):
        raise FileNotFoundError("gnome-extensions")
    monkeypatch.setattr(ge.subprocess, "run", no_cli)
    monkeypatch.setattr(ge, "_is_extension_enabled_via_dbus", lambda uuid: True)
    assert ge.is_extension_enabled(UUID) is True


def test_is_enabled_uses_cli_result_without_dbus_on_host(monkeypatch):
    # On a normal host the CLI answers, so detection must NOT fall through to
    # D-Bus (host behavior unchanged).
    def cli_enabled(argv, **k):
        return types.SimpleNamespace(returncode=0, stdout="State: ENABLED\n", stderr="")
    monkeypatch.setattr(ge.subprocess, "run", cli_enabled)
    monkeypatch.setattr(ge, "_is_extension_enabled_via_dbus",
                        lambda uuid: (_ for _ in ()).throw(AssertionError("D-Bus must not be used when the CLI answers")))
    assert ge.is_extension_enabled(UUID) is True


def test_enable_takes_it_off_the_disabled_list(monkeypatch):
    """An uninstall's `gnome-extensions disable` put it there, and GNOME
    wouldn't start a reinstall listed on it."""
    run, state = _fake_run(enable_rc=1, get_out="@as []\n")
    state["disabled"] = f"['other@x', '{UUID}']"
    monkeypatch.setattr(ge.subprocess, "run", run)
    assert ge.enable_extension(UUID) is True
    assert state["disabled"] == "['other@x']"


def test_enable_fails_when_the_disabled_list_cant_be_cleared(monkeypatch, caplog):
    """A reinstall listed on disabled-extensions doesn't start, even when
    it is on enabled-extensions too: setup mustn't say it worked."""
    run, state = _fake_run(enable_rc=1, get_out=f"['{UUID}']", disabled_set_rc=1)
    state["disabled"] = f"['{UUID}']"
    monkeypatch.setattr(ge.subprocess, "run", run)
    assert ge.enable_extension(UUID) is False
    assert "The key is not writable" in caplog.text


def test_forget_leaves_other_extensions_and_an_unreadable_list_alone(monkeypatch):
    run, state = _fake_run(enable_rc=0, get_out="@as []\n")
    state["disabled"] = "['a@x', 'b@y']"
    monkeypatch.setattr(ge.subprocess, "run", run)
    ge.forget_extension(UUID)
    assert state["disabled"] == "['a@x', 'b@y']"        # nothing of ours: untouched

    def failing(argv, **k):
        if argv[:2] == ["gsettings", "set"]:
            raise AssertionError("must not write a list it couldn't read")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")
    monkeypatch.setattr(ge.subprocess, "run", failing)
    ge.forget_extension(UUID)
