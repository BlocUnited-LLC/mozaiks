"""Own a disposable local Mongo, Keycloak and real platform host for acceptance.

Run with the Python environment containing Mozaiks dependencies. No dotenv file
is loaded. The foreground owner must stay running until ``stop`` is requested.
Synthetic realm credentials are fixtures; never use this realm in deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

WORKSPACE = Path(__file__).resolve().parents[1]
REPO = WORKSPACE.parents[2]
EVIDENCE_ROOT = REPO / ".local/evidence/community-env"
LABEL = "io.mozaiks.community-acceptance"
IMAGES = {"mongo": "mongo:7", "identity": "quay.io/keycloak/keycloak:26.0"}


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(f"Docker {args[0]} failed; no existing containers will be adopted.")
    return result.stdout.strip()


def free_port(port: int) -> None:
    with socket.socket() as sock:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))


def wait_http(url: str, *, process: subprocess.Popen | None = None, timeout: int = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError("Platform process exited; inspect its private log.")
        try:
            with urlopen(url, timeout=2) as response:
                return json.load(response)
        except (URLError, TimeoutError, ConnectionError):
            time.sleep(0.5)
    raise RuntimeError(f"Local service did not become ready within {timeout}s: {url}")


def environment(args: argparse.Namespace, evidence: Path) -> tuple[dict, dict]:
    web = f"http://127.0.0.1:{args.web_port}"
    api = f"http://127.0.0.1:{args.api_port}"
    issuer = f"http://127.0.0.1:{args.oidc_port}/realms/common-ground"
    frontend = {
        "PLATFORM_PATH": str(WORKSPACE / "app"),
        "MOZAIKS_APP_WORKSPACE_PATH": str(WORKSPACE),
        "MOZAIKS_WORKFLOWS_PATH": str(evidence / "empty-workflows"),
        "MOZAIKS_HOST": "platform",
        "MOZAIKS_BACKEND_URL": api,
        "VITE_API_URL": api,
        "VITE_OIDC_AUTHORITY": issuer,
        "VITE_OIDC_DISCOVERY_URL": f"{issuer}/.well-known/openid-configuration",
        "VITE_OIDC_CLIENT_ID": "common-ground",
        "VITE_OIDC_REDIRECT_URI": f"{web}/auth/callback",
        "VITE_OIDC_SCOPE": "openid profile email user_posts.read user_posts.create user_posts.react",
    }
    runtime = {
        **frontend,
        "PYTHONPATH": str(REPO),
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONUNBUFFERED": "1",
        "ENV": "local",
        "MONGO_URI": f"mongodb://127.0.0.1:{args.mongo_port}/?serverSelectionTimeoutMS=5000",
        "MOZAIKS_APP_DATABASE_NAME": "common_ground_acceptance",
        "MOZAIKS_DATABASE_STARTUP_POLICY": "required",
        "AUTH_ENABLED": "true",
        "AUTH_PROVIDER": "jwt",
        "AUTH_AUDIENCE": "common-ground-api",
        "AUTH_ISSUER": issuer,
        "AUTH_JWKS_URL": f"{issuer}/protocol/openid-connect/certs",
        "MOZAIKS_OIDC_AUTHORITY": issuer,
        "MOZAIKS_OIDC_DISCOVERY_URL": f"{issuer}/.well-known/openid-configuration",
        "AUTH_SCOPES_CLAIM": "scope",
        "AUTH_ROLES_CLAIM": "roles",
        "AUTH_ACCESS_TOKEN_TYPE_CLAIM": "typ",
        "AUTH_ACCESS_TOKEN_TYPE_VALUE": "Bearer",
        "FRONTEND_URL": web,
        "REACT_DEV_ORIGIN": web,
        # Both browser members share one loopback IP; this is not a load test.
        "RATE_LIMIT_ENABLED": "true",
        "RATE_LIMIT_REQUESTS_PER_MINUTE": "600",
        "LOGS_BASE_DIR": str(evidence / "runtime-logs"),
        "MOZAIKS_GENERATED_ARTIFACTS_PATH": str(evidence / "generated"),
        "AG2_RUNTIME_LOG_FILE": str(evidence / "ag2-runtime.log"),
    }
    return frontend, runtime


async def host(args: argparse.Namespace) -> None:
    # The parent sets the complete process environment before this import.
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        "mozaiksai.hosts.platform:app", host="127.0.0.1", port=args.api_port,
        lifespan="on", access_log=False,
    ))

    async def watch_stop() -> None:
        while not args.evidence_dir.joinpath("host-stop").exists():
            await asyncio.sleep(0.2)
        server.should_exit = True

    watcher = asyncio.create_task(watch_stop())
    try:
        await server.serve()
    finally:
        watcher.cancel()
    if not server.started:
        raise RuntimeError("Platform startup did not complete.")


def run(args: argparse.Namespace, evidence: Path) -> None:
    if not (WORKSPACE / "app/app.json").is_file():
        raise RuntimeError("Build the Common Ground reference app before starting acceptance.")
    for port in (args.mongo_port, args.oidc_port, args.api_port):
        free_port(port)
    images = {key: json.loads(docker("image", "inspect", name))[0]["Id"] for key, name in IMAGES.items()}
    run_id = "community-" + uuid4().hex[:12]
    for kind in IMAGES:
        if docker("ps", "-aq", "--filter", f"name=^/{run_id}-{kind}$"):
            raise RuntimeError("Owned container name already exists.")
    evidence.mkdir(parents=True, exist_ok=False)
    (evidence / "empty-workflows").mkdir()
    frontend, runtime = environment(args, evidence)
    realm = json.loads((WORKSPACE / "tests/identity-realm.json").read_text(encoding="utf-8"))
    web = f"http://127.0.0.1:{args.web_port}"
    realm["clients"][0]["redirectUris"] = [f"{web}/auth/callback"]
    realm["clients"][0]["webOrigins"] = [web]
    realm["clients"][0]["attributes"]["post.logout.redirect.uris"] = f"{web}/*"
    write_json(evidence / "identity-realm.json", realm)
    write_json(evidence / "frontend-env.json", frontend)
    write_json(evidence / "runtime-env.json", runtime)
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).splitlines()
    state = {
        "status": "starting", "run_id": run_id, "source_sha": source, "source_changes": dirty,
        "base_url": web, "api_url": frontend["VITE_API_URL"],
        "auth_issuer": frontend["VITE_OIDC_AUTHORITY"],
        "client_id": "common-ground", "audience": "common-ground-api",
        "users": [{"username": user["username"], "password": user["credentials"][0]["value"]} for user in realm["users"]],
        "frontend_env": frontend, "images": images, "containers": {}, "api_generation": 0,
        "python": sys.executable, "owner_pid": os.getpid(),
        "started_at": datetime.now(UTC).isoformat(),
        "rate_limit_note": "600 RPM per loopback client for acceptance; limiting remains enabled; not load qualification.",
    }
    path = evidence / "environment.json"
    write_json(path, state)
    containers: dict[str, str] = {}
    process: subprocess.Popen | None = None
    outcome = "stopped"
    # Deliberately omit inherited application, provider, proxy and secret env.
    os_keys = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA", "LANG"}
    child_env = {key: value for key, value in os.environ.items() if key.upper() in os_keys}
    child_env.update(runtime)

    def stop_host() -> None:
        if process is not None and process.poll() is None:
            (evidence / "host-stop").touch()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                state["forced_api_stop"] = True

    def start_host() -> subprocess.Popen:
        (evidence / "host-stop").unlink(missing_ok=True)
        state["api_generation"] += 1
        with (evidence / f"api-{state['api_generation']}.log").open("wb") as log:
            return subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "_host", "--evidence-dir", str(evidence), "--api-port", str(args.api_port)],
                cwd=REPO, env=child_env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

    try:
        # Record ownership before starting: a failed port bind must not leave
        # an untracked created container behind.
        containers["mongo"] = docker("create", "--pull=never", "--name", f"{run_id}-mongo", "--label", f"{LABEL}={run_id}", "-p", f"127.0.0.1:{args.mongo_port}:27017", IMAGES["mongo"])
        state["containers"] = dict(containers)
        write_json(path, state)
        docker("start", containers["mongo"])
        containers["identity"] = docker(
            "create", "--pull=never", "--name", f"{run_id}-identity", "--label", f"{LABEL}={run_id}",
            "-p", f"127.0.0.1:{args.oidc_port}:8080", "--mount",
            f"type=bind,source={evidence / 'identity-realm.json'},target=/opt/keycloak/data/import/common-ground.json,readonly",
            IMAGES["identity"], "start-dev", "--import-realm", "--hostname", f"http://127.0.0.1:{args.oidc_port}",
        )
        state["containers"] = dict(containers)
        write_json(path, state)
        docker("start", containers["identity"])
        wait_http(frontend["VITE_OIDC_DISCOVERY_URL"])
        process = start_host()
        state["api_pid"] = process.pid
        state["readiness"] = wait_http(f"{state['api_url']}/api/health/ready", process=process)
        state["status"] = "ready"
        write_json(path, state)
        print(json.dumps({"status": "ready", "environment_file": str(path), "base_url": web, "api_url": state["api_url"]}), flush=True)
        while True:
            if process.poll() is not None:
                raise RuntimeError("Owned platform process stopped unexpectedly.")
            request = evidence / "request.json"
            if request.exists():
                command = json.loads(request.read_text(encoding="utf-8"))
                if command.get("run_id") != run_id:
                    raise RuntimeError("Lifecycle request does not belong to this environment.")
                request.unlink()
                if command["action"] == "stop":
                    break
                if command["action"] != "restart-api":
                    raise RuntimeError("Unknown lifecycle request.")
                state["status"] = "restarting"
                write_json(path, state)
                stop_host()
                process = start_host()
                state["api_pid"] = process.pid
                state["readiness"] = wait_http(f"{state['api_url']}/api/health/ready", process=process)
                state["status"] = "ready"
                write_json(path, state)
            time.sleep(0.25)
    except KeyboardInterrupt:
        outcome = "interrupted"
    except Exception as exc:
        outcome = "failed"
        state["failure_type"] = type(exc).__name__
        raise
    finally:
        cleanup_errors = []
        try:
            stop_host()
        except Exception:
            cleanup_errors.append("api")
        for kind, container_id in reversed(list(containers.items())):
            try:
                info = json.loads(docker("inspect", container_id))[0]
                if info["Config"]["Labels"].get(LABEL) != run_id:
                    raise RuntimeError("Container ownership changed; refusing cleanup.")
                with (evidence / f"{kind}.log").open("wb") as log:
                    subprocess.run(["docker", "logs", container_id], stdout=log, stderr=subprocess.STDOUT, timeout=15, check=False)
                docker("stop", "--time", "10", container_id)
                docker("rm", "-v", container_id)
            except Exception:
                cleanup_errors.append(kind)
        state["outcome"] = outcome
        state["status"] = "cleanup_failed" if cleanup_errors else outcome
        state["cleanup_status"] = "failed" if cleanup_errors else "complete"
        state["cleanup_errors"] = cleanup_errors
        state["finished_at"] = datetime.now(UTC).isoformat()
        write_json(path, state)
        if cleanup_errors:
            raise RuntimeError("Owned resources could not all be cleaned up; inspect environment.json.")


def request_action(action: str, evidence: Path) -> None:
    state = json.loads((evidence / "environment.json").read_text(encoding="utf-8"))
    if state["status"] != "ready":
        raise RuntimeError("Only a ready owned environment accepts lifecycle requests.")
    request = evidence / "request.json"
    if request.exists():
        raise RuntimeError("A lifecycle request is already pending.")
    temporary = evidence / "request-writing.json"
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump({"action": action, "run_id": state["run_id"]}, stream)
    temporary.rename(request)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        current = json.loads((evidence / "environment.json").read_text(encoding="utf-8"))
        if current["run_id"] != state["run_id"]:
            raise RuntimeError("Environment identity changed.")
        if action == "stop" and current["status"] == "stopped":
            return
        if action == "restart-api" and current["status"] == "ready" and current["api_generation"] > state["api_generation"]:
            return
        if current["status"] in {"cleanup_failed", "stopped", "failed", "interrupted"}:
            raise RuntimeError("Environment stopped before requested operation completed.")
        time.sleep(0.25)
    raise RuntimeError("Lifecycle request timed out; inspect owned environment logs.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "restart-api", "_host"))
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--mongo-port", type=int, default=27443)
    parser.add_argument("--oidc-port", type=int, default=28443)
    parser.add_argument("--api-port", type=int, default=18443)
    parser.add_argument("--web-port", type=int, default=14443)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    if not evidence.is_relative_to(EVIDENCE_ROOT.resolve()) or evidence == EVIDENCE_ROOT.resolve():
        parser.error("Use a new run directory under this worktree's .local/evidence/community-env/.")
    args.evidence_dir = evidence
    if any(not 1 <= port <= 65535 for port in (args.mongo_port, args.oidc_port, args.api_port, args.web_port)):
        parser.error("Service ports must be between 1 and 65535.")
    if len({args.mongo_port, args.oidc_port, args.api_port, args.web_port}) != 4:
        parser.error("Each service needs a distinct loopback port.")
    try:
        if args.action == "_host":
            asyncio.run(host(args))
        elif args.action == "start":
            run(args, evidence)
        else:
            request_action(args.action, evidence)
    except Exception as exc:
        print(f"Local acceptance environment failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
