"""scripts/ci/pull_public_image.sh falls back to Docker Hub when ECR Public refuses.

On 2026-10-13 ECR Public answered `toomanyrequests: Data limit exceeded` to every
mongo:7 pull in several CI shards, failing PR and main runs (issue #970). The
script now tries Docker Hub's copy of a Docker official image on each attempt.
A fake `docker` on PATH records the pulls so no registry is contacted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "pull_public_image.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")

FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$DOCKER_CALLS"
if [[ $1 == pull ]]; then
  for allowed in $DOCKER_ALLOW; do
    [[ $2 == "$allowed" ]] && exit 0
  done
  echo "Error response from daemon: toomanyrequests: Data limit exceeded" >&2
  exit 1
fi
exit 0
"""


def _run(tmp_path: Path, image: str, allow: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER, encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    sleep = bin_dir / "sleep"  # keep the backoff instant
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
    sleep.chmod(0o755)
    calls = tmp_path / "calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DOCKER_CALLS": str(calls),
        "DOCKER_ALLOW": allow,
    }
    result = subprocess.run(
        [BASH, str(SCRIPT), image, "mongo:7"], env=env, capture_output=True, text=True, check=False
    )
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, lines


def test_a_refused_ecr_pull_falls_back_to_docker_hub(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "public.ecr.aws/docker/library/mongo:7", "docker.io/library/mongo:7")
    assert result.returncode == 0, result.stderr
    assert calls == [
        "pull public.ecr.aws/docker/library/mongo:7",
        "pull docker.io/library/mongo:7",
        "tag docker.io/library/mongo:7 mongo:7",
        # docker-image:// build contexts look the image up by its mirror name.
        "tag docker.io/library/mongo:7 public.ecr.aws/docker/library/mongo:7",
    ]
    assert "Pulled mongo:7 from docker.io/library/mongo:7" in result.stderr


def test_ecr_stays_the_first_choice(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "public.ecr.aws/docker/library/mongo:7", "public.ecr.aws/docker/library/mongo:7")
    assert result.returncode == 0, result.stderr
    assert calls == [
        "pull public.ecr.aws/docker/library/mongo:7",
        "tag public.ecr.aws/docker/library/mongo:7 mongo:7",
    ]


def test_both_registries_refusing_still_fails_after_six_attempts(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "public.ecr.aws/docker/library/mongo:7", "")
    assert result.returncode == 1
    assert len([c for c in calls if c.startswith("pull ")]) == 12
    assert "Unable to pull public.ecr.aws/docker/library/mongo:7 after 6 attempts" in result.stderr


def test_an_image_outside_the_official_mirror_has_no_fallback(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "ghcr.io/example/tool:1", "")
    assert result.returncode == 1
    assert set(calls) == {"pull ghcr.io/example/tool:1"}
