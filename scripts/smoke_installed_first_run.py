"""Prove an installed Mozaiks package boots a brand-new workspace, as a stranger would.

Run it with the Python of the environment under test, from outside any checkout,
against a throwaway MongoDB server on a non-default port::

    docker run --rm -d --name first-run-smoke-mongo -p 127.0.0.1:27018:27017 mongo:7
    python scripts/smoke_installed_first_run.py --mongo-uri mongodb://127.0.0.1:27018

The server must be a throwaway one, not the database name: the runtime ignores
the database name in the URI and uses fixed database names (``mozaiksai`` and
``mozaiks_apps``) on whatever server the URI points to. Module actions, such as
those a Studio session runs, also write audit records to a third database,
``mozaiks_audit``. Against the default ``localhost:27017`` this smoke writes
into your local development databases.

The release audit and CI pass the URI through ``MOZAIKS_FIRST_RUN_SMOKE_MONGO_URI``
so it does not appear in a child process command line.

It stops at the first failure and prints the server's own output. Steps:

1. ``mozaiksai``, ``mozaiks_cli`` and ``factory_app`` import from this
   interpreter's site-packages, not from a source checkout. The Studio web
   shell and chat UI resources also resolve from the installed package.
2. ``mozaiks init chat`` writes a scaffold whose ``app/security/secrets.yaml``
   validates as the names-only secret contract, whose ``app/app.json`` carries
   an ``appId``, and whose ``.env.example`` grants the local anonymous user the
   Studio admin role.
3. For each host (``platform``, ``studio``), ``mozaiks serve`` runs with a
   scrubbed environment (operating-system basics plus ``MONGO_URI`` only, no
   ``.env`` of its own), reaches ``/api/health/ready``, and ``/api/shell-config``
   answers with the scaffold's ``appId`` and an anonymous user holding ``admin``.
   The Studio shell declares ``/apps`` and its backing API responds successfully.
4. ``mozaiks serve`` against an unreachable ``MONGO_URI`` exits non-zero within
   seconds and names ``MONGO_URI``.

The release audit, the CI ``package`` job, and the release workflow run this so
that a release candidate is proven to start, not only to install.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_NAME = "First Run Smoke"
EXPECTED_APP_ID = "first-run-smoke"
READY_TIMEOUT_SECONDS = 180.0
UNREACHABLE_MONGO_BUDGET_SECONDS = 45.0
MONGO_URI_ENV = "MOZAIKS_FIRST_RUN_SMOKE_MONGO_URI"

# Operating-system variables a process needs to run at all. Everything else,
# including any MOZAIKS_*, MONGO_*, AUTH_*, ENV, or provider key the caller
# happens to have, is withheld: a stranger's shell has none of them.
_OS_ENV_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
)


class SmokeFailure(RuntimeError):
    pass


def _scrubbed_env(**extra: str) -> dict[str, str]:
    env = {key: os.environ[key] for key in _OS_ENV_KEYS if key in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.update(extra)
    return env


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_json(url: str, *, timeout: float = 15.0) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # nosec B310 - loopback smoke probe
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, body


def _log_tail(path: Path, lines: int = 40) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(no output captured)"
    return "\n".join(f"    {line}" for line in text.splitlines()[-lines:])


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=30)


def check_installed_imports() -> None:
    import factory_app
    import mozaiks_cli
    import mozaiksai
    from mozaiksai.resources import resolve_chat_ui_src_root, resolve_web_shell_root

    site_dirs = {Path(sysconfig.get_paths()[key]).resolve() for key in ("purelib", "platlib")}
    origins = {
        "mozaiksai": Path(mozaiksai.__file__).resolve(),
        "mozaiks_cli": Path(mozaiks_cli.__file__).resolve(),
        "factory_app": Path(next(iter(factory_app.__path__))).resolve(),
    }
    for name, origin in origins.items():
        if not any(origin.is_relative_to(site_dir) for site_dir in site_dirs):
            raise SmokeFailure(f"{name} imports from {origin}, not from site-packages {sorted(map(str, site_dirs))}")
        print(f"  OK {name} from {origin}")

    resources = {
        "web_shell": resolve_web_shell_root(),
        "chat_ui_src": resolve_chat_ui_src_root(),
    }
    for name, path in resources.items():
        if path is None or not any(path.resolve().is_relative_to(site_dir) for site_dir in site_dirs):
            raise SmokeFailure(f"{name} does not resolve from installed site-packages: {path}")
        if not path.is_dir():
            raise SmokeFailure(f"installed {name} resource is missing: {path}")
        print(f"  OK {name} from {path}")
    web_shell = resources["web_shell"]
    assert web_shell is not None
    if not (web_shell / "package.json").is_file():
        raise SmokeFailure(f"installed Studio web shell has no package.json: {web_shell}")


def init_workspace(workspace: Path) -> None:
    from mozaiksai.core.secrets.contract import validate_secret_contract_text

    command = [sys.executable, "-m", "mozaiks", "init", "chat", "--name", APP_NAME, "--dir", str(workspace)]
    result = subprocess.run(
        command,
        cwd=str(workspace.parent),
        env=_scrubbed_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise SmokeFailure(f"mozaiks init exited {result.returncode}:\n{result.stdout}\n{result.stderr}")
    print(f"  OK mozaiks init chat --name {APP_NAME!r} -> {workspace}")

    secrets_text = (workspace / "app" / "security" / "secrets.yaml").read_text(encoding="utf-8")
    contract = validate_secret_contract_text(secrets_text)
    print(f"  OK app/security/secrets.yaml validates (version={contract.version}, kind={contract.kind})")

    app_json = json.loads((workspace / "app" / "app.json").read_text(encoding="utf-8"))
    if app_json.get("appId") != EXPECTED_APP_ID:
        raise SmokeFailure(f"app/app.json appId is {app_json.get('appId')!r}, expected {EXPECTED_APP_ID!r}")
    print(f"  OK app/app.json appId={app_json['appId']!r}")

    env_example = (workspace / ".env.example").read_text(encoding="utf-8").splitlines()
    if "AUTH_ANON_ROLES=admin,user" not in env_example:
        raise SmokeFailure(".env.example does not grant the local anonymous user AUTH_ANON_ROLES=admin,user")
    print("  OK .env.example carries AUTH_ANON_ROLES=admin,user")
    if (workspace / ".env").exists():
        raise SmokeFailure("mozaiks init wrote a .env; the first serve must create it from .env.example")


def check_studio_apps(base: str, shell: dict, log_path: Path) -> None:
    """Check the Studio route and the API used by its Apps page."""
    pages = shell.get("pages")
    matches = [
        page for page in pages or []
        if isinstance(page, dict) and page.get("path") == "/apps"
    ]
    if len(matches) != 1 or matches[0].get("component") != "AppsPage":
        raise SmokeFailure("[studio] shell-config must declare /apps with AppsPage")
    meta = matches[0].get("meta")
    if not isinstance(meta, dict) or meta.get("requiresRole") != "admin":
        raise SmokeFailure("[studio] /apps must require the admin role")
    print("  OK [studio] shell-config declares /apps -> AppsPage (admin)")

    url = f"{base}/api/studio/apps"
    try:
        status, apps = _get_json(url)
    except (urllib.error.URLError, OSError) as exc:
        raise SmokeFailure(f"[studio] {url} could not be reached: {exc}\n{_log_tail(log_path)}") from exc
    studio = apps.get("studio") if isinstance(apps, dict) else None
    if (
        status != 200
        or not isinstance(apps, dict)
        or not isinstance(studio, dict)
        or studio.get("route") != "/apps"
        or not isinstance(apps.get("apps"), list)
    ):
        raise SmokeFailure(f"[studio] /api/studio/apps returned {status}: {apps!r}\n{_log_tail(log_path)}")
    print(f"  OK [studio] /api/studio/apps 200: {len(apps['apps'])} app(s)")


def serve_and_probe(workspace: Path, *, host: str, mongo_uri: str, log_dir: Path) -> None:
    port = _free_port()
    log_path = log_dir / f"serve-{host}.log"
    command = [sys.executable, "-m", "mozaiks", "serve", str(workspace), "--host", host, "--port", str(port)]
    base = f"http://127.0.0.1:{port}"
    started = time.monotonic()
    with log_path.open("wb") as log_file:
        process = subprocess.Popen(
            command,
            cwd=str(workspace.parent),
            env=_scrubbed_env(MONGO_URI=mongo_uri),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    try:
        ready_body: object = None
        while time.monotonic() - started < READY_TIMEOUT_SECONDS:
            if process.poll() is not None:
                raise SmokeFailure(
                    f"mozaiks serve --host {host} exited {process.returncode} before it was ready:\n"
                    f"{_log_tail(log_path)}"
                )
            try:
                status, ready_body = _get_json(f"{base}/api/health/ready", timeout=5.0)
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
                continue
            if status == 200:
                break
            time.sleep(0.5)
        else:
            raise SmokeFailure(
                f"mozaiks serve --host {host} was not ready within {READY_TIMEOUT_SECONDS:.0f}s "
                f"(last /api/health/ready: {ready_body!r}):\n{_log_tail(log_path)}"
            )
        print(f"  OK [{host}] /api/health/ready 200 after {time.monotonic() - started:.1f}s: {ready_body}")

        surface = "studio" if host == "studio" else "platform"
        status, shell = _get_json(f"{base}/api/shell-config?surface={surface}")
        if status != 200 or not isinstance(shell, dict):
            raise SmokeFailure(f"[{host}] /api/shell-config returned {status}: {shell!r}\n{_log_tail(log_path)}")
        if shell.get("appId") != EXPECTED_APP_ID:
            raise SmokeFailure(
                f"[{host}] /api/shell-config appId is {shell.get('appId')!r}, expected {EXPECTED_APP_ID!r}; "
                "the shell refuses to render without it"
            )
        runtime = (shell.get("auth") or {}).get("runtime") or {}
        roles = (runtime.get("user") or {}).get("roles") or []
        if "admin" not in roles:
            raise SmokeFailure(
                f"[{host}] the local anonymous user has roles {roles!r}; Studio pages require 'admin'"
            )
        print(f"  OK [{host}] /api/shell-config appId={shell['appId']!r} anonymous roles={roles}")
        if host == "studio":
            check_studio_apps(base, shell, log_path)
    finally:
        _stop(process)

    if not (workspace / ".env").is_file():
        raise SmokeFailure("mozaiks serve did not create .env from .env.example")


def serve_fails_fast_without_mongo(workspace: Path, *, log_dir: Path) -> None:
    unreachable = f"mongodb://127.0.0.1:{_free_port()}/first_run_smoke"
    command = [sys.executable, "-m", "mozaiks", "serve", str(workspace), "--port", str(_free_port())]
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=str(workspace.parent),
            env=_scrubbed_env(MONGO_URI=unreachable, MOZAIKS_MONGO_PREFLIGHT_TIMEOUT_MS="2000"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=UNREACHABLE_MONGO_BUDGET_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeFailure(
            f"mozaiks serve with an unreachable MONGO_URI was still running after "
            f"{UNREACHABLE_MONGO_BUDGET_SECONDS:.0f}s instead of stopping with an error"
        ) from exc
    elapsed = time.monotonic() - started
    output = f"{result.stdout}\n{result.stderr}"
    (log_dir / "serve-unreachable-mongo.log").write_text(output, encoding="utf-8")
    if result.returncode == 0 or "MONGO_URI" not in output:
        raise SmokeFailure(
            f"mozaiks serve with an unreachable MONGO_URI exited {result.returncode} without naming MONGO_URI:\n{output}"
        )
    print(f"  OK unreachable MONGO_URI: exit {result.returncode} after {elapsed:.1f}s, message names MONGO_URI")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get(MONGO_URI_ENV, ""),
        help=(
            "URI of a reachable, throwaway MongoDB server on a non-default port "
            f"(or set {MONGO_URI_ENV}). The runtime ignores the database name in the URI "
            "and uses fixed database names on that server."
        ),
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="empty directory for the workspace and server logs (default: a new temporary directory)",
    )
    parser.add_argument(
        "--hosts",
        nargs="+",
        choices=["platform", "studio"],
        default=["platform", "studio"],
        help="hosts to serve (default: platform studio)",
    )
    args = parser.parse_args(argv)
    if not args.mongo_uri:
        parser.error(
            f"the URI of a throwaway MongoDB server is required: pass --mongo-uri or set {MONGO_URI_ENV}"
        )

    work_dir = (args.work_dir or Path(tempfile.mkdtemp(prefix="mozaiks-first-run-"))).resolve()
    checkout_root = Path(__file__).resolve().parents[1]
    if work_dir == checkout_root or checkout_root in work_dir.parents:
        print(f"ERROR: --work-dir must be outside the source checkout: {work_dir}", file=sys.stderr)
        return 2
    if work_dir.exists() and (not work_dir.is_dir() or any(work_dir.iterdir())):
        print(f"ERROR: --work-dir must be empty: {work_dir}", file=sys.stderr)
        return 2
    work_dir.mkdir(parents=True, exist_ok=True)
    workspace = work_dir / "first-run-smoke"
    print(f"First-run smoke in {work_dir}")
    try:
        print("1. Installed package")
        check_installed_imports()
        print("2. mozaiks init")
        init_workspace(workspace)
        for index, host in enumerate(args.hosts, start=3):
            print(f"{index}. mozaiks serve --host {host}")
            serve_and_probe(workspace, host=host, mongo_uri=args.mongo_uri, log_dir=work_dir)
        print(f"{3 + len(args.hosts)}. mozaiks serve with an unreachable MONGO_URI")
        serve_fails_fast_without_mongo(workspace, log_dir=work_dir)
    except SmokeFailure as exc:
        print(f"\nFIRST-RUN SMOKE FAILED: {exc}", file=sys.stderr)
        return 1
    print("\nFirst-run smoke passed: a new workspace boots from the installed package.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
