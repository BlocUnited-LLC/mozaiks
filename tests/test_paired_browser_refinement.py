"""Opt-in real Studio refinement of an accepted paired B browser candidate.

Set MOZAIKS_PAIRED_REFINEMENT=1, MOZAIKS_PAIRED_BROWSER_BASELINE to the accepted
B evidence directory, MOZAIKS_APP_RUNTIME_IMAGE_ID to an immutable local image,
and MOZAIKS_PAIRED_VALIDATOR_SOURCE_SHA to that image's committed source. Run
this module alone from a clean checkout. Image source labels and runtime-source
compatibility are checked against the checkout.

Only AG2 provider responses are scripted. Keycloak login, Mongo/GridFS, Studio
authorization, scoped finalization and Docker candidate validation remain real.
The saved child receives real browser acceptance before the owner accepts its
review draft. It is not promoted. No live model creativity is claimed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = "ui/pages/custom/board.jsx"
REQUEST = "On mobile, open task details in a bottom panel. Preserve the desktop workspace, routes, brand, and underlying task behavior."
pytestmark = pytest.mark.skipif(
    os.environ.get("MOZAIKS_PAIRED_REFINEMENT") != "1",
    reason="Set MOZAIKS_PAIRED_REFINEMENT=1 for real local refinement acceptance.",
)
RUNTIME_PATHS = (
    "pyproject.toml", "setup.py", "MANIFEST.in", "infra/docker/Dockerfile.preview",
    "mozaiks", "mozaiksai", "mozaiks_cli", "logs", "factory_app", "web_shell", "chat-ui",
    ":(exclude)web_shell/playwright/**",
)


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _hashes(files):
    return {path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in sorted(files.items())}


def _baseline_packs(receipt, files):
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        verified_pack_template_inventory,
    )

    packs = receipt["capability_packs"]
    saved = json.loads(files[".mozaiks/pack_provenance.json"])["packs"]
    fields = ("pack_id", "version", "digest", "source")
    identities = [{key: pack[key] for key in fields} for pack in saved]
    fresh = [{key: pack[key] for key in fields} for pack in verified_pack_template_inventory(packs)]
    assert packs and {pack["pack_id"] for pack in saved} == {"messaging"}
    assert len(identities) == len(packs) == len({pack["pack_id"] for pack in saved})
    assert sorted(identities, key=lambda pack: pack["pack_id"]) == sorted(fresh, key=lambda pack: pack["pack_id"])
    return packs


def _compatible_sources(source, current):
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{source}^{{commit}}"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    changed = subprocess.run(
        ["git", "diff", "--name-only", resolved, current, "--", *RUNTIME_PATHS],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert not changed, f"Acceptance runtime sources differ from {resolved}: {changed}"
    return resolved


def _inspect_image(image_id, source_sha):
    from mozaiksai.core.adapters.local_docker_cli import _docker_cli_env, _docker_prefix

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
    fields = {
        "image_id": ".Id", "source_sha": '(index .Config.Labels "org.opencontainers.image.revision")',
        "source_dirty": '(index .Config.Labels "io.mozaiks.source.dirty")',
        "context_sha256": '(index .Config.Labels "io.mozaiks.source.context.sha256")',
    }
    template = "{" + ",".join(json.dumps(key) + ":{{json " + value + "}}" for key, value in fields.items()) + "}"
    with tempfile.TemporaryDirectory(prefix="paired-refinement-docker-") as config:
        inspected = subprocess.run(
            [*_docker_prefix(config), "image", "inspect", image_id, "--format", template],
            env=_docker_cli_env(), capture_output=True, text=True, timeout=20, check=True,
        )
    identity = json.loads(inspected.stdout)
    assert identity["image_id"] == image_id and identity["source_sha"] == source_sha
    assert identity["source_dirty"] == "false"
    assert re.fullmatch(r"[0-9a-f]{64}", identity["context_sha256"])
    return identity


def _accepted_baseline(directory):
    from scripts.generated_ui_acceptance import bundle_digest

    result, profile = _json(directory / "result.json"), _json(directory / "profile.json")
    receipt = _json(directory / "replay-receipt.json")
    candidate = profile["candidate"]
    assert result["success"] is True and result["runtime_identity"] == "verified_owned_launch"
    assert result["candidate"] == candidate
    assert receipt["presentation"] == "B" and receipt["live_llm_calls"] is False
    assert receipt["source_commit"] == candidate["source_sha"]
    expected = _json(directory / "files.sha256.json")
    assert expected == receipt["files"]
    app_root = (directory / "workspace/app").resolve(strict=True)
    files = {}
    for relative in expected:
        path = app_root / relative
        assert path.resolve(strict=True).is_relative_to(app_root) and not path.is_symlink()
        files[relative] = path.read_bytes().decode("utf-8")
    assert _hashes(files) == expected
    assert bundle_digest(app_root) == candidate["bundle_sha256"]
    assert PAGE in files and "ui/pages/projects.yaml" in files
    _baseline_packs(receipt, files)
    return files, candidate


def _mobile_board(source):
    """Derive one scripted response from the exact accepted board bytes."""
    substitutions = [
        ("useCallback, useEffect, useState", "useCallback, useEffect, useRef, useState"),
        ("import { moduleAction }", "import { Modal } from '@mozaiks/chat-ui/ui';\nimport { moduleAction }"),
        ("  const [loading, setLoading] = useState(true);", """  const [loading, setLoading] = useState(true);
  const detailsTrigger = useRef(null);
  const [mobileDetails, setMobileDetails] = useState(false);
  const [isMobile, setIsMobile] = useState(() => window.matchMedia('(max-width: 639px)').matches);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 639px)');
    const update = () => setIsMobile(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  function closeDetails() {
    setMobileDetails(false);
    requestAnimationFrame(() => detailsTrigger.current?.focus());
  }"""),
        ("onClick={() => setSelected(task)}", "onClick={(event) => { detailsTrigger.current = event.currentTarget; setSelected(task); setMobileDetails(isMobile); }}"),
        ('<aside className="self-start', '<aside className="hidden sm:block self-start'),
    ]
    for before, after in substitutions:
        assert source.count(before) == 1, f"Accepted board does not match the scoped patch anchor: {before}"
        source = source.replace(before, after)
    anchor = "          </aside>"
    assert source.count(anchor) == 1
    return source.replace(anchor, anchor + """
          <Modal id="mobile-task-details" title={active?.title || 'Task details'} description="Task details"
            open={isMobile && mobileDetails && Boolean(active)} onClose={closeDetails}
            className="border-[#dce5e5] bg-white text-[#1b2831]">
            {active && <div><p className="whitespace-pre-wrap text-[#53646e]">{active.description || 'No description yet.'}</p><dl className="mt-5 border-t border-[#e2e9e9] pt-4 text-sm"><dt className="text-[#647782]">Status</dt><dd className="mt-1 font-semibold capitalize">{active.status}</dd><dt className="mt-4 text-[#647782]">Project</dt><dd className="mt-1 font-semibold">{active.project_id}</dd></dl></div>}
          </Modal>""")


LOGIN_SCRIPT = r"""
const { chromium, expect } = require('@playwright/test');
(async () => {
  let input = ''; for await (const chunk of process.stdin) input += chunk;
  const config = JSON.parse(input), issuer = new URL(config.auth_issuer);
  const browser = await chromium.launch();
  const tokens = {};
  try {
    for (const username of ['alice', 'bob']) {
      const user = config.users.find(value => value.username === username);
      if (!user) throw new Error('Missing synthetic login identity');
      const context = await browser.newContext();
      try {
        const page = await context.newPage();
        const target = new URL('/board', config.base_url).href;
        await page.goto(target);
        await page.getByRole('button', {name: 'Sign in', exact: true}).click();
        await expect(page).toHaveURL(url => url.origin === issuer.origin
          && url.pathname === issuer.pathname + '/protocol/openid-connect/auth');
        await page.getByLabel('Username or email', {exact: true}).fill(username);
        await page.getByLabel('Password', {exact: true}).fill(user.password);
        await page.getByRole('button', {name: 'Sign In', exact: true}).click();
        await expect(page).toHaveURL(target);
        await expect.poll(() => page.evaluate(async () => Boolean(
          (await window.mozaiksAuth?.getCurrentUser())?.id))).toBe(true);
        tokens[username] = await page.evaluate(() => window.mozaiksAuth.getAccessToken());
      } finally { await context.close(); }
    }
    process.stdout.write(JSON.stringify(tokens));
  } finally { await browser.close(); }
})().catch(() => { process.stderr.write('Real browser login failed\n'); process.exitCode = 1; });
"""


def _user_prompt(provider):
    assert len(provider.calls) == 1
    messages = [message for message in provider.calls[0]["messages"] if message["role"] == "user"]
    content = messages[-1]["content"]
    return content if isinstance(content, str) else "\n".join(item["text"] for item in content if item.get("type") == "text")


def _assert_delivered_context(providers, files, baseline_id):
    classifier = _user_prompt(providers["classifier"])
    assert REQUEST in classifier and baseline_id in classifier
    planner = json.JSONDecoder().raw_decode(_user_prompt(providers["planner"]).split("payload_json:\n", 1)[1])[0]
    assert planner["raw_user_request"] == REQUEST and planner["allowed_paths"] == [PAGE]
    assert "board" in json.dumps(planner["available_targets"])
    worker = json.JSONDecoder().raw_decode(_user_prompt(providers["generator"]).split("payload_json:\n", 1)[1])[0]
    assert worker["user_request"] == REQUEST and worker["affected_paths"] == [PAGE]
    assert worker["current_files"] == {PAGE: files[PAGE]}
    read_only = worker["read_only_files"]
    for path in ("app.json", "brand/theme_config.json", "data/contract.json", "ui/route_manifest.json",
                 "ui/index.js", "modules/messages/module.yaml", ".mozaiks/pack_provenance.json"):
        assert read_only[path] == files[path]
    assert all(files[path] == content for path, content in read_only.items())
    assert PAGE not in read_only
    assert {pack["pack_id"] for pack in json.loads(read_only[".mozaiks/pack_provenance.json"])["packs"]} == {"messaging"}


def _scripted_harness(files, store, output_root):
    from ag2 import Agent

    from mozaiksai.control_plane.config import load_control_plane_config
    from mozaiksai.control_plane.implementations.change_classifier import LLMChangeClassifier
    from mozaiksai.control_plane.implementations.coding_worker import ScopedRefinementCodingWorker
    from mozaiksai.control_plane.implementations.contract_surface_planner import (
        ContractSurfacePlanner,
    )
    from mozaiksai.control_plane.implementations.harness_decision import (
        FirstPartyHarnessDecisionPolicy,
    )
    from mozaiksai.control_plane.implementations.orchestration_control import (
        OrchestrationControlHarness,
    )
    from mozaiksai.control_plane.implementations.refinement_router import (
        RefinementTriggerRouteResolver,
    )
    from mozaiksai.control_plane.implementations.surface_regeneration_worker import (
        SurfaceRegenerationWorker,
    )
    from mozaiksai.control_plane.loader import load_refinement_harness
    from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
    from tests.test_ui_worker_context_acceptance import _ScriptedProvider

    config = load_control_plane_config(ROOT / "factory_app/app")
    pack = load_refinement_harness(app_root=ROOT / "factory_app/app")
    options = {"config_loader": lambda: config, "pack_loader": lambda: pack}
    outputs = {
        "classifier": {"change_class": "design", "rationale": REQUEST, "confidence": 0.99, "signals": ["mobile_task_details"]},
        "planner": {"surfaces": [{"kind": "page_binding", "target_id": "board", "target_kind": "page",
                                  "rationale": REQUEST, "confidence": 0.99, "generation_hint": REQUEST}],
                    "summary": REQUEST, "confidence": 0.99, "requires_schema_migration": False,
                    "fallback_to_workflow": False, "fallback_reason": None},
        "generator": {"summary": REQUEST, "rationale": "Use the shipped mobile Modal and preserve the desktop inspector.",
                      "updated_files": [{"path": PAGE, "content": _mobile_board(files[PAGE])}]},
    }
    providers = {name: _ScriptedProvider(output) for name, output in outputs.items()}

    def runner(name):
        def factory(system_prompt, llm_config, *, middleware):
            return Agent(f"scripted_{name}", system_prompt, config=providers[name], middleware=middleware)
        return AG2StructuredAgentRunner(agent_factory=factory)

    return OrchestrationControlHarness(
        refinement_resolver=RefinementTriggerRouteResolver(
            classifier=LLMChangeClassifier(agent_runner=runner("classifier"), **options), pack_loader=lambda: pack,
        ),
        contract_surface_planner=ContractSurfacePlanner(agent_runner=runner("planner"), **options),
        surface_regeneration_worker=SurfaceRegenerationWorker(agent_runner=runner("generator"), **options),
        coding_worker=ScopedRefinementCodingWorker(artifact_store=store, output_root=output_root, **options),
        decision_policy=FirstPartyHarnessDecisionPolicy(pack_loader=lambda: pack),
        config_loader=lambda: config,
    ), providers


def _login_tokens(config):
    from tests.test_paired_experience_browser import _live_owner

    login = subprocess.run(
        [shutil.which("node") or "node", "-e", LOGIN_SCRIPT], cwd=ROOT / "web_shell",
        env=_live_owner().clean_environment(dict(os.environ), {}),
        input=json.dumps(config), capture_output=True, text=True, timeout=120, check=False,
    )
    assert login.returncode == 0, "Real browser token acquisition failed; tokens were not logged"
    tokens = json.loads(login.stdout)
    assert set(tokens) == {"alice", "bob"} and all(isinstance(value, str) and value for value in tokens.values())
    return tokens


async def _refine(case_root, login_config):
    import httpx

    from factory_app.app.modules.app_registry.backend.service import AppRegistryService
    from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
    from factory_app.workflows.AppGenerator.tools.generate_and_download import (
        _register_app_bundle_artifact_version,
    )
    from mozaiksai.core.artifacts import (
        ArtifactLifecycleStatus,
        ArtifactValidationStatus,
        get_artifact_store,
    )
    from mozaiksai.core.artifacts.content_store import read_verified_artifact_bundle
    from mozaiksai.core.auth import get_auth_adapter
    from mozaiksai.core.core_config import close_mongo_client
    from mozaiksai.core.session.build_binding import RunBuildBinding
    from mozaiksai.core.validation.generated_app import validate_generated_app_candidate
    from mozaiksai.hosts import studio
    from tests.test_paired_experience_browser import _live_owner

    live = _live_owner()
    assert os.environ["AUTH_ENABLED"] == "true"
    assert not any(key.startswith(("OPENAI_", "ANTHROPIC_", "AZURE_OPENAI_", "GEMINI_", "GOOGLE_", "E2B_"))
                   for key in os.environ), "Refinement acceptance must not inherit model-provider credentials"
    facts = _json(case_root / "inputs.json")
    files, _ = _accepted_baseline(Path(facts["baseline_directory"]))
    replay_receipt = _json(Path(facts["baseline_directory"]) / "replay-receipt.json")
    packs = _baseline_packs(replay_receipt, files)
    app_id = json.loads(files["app.json"])["appId"]
    validation = await validate_generated_app_candidate(files=files, app_id=app_id, validation_strategy="docker", capability_packs=packs)
    live.write_json(case_root / "baseline-validation.json", validation)
    assert validation["validation_status"] == "passed", "Real baseline validator rejected the accepted browser candidate"
    assert validation["app_bundle_acceptance_result"]["passed"] is True
    assert validation["app_validation_result"]["validation_status"] == "passed"
    # Acquire fresh browser tokens after the potentially lengthy baseline gate.
    tokens = _login_tokens(login_config)
    claims = await get_auth_adapter().validate_token(tokens["alice"])
    owner_id = claims.user_id
    assert claims.app_id == app_id and owner_id
    registry, store = AppRegistryService(), get_artifact_store()
    build_id = f"build_{uuid4().hex}"
    record = (await registry.create_app_record(
        owner_user_id=owner_id, app_id=app_id, chat_app_id=claims.app_id, name="Paired Projects",
        status="building", current_build_run={"build_id": build_id, "phase": "genesis"},
    ))["app"]
    binding = RunBuildBinding(build_registry_id=record["build_registry_id"], target_app_id=app_id,
                              build_id=build_id, phase="genesis")
    bundle_name = f"accepted_browser_{uuid4().hex}"
    archive = case_root / f"{bundle_name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path, content in sorted(files.items()):
            zipped.writestr(f"{bundle_name}/{path}", content.encode("utf-8"))
    acceptance, build = validation["app_bundle_acceptance_result"], validation["app_validation_result"]
    baseline = await _register_app_bundle_artifact_version(
        app_id=app_id, user_id=owner_id, workflow_name="AppGenerator", chat_id=None,
        bundle_name=bundle_name, zip_path=archive, app_dir=case_root / "workspace/app",
        written_paths=sorted(files), artifact_store=store,
        context_variables={"run_build_binding": binding.model_dump(), "capability_packs": packs,
                           "app_bundle_acceptance_status": acceptance["status"],
                           "app_bundle_acceptance_result": acceptance,
                           "app_bundle_validation_evidence": acceptance.get("validation_evidence"),
                           "app_validation_status": build["validation_status"],
                           "app_validation_strategy_used": build["validation_strategy"], "app_validation_result": build},
    )
    assert baseline.lifecycle_status == ArtifactLifecycleStatus.CURRENT
    assert baseline.validation_status == ArtifactValidationStatus.PASSED
    assert baseline.commit_metadata.metadata["content_backend"] == "gridfs"
    assert await read_verified_artifact_bundle(baseline) == archive.read_bytes()
    saved_files, diagnostics = await read_artifact_bundle(baseline, retain_svg_text=True)
    assert not diagnostics and saved_files == files
    reviewed = await registry.update_build_status(
        owner_user_id=owner_id, build_registry_id=binding.build_registry_id, status="review",
        artifact_version_id=baseline.id, bundle_path=str(case_root / "workspace/app"), expected_build_id=build_id,
    )
    assert reviewed["success"] and reviewed["app"]["lifecycle_state"] == "review"
    harness, providers = _scripted_harness(files, store, Path(facts["refinement_output_root"]))
    payload = {"build_registry_id": binding.build_registry_id, "trigger_source": "refinement",
               "trigger_payload": {"refinement_request": {"build_family": "app_bundle", "build_key": "app_bundle",
                                    "build_record_id": baseline.id, "raw_user_request": REQUEST, "source_surface": "app_workbench"},
                                   "coding_request": {"files": {PAGE: ""}}}}
    assert not studio.app.dependency_overrides, "Real route acceptance cannot use dependency overrides"
    try:
        with patch.object(studio, "get_orchestration_control_harness", return_value=harness):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app), base_url="http://testserver", timeout=None) as client:
                anonymous = await client.post("/api/workflows/trigger", json=payload)
                other = await client.post("/api/workflows/trigger", json=payload, headers={"Authorization": f"Bearer {tokens['bob']}"})
                assert anonymous.status_code == 401 and other.status_code == 404
                response = await client.post("/api/workflows/trigger", json=payload, headers={"Authorization": f"Bearer {tokens['alice']}"})
        live.write_json(case_root / "endpoint-result.json", {"status_code": response.status_code, "body": response.json()})
        for name, provider in providers.items():
            requests = [{"prompt": call["prompt"], "messages": call["messages"]} for call in provider.calls]
            encoded = json.dumps(requests)
            assert all(token not in encoded for token in tokens.values())
            live.write_json(case_root / f"provider-{name}-requests.json", requests)
        assert response.status_code == 200, "Real Studio refinement route rejected the request; inspect endpoint-result.json"
        result = response.json()
        assert result["execution_mode"] == "surface_regeneration" and result["surface_result"]["status"] == "success"
        child = await store.get_build_record(app_id=app_id, build_record_id=result["surface_result"]["metadata"]["build_record_id"])
        assert child.parent_build_record_id == baseline.id and child.lifecycle_status == ArtifactLifecycleStatus.DRAFT
        assert child.validation_status == ArtifactValidationStatus.PASSED and child.app_validation_status == "passed"
        assert child.commit_metadata.metadata["content_backend"] == "gridfs"
        child_files, diagnostics = await read_artifact_bundle(child, retain_svg_text=True)
        assert not diagnostics and set(child_files) == set(files)
        assert {path for path in files if child_files[path] != files[path]} == {PAGE}
        assert child_files[PAGE] == providers["generator"].output["updated_files"][0]["content"]
        assert child.commit_metadata.metadata["capability_packs"] == packs
        provenance = json.loads(files[".mozaiks/pack_provenance.json"])
        retained_pack_paths = sorted({entry["path"] for pack in provenance["packs"]
                                      for entry in pack["materialized_owned_files"]})
        assert retained_pack_paths
        assert all(child_files[path] == files[path] for path in retained_pack_paths)
        current = (await registry.get_app_record(owner_user_id=owner_id, build_registry_id=binding.build_registry_id))["app"]
        assert current["lifecycle_state"] == "review" and current["current_build_run"]["artifact_version_id"] == child.id
        assert result["refinement_session_id"] and result["review_continuation"] is None
        _assert_delivered_context(providers, files, baseline.id)
        child_root = case_root / "refined-workspace/app"
        for path, content in child_files.items():
            target = child_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
        (case_root / "child.zip").write_bytes(await read_verified_artifact_bundle(child))
        live.write_json(case_root / "child-files.sha256.json", _hashes(child_files))
        live.write_json(case_root / "baseline-record.json", baseline.model_dump(mode="json"))
        live.write_json(case_root / "child-record.json", child.model_dump(mode="json"))
        live.write_json(case_root / "result.json", {
            "success": True, "evidence_kind": "authorized_studio_refinement_with_scripted_ag2_providers",
            "live_llm_calls": False, "browser_acceptance": "not_evaluated_for_child", "capability_packs": packs,
            "source": facts, "request": REQUEST, "permitted_paths": [PAGE], "changed_paths": [PAGE],
            "retained_paths": sorted(path for path in files if child_files[path] == files[path]),
            "retained_pack_paths": retained_pack_paths, "baseline_files": _hashes(files),
            "design_source": replay_receipt["design_source"],
            "baseline_build_record_id": baseline.id, "child_build_record_id": child.id,
            "refinement_session_id": result["refinement_session_id"], "registry": current,
            "anonymous_status": anonymous.status_code, "non_owner_status": other.status_code,
            "provider_calls": {name: {"count": len(provider.calls),
                                      "requests_sha256": hashlib.sha256((case_root / f"provider-{name}-requests.json").read_bytes()).hexdigest()}
                               for name, provider in providers.items()},
            "child_workspace": str(child_root.parent), "child_files": _hashes(child_files),
        })
    finally:
        close_mongo_client()


async def _accept(case_root, login_config):
    import httpx

    from mozaiksai.core.artifacts import ArtifactLifecycleStatus, get_artifact_store
    from mozaiksai.core.artifacts.content_store import read_verified_artifact_bundle
    from mozaiksai.core.core_config import close_mongo_client
    from mozaiksai.hosts import studio
    from tests.test_paired_experience_browser import _live_owner

    live = _live_owner()
    result = _json(case_root / "result.json")
    browser = _json(case_root / "child-browser-result.json")
    assert browser["success"] and browser["runtime_identity"] == "verified_owned_launch"
    assert browser["candidate"] == _json(case_root / "child-browser-profile.json")["candidate"]
    assert browser["candidate"]["revision"] == result["child_build_record_id"]
    assert not studio.app.dependency_overrides
    tokens = _login_tokens(login_config)
    app_id = result["source"]["candidate"]["app_id"]
    child_id = result["child_build_record_id"]
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app), base_url="http://testserver", timeout=None) as client:
            response = await client.post(
                f"/api/studio/build/artifacts/{child_id}/accept",
                params={"build_registry_id": result["registry"]["build_registry_id"]},
                json={"notes": "Accepted after owned desktop and mobile browser checks of the provider-scripted refinement."},
                headers={"Authorization": f"Bearer {tokens['alice']}"},
            )
        live.write_json(case_root / "acceptance-endpoint-result.json", {"status_code": response.status_code, "body": response.json()})
        assert response.status_code == 200 and response.json()["accepted"] is True
        store = get_artifact_store()
        child = await store.get_build_record(app_id=app_id, build_record_id=child_id)
        assert child.lifecycle_status == ArtifactLifecycleStatus.CURRENT
        assert await read_verified_artifact_bundle(child) == (case_root / "child.zip").read_bytes()
        sessions = await store.list_refinement_sessions(app_id=app_id, result_build_record_id=child_id, limit=20)
        session = next(item for item in sessions if item.id == result["refinement_session_id"])
        assert session.status.value == "accepted"
        live.write_json(case_root / "accepted-child-record.json", child.model_dump(mode="json"))
        live.write_json(case_root / "accepted-refinement-session.json", session.model_dump(mode="json"))
        result.update({"browser_acceptance": "passed", "child_browser_candidate": browser["candidate"],
                       "review_acceptance": "accepted", "promotion": "not_requested"})
        live.write_json(case_root / "result.json", result)
    finally:
        close_mongo_client()


def _child_browser(case_root, source_sha):
    from scripts import generated_ui_acceptance as acceptance
    from tests.test_paired_experience_browser import (
        _identity_realm_fixture,
        _live_owner,
        _ports,
        _profile,
        _sanitized,
    )

    live = _live_owner()
    result = _json(case_root / "result.json")
    app_root = Path(result["child_workspace"]) / "app"
    assert _hashes({path: (app_root / path).read_bytes().decode("utf-8") for path in result["child_files"]}) == result["child_files"]
    candidate = {"app_id": result["source"]["candidate"]["app_id"], "revision": result["child_build_record_id"],
                 "source_sha": source_sha, "bundle_sha256": acceptance.bundle_digest(app_root)}
    profile_path = case_root / "child-browser-profile.json"
    live.write_json(profile_path, _profile("B", candidate))
    acceptance.validate_real_host_profile(_json(profile_path), app_root=app_root, repo_root=ROOT)
    private = live.EVIDENCE_ROOT / f"paired-refined-child-{case_root.name}"
    identity_realm = _identity_realm_fixture(live.EVIDENCE_ROOT / "identity-inputs" / f"child-{case_root.name}.json", {
        path: (app_root / path).read_text(encoding="utf-8") for path in result["child_files"]
    })
    mongo, oidc, api, web = _ports()
    args = argparse.Namespace(workspace=app_root.parent, mongo_port=mongo, oidc_port=oidc,
                              api_port=api, web_port=web, native_callback=None, client_origin=None,
                              identity_realm=identity_realm)

    def exercise(state, backend, runtime_env):
        with live.frontend_preview(state, private, runtime_env) as preview:
            owned = acceptance.OwnedBrowserLaunch.from_driver(
                app_root=app_root, repo_root=ROOT, candidate=candidate, state=state,
                backend=backend, runtime_env=runtime_env, preview=preview,
            )
            browser_env = live.clean_environment(dict(os.environ), {
                "PYTHON_DOTENV_DISABLED": "1", "COMMUNITY_LIVE_CONFIG": str(private / "environment.json"),
                "MOZAIKS_PAIRED_EXPECT_MOBILE_PANEL": "1",
            })
            with patch.dict(os.environ, browser_env, clear=True):
                browser = acceptance.run_playwright_acceptance(
                    app_root=app_root, repo_root=ROOT, mode="real_host", profile_path=profile_path,
                    base_url=state["base_url"], spec_path="playwright/generated-ui/paired-app.real-host.acceptance.spec.js",
                    owned_launch=owned, evidence_dir=case_root / "child-browser-evidence", timeout_seconds=600,
                )
            browser = _sanitized(browser, [user["password"] for user in state["users"]])
            live.write_json(case_root / "child-browser-result.json", browser)
            return browser

    browser = live.run(args, private, exercise=exercise)
    assert browser["success"] and browser["runtime_identity"] == "verified_owned_launch"
    assert acceptance.bundle_digest(app_root) == candidate["bundle_sha256"]


def test_accepted_board_refines_through_real_studio():
    from scripts import generated_ui_acceptance as acceptance
    from tests.test_paired_experience_browser import _identity_realm_fixture, _live_owner, _ports

    live = _live_owner()
    baseline_dir = Path(os.environ["MOZAIKS_PAIRED_BROWSER_BASELINE"]).resolve(strict=True)
    image_id, image_source = os.environ["MOZAIKS_APP_RUNTIME_IMAGE_ID"], os.environ["MOZAIKS_PAIRED_VALIDATOR_SOURCE_SHA"]
    source_sha = acceptance._source_commit(ROOT)
    files, prior_candidate = _accepted_baseline(baseline_dir)
    _compatible_sources(prior_candidate["source_sha"], source_sha)
    image_source = _compatible_sources(image_source, source_sha)
    image_identity = _inspect_image(image_id, image_source)
    case_root = ROOT / ".local/evidence/paired-refinement" / uuid4().hex
    app_root = case_root / "workspace/app"
    for relative, content in files.items():
        destination = app_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content.encode("utf-8"))
    candidate = {**prior_candidate, "source_sha": source_sha}
    assert acceptance.bundle_digest(app_root) == candidate["bundle_sha256"]
    private = live.EVIDENCE_ROOT / f"paired-refinement-{case_root.name}"
    # The existing finalizer adds its own nested workspace paths. Keep its
    # retained staging directory short enough for Windows file operations.
    refinement_output_root = Path(tempfile.mkdtemp(prefix="refine-", dir=ROOT / ".local"))
    live.write_json(case_root / "inputs.json", {"baseline_directory": str(baseline_dir), "candidate": candidate,
                    "baseline_browser_source_sha": prior_candidate["source_sha"],
                    "refinement_output_root": str(refinement_output_root),
                    "validator_image": image_identity})
    identity_realm = _identity_realm_fixture(live.EVIDENCE_ROOT / "identity-inputs" / f"refine-{case_root.name}.json", files)
    mongo, oidc, api, web = _ports()
    args = argparse.Namespace(workspace=app_root.parent, mongo_port=mongo, oidc_port=oidc,
                              api_port=api, web_port=web, native_callback=None, client_origin=None,
                              identity_realm=identity_realm)

    def exercise(state, backend, runtime_env):
        with live.frontend_preview(state, private, runtime_env) as preview:
            owned = acceptance.OwnedBrowserLaunch.from_driver(
                app_root=app_root, repo_root=ROOT, candidate=candidate, state=state,
                backend=backend, runtime_env=runtime_env, preview=preview,
            )
            env = live.clean_environment(runtime_env, {
                **runtime_env, "MOZAIKS_HOST": "studio", "MOZAIKS_WORKFLOWS_PATH": str(ROOT / "factory_app/workflows"),
                "MOZAIKS_APP_RUNTIME_IMAGE_ID": image_id, "MOZAIKS_APP_VALIDATION_STRATEGY": "docker",
                "MOZAIKS_ARTIFACT_CONTENT_BACKEND": "gridfs", "MOZAIKS_BUNDLE_CONTENT_BACKEND": "gridfs",
                "LOGS_BASE_DIR": str(private / "refinement-logs"),
            })
            def worker(mode):
                log_path = private / f"{mode}-worker.log"
                with log_path.open("wb") as log:
                    completed = subprocess.run([sys.executable, "-P", str(Path(__file__).resolve()), f"--{mode}", str(case_root)],
                                               cwd=ROOT, env=env,
                                               input=json.dumps({key: state[key] for key in ("base_url", "auth_issuer", "users")}).encode(),
                                               stdout=log, stderr=subprocess.STDOUT, timeout=1200, check=False)
                owned.verify(app_root=app_root, repo_root=ROOT, candidate=owned.candidate, base_url=state["base_url"])
                assert completed.returncode == 0, f"Real {mode} failed; inspect {case_root} and {log_path}"

            worker("refine")
            _child_browser(case_root, source_sha)
            worker("accept")
            return _json(case_root / "result.json")

    result = live.run(args, private, exercise=exercise)
    assert result["success"] and acceptance.bundle_digest(app_root) == candidate["bundle_sha256"]
    print(f"Paired B refinement evidence: {case_root}")


if __name__ == "__main__":
    assert len(sys.argv) == 3 and sys.argv[1] in {"--refine", "--accept"}
    operation = _refine if sys.argv[1] == "--refine" else _accept
    asyncio.run(operation(Path(sys.argv[2]).resolve(strict=True), json.loads(sys.stdin.read())))
