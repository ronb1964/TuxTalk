"""pinned_download (packaging/pinned-download.sh) gives the AppImage and Vulkan
engine builds only files with the pinned SHA256: a changed or half-written
cached tool is downloaded again, and a download that doesn't match stops the
build without reaching the cache."""
import hashlib
import pathlib
import subprocess

HELPER = pathlib.Path(__file__).resolve().parent.parent / "packaging" / "pinned-download.sh"
TOOL = b"the pinned tool"
SHA = hashlib.sha256(TOOL).hexdigest()


def _run(tmp_path, served, cache_contents=None):
    """Runs pinned_download with a wget that serves *served* (None: no network)
    and logs each call; returns the result, the cache file and the copy."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "served").write_bytes(served or b"")
    fail = "exit 4" if served is None else f'cp "{tmp_path}/served" "$3"'
    (bin_dir / "wget").write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/wget.log"\n{fail}\n')
    (bin_dir / "wget").chmod(0o755)
    cache = tmp_path / "tool.cached"
    if cache_contents is not None:
        cache.write_bytes(cache_contents)
    dest = tmp_path / "private" / "tool"
    dest.parent.mkdir()
    result = subprocess.run(
        ["bash", "-c", 'source "$0"; pinned_download "$@"', str(HELPER),
         "https://example.invalid/tool", SHA, str(cache), str(dest)],
        capture_output=True, text=True, env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    return result, cache, dest


def _downloads(tmp_path):
    log = tmp_path / "wget.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_a_cached_tool_that_matches_is_used_without_downloading(tmp_path):
    result, _, dest = _run(tmp_path, served=None, cache_contents=TOOL)
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == TOOL
    assert _downloads(tmp_path) == 0


def test_the_first_build_downloads_and_caches_it(tmp_path):
    result, cache, dest = _run(tmp_path, served=TOOL)
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == TOOL and cache.read_bytes() == TOOL
    assert not list(tmp_path.glob("tool.cached.part*"))


def test_a_changed_or_half_written_cache_is_downloaded_again(tmp_path):
    result, cache, dest = _run(tmp_path, served=TOOL, cache_contents=TOOL[:5])
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == TOOL and cache.read_bytes() == TOOL
    assert _downloads(tmp_path) == 1


def test_a_download_that_does_not_match_stops_the_build_and_is_not_cached(tmp_path):
    result, cache, _ = _run(tmp_path, served=b"something else")
    assert result.returncode != 0
    assert "doesn't match its pinned SHA256" in result.stderr
    assert not cache.exists()


def test_a_failed_download_stops_the_build(tmp_path):
    result, cache, _ = _run(tmp_path, served=None)
    assert result.returncode != 0
    assert not cache.exists()
