"""The tray shows notices without loading the dictation engine.

Borrowing app.py's libnotify wrapper would import app.py, which loads numpy,
faster_whisper and torch: about 300 MB that the tray would then keep for the
whole session, the first time anyone picks a Performance preset. A fresh
interpreter is used because the rest of the suite has already imported
tuxtalk.app.
"""

import os
import subprocess
import sys
import textwrap


SCRIPT = textwrap.dedent("""
    import sys, types
    import tuxtalk.config as config
    import tuxtalk.model_helper as model_helper
    import tuxtalk.notify as notify
    from tuxtalk import tray, tray_presets
    from tuxtalk.recommend import Preset

    cfg = types.SimpleNamespace(model="small", device="cpu", notify=True)
    config.load_config = lambda: cfg
    config.save_config = lambda c: None
    model_helper.is_model_cached_fast = lambda model: True
    shown = []
    notify.notify = lambda title, body: shown.append(title + " | " + body)

    lightest = Preset("lightest", "Lightest", "", "base", "cpu")
    tray_presets.current_presets = lambda: [lightest]
    tray_presets.preset_in_use = lambda: "recommended"
    class Tray(tray_presets.Presets):
        _applying_preset = False
        update_menu_display = _emit_model_changed = restart_service = (
            lambda self, *a: None)
    Tray().set_performance_preset("lightest")

    assert shown and shown[0].startswith("Performance: Lightest"), shown
    # (numpy comes along with whisper_vulkan, which the preset asks about the
    # graphics chip; the notice itself must not bring the engine.)
    for heavy in ("tuxtalk.app", "faster_whisper", "torch"):
        assert heavy not in sys.modules, heavy + " was loaded into the tray"
    print("ok")
""")


def test_a_tray_notice_does_not_load_the_dictation_engine():
    env = dict(os.environ)
    out = subprocess.run([sys.executable, "-c", SCRIPT], env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip().endswith("ok"), out.stderr
