"""GPU detection must work inside a Flatpak, where nvidia-smi is absent.

The sandbox has no nvidia-smi binary, and cuda_preload clears CUDA_VISIBLE_DEVICES
on first run (before any CUDA libs exist), so a ctranslate2/libcuda probe would
report zero devices. The driver's device node (/dev/nvidiactl, exposed by the
manifest's --device=dri) is the reliable, dependency-free signal. On the host the
nvidia-smi path is unchanged.
"""

import types


def test_detect_nvidia_uses_device_node_in_flatpak(monkeypatch):
    from tuxtalk import cuda_helper

    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    monkeypatch.setattr(cuda_helper.os.path, "exists", lambda p: p == "/dev/nvidiactl")
    assert cuda_helper.detect_nvidia_gpu() is True

    monkeypatch.setattr(cuda_helper.os.path, "exists", lambda p: False)
    assert cuda_helper.detect_nvidia_gpu() is False


def test_detect_nvidia_does_not_shell_out_in_flatpak(monkeypatch):
    """In a Flatpak it must NOT call nvidia-smi (it isn't there and would error)."""
    from tuxtalk import cuda_helper

    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    monkeypatch.setattr(cuda_helper.os.path, "exists", lambda p: False)

    def boom(*a, **k):
        raise AssertionError("nvidia-smi must not be invoked inside a Flatpak")

    monkeypatch.setattr(cuda_helper.subprocess, "run", boom)
    assert cuda_helper.detect_nvidia_gpu() is False


def test_detect_nvidia_uses_nvidia_smi_on_host(monkeypatch):
    from tuxtalk import cuda_helper

    monkeypatch.delenv("FLATPAK_ID", raising=False)
    seen = {}

    def fake_run(cmd, **k):
        seen["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="NVIDIA GeForce RTX 4070 SUPER\n")

    monkeypatch.setattr(cuda_helper.subprocess, "run", fake_run)
    assert cuda_helper.detect_nvidia_gpu()
    assert seen["cmd"][0] == "nvidia-smi"
