"""Start a Mozaiks host from one pinned install, prove where it loads from, and stop it.

``start`` installs one OSS candidate (a wheel, or a commit built with
``git archive`` so the working tree never leaks in) into a fresh venv, proves
the environment, then starts the host as a detached process on 127.0.0.1 and
returns. ``stop`` ends that host by PID. ``proof`` re-runs the proof.

Usage::

    python scripts/pinned_acceptance_env.py start --evidence-dir evidence/run-1 \\
        (--oss-sha <commit> | --oss-wheel dist/mozaiks-X-py3-none-any.whl) \\
        --env-file host.env [--workspace <checkout> --workspace-sha <commit>] \\
        [--host-app mozaiksai.hosts.studio:app] [--requirements extra.txt]
    python scripts/pinned_acceptance_env.py proof --evidence-dir evidence/run-1
    python scripts/pinned_acceptance_env.py stop --evidence-dir evidence/run-1

The host's environment is explicit: a few operating-system variables, the
variables in ``--env-file``, and the path variables this script sets.
Nothing else is inherited from the caller, and ``PYTHON_DOTENV_DISABLED=1``
stops every ``.env`` search, so a stale workspace ``.env`` cannot repoint a
path. ``MOZAIKS_FACTORY_APP_PATH``, ``MOZAIKS_WORKFLOWS_PATH``,
``MOZAIKS_CHAT_UI_PATH`` and ``MOZAIKS_WEB_SHELL_PATH`` always point into the
venv, and generated artifacts go to the evidence folder. The env file must set
``MONGO_URI``: the generated app's runtime check runs against that database,
and when that check cannot run the export stays blocked. It must also choose
how the host authenticates (for example ``AUTH_ENABLED=false``, which serves
requests from this machine only): with no auth setting the host refuses to
start.

The proof runs in the venv's interpreter with the host's environment, after
importing the host module. It records the origin of every package the mozaiks
distribution installs, ``sys.path``, the factory, workflow, chat-ui and
web-shell roots, every capability pack, and whether the runtime-check database
answers. It fails when any of them resolves outside the venv, when a
``MOZAIKS_*_PATH`` variable points outside the venv, workspace or evidence
folder, when the installed mozaiks is not the candidate, or when the database
is missing or unreachable; ``start`` then starts no host.

Files written to ``--evidence-dir``: ``venv/``, ``build/``, ``pip-freeze.txt``,
``environment-proof.json``, ``host.json``, ``host.pid``, ``host.log`` and, after
``stop``, ``host-stopped.json``. They record variable names, never the env
file's values. This script imports nothing from the packages it installs.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import warnings
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROOF_SCHEMA_VERSION = "mozaiks.pinned_environment_proof.v1"
HOST_SCHEMA_VERSION = "mozaiks.pinned_host.v1"
DEFAULT_HOST_APP = "mozaiksai.hosts.studio:app"
DISTRIBUTION = "mozaiks"
# What the wheel installs, used when its metadata has no top_level.txt.
EXPECTED_TOP_LEVEL = ("factory_app", "logs", "mozaiks", "mozaiks_chat_ui", "mozaiks_cli", "mozaiksai", "web_shell")
# Operating-system variables a Python process needs; nothing else is inherited.
OS_VARIABLES = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH",
    "LANG", "LC_ALL", "LC_CTYPE", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
)
# Never taken from the env file: they would change what the interpreter imports.
INTERPRETER_VARIABLES = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
READINESS_PATH = "/api/health/live"
PRICING_CATALOG = Path("mozaiksai") / "core" / "usage" / "catalogs" / "usage-pricing.generated.json"
REPO_ROOT = Path(__file__).resolve().parents[1]
PROOF_MARKER = "@@mozaiks-environment-proof@@"

# Runs inside the venv's interpreter with the host's environment and working
# directory. It imports the host module first, so any sys.path change the host
# makes on import is in force when the origins are read.
PROOF_PROGRAM = r'''
import importlib, importlib.metadata, importlib.util, json, os, sys

host_app, distribution, expected = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
result = {"host_app": host_app, "cwd": os.getcwd(), "python": {
    "executable": sys.executable, "prefix": sys.prefix, "base_prefix": sys.base_prefix, "version": sys.version}}
module_name, _, attribute = host_app.partition(":")
try:
    module = importlib.import_module(module_name)
    if attribute:
        getattr(module, attribute)
    result["host_import"] = {"ok": True}
except Exception as exc:
    result["host_import"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

dist = importlib.metadata.distribution(distribution)
top_level = [line.strip() for line in (dist.read_text("top_level.txt") or "").splitlines() if line.strip()]
result["distribution"] = {"name": dist.metadata["Name"], "version": dist.version,
                          "location": str(dist.locate_file("")), "direct_url": dist.read_text("direct_url.json"),
                          "top_level": top_level}
packages = {}
for name in top_level or expected:
    try:
        spec = importlib.util.find_spec(name)
    except Exception as exc:
        packages[name] = {"error": f"{type(exc).__name__}: {exc}"}
        continue
    loaded = sys.modules.get(name)
    packages[name] = {
        "origin": spec.origin if spec else None,
        "locations": list(spec.submodule_search_locations or []) if spec else [],
        "loaded_from": getattr(loaded, "__file__", None) if loaded else None,
    }
result["packages"] = packages

from mozaiksai import resources

result["resolved_paths"] = {
    "factory_app": str(resources.resolve_factory_app_root() or ""),
    "factory_workflows": str(resources.resolve_factory_workflows_root() or ""),
    "chat_ui": str(resources.resolve_chat_ui_root() or ""),
    "web_shell": str(resources.resolve_web_shell_root() or ""),
}
factory_root = resources.resolve_factory_app_root()
result["packs"] = [
    {"id": context.parent.name, "path": str(context.parent)}
    for context in sorted((factory_root / "build_context").glob("*/context.yaml"))
] if factory_root is not None else []

database = {"configured": False, "reachable": False, "error": None}
try:
    from mozaiksai.core.secrets import inspect_secret_config, resolve_secret

    database["configured"] = bool(inspect_secret_config("MONGO_URI").configured)
    if database["configured"]:
        from pymongo import MongoClient

        client = MongoClient(str(resolve_secret("MONGO_URI")), serverSelectionTimeoutMS=5000)
        try:
            client.admin.command("ping")
            database["reachable"] = True
        finally:
            client.close()
except Exception as exc:
    database["error"] = type(exc).__name__
result["runtime_check_database"] = database
result["sys_path"] = list(sys.path)
result["environment_paths"] = {key: value for key, value in os.environ.items()
                               if (key.startswith("MOZAIKS_") and key.endswith("_PATH")) or key == "PLATFORM_PATH"}
print("@@mozaiks-environment-proof@@" + json.dumps(result))
'''


class PreparationError(Exception):
    """The pinned environment could not be prepared; no host was started."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(command: Sequence[str], *, runner: Runner = subprocess.run, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    # Children log UTF-8; the console code page would fail on the first emoji.
    return runner(list(command), capture_output=True, encoding="utf-8", errors="replace", check=False, **kwargs)


def _require(result: subprocess.CompletedProcess[str], what: str) -> subprocess.CompletedProcess[str]:
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-2000:]
        raise PreparationError(f"{what} failed (exit {result.returncode}): {tail}")
    return result


# --------------------------------------------------------------------------- venv and candidate


def venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def create_venv(python: str, venv_dir: Path, *, runner: Runner = subprocess.run) -> Path:
    """Create a fresh venv; bootstrap pip from its bundled wheel when ensurepip fails."""
    if _run([python, "-m", "venv", str(venv_dir)], runner=runner).returncode != 0:
        # Some interpreter builds fail at ensurepip; pip can run from its own wheel.
        shutil.rmtree(venv_dir, ignore_errors=True)
        _require(_run([python, "-m", "venv", "--without-pip", str(venv_dir)], runner=runner), "creating the venv")
        located = _require(_run([python, "-c", (
            "import ensurepip, pathlib; "
            "print(sorted(pathlib.Path(ensurepip.__file__).parent.joinpath('_bundled').glob('pip-*.whl'))[-1])"
        )], runner=runner), "locating the bundled pip wheel")
        wheel = located.stdout.strip()
        _require(
            _run([str(venv_python(venv_dir)), f"{wheel}/pip", "install", "--no-index", wheel], runner=runner),
            "bootstrapping pip",
        )
    return venv_python(venv_dir)


def resolve_commit(repo: Path, sha: str, *, runner: Runner = subprocess.run) -> str:
    result = _require(_run(["git", "-C", str(repo), "rev-parse", "--verify", f"{sha}^{{commit}}"], runner=runner),
                      f"resolving {sha} in {repo}")
    return result.stdout.strip()


def export_commit(repo: Path, commit: str, destination: Path, *, runner: Runner = subprocess.run) -> Path:
    """Write the tree of ``commit`` to ``destination`` with git archive; the working tree is never read."""
    destination.mkdir(parents=True)
    archive = destination.parent / f"{commit[:12]}.tar"
    _require(_run(["git", "-C", str(repo), "archive", "--format=tar", "-o", str(archive), commit], runner=runner),
             f"archiving {commit}")
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter="data")
    archive.unlink()
    return destination


def build_wheel(python: Path, source: Path, wheel_dir: Path, *, runner: Runner = subprocess.run) -> Path:
    _require(_run([str(python), "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(source)],
                  runner=runner), "building the candidate wheel")
    wheels = sorted(wheel_dir.glob(f"{DISTRIBUTION}-*.whl"))
    if len(wheels) != 1:
        raise PreparationError(f"expected one {DISTRIBUTION} wheel in {wheel_dir}, found {[w.name for w in wheels]}")
    return wheels[0]


def site_packages(python: Path, *, runner: Runner = subprocess.run) -> Path:
    result = _require(_run([str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                           runner=runner), "locating site-packages")
    return Path(result.stdout.strip())


# --------------------------------------------------------------------------- host environment


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; ``#`` comments, ``export`` prefixes and surrounding quotes are allowed."""
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[len("export "):].strip()
        key, separator, value = text.partition("=")
        key = key.strip()
        if not separator or not key.replace("_", "").isalnum():
            raise PreparationError(f"{path}:{number} is not a KEY=VALUE line")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def install_paths(packages_dir: Path, evidence_dir: Path) -> dict[str, str]:
    """The path variables the host gets, each inside the venv or the evidence folder."""
    return {
        "MOZAIKS_FACTORY_APP_PATH": str(packages_dir / "factory_app"),
        "MOZAIKS_WORKFLOWS_PATH": str(packages_dir / "factory_app" / "workflows"),
        "MOZAIKS_CHAT_UI_PATH": str(packages_dir / "mozaiks_chat_ui"),
        "MOZAIKS_WEB_SHELL_PATH": str(packages_dir / "web_shell"),
        "MOZAIKS_GENERATED_ARTIFACTS_PATH": str(evidence_dir / "generated"),
    }


def host_environment(
    *,
    file_values: Mapping[str, str],
    paths: Mapping[str, str],
    caller: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """The host's whole environment, and the env-file variables it replaced or dropped."""
    caller = os.environ if caller is None else caller
    environment = {name: caller[name] for name in OS_VARIABLES if caller.get(name)}
    environment.update(file_values)
    replaced = {name for name in paths if name in file_values and file_values[name] != paths[name]}
    replaced |= {name for name in INTERPRETER_VARIABLES if name in file_values}
    environment.update(paths)
    for name in INTERPRETER_VARIABLES:
        environment.pop(name, None)
    environment.update({"PYTHON_DOTENV_DISABLED": "1", "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1"})
    return environment, sorted(replaced)


# --------------------------------------------------------------------------- proof


def _inside(path: str | None, root: Path) -> bool:
    if not path:
        return False
    try:
        candidate = os.path.normcase(str(Path(path).resolve()))
        base = os.path.normcase(str(root.resolve()))
        return os.path.commonpath([candidate, base]) == base
    except (OSError, ValueError):
        return False


def gather_proof(
    python: Path,
    *,
    host_app: str,
    environment: Mapping[str, str],
    cwd: Path,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    result = _run(
        [str(python), "-c", PROOF_PROGRAM, host_app, DISTRIBUTION, json.dumps(EXPECTED_TOP_LEVEL)],
        runner=runner, env=dict(environment), cwd=str(cwd), timeout=600,
    )
    for line in reversed(result.stdout.splitlines()):
        if line.startswith(PROOF_MARKER):
            proof: dict[str, Any] = json.loads(line[len(PROOF_MARKER):])
            return proof
    raise PreparationError(
        f"the environment proof did not run (exit {result.returncode}): {(result.stderr or result.stdout)[-2000:]}"
    )


def evaluate_proof(
    proof: Mapping[str, Any],
    *,
    venv_dir: Path,
    evidence_dir: Path,
    workspace: Path | None,
    candidate_wheel: str | None,
) -> list[str]:
    """Every way the proved environment departs from the pinned install."""
    violations: list[str] = []
    host_import = proof.get("host_import") or {}
    if not host_import.get("ok"):
        violations.append(f"the host module did not import: {host_import.get('error')}")
    packages = proof.get("packages") or {}
    for name in sorted(set(EXPECTED_TOP_LEVEL) | set(packages)):
        info = packages.get(name) or {}
        places = [info.get("origin"), info.get("loaded_from"), *(info.get("locations") or [])]
        places = [place for place in places if place and place not in {"namespace", "built-in", "frozen"}]
        if not places:
            violations.append(f"package {name} does not resolve{': ' + info['error'] if info.get('error') else ''}")
        for place in places:
            if not _inside(place, venv_dir):
                violations.append(f"package {name} resolves outside the venv: {place}")
    for label, path in (proof.get("resolved_paths") or {}).items():
        if not _inside(path, venv_dir):
            violations.append(f"the {label} root resolves outside the venv: {path or '(none)'}")
    packs = proof.get("packs") or []
    if not packs:
        violations.append("no capability pack resolves")
    for pack in packs:
        if not _inside(pack.get("path"), venv_dir):
            violations.append(f"capability pack {pack.get('id')} resolves outside the venv: {pack.get('path')}")
    allowed_paths = [venv_dir, evidence_dir, *([workspace] if workspace else [])]
    for name, value in sorted((proof.get("environment_paths") or {}).items()):
        if not any(_inside(value, root) for root in allowed_paths):
            violations.append(f"{name} points outside the venv, workspace and evidence folder: {value}")
    python = proof.get("python") or {}
    import_roots = [venv_dir, *([workspace] if workspace else [])]
    import_roots += [Path(python["base_prefix"])] if python.get("base_prefix") else []
    import_roots += [Path(proof["cwd"])] if proof.get("cwd") else []
    for entry in proof.get("sys_path") or []:
        if entry and not any(_inside(entry, root) for root in import_roots):
            violations.append(f"sys.path holds a directory outside the venv and workspace: {entry}")
    database = proof.get("runtime_check_database") or {}
    if not database.get("configured"):
        violations.append("MONGO_URI is not configured, so the runtime check cannot run and export stays blocked")
    elif not database.get("reachable"):
        violations.append(f"the runtime-check database does not answer ({database.get('error') or 'no ping'})")
    if candidate_wheel:
        direct_url = (proof.get("distribution") or {}).get("direct_url") or ""
        try:
            url = str(json.loads(direct_url).get("url", ""))
        except (TypeError, ValueError, AttributeError):
            url = ""
        if not url.endswith("/" + candidate_wheel):
            violations.append(f"the installed {DISTRIBUTION} is not the candidate {candidate_wheel}: {url or '(unknown)'}")
    return violations


def prove(
    state: Mapping[str, Any],
    environment: Mapping[str, str],
    *,
    runner: Runner = subprocess.run,
) -> tuple[dict[str, Any], list[str]]:
    evidence_dir = Path(state["evidence_dir"])
    workspace = Path(state["workspace"]) if state.get("workspace") else None
    proof = gather_proof(
        Path(state["python"]), host_app=state["host_app"], environment=environment,
        cwd=Path(state["cwd"]), runner=runner,
    )
    violations = evaluate_proof(
        proof, venv_dir=Path(state["venv_dir"]), evidence_dir=evidence_dir, workspace=workspace,
        candidate_wheel=(state.get("candidate") or {}).get("wheel"),
    )
    report = {
        "schema_version": PROOF_SCHEMA_VERSION,
        "proved_at": _now(),
        "passed": not violations,
        "violations": violations,
        "candidate": state.get("candidate"),
        "workspace": state.get("workspace_record"),
        "venv_dir": state["venv_dir"],
        "host_app": state["host_app"],
        "environment_variables": sorted(environment),
        "env_file": state.get("env_file"),
        "env_file_variables": state.get("env_file_variables"),
        "replaced_env_file_variables": state.get("replaced"),
        "proof": proof,
    }
    _write_json(evidence_dir / "environment-proof.json", report)
    return report, violations


# --------------------------------------------------------------------------- processes


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def spawn_detached(command: Sequence[str], *, environment: Mapping[str, str], cwd: Path, log_path: Path) -> int:
    """Start ``command`` in its own process group, tied to neither this process nor its console."""
    with log_path.open("ab") as log:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
            "cwd": str(cwd), "env": dict(environment), "close_fds": True,
        }
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            try:
                # Leave the caller's job object too, so the caller's time limit cannot end the host.
                process = subprocess.Popen(
                    list(command), creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs,
                )
            except OSError:
                process = subprocess.Popen(list(command), creationflags=flags, **kwargs)
        else:
            process = subprocess.Popen(list(command), start_new_session=True, **kwargs)
    pid = process.pid
    # Outliving this handle is the point; Popen would warn that the child still runs.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        del process
    return pid


def process_state(pid: int) -> tuple[bool, str | None]:
    """Whether ``pid`` runs, and its executable (Windows) or command line (elsewhere)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False, None
        try:
            code = wintypes.DWORD()
            alive = bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            found = kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
            return alive, buffer.value if found else None
        finally:
            kernel32.CloseHandle(handle)
    # Reap it if it is our own exited child; an unreaped child still answers kill(pid, 0).
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, os.WNOHANG)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, None
    except PermissionError:
        return True, None
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        if stat.read_text(errors="replace").rpartition(")")[2].split()[:1] == ["Z"]:
            return False, None
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        return True, cmdline or None
    result = subprocess.run(["ps", "-o", "stat=,command=", "-p", str(pid)], capture_output=True, text=True, check=False)
    state, _, command = result.stdout.strip().partition(" ")
    if not state or state.startswith("Z"):
        return False, None
    return True, command.strip() or None


def belongs_to(description: str | None, python: str) -> bool:
    if not description:
        return False
    if sys.platform == "win32":
        return os.path.normcase(description) == os.path.normcase(python)
    return description.split(" ", 1)[0] in {python, os.path.realpath(python)}


def terminate(pid: int) -> str:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, check=False)
        return "taskkill /T /F"
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        return "already gone"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and process_state(pid)[0]:
        time.sleep(0.2)
    if process_state(pid)[0]:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        return "SIGTERM, then SIGKILL"
    return "SIGTERM"


def wait_ready(base_url: str, pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_state(pid)[0]:
            return False
        try:
            with urllib.request.urlopen(base_url + READINESS_PATH, timeout=2) as response:  # noqa: S310 - loopback
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


# --------------------------------------------------------------------------- commands


def _workspace_record(workspace: Path | None, expected_sha: str | None, *, runner: Runner) -> dict[str, Any] | None:
    if workspace is None:
        if expected_sha:
            raise PreparationError("--workspace-sha needs --workspace")
        return None
    if not workspace.is_dir():
        raise PreparationError(f"the workspace {workspace} is not a directory")
    record: dict[str, Any] = {"path": str(workspace)}
    head = _run(["git", "-C", str(workspace), "rev-parse", "HEAD"], runner=runner)
    if head.returncode == 0:
        record["head"] = head.stdout.strip()
        status = _run(["git", "-C", str(workspace), "status", "--porcelain", "--untracked-files=no"], runner=runner)
        record["uncommitted_changes"] = bool(status.stdout.strip())
    if expected_sha:
        if not str(record.get("head", "")).startswith(expected_sha):
            raise PreparationError(f"the workspace is at {record.get('head') or 'no commit'}, not {expected_sha}")
        if record.get("uncommitted_changes"):
            raise PreparationError("the workspace has uncommitted changes to tracked files")
    return record


def command_start(args: argparse.Namespace, *, runner: Runner = subprocess.run) -> int:
    evidence_dir: Path = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    used = [name for name in ("host.json", "venv", "build", "environment-proof.json") if (evidence_dir / name).exists()]
    if used:
        raise PreparationError(f"{evidence_dir} already holds {used}; use a new evidence folder")
    workspace = args.workspace.resolve() if args.workspace else None
    workspace_record = _workspace_record(workspace, args.workspace_sha, runner=runner)
    env_file: Path = args.env_file.resolve()
    file_values = parse_env_file(env_file)
    if not file_values.get("MONGO_URI"):
        raise PreparationError(f"{env_file} must set MONGO_URI for the runtime check")

    venv_dir = evidence_dir / "venv"
    build_dir = evidence_dir / "build"
    python = create_venv(args.python, venv_dir, runner=runner)
    candidate: dict[str, Any]
    if args.oss_sha:
        repo: Path = args.oss_repo.resolve()
        commit = resolve_commit(repo, args.oss_sha, runner=runner)
        source = export_commit(repo, commit, build_dir / "source", runner=runner)
        wheel = build_wheel(python, source, build_dir / "dist", runner=runner)
        candidate = {"kind": "commit", "commit": commit, "repository": str(repo)}
    else:
        wheel = args.oss_wheel.resolve()
        if not wheel.is_file():
            raise PreparationError(f"the wheel {wheel} does not exist")
        candidate = {"kind": "wheel"}
    candidate.update({"wheel": wheel.name, "wheel_path": str(wheel), "wheel_sha256": _sha256(wheel)})
    _require(_run([str(python), "-m", "pip", "install", str(wheel)], runner=runner), "installing the candidate")
    for requirements in args.requirements or []:
        _require(_run([str(python), "-m", "pip", "install", "-r", str(requirements)], runner=runner),
                 f"installing {requirements}")
    if args.requirements:
        # Extra requirements may pin another mozaiks build; the candidate is what runs.
        _require(_run([str(python), "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)],
                      runner=runner), "reinstalling the candidate")
    freeze = _require(_run([str(python), "-m", "pip", "freeze", "--all"], runner=runner), "pip freeze")
    (evidence_dir / "pip-freeze.txt").write_text(freeze.stdout, encoding="utf-8")

    packages_dir = site_packages(python, runner=runner)
    paths = install_paths(packages_dir, evidence_dir)
    environment, replaced = host_environment(file_values=file_values, paths=paths)
    cwd = workspace or evidence_dir
    port = args.port or free_port()
    state: dict[str, Any] = {
        "schema_version": HOST_SCHEMA_VERSION,
        "evidence_dir": str(evidence_dir),
        "venv_dir": str(venv_dir),
        "python": str(python),
        "host_app": args.host_app,
        "cwd": str(cwd),
        "workspace": str(workspace) if workspace else None,
        "workspace_record": workspace_record,
        "candidate": candidate,
        "env_file": str(env_file),
        "env_file_variables": sorted(file_values),
        "replaced": replaced,
        "paths": paths,
        "port": port,
        "base_url": f"http://127.0.0.1:{port}",
    }
    _, violations = prove(state, environment, runner=runner)
    if violations:
        print(json.dumps({"started": False, "violations": violations,
                          "proof": str(evidence_dir / "environment-proof.json")}, indent=2))
        return 1

    command = [str(python), "-m", "uvicorn", args.host_app, "--host", "127.0.0.1", "--port", str(port),
               "--log-level", "info", "--no-access-log"]
    pid = spawn_detached(command, environment=environment, cwd=cwd, log_path=evidence_dir / "host.log")
    state.update({"pid": pid, "command": command, "started_at": _now()})
    _write_json(evidence_dir / "host.json", state)
    (evidence_dir / "host.pid").write_text(f"{pid}\n", encoding="utf-8")
    if not wait_ready(state["base_url"], pid, args.ready_timeout):
        if process_state(pid)[0]:
            terminate(pid)
        print(json.dumps({"started": False, "pid": pid, "reason": "the host did not become ready",
                          "log": str(evidence_dir / "host.log")}, indent=2))
        return 1
    print(json.dumps({
        "started": True,
        "pid": pid,
        "base_url": state["base_url"],
        "proof": str(evidence_dir / "environment-proof.json"),
        "log": str(evidence_dir / "host.log"),
        "pricing_file": str(packages_dir / PRICING_CATALOG),
        "stop": f"python {Path(__file__).name} stop --evidence-dir {evidence_dir}",
    }, indent=2))
    return 0


def _load_state(evidence_dir: Path) -> dict[str, Any]:
    path = evidence_dir.resolve() / "host.json"
    if not path.is_file():
        raise PreparationError(f"{evidence_dir} has no host.json; no host was started there")
    state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return state


def command_proof(args: argparse.Namespace, *, runner: Runner = subprocess.run) -> int:
    state = _load_state(args.evidence_dir)
    environment, _ = host_environment(file_values=parse_env_file(Path(state["env_file"])), paths=state["paths"])
    report, violations = prove(state, environment, runner=runner)
    print(json.dumps({"passed": report["passed"], "violations": violations}, indent=2))
    return 0 if not violations else 1


def command_stop(args: argparse.Namespace) -> int:
    evidence_dir: Path = args.evidence_dir.resolve()
    state = _load_state(evidence_dir)
    pid = int(state["pid"])
    alive, description = process_state(pid)
    record: dict[str, Any] = {"pid": pid, "stopped_at": _now()}
    if not alive:
        record["result"] = "already stopped"
    elif not belongs_to(description, state["python"]):
        record["result"] = "refused"
        record["detail"] = f"PID {pid} now runs {description or 'an unknown program'}, not this host"
    else:
        record["method"] = terminate(pid)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process_state(pid)[0]:
            time.sleep(0.2)
        record["result"] = "still running" if process_state(pid)[0] else "stopped"
    _write_json(evidence_dir / "host-stopped.json", record)
    print(json.dumps(record, indent=2))
    return 0 if record["result"] in {"stopped", "already stopped"} else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start, prove and stop a Mozaiks host from one pinned install.")
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="Install a candidate into a fresh venv, prove it, start the host.")
    start.add_argument("--evidence-dir", type=Path, required=True)
    candidate = start.add_mutually_exclusive_group(required=True)
    candidate.add_argument("--oss-sha", help="OSS commit to build the candidate wheel from.")
    candidate.add_argument("--oss-wheel", type=Path, help="Prebuilt mozaiks wheel to install.")
    start.add_argument("--oss-repo", type=Path, default=REPO_ROOT, help="Git repository holding --oss-sha.")
    start.add_argument("--env-file", type=Path, required=True, help="The host's variables; must set MONGO_URI.")
    start.add_argument("--workspace", type=Path, help="App workspace the host runs in (its working directory).")
    start.add_argument("--workspace-sha", help="Commit the workspace must be at, with no tracked changes.")
    start.add_argument("--host-app", default=DEFAULT_HOST_APP, help=f"ASGI app to serve (default {DEFAULT_HOST_APP}).")
    start.add_argument("--requirements", type=Path, action="append", help="Extra requirements file; repeatable.")
    start.add_argument("--python", default=sys.executable, help="Interpreter that creates the venv.")
    start.add_argument("--port", type=int, help="Port on 127.0.0.1 (default: a free port).")
    start.add_argument("--ready-timeout", type=float, default=120.0, help="Seconds to wait for the host to answer.")

    proof = commands.add_parser("proof", help="Prove a started environment again.")
    proof.add_argument("--evidence-dir", type=Path, required=True)

    stop = commands.add_parser("stop", help="Stop the host started in an evidence folder.")
    stop.add_argument("--evidence-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "start":
            return command_start(args)
        if args.command == "proof":
            return command_proof(args)
        return command_stop(args)
    except PreparationError as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
