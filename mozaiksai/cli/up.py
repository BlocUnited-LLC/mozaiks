"""One-command local startup for first-time and everyday dev workflows."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from mozaiksai.cli import doctor as doctor_cli
from mozaiksai.cli.generators import realm as realm_generator
from mozaiksai.cli.generators import theme as theme_generator
from mozaiksai.cli.paths import find_project_root


def _run_command(command: list[str], *, cwd: Path | None = None) -> int:
    print(f"+ {' '.join(command)}")
    try:
        result = subprocess.run(command, cwd=str(cwd) if cwd else None, check=False)
    except FileNotFoundError:
        print(f"FAIL: command not found: {command[0]}")
        return 127
    return result.returncode


def _start_frontend(root: Path) -> int:
    if shutil.which("npm") is None:
        print("FAIL: npm is not on PATH. Install Node.js and npm first.")
        return 1

    app_dir = root / "app"
    print("+ npm run dev  (in app/)")
    process = subprocess.Popen(["npm", "run", "dev"], cwd=str(app_dir))
    print(f"OK: frontend started (pid={process.pid})")
    return 0


def _run_local_script(root: Path, start_frontend: bool) -> int:
    if sys.platform != "win32":
        print("FAIL: --mode local currently uses start-dev.ps1 and is supported on Windows only.")
        return 1

    script = root / "start-dev.ps1"
    if not script.is_file():
        print(f"FAIL: start-dev.ps1 not found at {script}")
        return 1

    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Mode",
        "local",
    ]
    if start_frontend:
        command.append("-StartFrontend")
    return _run_command(command, cwd=root)


def _run_generate(root: Path) -> int:
    print("Syncing generated artifacts...")
    rc = realm_generator.run(root=root, dry_run=False)
    if rc != 0:
        return rc
    rc = theme_generator.run(root=root, dry_run=False)
    return rc


def run(
    *,
    root: Path | None = None,
    mode: str = "docker",
    start_frontend: bool = False,
    build: bool = False,
    detach: bool = True,
    skip_generate: bool = False,
    skip_doctor: bool = False,
    strict_doctor: bool = False,
) -> int:
    """Run preflight checks and start the selected local runtime mode."""
    root = root or find_project_root()

    if not skip_generate:
        rc = _run_generate(root)
        if rc != 0:
            return rc

    if not skip_doctor:
        print("Running preflight diagnostics...")
        rc = doctor_cli.run(root=root, strict=strict_doctor, skip_network=True)
        if rc != 0:
            return rc

    if mode == "local":
        return _run_local_script(root, start_frontend)

    command = ["docker", "compose", "-f", "infra/compose/docker-compose.yml", "up"]
    if detach:
        command.extend(["-d", "--remove-orphans"])
    if build:
        command.append("--build")

    rc = _run_command(command, cwd=root)
    if rc != 0:
        return rc

    if start_frontend:
        rc = _start_frontend(root)
        if rc != 0:
            return rc

    if not skip_doctor:
        print("Running post-start diagnostics...")
        doctor_cli.run(root=root, strict=False, skip_network=False)

    print("OK: startup complete")
    return 0
