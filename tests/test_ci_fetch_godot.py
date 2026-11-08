"""scripts/ci/fetch_godot.sh: the pinned Godot file is accepted from a cache, the release URL or the API route, and
ONLY if its SHA-512 matches. Run against fake curl/gh so no network is used."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci" / "fetch_godot.sh"
GOOD = b"the pinned godot bytes"
SHA = hashlib.sha512(GOOD).hexdigest()
ZIP = "Godot_v4.3-stable_linux.x86_64.zip"

pytestmark = pytest.mark.skipif(not shutil.which("bash") or os.name == "nt", reason="needs bash")


def _shim(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, *, curl, gh, cached=None):
    bins = tmp_path / "bin"
    bins.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    log.write_text("")
    out_dir = tmp_path / "dl"
    out_dir.mkdir(exist_ok=True)
    if cached is not None:
        (out_dir / ZIP).write_bytes(cached)
    # the fake curl finds the -o argument; gh writes into --dir
    _shim(bins / "curl", f'echo curl >> "{log}"\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n{curl}\n')
    if gh is None:
        (bins / "gh").unlink(missing_ok=True)
    else:
        _shim(bins / "gh", f'echo gh >> "{log}"\ndir=""\nwhile [ $# -gt 0 ]; do [ "$1" = "--dir" ] && dir="$2"; shift; done\n{gh}\n')
    env = {**os.environ, "PATH": f"{bins}:/usr/bin:/bin"}
    if gh is None:
        env["PATH"] = f"{bins}:/usr/bin:/bin"          # no gh shim in front; the system one may exist, so hide it:
        _shim(bins / "gh", f'echo gh >> "{log}"\nexit 127')
    res = subprocess.run(["bash", str(SCRIPT), str(out_dir), ZIP, SHA], env=env, capture_output=True, text=True, timeout=60)
    calls = log.read_text().split()
    return res, calls, out_dir / ZIP


WRITE_GOOD_TO_OUT = f'printf %s "{GOOD.decode()}" > "$out"'
WRITE_BAD_TO_OUT = 'printf %s "something else" > "$out"'


def test_a_verified_cached_file_is_used_without_any_download(tmp_path):
    res, calls, file = _run(tmp_path, curl="exit 1", gh="exit 1", cached=GOOD)
    assert res.returncode == 0 and calls == [] and file.read_bytes() == GOOD
    assert "cached" in res.stdout


def test_a_corrupt_cache_is_discarded_and_replaced_not_trusted(tmp_path):
    res, calls, file = _run(tmp_path, curl=WRITE_GOOD_TO_OUT, gh="exit 1", cached=b"half a file")
    assert res.returncode == 0 and calls == ["curl"] and file.read_bytes() == GOOD


def test_the_release_url_is_used_first(tmp_path):
    res, calls, file = _run(tmp_path, curl=WRITE_GOOD_TO_OUT, gh="exit 1")
    assert res.returncode == 0 and calls == ["curl"] and file.read_bytes() == GOOD


def test_when_the_release_url_keeps_failing_the_api_route_gives_the_same_verified_file(tmp_path):
    res, calls, file = _run(tmp_path, curl="exit 22", gh=f'printf %s "{GOOD.decode()}" > "$dir/{ZIP}"')
    assert res.returncode == 0 and calls == ["curl", "gh"] and file.read_bytes() == GOOD
    assert "API route" in res.stderr


def test_a_wrong_file_from_the_url_is_rejected_and_the_api_route_is_tried(tmp_path):
    res, calls, file = _run(tmp_path, curl=WRITE_BAD_TO_OUT, gh=f'printf %s "{GOOD.decode()}" > "$dir/{ZIP}"')
    assert res.returncode == 0 and calls == ["curl", "gh"] and file.read_bytes() == GOOD


def test_a_wrong_file_from_every_route_fails_and_leaves_nothing_behind(tmp_path):
    res, calls, file = _run(tmp_path, curl=WRITE_BAD_TO_OUT, gh=f'printf %s "tampered" > "$dir/{ZIP}"')
    assert res.returncode == 1 and calls == ["curl", "gh"] and not file.exists()          # never leaves an unverified file
    assert "SHA-512" in res.stderr


def test_no_route_available_fails_with_a_reason(tmp_path):
    res, calls, file = _run(tmp_path, curl="exit 22", gh=None)
    assert res.returncode == 1 and not file.exists() and "no route produced" in res.stderr


def test_the_workflow_uses_the_script_and_keeps_the_pin_and_a_cache():
    text = (SCRIPT.parent.parent.parent / ".github" / "workflows" / "docker.yml").read_text()
    assert "scripts/ci/fetch_godot.sh" in text and "actions/cache@v4" in text
    assert "fd52bb4ba8acc30ca5accd1c566d470ad7282f891ccc0995dfafabcf92bcf76280ce182bf9d80ebd885f3ed2165d01e1fc3f2928436b15498dfbd98656c2a45a" in text
    assert "curl -fsSL" not in text                                           # no unverified download left in the workflow itself
