"""Opt-in browser acceptance for Projects/Tasks with the selected Messaging pack.

From a clean committed checkout with dependencies, cached Chromium and the
Community driver's Docker images installed, set MOZAIKS_PAIRED_BROWSER=1 and run
``python -m pytest -q --no-cov tests/test_paired_experience_browser.py``.
Each presentation gets a disposable Mongo/Keycloak pair and fresh frontend build
through the existing Community owner. Evidence remains under .local/evidence/
paired-browser, with private runtime files under the owner's community-env root.
Private runtime and browser-evidence files may contain synthetic login
credentials or session traces and must stay local. No live LLM generation is
claimed, and the admitted fixture bytes are never repaired by this test.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import socket
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("MOZAIKS_PAIRED_BROWSER") != "1",
    reason="Set MOZAIKS_PAIRED_BROWSER=1 for disposable real-host browser acceptance.",
)


def _live_owner():
    spec = importlib.util.spec_from_file_location(
        "paired_browser_live_owner",
        ROOT / "examples/canonical-apps/community/scripts/live_environment.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ports():
    # Reserve together to avoid selecting one ephemeral port more than once.
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(4)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]


def _identity_realm_fixture(destination: Path, files: dict[str, str]) -> Path:
    """Write private synthetic identities with the admitted public action scopes.

    Keep this input outside the driver's fresh run directory. Passwords and
    tokens never belong in the public replay receipt or generated app bundle.
    """
    realm = json.loads((ROOT / "examples/canonical-apps/community/tests/identity-realm.json").read_text(encoding="utf-8"))
    permissions = set()
    for path, content in files.items():
        if path.startswith("modules/") and path.endswith("/module.yaml"):
            manifest = yaml.safe_load(content)
            for action in manifest.get("actions", []):
                if action.get("api_surface") not in {"internal", "admin_internal"}:
                    permissions.update(action.get("permissions", []))
    base_scopes = {"basic", "profile", "email"}
    realm["clientScopes"] = [scope for scope in realm["clientScopes"] if scope["name"] in base_scopes]
    realm["clientScopes"].extend({
        "name": permission, "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "false"},
    } for permission in sorted(permissions - base_scopes))
    realm["clients"][0]["defaultClientScopes"] = ["basic", "profile", "email", *sorted(permissions - base_scopes)]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(realm, stream, indent=2)
        stream.write("\n")
    return destination


def _profile(presentation, candidate):
    routes = [
        {"path": "/projects", "component": "SchemaPage", "schema": "projects",
         "kind": "declarative", "heading": "Projects"},
        {"path": "/messages", "component": "Messages", "kind": "custom", "heading": "Messages"},
        {"path": "/login", "component": "LoginPage", "kind": "auth",
         "excluded_reason": "Real sign-in is exercised before every required app route."},
        {"path": "/auth/callback", "component": "AuthCallbackPage", "kind": "auth",
         "excluded_reason": "The real PKCE callback is exercised by every sign-in; it is not a standalone page."},
        {"path": "/me", "component": "ProfilePage", "kind": "extension",
         "excluded_reason": "Authenticated profile editing and reload persistence are exercised within each /projects case."},
        {"path": "/u/:username", "component": "ProfilePage", "kind": "extension",
         "excluded_reason": "Inherited public profile lookup is outside this workload; not evaluated."},
    ]
    routes.append(
        {"path": "/tasks", "component": "SchemaPage", "schema": "tasks",
         "kind": "declarative", "heading": "Task records"}
        if presentation == "A" else
        {"path": "/board", "component": "PlanningBoard", "kind": "custom",
         "heading": "The work, in motion."}
    )
    return {
        "candidate": candidate,
        "routes": routes,
        "cases": [
            {"id": f"route:{route['path']}", "route": route["path"], "project": project}
            for route in routes if "excluded_reason" not in route
            for project in ("generated-desktop", "generated-mobile")
        ],
    }


def _sanitized(value, passwords):
    if isinstance(value, dict):
        return {key: _sanitized(item, passwords) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitized(item, passwords) for item in value]
    if isinstance(value, str):
        for password in passwords:
            if password:
                value = value.replace(password, "[redacted]")
        value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[redacted]", value)
        return re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [redacted]", value)
    return value


@pytest.fixture(scope="module")
def paired_browser_evidence():
    destination = ROOT / ".local/evidence/paired-browser" / uuid4().hex
    destination.mkdir(parents=True, exist_ok=False)
    return destination


@pytest.mark.asyncio
@pytest.mark.parametrize("presentation", ["A", "B"])
async def test_admitted_paired_app_on_owned_browser(presentation, paired_browser_evidence):
    live = _live_owner()
    case_root = paired_browser_evidence / presentation
    case_root.mkdir()
    workspace = case_root / "workspace"
    app_root = workspace / "app"
    private = live.EVIDENCE_ROOT / f"paired-{paired_browser_evidence.name}-{presentation}"
    profile_path = case_root / "profile.json"
    result_path = case_root / "result.json"
    clean_env = live.clean_environment(dict(os.environ), {
        "PYTHON_DOTENV_DISABLED": "1",
        "LOGS_BASE_DIR": str(case_root / "replay-logs"),
        "AG2_RUNTIME_LOG_FILE": str(case_root / "replay-ag2.log"),
        "MOZAIKS_GENERATED_ARTIFACTS_PATH": str(case_root / "replay-generated"),
    })
    with patch.dict(os.environ, clean_env, clear=True):
        from mozaiksai import __version__ as mozaiks_version
        from mozaiksai.core.workflow.context.frozen import detach
        from scripts import generated_ui_acceptance as acceptance
        from tests import test_paired_experience_demo_960 as replay

        source_sha = acceptance._source_commit(ROOT)
        files, context = await replay._scripted_replay(presentation, build_context_root=workspace / "build_context")
        assert context.get("app_task_batch_status") == "completed"
        app_root.mkdir(parents=True)
        for relative, content in files.items():
            destination = app_root / relative
            assert destination.resolve().is_relative_to(app_root.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content.encode("utf-8"))
        hashes = {relative: hashlib.sha256(content.encode("utf-8")).hexdigest()
                  for relative, content in sorted(files.items())}
        binding = detach(context.get("run_build_binding"))
        manifest = json.loads(files["app.json"])
        assert binding["target_app_id"] == manifest["appId"]
        candidate = {
            "app_id": manifest["appId"], "revision": binding["build_id"],
            "source_sha": source_sha, "bundle_sha256": acceptance.bundle_digest(app_root),
        }
        # Keep the original replay receipt fields; add the admitted build binding.
        live.write_json(case_root / "replay-receipt.json", {
            "evidence_kind": "scripted_worker_outputs_through_real_ag2_task_admission",
            "live_llm_calls": False, "presentation": presentation,
            "source_commit": source_sha,
            "ag2_version": importlib.metadata.version("ag2"),
            "mozaiks_runtime_version": mozaiks_version,
            "python_version": platform.python_version(),
            "domain_contract_sha256": hashlib.sha256(
                json.dumps(replay._domain_contract(), sort_keys=True).encode("utf-8"),
            ).hexdigest(),
            "board_source_sha256": hashlib.sha256(replay.BOARD_SOURCE.read_bytes()).hexdigest(),
            "admitted_task_ids": sorted(
                key for key in detach(context.get("app_task_batch_results")) if not key.startswith("_")
            ),
            "files": hashes, "run_build_binding": binding,
            "private_environment_path": str(private / "environment.json"),
            **replay._pack_receipt(files, context),
        })
        live.write_json(case_root / "files.sha256.json", hashes)
        profile = _profile(presentation, candidate)
        live.write_json(profile_path, profile)
        acceptance.validate_real_host_profile(profile, app_root=app_root, repo_root=ROOT)
        mongo, oidc, api, web = _ports()
        identity_realm = _identity_realm_fixture(case_root / "identity-private/identity-realm.json", files)
        args = argparse.Namespace(
            workspace=workspace, mongo_port=mongo, oidc_port=oidc,
            api_port=api, web_port=web, native_callback=None, client_origin=None,
            identity_realm=identity_realm,
        )

        def exercise(state, backend, runtime_env):
            with live.frontend_preview(state, private, runtime_env) as preview:
                owned = acceptance.OwnedBrowserLaunch.from_driver(
                    app_root=app_root, repo_root=ROOT, candidate=candidate,
                    state=state, backend=backend, runtime_env=runtime_env, preview=preview,
                )
                browser_env = live.clean_environment(dict(os.environ), {
                    "PYTHON_DOTENV_DISABLED": "1",
                    "COMMUNITY_LIVE_CONFIG": str(private / "environment.json"),
                })
                with patch.dict(os.environ, browser_env, clear=True):
                    result = acceptance.run_playwright_acceptance(
                        app_root=app_root, repo_root=ROOT, mode="real_host",
                        profile_path=profile_path, base_url=state["base_url"],
                        spec_path="playwright/generated-ui/paired-app.real-host.acceptance.spec.js",
                        owned_launch=owned, evidence_dir=case_root / "browser-evidence",
                        timeout_seconds=600,
                    )
                result = _sanitized(result, [user["password"] for user in state["users"]])
                live.write_json(result_path, result)
                return result

        # run() creates new labeled containers, with no reused volumes, for each
        # presentation and removes only those owned resources in its finally block.
        try:
            result = live.run(args, private, exercise=exercise)
        except Exception as exc:
            live.write_json(case_root / "driver-failure.json", {
                "success": False, "candidate": candidate, "failure_type": type(exc).__name__,
                "profile_path": str(profile_path), "private_logs": str(private),
            })
            pytest.fail(f"Owned browser driver failed ({type(exc).__name__}); inspect {case_root}", pytrace=False)
        assert acceptance.bundle_digest(app_root) == candidate["bundle_sha256"]
        print(f"Paired {presentation} browser evidence: {case_root}")
        assert result["success"], f"Browser acceptance rejected this candidate; inspect {result_path}"
        assert result["runtime_identity"] == "verified_owned_launch"
