"""Exercise the CI installer with local downloads; never install or run a scanner."""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

BASH = shutil.which("bash")
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
ARCHIVE_SHA = "ba6dbb656933921c775ee5a2d1c13a91046e7952e9d919f9bac4cec61d628e7d"
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")


@pytest.mark.parametrize("download", ["valid", "corrupt", "unavailable"])
def test_install_requires_a_complete_verified_download(tmp_path: Path, download: str):
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["secret-scan"]["steps"]
    step = next(item for item in steps if item["name"] == "Install gitleaks")
    fixture = tmp_path / "fixture.tar.gz"
    with tarfile.open(fixture, "w:gz") as archive:
        for name in ("gitleaks", "unrequested-file"):
            content = b"test fixture, never executed\n"
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    expected = hashlib.sha256(fixture.read_bytes()).hexdigest()
    # Substitute only the test archive pin; checksum calculation and extraction are real.
    assert step["run"].count(ARCHIVE_SHA) == 1
    script = '''
curl() {
  if [[ $TEST_DOWNLOAD == unavailable ]]; then
    echo 'simulated network failure' >&2
    return 22
  fi
  while [[ $1 != --output ]]; do shift; done
  if [[ $TEST_DOWNLOAD == corrupt ]]; then
    printf 'incomplete archive' > "$2"
  else
    cp "$TEST_ARCHIVE" "$2"
  fi
}
sudo() { printf '%s\\n' "$@" > "$TEST_INSTALL_CALLS"; }
''' + step["run"].replace(ARCHIVE_SHA, expected)
    calls = tmp_path / "install.log"
    runner = tmp_path / "installer.sh"
    runner.write_text(script, encoding="utf-8", newline="\n")
    result = subprocess.run(
        [BASH, str(runner)], cwd=tmp_path,
        env={**os.environ, "RUNNER_TEMP": ".", "TEST_ARCHIVE": str(fixture),
             "TEST_INSTALL_CALLS": str(calls), "TEST_DOWNLOAD": download},
        capture_output=True, text=True, check=False,
    )
    extracted = list(tmp_path.glob("gitleaks.*/gitleaks"))
    assert not list(tmp_path.glob("gitleaks.*/unrequested-file"))
    if download == "valid":
        assert result.returncode == 0, result.stderr
        assert len(extracted) == 1
        arguments = calls.read_text().splitlines()
        assert arguments[:3] == ["install", "-m", "0755"]
        assert arguments[-1] == "/usr/local/bin/gitleaks"
    else:
        assert result.returncode != 0
        assert not calls.exists() and not extracted
        assert "::error::" in result.stdout
        if download == "unavailable":
            assert "simulated network failure" in result.stderr
        else:
            assert "checksum mismatch" in result.stdout
