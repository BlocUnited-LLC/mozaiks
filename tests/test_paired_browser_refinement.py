"""Opt-in real Studio refinement of an accepted paired B browser candidate.

Set MOZAIKS_PAIRED_REFINEMENT=1, MOZAIKS_PAIRED_BROWSER_BASELINE to the accepted
B evidence directory, MOZAIKS_APP_RUNTIME_IMAGE_ID to an immutable local image,
and MOZAIKS_PAIRED_VALIDATOR_SOURCE_SHA to that image's committed source. Run
this module alone from a clean checkout. Image source labels and runtime-source
compatibility are checked against the checkout.

Only AG2 provider responses are scripted. Keycloak login, Mongo/GridFS, Studio
authorization, scoped finalization and Docker candidate validation remain real.
The backend lane runs first on the app-owned Messaging handler: an inferred
scope returns a confirmation question that leaves the accepted baseline current,
and the owner's confirmation applies it; a scripted handler that passes a
privileged service flag is rejected before validation, leaving the accepted
build current, and the owner's explicit retry applies; each child is accepted.
A confirmation proposed before that accepted change is refused, and an older
candidate saved from the same build is refused after the board child is
accepted. The board refinement builds on the latest accepted handler. The saved
board child receives real browser acceptance, including the handler's send
behavior, before the owner accepts its review draft. It is not promoted. No live
model creativity is claimed.
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
HANDLER = "modules/messages/backend/handler.py"
BACKEND_REQUEST = "Collapse repeated spaces in sent messages before they are stored. Keep every pack file and Messaging authorization unchanged."
SEND_OVERRIDE = """
    async def send_message(self, ctx, *, thread_id, body, message_type="text", **_):
        # Collapse whitespace runs before the template service validates and stores the body.
        return await super().send_message(
            ctx, thread_id=thread_id, body=" ".join(body.split()), message_type=message_type,
        )
"""
# The explicit retry: the same normalization, restated after the rejected attempt.
RETRY_OVERRIDE = SEND_OVERRIDE.replace(
    "# Collapse whitespace runs before the template service validates and stores the body.",
    "# Retried after a rejected attempt: collapse whitespace runs, then let the template service store the body.",
)
# Passing the service's privileged flag would let a non-participant post, so admission rejects it.
PRIVILEGED_OVERRIDE = """
    async def send_message(self, ctx, *, thread_id, body, message_type="text", **_):
        return await self._service.send_message(
            ctx, thread_id=thread_id, body=body, message_type=message_type, allow_nonparticipant_sender=True,
        )
"""
OLDER_CANDIDATE_REQUEST = "Note in the board source that this revision was saved for review."
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


def _normalizing_handler(source):
    """Derive the scripted backend response from the exact accepted handler bytes."""
    assert "def send_message" not in source, "Accepted handler already overrides send_message"
    assert source.rstrip().endswith('"""'), "Accepted handler must end inside its class docstring"
    return source + SEND_OVERRIDE


def _retried_handler(source):
    """The explicit retry's scripted response, from the baseline's handler bytes."""
    return _normalizing_handler(source).replace(SEND_OVERRIDE, RETRY_OVERRIDE)


def _privileged_handler(source):
    """A scripted response the handler extension grammar rejects before validation."""
    return _normalizing_handler(source).replace(SEND_OVERRIDE, PRIVILEGED_OVERRIDE)


def _template_pack_paths(files):
    """Template-owned materialized pack paths of the accepted baseline.

    _baseline_packs verifies the baseline's pack identity against freshly
    verified pinned sources, so these bytes are the pinned pack templates as
    assembled. Composed shared roots merge app declarations and are excluded.
    """
    from mozaiksai.core.workflow.generator_support.module_action_inventory import (
        SHARED_PACK_OUTPUT_PATHS,
    )

    provenance = json.loads(files[".mozaiks/pack_provenance.json"])
    owned = {entry["path"]: entry["owner"] for pack in provenance["packs"] for entry in pack["materialized_owned_files"]}
    assert owned.get(HANDLER) == "workspace" and owned.get("modules/messages/backend/base_handler.py") == "templates"
    return sorted(path for path, owner in owned.items() if owner == "templates" and path not in SHARED_PACK_OUTPUT_PATHS)


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
    for path in ("app.json", "config/auth.yaml", "config/shell.json", "data/contract.json",
                 "ui/route_manifest.json", "ui/index.js", "ui/pages/projects.yaml",
                 "modules/projects/module.yaml", "modules/tasks/module.yaml",
                 "modules/messages/module.yaml", ".mozaiks/pack_provenance.json"):
        assert read_only[path] == files[path]
    # A baseline without an app-owned theme inherits the framework theme. The
    # worker must neither invent that file nor omit it when it was saved.
    theme = "brand/theme_config.json"
    assert (theme in read_only) == (theme in files)
    if theme in files:
        assert read_only[theme] == files[theme]
    assert all(files[path] == content for path, content in read_only.items())
    assert PAGE not in read_only
    assert {pack["pack_id"] for pack in json.loads(read_only[".mozaiks/pack_provenance.json"])["packs"]} == {"messaging"}
    return {"read_only_paths": sorted(read_only), "app_owned_theme_present": theme in files,
            "theme_source": "app_artifact" if theme in files else "inherited_framework"}


def _scripted_backend_harness(files, store, output_root, *, coder=None):
    """The patch lane: scripted classifier, scope and coding providers, real worker and finalizer.

    The coder's ``output`` is set before each run that reaches it; every other
    provider returns one fixed response. Contract-surface planning is not part of
    this lane, so it fails loudly if a request is ever routed there.
    """
    from ag2 import Agent

    from mozaiksai.control_plane.config import load_control_plane_config
    from mozaiksai.control_plane.implementations.change_classifier import LLMChangeClassifier
    from mozaiksai.control_plane.implementations.coding_worker import ScopedRefinementCodingWorker
    from mozaiksai.control_plane.implementations.harness_decision import (
        FirstPartyHarnessDecisionPolicy,
    )
    from mozaiksai.control_plane.implementations.orchestration_control import (
        OrchestrationControlHarness,
    )
    from mozaiksai.control_plane.implementations.refinement_router import (
        RefinementTriggerRouteResolver,
    )
    from mozaiksai.control_plane.implementations.scope_proposer import ArtifactScopeProposer
    from mozaiksai.control_plane.loader import load_refinement_harness
    from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
    from tests.test_ui_worker_context_acceptance import _ScriptedProvider

    config = load_control_plane_config(ROOT / "factory_app/app")
    pack = load_refinement_harness(app_root=ROOT / "factory_app/app")
    options = {"config_loader": lambda: config, "pack_loader": lambda: pack}
    outputs = {
        "classifier": {"change_class": "patch", "rationale": BACKEND_REQUEST, "confidence": 0.99, "signals": ["message_whitespace"]},
        "scope": {"resolution": "scoped_files", "selected_paths": [HANDLER], "confidence": 0.99,
                  "rationale": "The send action that stores message bodies is the app-owned Messaging handler.",
                  "clarification_question": None, "signals": ["message_whitespace"]},
        "coder": coder or _coder_output(HANDLER, _normalizing_handler(files[HANDLER])),
    }
    providers = {name: _ScriptedProvider(output) for name, output in outputs.items()}

    def runner(name):
        def factory(system_prompt, llm_config, *, middleware):
            return Agent(f"scripted_{name}", system_prompt, config=providers[name], middleware=middleware)
        return AG2StructuredAgentRunner(agent_factory=factory)

    class _NoSurfacePlanning:
        async def propose(self, **_):
            raise AssertionError("The backend lane is a scripted patch; it never plans contract surfaces")

    return OrchestrationControlHarness(
        refinement_resolver=RefinementTriggerRouteResolver(
            classifier=LLMChangeClassifier(agent_runner=runner("classifier"), **options), pack_loader=lambda: pack,
        ),
        coding_worker=ScopedRefinementCodingWorker(
            agent_runner=runner("coder"), artifact_store=store, output_root=output_root, **options,
        ),
        scope_proposer=ArtifactScopeProposer(agent_runner=runner("scope"), artifact_store=store, **options),
        contract_surface_planner=_NoSurfacePlanning(),
        decision_policy=FirstPartyHarnessDecisionPolicy(pack_loader=lambda: pack),
        config_loader=lambda: config,
    ), providers


def _coder_output(path, content, summary=BACKEND_REQUEST):
    return {"summary": summary, "owned_paths": [path],
            "updated_files": [{"path": path, "content": content}],
            "validation_strategy": "docker", "validation_commands": [], "start_preview": False,
            "needs_human_review": True,
            "rationale": "Extend the app-owned handler; the template service keeps validation and authorization."}


def _user_prompt_at(provider, index):
    messages = [message for message in provider.calls[index]["messages"] if message["role"] == "user"]
    content = messages[-1]["content"]
    return content if isinstance(content, str) else "\n".join(item["text"] for item in content if item.get("type") == "text")


async def _refine_backend(*, case_root, live, client, studio, store, registry, binding, baseline, files, packs, tokens, owner_id):
    """Confirm, retry and accept app-owned handler changes through Studio, and record stale work being refused.

    (a) An inferred scope over the handler returns a confirmation question that
    leaves the accepted baseline current; the owner's confirmation then applies
    it and the child is accepted. (b) A scripted handler that passes a privileged
    service flag is rejected before validation; no child exists for that run and
    the accepted build stays current, so the owner's explicit retry is admitted
    and accepted. (c) A confirmation proposed before that accepted change is
    refused before it is used. Returns the latest accepted handler child.
    """
    from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
    from mozaiksai.core.artifacts import ArtifactLifecycleStatus, ArtifactValidationStatus
    from mozaiksai.core.artifacts.content_store import read_verified_artifact_bundle

    app_id = baseline.app_id
    harness, providers = _scripted_backend_harness(files, store, Path(_json(case_root / "inputs.json")["refinement_output_root"]))
    alice = {"Authorization": f"Bearer {tokens['alice']}"}
    steps = []

    def payload(parent_id, coding_request, *, request=BACKEND_REQUEST, **extra):
        return {"build_registry_id": binding.build_registry_id, "trigger_source": "refinement",
                "trigger_payload": {"refinement_request": {"build_family": "app_bundle", "build_key": "app_bundle",
                                                           "build_record_id": parent_id, "raw_user_request": request,
                                                           "source_surface": "app_workbench"},
                                    "coding_request": coding_request, **extra}}

    def confirmation(decision, parent_id):
        return payload(parent_id, {}, change_request_id=decision["change_request_id"], revision_id=decision["revision_id"],
                       harness_action={"action_id": "apply_proposed_scope"})

    async def registry_record():
        return (await registry.get_app_record(owner_user_id=owner_id, build_registry_id=binding.build_registry_id))["app"]

    async def lifecycle(record_id):
        record = await store.get_build_record(app_id=app_id, build_record_id=record_id)
        return {"build_record_id": record_id, "lifecycle_status": record.lifecycle_status.value,
                "invalidation_reason": record.invalidation_reason,
                "stale_at": record.stale_at.isoformat() if record.stale_at else None}

    async def step(name, body, parent_id, *, headers=alice):
        before = await lifecycle(parent_id)
        response = await client.post("/api/workflows/trigger", json=body, headers=headers)
        live.write_json(case_root / f"backend-{name}-endpoint-result.json", {"status_code": response.status_code, "body": response.json()})
        result = response.json() if response.status_code == 200 else {}
        coding = result.get("coding_worker") or {}
        run = (await registry_record()).get("current_build_run") or {}
        children = await store.list_run_child_build_records(
            app_id=app_id, parent_build_record_id=parent_id, build_id=run["build_id"],
        ) if run.get("build_id") else []
        after = await lifecycle(parent_id)
        entry = {
            "step": name, "status_code": response.status_code, "parent_build_record_id": parent_id,
            "execution_mode": result.get("execution_mode"),
            "decision_type": (result.get("harness_decision") or {}).get("decision_type"),
            "coding_status": coding.get("status"),
            "candidate_admission_code": ((coding.get("metadata") or {}).get("candidate_admission") or {}).get("code"),
            "child_build_record_id": (coding.get("metadata") or {}).get("build_record_id"),
            "registry_run": {key: run.get(key) for key in ("build_id", "status", "artifact_version_id")},
            "children_of_registry_run": [child.id for child in children],
            "parent_before": before, "parent_after": after,
            "invalidation_observed": before["lifecycle_status"] != "stale" and after["lifecycle_status"] == "stale",
            "detail": None if response.status_code == 200 else response.json().get("detail"),
        }
        steps.append(entry)
        return response, entry

    async def accept(name, record_id, notes):
        accepted = await client.post(
            f"/api/studio/build/artifacts/{record_id}/accept", params={"build_registry_id": binding.build_registry_id},
            json={"notes": notes}, headers=alice,
        )
        live.write_json(case_root / f"backend-{name}-acceptance-endpoint-result.json", {"status_code": accepted.status_code, "body": accepted.json()})
        assert accepted.status_code == 200 and accepted.json()["accepted"] is True
        record = await store.get_build_record(app_id=app_id, build_record_id=record_id)
        assert record.lifecycle_status == ArtifactLifecycleStatus.CURRENT
        steps.append({"step": f"accept_{name}", "status_code": accepted.status_code, "accepted_build_record_id": record_id})
        return record

    async def handler_child(entry, *, parent, parent_files, content, scope_origin):
        """The saved child of one authorized run, held to its parent and the pack contract."""
        child = await store.get_build_record(app_id=app_id, build_record_id=entry["child_build_record_id"])
        assert child.parent_build_record_id == parent.id and child.lifecycle_status == ArtifactLifecycleStatus.DRAFT
        assert child.validation_status == ArtifactValidationStatus.PASSED and child.app_validation_status == "passed"
        assert entry["children_of_registry_run"] == [child.id]
        metadata = child.commit_metadata.metadata
        assert metadata["content_backend"] == "gridfs" and metadata["applied_paths"] == [HANDLER]
        assert metadata["refinement_scope"] == {
            "selected_file_paths": [HANDLER], "scope_origin": scope_origin, "changed_workspace_pack_paths": [HANDLER],
        }
        # An app revision: the pack's source, version and digest are unchanged.
        assert metadata["capability_packs"] == packs
        child_files, diagnostics = await read_artifact_bundle(child, retain_svg_text=True)
        assert not diagnostics and set(child_files) == set(files) == set(parent_files)
        assert {path for path in parent_files if child_files[path] != parent_files[path]} == {HANDLER}
        assert {path for path in files if child_files[path] != files[path]} == {HANDLER}
        assert child_files[HANDLER] == content
        assert child_files[".mozaiks/pack_provenance.json"] == files[".mozaiks/pack_provenance.json"]
        assert all(child_files[path] == files[path] for path in _template_pack_paths(files))
        # The run stored a reviewable child, so it invalidated its parent.
        assert entry["invalidation_observed"] and entry["parent_after"]["invalidation_reason"].startswith("change_request:")
        current = await registry_record()
        assert current["lifecycle_state"] == "review" and current["current_build_run"]["artifact_version_id"] == child.id
        return child, child_files

    with patch.object(studio, "get_orchestration_control_harness", return_value=harness):
        # (a) The inferred scope reaches the app-owned handler, so Studio asks first.
        proposal = payload(baseline.id, {})
        anonymous = await client.post("/api/workflows/trigger", json=proposal)
        other = await client.post("/api/workflows/trigger", json=proposal, headers={"Authorization": f"Bearer {tokens['bob']}"})
        assert anonymous.status_code == 401 and other.status_code == 404
        run_before = (await registry_record())["current_build_run"]["build_id"]
        asked, entry = await step("confirmation_question", proposal, baseline.id)
        assert asked.status_code == 200, "Real Studio scope proposal was rejected; inspect backend-confirmation_question-endpoint-result.json"
        decision = asked.json()
        assert decision["execution_mode"] == "harness_decision"
        assert decision["harness_decision"]["decision_type"] == "clarify_scope"
        assert decision["harness_decision"]["selected_paths"] == [HANDLER]
        assert decision["harness_decision"]["metadata"]["confirmation_required_paths"] == [HANDLER]
        assert "apply_proposed_scope" in [action["action_id"] for action in decision["harness_decision"]["actions"]]
        # A question changes nothing: the accepted baseline stays current and no run began.
        assert entry["parent_after"]["lifecycle_status"] == "current" and not entry["invalidation_observed"]
        assert entry["registry_run"]["build_id"] == run_before and len(providers["coder"].calls) == 0
        confirm = confirmation(decision, baseline.id)
        confirm_anonymous = await client.post("/api/workflows/trigger", json=confirm)
        confirm_other = await client.post("/api/workflows/trigger", json=confirm, headers={"Authorization": f"Bearer {tokens['bob']}"})
        assert confirm_anonymous.status_code == 401 and confirm_other.status_code == 404
        confirmed_content = providers["coder"].output["updated_files"][0]["content"]
        confirmed, entry = await step("confirmation", confirm, baseline.id)
        assert confirmed.status_code == 200 and confirmed.json()["coding_worker"]["status"] == "validated", confirmed.json()
        coder = json.JSONDecoder().raw_decode(_user_prompt_at(providers["coder"], 0).split("payload_json:\n", 1)[1])[0]
        assert coder["file_paths"] == [HANDLER] and coder["input_files"] == {HANDLER: files[HANDLER]}
        confirmed_child, confirmed_files = await handler_child(
            entry, parent=baseline, parent_files=files, content=confirmed_content, scope_origin="confirmed",
        )
        replay, entry = await step("confirmation_replay", confirm, baseline.id)
        assert replay.status_code == 409 and len(providers["coder"].calls) == 1
        await accept("confirmed", confirmed_child.id, "Accepted the confirmed handler-only backend refinement after canonical validation.")

        # (b) A run rejected before validation stores nothing and leaves the accepted build current.
        providers["coder"].output = _coder_output(HANDLER, _privileged_handler(files[HANDLER]))
        rejected, entry = await step("rejected_attempt", payload(confirmed_child.id, {"files": {HANDLER: ""}}), confirmed_child.id)
        assert rejected.status_code == 200 and entry["coding_status"] == "failed", rejected.json()
        assert entry["candidate_admission_code"] == "WORKSPACE_HANDLER_EXTENSION_REJECTED"
        assert entry["child_build_record_id"] is None and entry["children_of_registry_run"] == []
        assert entry["parent_after"]["lifecycle_status"] == "current" and not entry["invalidation_observed"]
        # A question proposed now, before the retry below changes this build.
        asked_again, entry = await step("later_confirmation_question", payload(confirmed_child.id, {}), confirmed_child.id)
        assert asked_again.status_code == 200 and entry["decision_type"] == "clarify_scope"
        assert not entry["invalidation_observed"]
        later_decision = asked_again.json()
        retry_content = _retried_handler(files[HANDLER])
        providers["coder"].output = _coder_output(HANDLER, retry_content)
        response, entry = await step("explicit_retry", payload(confirmed_child.id, {"files": {HANDLER: ""}}), confirmed_child.id)
        live.write_json(case_root / "backend-endpoint-result.json", {"status_code": response.status_code, "body": response.json()})
        assert response.status_code == 200, "Real Studio backend retry was rejected; inspect backend-endpoint-result.json"
        result = response.json()
        assert result["execution_mode"] == "coding_worker" and result["coding_worker"]["status"] == "validated", result["coding_worker"]
        coder = json.JSONDecoder().raw_decode(_user_prompt_at(providers["coder"], 2).split("payload_json:\n", 1)[1])[0]
        assert coder["file_paths"] == [HANDLER] and coder["input_files"] == {HANDLER: confirmed_files[HANDLER]}
        child, child_files = await handler_child(
            entry, parent=confirmed_child, parent_files=confirmed_files, content=retry_content, scope_origin="explicit",
        )
        metadata = child.commit_metadata.metadata
        (case_root / "backend-child.zip").write_bytes(await read_verified_artifact_bundle(child))
        live.write_json(case_root / "backend-child-record.json", child.model_dump(mode="json"))
        live.write_json(case_root / "backend-child-files.sha256.json", _hashes(child_files))
        accepted = await client.post(
            f"/api/studio/build/artifacts/{child.id}/accept", params={"build_registry_id": binding.build_registry_id},
            json={"notes": "Accepted the handler-only backend refinement after canonical validation."},
            headers=alice,
        )
        live.write_json(case_root / "backend-acceptance-endpoint-result.json", {"status_code": accepted.status_code, "body": accepted.json()})
        assert accepted.status_code == 200 and accepted.json()["accepted"] is True
        child = await store.get_build_record(app_id=app_id, build_record_id=child.id)
        assert child.lifecycle_status == ArtifactLifecycleStatus.CURRENT
        live.write_json(case_root / "backend-accepted-child-record.json", child.model_dump(mode="json"))
        steps.append({"step": "accept_retry", "status_code": accepted.status_code, "accepted_build_record_id": child.id})

        # (c) A genuine accepted change still refuses work made before it.
        stale, entry = await step("stale_confirmation", confirmation(later_decision, confirmed_child.id), confirmed_child.id)
        assert stale.status_code == 409 and "Request the change again" in entry["detail"], stale.json()
        assert len(providers["coder"].calls) == 3 and len(providers["scope"].calls) == 2
        chat_app_id = (await registry_record())["chat_app_id"]
        session = await studio.get_session_router().for_target(app_id).get_session_snapshot(app_id=chat_app_id, user_id=owner_id)
        # Refused before the decision was used: it is still pending, not consumed.
        assert (session.get("pending_harness_decision") or {}).get("decision_id") == later_decision["change_request_id"]

    for name, provider in providers.items():
        requests = [{"prompt": call["prompt"], "messages": call["messages"]} for call in provider.calls]
        assert all(token not in json.dumps(requests) for token in tokens.values())
        live.write_json(case_root / f"backend-provider-{name}-requests.json", requests)
    classifier = _user_prompt_at(providers["classifier"], 0)
    assert BACKEND_REQUEST in classifier and baseline.id in classifier
    assert BACKEND_REQUEST in _user_prompt_at(providers["scope"], 0)
    assert {name: len(provider.calls) for name, provider in providers.items()} == {"classifier": 5, "scope": 2, "coder": 3}
    live.write_json(case_root / "backend-steps.json", steps)
    return child, child_files, {
        "request": BACKEND_REQUEST, "permitted_paths": [HANDLER], "changed_paths": [HANDLER],
        "build_record_id": child.id, "parent_build_record_id": confirmed_child.id, "review_acceptance": "accepted",
        "refinement_scope": metadata["refinement_scope"], "anonymous_status": anonymous.status_code,
        "non_owner_status": other.status_code, "child_files": _hashes(child_files),
        "confirmation_anonymous_status": confirm_anonymous.status_code,
        "confirmation_non_owner_status": confirm_other.status_code,
        "baseline_build_record_id": baseline.id, "confirmed_child_build_record_id": confirmed_child.id,
        "steps": steps,
        "provider_calls": {name: {"count": len(provider.calls),
                                  "requests_sha256": hashlib.sha256((case_root / f"backend-provider-{name}-requests.json").read_bytes()).hexdigest()}
                           for name, provider in providers.items()},
    }


async def _save_older_candidate(*, case_root, live, studio, store, binding, parent, parent_files, tokens, output_root):
    """Save a validated sibling of the board child from the same accepted handler build.

    It keeps the accepted handler and changes only the board source, so the
    app-owned code check passes; it is refused at acceptance only because the
    board child accepted before it is not in its lineage.
    """
    import httpx

    from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
    from mozaiksai.core.artifacts import ArtifactLifecycleStatus, ArtifactValidationStatus

    content = parent_files[PAGE] + "\n// Saved for review from the same accepted handler build as the board refinement.\n"
    harness, providers = _scripted_backend_harness(
        parent_files, store, output_root, coder=_coder_output(PAGE, content, summary=OLDER_CANDIDATE_REQUEST),
    )
    payload = {"build_registry_id": binding.build_registry_id, "trigger_source": "refinement",
               "trigger_payload": {"refinement_request": {"build_family": "app_bundle", "build_key": "app_bundle",
                                    "build_record_id": parent.id, "raw_user_request": OLDER_CANDIDATE_REQUEST,
                                    "source_surface": "app_workbench"},
                                   "coding_request": {"files": {PAGE: ""}}}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app), base_url="http://testserver", timeout=None) as client:
        with patch.object(studio, "get_orchestration_control_harness", return_value=harness):
            response = await client.post("/api/workflows/trigger", json=payload, headers={"Authorization": f"Bearer {tokens['alice']}"})
    live.write_json(case_root / "older-candidate-endpoint-result.json", {"status_code": response.status_code, "body": response.json()})
    for name, provider in providers.items():
        requests = [{"prompt": call["prompt"], "messages": call["messages"]} for call in provider.calls]
        assert all(token not in json.dumps(requests) for token in tokens.values())
        live.write_json(case_root / f"older-candidate-provider-{name}-requests.json", requests)
    assert response.status_code == 200 and response.json()["coding_worker"]["status"] == "validated", response.json()
    assert {name: len(provider.calls) for name, provider in providers.items()} == {"classifier": 1, "scope": 0, "coder": 1}
    older = await store.get_build_record(app_id=parent.app_id, build_record_id=response.json()["coding_worker"]["metadata"]["build_record_id"])
    assert older.parent_build_record_id == parent.id and older.lifecycle_status == ArtifactLifecycleStatus.DRAFT
    assert older.validation_status == ArtifactValidationStatus.PASSED
    older_files, diagnostics = await read_artifact_bundle(older, retain_svg_text=True)
    assert not diagnostics and {path for path in parent_files if older_files[path] != parent_files[path]} == {PAGE}
    assert older_files[HANDLER] == parent_files[HANDLER]
    live.write_json(case_root / "older-candidate-record.json", older.model_dump(mode="json"))
    return {"build_record_id": older.id, "parent_build_record_id": parent.id, "request": OLDER_CANDIDATE_REQUEST,
            "changed_paths": [PAGE], "files": _hashes(older_files)}


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
    assert not studio.app.dependency_overrides, "Real route acceptance cannot use dependency overrides"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app), base_url="http://testserver", timeout=None) as client:
            # The authorized backend child is the parent of the board refinement.
            backend, parent_files, backend_evidence = await _refine_backend(
                case_root=case_root, live=live, client=client, studio=studio, store=store, registry=registry,
                binding=binding, baseline=baseline, files=files, packs=packs, tokens=tokens, owner_id=owner_id,
            )
            harness, providers = _scripted_harness(parent_files, store, Path(facts["refinement_output_root"]))
            payload = {"build_registry_id": binding.build_registry_id, "trigger_source": "refinement",
                       "trigger_payload": {"refinement_request": {"build_family": "app_bundle", "build_key": "app_bundle",
                                            "build_record_id": backend.id, "raw_user_request": REQUEST, "source_surface": "app_workbench"},
                                           "coding_request": {"files": {PAGE: ""}}}}
            with patch.object(studio, "get_orchestration_control_harness", return_value=harness):
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
        assert child.parent_build_record_id == backend.id and child.lifecycle_status == ArtifactLifecycleStatus.DRAFT
        assert child.validation_status == ArtifactValidationStatus.PASSED and child.app_validation_status == "passed"
        assert child.commit_metadata.metadata["content_backend"] == "gridfs"
        child_files, diagnostics = await read_artifact_bundle(child, retain_svg_text=True)
        # Retain the actual saved child before later context/preservation checks
        # can fail. These bytes are evidence, never an edited acceptance input.
        child_root = case_root / "refined-workspace/app"
        for path, content in child_files.items():
            target = child_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
        (case_root / "child.zip").write_bytes(await read_verified_artifact_bundle(child))
        live.write_json(case_root / "child-files.sha256.json", _hashes(child_files))
        live.write_json(case_root / "baseline-record.json", baseline.model_dump(mode="json"))
        live.write_json(case_root / "child-record.json", child.model_dump(mode="json"))
        assert not diagnostics and set(child_files) == set(files) == set(parent_files)
        assert {path for path in parent_files if child_files[path] != parent_files[path]} == {PAGE}
        assert {path for path in files if child_files[path] != files[path]} == {PAGE, HANDLER}
        assert child_files[PAGE] == providers["generator"].output["updated_files"][0]["content"]
        assert child.commit_metadata.metadata["capability_packs"] == packs
        assert child.commit_metadata.metadata["refinement_scope"] == {
            "selected_file_paths": [PAGE], "scope_origin": "explicit", "changed_workspace_pack_paths": [],
        }
        provenance = json.loads(files[".mozaiks/pack_provenance.json"])
        retained_pack_paths = sorted({entry["path"] for pack in provenance["packs"]
                                      for entry in pack["materialized_owned_files"]})
        assert retained_pack_paths
        # The UI refinement keeps every pack path of its parent, including the
        # accepted backend handler; template-owned paths stay the pinned bytes.
        assert all(child_files[path] == parent_files[path] for path in retained_pack_paths)
        assert child_files[HANDLER] == parent_files[HANDLER] != files[HANDLER]
        assert all(child_files[path] == files[path] for path in _template_pack_paths(files))
        current = (await registry.get_app_record(owner_user_id=owner_id, build_registry_id=binding.build_registry_id))["app"]
        assert current["lifecycle_state"] == "review" and current["current_build_run"]["artifact_version_id"] == child.id
        assert result["refinement_session_id"] and result["review_continuation"] is None
        delivered_context = _assert_delivered_context(providers, parent_files, backend.id)
        # (c) A sibling saved from the same build, after the board child; acceptance refuses it later.
        older_candidate = await _save_older_candidate(
            case_root=case_root, live=live, studio=studio, store=store, binding=binding, parent=backend,
            parent_files=parent_files, tokens=tokens, output_root=Path(facts["refinement_output_root"]),
        )
        live.write_json(case_root / "result.json", {
            "success": True, "evidence_kind": "authorized_studio_refinement_with_scripted_ag2_providers",
            "live_llm_calls": False, "browser_acceptance": "not_evaluated_for_child", "capability_packs": packs,
            "source": facts, "request": REQUEST, "permitted_paths": [PAGE], "changed_paths": [PAGE],
            "delivered_context": delivered_context,
            "retained_paths": sorted(path for path in parent_files if child_files[path] == parent_files[path]),
            "retained_pack_paths": retained_pack_paths, "baseline_files": _hashes(files),
            "design_source": replay_receipt["design_source"], "backend_refinement": backend_evidence,
            "older_candidate": older_candidate,
            "baseline_build_record_id": baseline.id, "parent_build_record_id": backend.id,
            "child_build_record_id": child.id,
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
            # The older candidate shares the accepted handler but was made from the
            # board child's parent, so accepting it would revert the accepted board.
            older_id = result["older_candidate"]["build_record_id"]
            stale_candidate = await client.post(
                f"/api/studio/build/artifacts/{older_id}/accept",
                params={"build_registry_id": result["registry"]["build_registry_id"]},
                json={"notes": "An older candidate offered after a newer accepted build."},
                headers={"Authorization": f"Bearer {tokens['alice']}"},
            )
        live.write_json(case_root / "stale-candidate-acceptance-endpoint-result.json",
                        {"status_code": stale_candidate.status_code, "body": stale_candidate.json()})
        assert stale_candidate.status_code == 409 and "earlier build" in stale_candidate.json()["detail"]
        store = get_artifact_store()
        older = await store.get_build_record(app_id=app_id, build_record_id=older_id)
        assert older.lifecycle_status == ArtifactLifecycleStatus.DRAFT
        child = await store.get_build_record(app_id=app_id, build_record_id=child_id)
        assert child.lifecycle_status == ArtifactLifecycleStatus.CURRENT
        assert await read_verified_artifact_bundle(child) == (case_root / "child.zip").read_bytes()
        sessions = await store.list_refinement_sessions(app_id=app_id, result_build_record_id=child_id, limit=20)
        session = next(item for item in sessions if item.id == result["refinement_session_id"])
        assert session.status.value == "accepted"
        live.write_json(case_root / "accepted-child-record.json", child.model_dump(mode="json"))
        live.write_json(case_root / "accepted-refinement-session.json", session.model_dump(mode="json"))
        result.update({"browser_acceptance": "passed", "child_browser_candidate": browser["candidate"],
                       "review_acceptance": "accepted", "promotion": "not_requested",
                       "stale_candidate_acceptance": {"build_record_id": older_id, "status_code": stale_candidate.status_code,
                                                      "detail": stale_candidate.json()["detail"],
                                                      "lifecycle_status": older.lifecycle_status.value}})
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
                "MOZAIKS_PAIRED_EXPECT_MOBILE_PANEL": "1", "MOZAIKS_PAIRED_EXPECT_NORMALIZED_MESSAGE": "1",
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
                    "harness_path": str(Path(__file__).resolve()),
                    "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
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
                    # Refinement validates the baseline and four saved children in Docker.
                    completed = subprocess.run([sys.executable, "-P", str(Path(__file__).resolve()), f"--{mode}", str(case_root)],
                                               cwd=ROOT, env=env,
                                               input=json.dumps({key: state[key] for key in ("base_url", "auth_issuer", "users")}).encode(),
                                               stdout=log, stderr=subprocess.STDOUT,
                                               timeout=3600 if mode == "refine" else 1200, check=False)
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
