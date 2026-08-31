from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from factory_app.workflows.AppGenerator.tools.deployment_contract import (
    generate_deployment_artifacts,
)
from factory_app.workflows.AppGenerator.tools.requirements_scanner import scan_requirements
from tests.test_continuous_deterministic_materialization import _write_bundle
from tests.test_materialized_bundle_production_runtime import _assemble_from_payload


class _QuietFileHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, json.load(response)


@pytest.mark.asyncio
async def test_materialized_generated_app_image_boots_and_serves_runtime(
    tmp_path: Path,
) -> None:
    if os.environ.get("MOZAIKS_RUN_GENERATED_APP_DOCKER_SMOKE") != "1":
        pytest.skip("set MOZAIKS_RUN_GENERATED_APP_DOCKER_SMOKE=1 for the Docker smoke")

    repo_root = Path(__file__).parents[1]
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(repo_root),
        ],
        check=True,
        cwd=repo_root,
    )
    wheels = list(wheel_dir.glob("mozaiks-*.whl"))
    assert len(wheels) == 1

    package_port = _free_port()
    handler = partial(_QuietFileHandler, directory=str(wheel_dir))
    package_server = ThreadingHTTPServer(("0.0.0.0", package_port), handler)
    package_thread = threading.Thread(target=package_server.serve_forever, daemon=True)
    package_thread.start()

    token = uuid.uuid4().hex[:12]
    image = f"mozaiks-generated-app-smoke:{token}"
    container = f"mozaiks-generated-app-smoke-{token}"
    host_port = _free_port()
    app_root = tmp_path / "generated-app"
    logs = ""
    try:
        files, _ = await _assemble_from_payload()
        requirements = scan_requirements(files).splitlines()
        requirements[requirements.index("mozaiks")] = (
            f"http://host.docker.internal:{package_port}/{wheels[0].name}"
        )
        files["requirements.txt"] = "\n".join(requirements) + "\n"
        deployment = generate_deployment_artifacts(
            app_id="deterministic-reports",
            deployment_profile="production_container",
            include_dockerfiles=True,
            include_workflow=False,
            include_compose=False,
        )
        assert deployment["deploy_target_spec_errors"] == []
        assert deployment["bundle_errors"] == []
        files.update(deployment["artifacts"])
        _write_bundle(app_root, files)

        subprocess.run(
            [
                "docker",
                "build",
                "--add-host=host.docker.internal:host-gateway",
                "--tag",
                image,
                str(app_root),
            ],
            check=True,
        )
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container,
                "--add-host=host.docker.internal:host-gateway",
                "--publish",
                f"{host_port}:8000",
                "--env",
                "AUTH_ENABLED=false",
                "--env",
                "RATE_LIMIT_ENABLED=false",
                "--env",
                "OPENAI_API_KEY=sk-test-placeholder",
                "--env",
                "MONGO_URI=mongodb://host.docker.internal:27017/ci_generated_app",
                image,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        deadline = time.monotonic() + 90
        health: tuple[int, Any] | None = None
        while time.monotonic() < deadline:
            try:
                health = _request_json(f"http://127.0.0.1:{host_port}/api/health")
                if health[0] == 200 and health[1].get("status") == "healthy":
                    break
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                pass
            time.sleep(2)
        else:
            logs = subprocess.run(
                ["docker", "logs", container],
                check=False,
                capture_output=True,
                text=True,
            ).stdout
            pytest.fail(f"generated app image did not become healthy:\n{logs}")

        assert health is not None
        assert health[0] == 200
        assert health[1]["status"] == "healthy"
        page_status, page = _request_json(f"http://127.0.0.1:{host_port}/api/pages/reports")
        assert page_status == 200
        assert page["sections"][0]["config"]["api_endpoint"] == (
            "/api/modules/reports/list_reports"
        )

        action_status, action = _request_json(
            f"http://127.0.0.1:{host_port}/api/modules/reports/list_reports",
            payload={"params": {}},
        )
        assert action_status == 200
        assert action == {"reports": [{"id": "report-1", "title": "Readiness", "status": "ready"}]}
    finally:
        package_server.shutdown()
        package_server.server_close()
        package_thread.join(timeout=5)
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["docker", "image", "rm", "--force", image],
            check=False,
            capture_output=True,
        )
