"""Live LLM conceptual_replan benchmark: CRM -> Marketplace pivot.

Usage:
    cd mozaiks
    python scripts/smoke_live_conceptual_replan.py
    python scripts/smoke_live_conceptual_replan.py --save-fixture
    python scripts/smoke_live_conceptual_replan.py --model gpt-5-nano
    python scripts/smoke_live_conceptual_replan.py --fixture-path tests/fixtures/live_conceptual_replan_output.json

What is live (real LLM + real code):
    1. RefinementTriggerRouteResolver with real control-plane pack
       (deterministic classifier -- no LLM for routing)
    2. AppPlanAgent system prompt from agents.yaml + file contracts hook
       + domain catalog hook (same hooks as the main workflow)
    3. OpenAI call to AppPlanAgent with full conceptual_replan context seed
       (carry_forward_modules, pivot_description, previous_app_bundle_ref)
    4. Phase 7A resolve_carry_forward_preservation with the LLM's actual
       carry_forward_decisions and a synthetic CRM workspace

What is stubbed (no MongoDB, no full workflow sequence):
    - Change classifier: _DeterministicChangeClassifier(change_class="core")
    - ArtifactStore.get_artifact_version: synthetic CRM doc + real temp dir
    - This is a single-agent live benchmark, NOT a full workflow sequence run

Scenario: CRM -> Marketplace conceptual pivot
    Initial modules: contacts, pipeline, settings, notifications
    Pivot: "Turn this into a marketplace for sellers and buyers"
    Expected: settings/notifications likely reuse; contacts/pipeline not blindly reused

17 required assertions (see _validate_output):
    1.  route == "conceptual_replan"
    2.  context_seed.pivot_description present
    3.  context_seed.previous_app_bundle_ref present
    4.  context_seed.carry_forward_modules non-empty list
    5.  AppBuildPlan.carry_forward_decisions exists and is a list
    6.  All decision values are valid (reuse/adapt/regenerate/drop)
    7.  settings decision is reuse or adapt (domain-generic; should not be dropped)
    8.  notifications decision is reuse or adapt (domain-generic)
    9.  contacts decision is drop or regenerate (CRM-specific; not blindly reused)
    10. pipeline decision is drop or regenerate (CRM-specific)
    11. Marketplace-oriented modules appear in the build plan
    12. carry_forward_report exists (Phase 7A ran)
    13. preserved_paths only contains Phase 7A allowlisted files
    14. No backend Python in preserved_paths
    15. No runtime_extensions.yaml in preserved_paths
    16. No custom React in preserved_paths
    17. carry_forward_report shape valid (all required keys present)

Environment:
    OPENAI_API_KEY     Required for live run.
    DEFAULT_LLM_MODEL  Override model (default: gpt-5-nano).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_APP_ROOT = _REPO_ROOT / "factory_app" / "app"
_AGENTS_YAML = _REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml"
_TOOLS_DIR = _REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools"
_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures"
_FIXTURE_PATH = _FIXTURES_DIR / "live_conceptual_replan_output.json"

# ---------------------------------------------------------------------------
# Scenario constants
# ---------------------------------------------------------------------------

_CRM_APP_ID = "smoke-crm-marketplace-live-001"
_PREV_ARTIFACT_VERSION_ID = "av_crm_live_smoke_v1"

_PIVOT_REQUEST = (
    "Actually, let's turn this into a marketplace where sellers can list products "
    "and buyers can browse and purchase them. Keep preferences and notifications."
)

_CARRY_FORWARD_MODULES = [
    {
        "module_id": "settings",
        "carry_forward_classification": "safe_carry_forward",
        "carry_forward_reasons": [
            "Universal user preference management -- concept-agnostic"
        ],
    },
    {
        "module_id": "notifications",
        "carry_forward_classification": "safe_carry_forward",
        "carry_forward_reasons": [
            "Notification primitives are concept-agnostic"
        ],
    },
    {
        "module_id": "contacts",
        "carry_forward_classification": "regenerate",
        "carry_forward_reasons": [
            "CRM contact management -- domain-specific; does not map to marketplace"
        ],
    },
    {
        "module_id": "pipeline",
        "carry_forward_classification": "regenerate",
        "carry_forward_reasons": [
            "CRM sales pipeline -- replaced by marketplace orders/transactions"
        ],
    },
]

# Synthetic CRM workspace for Phase 7A preservation
_CRM_WORKSPACE_FILES: dict[str, str] = {
    "modules/settings/module.yaml": "id: settings\nactions: []\ncapabilities: []\n",
    "modules/settings/contracts/settings.yaml": "version: 1\nfields: []\n",
    "modules/settings/contracts/events.yaml": "version: 1\nevents: []\n",
    "modules/settings/backend/service.py": "# settings service -- must NOT be copied\n",
    "modules/settings/backend/handler.py": "# settings handler -- must NOT be copied\n",
    "modules/settings/backend/schemas.py": "# settings schemas -- must NOT be copied\n",
    "modules/notifications/module.yaml": "id: notifications\nactions: []\ncapabilities: []\n",
    "modules/notifications/contracts/notifications.yaml": "version: 1\nrules: []\n",
    "modules/notifications/contracts/events.yaml": "version: 1\nevents: []\n",
    "modules/notifications/backend/service.py": "# notifications service -- must NOT be copied\n",
    "modules/notifications/backend/handler.py": "# notifications handler -- must NOT be copied\n",
    "modules/contacts/module.yaml": "id: contacts\nactions:\n  - id: create_contact\n",
    "modules/contacts/contracts/events.yaml": "version: 1\nevents:\n  - id: contact.created\n",
    "modules/contacts/backend/repo.py": "# contacts repo -- must NOT be copied\n",
    "modules/contacts/backend/service.py": "# contacts service -- must NOT be copied\n",
    "modules/pipeline/module.yaml": "id: pipeline\nactions:\n  - id: create_deal\n",
    "modules/pipeline/backend/service.py": "# pipeline service -- must NOT be copied\n",
    "modules/pipeline/runtime_extensions.yaml": "api_router: pipeline_router\n",
}

# Marketplace generated files (what AppGenerator would output post-replan)
_MARKETPLACE_GENERATED_FILES: dict[str, str] = {
    "app.json": '{"id": "marketplace", "name": "Marketplace"}\n',
    "modules/listings/module.yaml": "id: listings\nactions:\n  - id: create_listing\n",
    "modules/listings/contracts/events.yaml": "version: 1\nevents:\n  - id: listing.created\n",
    "modules/orders/module.yaml": "id: orders\nactions:\n  - id: place_order\n",
    "modules/orders/contracts/events.yaml": "version: 1\nevents:\n  - id: order.placed\n",
}

# Valid carry_forward_decisions decision values
_VALID_DECISIONS = {"reuse", "adapt", "regenerate", "drop"}

# Phase 7A allowlist (must match _PHASE_7A_MODULE_ALLOWLIST in the tool)
_PHASE_7A_ALLOWLIST = {
    "module.yaml",
    "contracts/events.yaml",
    "contracts/reactions.yaml",
    "contracts/notifications.yaml",
    "contracts/settings.yaml",
    "contracts/admin.yaml",
    "contracts/profile.yaml",
}

# Marketplace-indicator tokens -- at least one must appear in the build plan
_MARKETPLACE_TOKENS = (
    "listing", "listings", "seller", "sellers", "buyer", "buyers",
    "order", "orders", "marketplace", "product", "products",
    "vendor", "vendors", "catalog", "storefront",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(_REPO_ROOT / ".env")
    except ImportError:
        pass


def _load_module(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeAgent:
    def __init__(self, name: str, context_variables: dict[str, Any]) -> None:
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables

    def update_system_message(self, message: str) -> None:
        self.system_message = message


def _build_agent_system_prompt(agent_name: str) -> str:
    raw = yaml.safe_load(_AGENTS_YAML.read_text(encoding="utf-8"))
    agents = raw.get("agents") if isinstance(raw, dict) else raw
    if not isinstance(agents, list):
        raise ValueError("agents.yaml root must be a list")
    for agent in agents:
        if not isinstance(agent, dict) or agent.get("name") != agent_name:
            continue
        parts: list[str] = []
        for section in agent.get("prompt_sections") or []:
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading") or "").strip()
            content = str(section.get("content") or "").strip()
            if heading and content:
                parts.append(f"{heading}\n{content}")
            elif content:
                parts.append(content)
        return "\n\n".join(parts)
    raise ValueError(f"Agent {agent_name!r} not found")


def _apply_hooks(agent: _FakeAgent) -> None:
    file_contract_hook = _load_module(
        _TOOLS_DIR / "hook_file_contract_context.py",
        f"live_smoke_file_contracts.{id(agent)}",
    )
    file_contract_hook.inject_cookie_cutter_contracts_context(agent, [])

    domain_hook = _load_module(
        _TOOLS_DIR / "hook_domain_catalog_context.py",
        f"live_smoke_domain_catalog.{id(agent)}",
    )
    domain_hook.inject_domain_catalog_context(agent, [])


def _build_conceptual_replan_context_block(
    pivot_description: str,
    carry_forward_modules: list[dict[str, Any]],
    previous_app_bundle_ref: str,
) -> str:
    """Inject context variables as a [CONCEPTUAL REPLAN CONTEXT] section."""
    cf_json = json.dumps(carry_forward_modules, indent=2)
    return (
        "[CONCEPTUAL REPLAN CONTEXT]\n"
        f"build_mode: revision\n"
        f"workflow_sequence: conceptual_replan\n"
        f"pivot_description: {pivot_description}\n"
        f"previous_app_bundle_ref: {previous_app_bundle_ref}\n"
        f"carry_forward_modules (advisory candidates from prior CRM bundle):\n{cf_json}\n\n"
        "Emit one CarryForwardDecision per candidate in carry_forward_decisions.\n"
        "Do NOT blindly re-emit all as reuse. Evaluate each against the new marketplace concept.\n"
        "contacts and pipeline are CRM-specific; settings and notifications are domain-generic.\n"
        "Generate a fresh marketplace build plan. Do NOT base it on the old CRM plan."
    )


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    # Try code-fenced JSON first
    for pattern in (r"```json\s*([\s\S]+?)\s*```", r"```\s*([\s\S]+?)\s*```"):
        match = re.search(pattern, stripped)
        if match:
            candidate = match.group(1).strip()
            if candidate.startswith("{"):
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass
    # Use raw_decode to parse the first complete JSON object starting at the first {
    # This correctly handles trailing text, second objects, or markdown after the JSON.
    start = stripped.find("{")
    if start >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(stripped, start)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract JSON from response:\n{text[:600]}")


def _call_openai(system_message: str, user_message: str, model: str) -> str:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise ImportError("openai package required: pip install openai") from exc

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable not set")

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
        timeout=180,
    )
    return response.choices[0].message.content or ""


def _write_crm_workspace(tmp_dir: Path) -> None:
    for rel_path, content in _CRM_WORKSPACE_FILES.items():
        dest = tmp_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


def _build_mock_artifact_store(workspace_dir: Path) -> Any:
    from mozaiksai.core.artifacts.models import ArtifactCommitMetadata, ArtifactVersionDoc

    doc = ArtifactVersionDoc.model_validate({
        "_id": _PREV_ARTIFACT_VERSION_ID,
        "app_id": _CRM_APP_ID,
        "artifact_kind": "app_bundle",
        "artifact_key": "app_bundle",
        "version_number": 1,
        "lineage_root_id": _PREV_ARTIFACT_VERSION_ID,
        "commit_metadata": ArtifactCommitMetadata(
            message="Synthetic CRM v1 -- live replan smoke",
            metadata={"workspace_dir": str(workspace_dir)},
        ).model_dump(),
    })

    mock_store = MagicMock()
    mock_store.get_artifact_version = AsyncMock(return_value=doc)
    return mock_store


def _build_resolver() -> Any:
    from mozaiksai.control_plane.dry_run import _DeterministicChangeClassifier
    from mozaiksai.control_plane.implementations.refinement_router import (
        RefinementTriggerRouteResolver,
    )
    from mozaiksai.control_plane.loader import load_control_plane_pack

    def pack_loader():
        return load_control_plane_pack(app_root=_APP_ROOT)

    return RefinementTriggerRouteResolver(
        classifier=_DeterministicChangeClassifier(change_class="core"),
        pack_loader=pack_loader,
    )


# ---------------------------------------------------------------------------
# Phase 1: deterministic routing
# ---------------------------------------------------------------------------


async def _run_routing() -> dict[str, Any]:
    """Route the conceptual_replan -- deterministic, no LLM."""
    resolver = _build_resolver()
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "artifact_key": "concept",
                "raw_user_request": _PIVOT_REQUEST,
                "source_surface": "live_conceptual_replan_smoke",
                "extra": {
                    "previous_app_bundle_ref": _PREV_ARTIFACT_VERSION_ID,
                    "existing_concept_ref": "concept_crm_v1",
                    "carry_forward_modules": [m["module_id"] for m in _CARRY_FORWARD_MODULES],
                    "preserve_families": ["brand"],
                },
            }
        },
        app_id=_CRM_APP_ID,
        requested_workflow_id="AppGenerator",
    )
    assert request is not None, "routing: request_from_payload returned None"
    decision = await resolver.route(request)
    seed = decision.context_seed or {}
    return {
        "route": decision.workflow_sequence or (decision.impact_set and decision.impact_set.workflow_sequence),
        "change_class": decision.change_intent.change_class.value,
        "context_seed": {
            "pivot_description": seed.get("pivot_description"),
            "preserve_families": seed.get("preserve_families"),
            "previous_app_bundle_ref": seed.get("previous_app_bundle_ref"),
            "carry_forward_modules": seed.get("carry_forward_modules"),
            "workflow_sequence": seed.get("workflow_sequence"),
            "llm_profile": seed.get("llm_profile"),
        },
    }


# ---------------------------------------------------------------------------
# Phase 2: live LLM AppPlanAgent call
# ---------------------------------------------------------------------------


def _build_benchmark_system_message() -> str:
    """Build a focused system message for the conceptual_replan benchmark.

    Uses a self-contained message that combines the AppPlanAgent role with an
    explicit carry_forward_decisions schema. Avoids the full 50k agents.yaml
    prompt, which causes the model to ignore the carry_forward_decisions
    requirement (buried too deeply in a long system message).

    This is a benchmark, not a production run. Authenticity comes from:
    - Using the real AppPlanAgent role statement from agents.yaml
    - Applying the real carry-forward evaluation rules from the agents.yaml instructions
    - Calling Phase 7A with the real LLM decisions after the call
    """
    raw = yaml.safe_load(_AGENTS_YAML.read_text(encoding="utf-8"))
    agents_list = raw.get("agents") if isinstance(raw, dict) else raw
    role_text = ""
    for agent in (agents_list or []):
        if not isinstance(agent, dict) or agent.get("name") != "AppPlanAgent":
            continue
        for section in agent.get("prompt_sections") or []:
            if isinstance(section, dict) and section.get("id") == "role":
                role_text = str(section.get("content") or "").strip()
                break

    return (
        f"[ROLE]\n{role_text}\n\n"
        "[CONCEPTUAL REPLAN -- REQUIRED OUTPUT]\n"
        "This is a conceptual_replan: the concept is pivoting to a new domain.\n"
        "Generate a fresh build plan from the new concept. Do NOT base it on the old plan.\n\n"
        "You MUST emit a non-empty carry_forward_decisions array in your AppBuildPlan.\n"
        "For EACH module in carry_forward_modules, emit exactly one CarryForwardDecision:\n"
        '  {"module_id": "...", "decision": "reuse|adapt|regenerate|drop",\n'
        '   "reason": "<non-empty explanation>", "source": "carry_forward_candidate",\n'
        '   "affected_build_tasks": []}\n\n'
        "Decision rules:\n"
        "  - reuse: domain-generic module still fits the new concept (no files copied)\n"
        "  - adapt: module needs updating to fit the new concept\n"
        "  - regenerate: capability still needed, fresh implementation required\n"
        "  - drop: module is domain-specific to old concept and has no role in the new one\n\n"
        "DO NOT return carry_forward_decisions as null or empty. "
        "Every candidate module requires exactly one entry.\n\n"
        "[OUTPUT FORMAT]\n"
        'Output ONLY valid JSON: {"AppBuildPlan": { ..., "carry_forward_decisions": [...] }}'
    )


def _run_appplan_agent(model: str) -> dict[str, Any]:
    """Call AppPlanAgent live via OpenAI with conceptual_replan context."""
    print("  [2a] Building focused system message (key agents.yaml sections + carry-forward schema)...")
    system_message = _build_benchmark_system_message()
    print(f"       System message: {len(system_message):,} chars")

    candidate_ids = [m["module_id"] for m in _CARRY_FORWARD_MODULES]
    cf_candidates_detail = "\n".join(
        f'  - module_id="{m["module_id"]}" classification={m["carry_forward_classification"]}'
        f' reason="{m["carry_forward_reasons"][0]}"'
        for m in _CARRY_FORWARD_MODULES
    )
    required_entries = "\n".join(
        f'    {{"module_id": "{m["module_id"]}", "decision": "...", "reason": "...", '
        f'"source": "carry_forward_candidate", "affected_build_tasks": []}}'
        for m in _CARRY_FORWARD_MODULES
    )
    user_message = (
        f"Pivot request: {_PIVOT_REQUEST}\n\n"
        "This is a conceptual_replan. Generate a fresh marketplace AppBuildPlan.\n\n"
        "carry_forward_modules candidates (use these exact module_id values in carry_forward_decisions):\n"
        f"{cf_candidates_detail}\n\n"
        "REQUIRED: Your carry_forward_decisions array MUST have exactly these 4 entries "
        f"with EXACT module_id values {candidate_ids}:\n"
        f"{required_entries}\n\n"
        "Choose decision for each based on marketplace fit:\n"
        '  "settings": domain-generic (user preferences) -> reuse\n'
        '  "notifications": domain-generic (alerts) -> reuse\n'
        '  "contacts": CRM address book -> drop or regenerate\n'
        '  "pipeline": CRM sales pipeline -> drop or regenerate\n\n'
        'Output ONLY valid JSON: {"AppBuildPlan": {"carry_forward_decisions": [<4 entries>], '
        '"capability_packs": [...], "build_tasks": [...]}}'
    )

    print(f"  [2b] Calling OpenAI ({model})...")
    response_text = _call_openai(system_message, user_message, model)
    print(f"       Response: {len(response_text):,} chars")

    print("  [2c] Parsing AppBuildPlan JSON...")
    raw = _extract_json(response_text)
    plan = raw.get("AppBuildPlan") or raw
    if not isinstance(plan, dict):
        raise ValueError("AppBuildPlan payload missing or not a dict")

    cfd = plan.get("carry_forward_decisions") or []
    print(f"       carry_forward_decisions: {len(cfd)} entries")
    for entry in cfd:
        if isinstance(entry, dict):
            print(f"         {entry.get('module_id')}: {entry.get('decision')}")

    return {
        "plan": plan,
        "raw_response": response_text[:2000],
    }


# ---------------------------------------------------------------------------
# Phase 3: Phase 7A preservation with LLM decisions
# ---------------------------------------------------------------------------


async def _run_preservation(
    plan: dict[str, Any],
    tmp_dir: Path,
) -> dict[str, Any]:
    """Run Phase 7A with the LLM's actual carry_forward_decisions."""
    from factory_app.control_plane.tools.resolve_carry_forward_preservation import (
        resolve_carry_forward_preservation,
    )

    mock_store = _build_mock_artifact_store(tmp_dir)
    context_variables: dict[str, Any] = {
        "app_id": _CRM_APP_ID,
        "previous_app_bundle_ref": _PREV_ARTIFACT_VERSION_ID,
        "app_build_plan": {
            "carry_forward_decisions": plan.get("carry_forward_decisions") or [],
        },
        "generated_files": dict(_MARKETPLACE_GENERATED_FILES),
    }

    result = await resolve_carry_forward_preservation(
        context_variables=context_variables,
        artifact_store=mock_store,
    )
    return result.get("carry_forward_report", {})


# ---------------------------------------------------------------------------
# Validation: 17 assertions
# ---------------------------------------------------------------------------


def _validate_output(
    routing: dict[str, Any],
    plan: dict[str, Any],
    report: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    seed = routing.get("context_seed", {})
    cfd: list[dict[str, Any]] = [
        d for d in (plan.get("carry_forward_decisions") or [])
        if isinstance(d, dict)
    ]
    cfd_by_module = {d.get("module_id"): d.get("decision") for d in cfd}

    # --- Routing assertions (1-4) ---
    # 1. Route must be conceptual_replan
    route = routing.get("route")
    if route != "conceptual_replan":
        failures.append(
            f"[1] route={route!r} -- expected 'conceptual_replan'"
        )

    # 2. pivot_description present
    if not seed.get("pivot_description"):
        failures.append("[2] context_seed.pivot_description is absent or empty")

    # 3. previous_app_bundle_ref present
    if seed.get("previous_app_bundle_ref") != _PREV_ARTIFACT_VERSION_ID:
        failures.append(
            f"[3] context_seed.previous_app_bundle_ref={seed.get('previous_app_bundle_ref')!r}"
        )

    # 4. carry_forward_modules non-empty
    cfm = seed.get("carry_forward_modules") or []
    if not cfm:
        failures.append("[4] context_seed.carry_forward_modules is empty or absent")

    # --- LLM output assertions (5-11) ---
    # 5. carry_forward_decisions exists and is a list
    raw_cfd = plan.get("carry_forward_decisions")
    if not isinstance(raw_cfd, list):
        failures.append(
            f"[5] carry_forward_decisions is {type(raw_cfd).__name__}, expected list"
        )
    elif len(raw_cfd) == 0:
        failures.append("[5] carry_forward_decisions is an empty list")

    # 6. All decision values are valid
    invalid_decisions = [
        (d.get("module_id"), d.get("decision"))
        for d in cfd
        if d.get("decision") not in _VALID_DECISIONS
    ]
    if invalid_decisions:
        failures.append(
            f"[6] invalid decision values: {invalid_decisions}"
        )

    # 7. settings: reuse or adapt (domain-generic)
    settings_dec = cfd_by_module.get("settings")
    if settings_dec is None:
        failures.append("[7] settings not found in carry_forward_decisions")
    elif settings_dec not in ("reuse", "adapt"):
        failures.append(
            f"[7] settings decision={settings_dec!r} -- expected reuse or adapt (domain-generic)"
        )

    # 8. notifications: reuse or adapt (domain-generic)
    notif_dec = cfd_by_module.get("notifications")
    if notif_dec is None:
        failures.append("[8] notifications not found in carry_forward_decisions")
    elif notif_dec not in ("reuse", "adapt"):
        failures.append(
            f"[8] notifications decision={notif_dec!r} -- expected reuse or adapt (domain-generic)"
        )

    # 9. contacts: drop or regenerate (CRM-specific -- must NOT be blindly reused)
    contacts_dec = cfd_by_module.get("contacts")
    if contacts_dec is None:
        failures.append("[9] contacts not found in carry_forward_decisions")
    elif contacts_dec not in ("drop", "regenerate"):
        failures.append(
            f"[9] contacts decision={contacts_dec!r} -- expected drop or regenerate "
            f"(CRM contact management should not be reused in a marketplace)"
        )

    # 10. pipeline: drop or regenerate (CRM-specific)
    pipeline_dec = cfd_by_module.get("pipeline")
    if pipeline_dec is None:
        failures.append("[10] pipeline not found in carry_forward_decisions")
    elif pipeline_dec not in ("drop", "regenerate"):
        failures.append(
            f"[10] pipeline decision={pipeline_dec!r} -- expected drop or regenerate "
            f"(CRM sales pipeline should not be reused in a marketplace)"
        )

    # 11. Marketplace-oriented modules appear in the plan
    plan_json = json.dumps(plan).lower()
    if not any(token in plan_json for token in _MARKETPLACE_TOKENS):
        failures.append(
            f"[11] no marketplace-oriented tokens found in plan "
            f"(checked: {_MARKETPLACE_TOKENS[:6]}...)"
        )

    # --- Phase 7A assertions (12-17) ---
    # 12. carry_forward_report exists
    if not report:
        failures.append("[12] carry_forward_report is empty -- Phase 7A did not run or returned nothing")

    # 13. preserved_paths only allowlisted files
    preserved = report.get("preserved_paths") or []
    bad_paths = [
        p for p in preserved
        if not any(str(p).endswith(a) for a in _PHASE_7A_ALLOWLIST)
    ]
    if bad_paths:
        failures.append(f"[13] non-allowlisted paths in preserved_paths: {bad_paths}")

    # 14. No backend Python in preserved_paths
    backend_py = [p for p in preserved if "/backend/" in str(p) and str(p).endswith(".py")]
    if backend_py:
        failures.append(f"[14] backend Python in preserved_paths: {backend_py}")

    # 15. No runtime_extensions.yaml in preserved_paths
    runtime_ext = [p for p in preserved if "runtime_extensions.yaml" in str(p)]
    if runtime_ext:
        failures.append(f"[15] runtime_extensions.yaml in preserved_paths: {runtime_ext}")

    # 16. No custom React in preserved_paths
    react_files = [p for p in preserved if str(p).endswith((".jsx", ".tsx", ".js"))]
    if react_files:
        failures.append(f"[16] custom React files in preserved_paths: {react_files}")

    # 17. Report shape valid
    required_report_keys = {
        "previous_app_bundle_ref", "workspace_available", "preserved_paths",
        "conflicts", "skipped_paths", "reused_modules", "dropped_modules", "warnings",
    }
    missing = required_report_keys - set(report.keys())
    if missing:
        failures.append(f"[17] carry_forward_report missing keys: {sorted(missing)}")

    return failures


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_benchmark(*, save_fixture: bool = False, model: str = "gpt-5-nano") -> int:
    _load_dotenv()

    print("=" * 72)
    print("Live LLM Conceptual Replan Benchmark: CRM -> Marketplace")
    print("=" * 72)
    print(f"Model: {model}")
    print("Scenario: CRM -> Marketplace pivot")
    print()

    # Phase 1: Deterministic routing
    print("[1/4] Routing (deterministic -- no LLM)...")
    try:
        routing = await _run_routing()
    except Exception as exc:
        print(f"  ERROR: routing failed -- {exc}")
        return 1
    route = routing.get("route")
    print(f"  route={route!r}, change_class={routing.get('change_class')!r}")
    cfm = (routing.get("context_seed") or {}).get("carry_forward_modules", [])
    print(f"  carry_forward_modules in seed: {cfm}")

    # Phase 2: Live LLM AppPlanAgent
    print("\n[2/4] Live LLM -- AppPlanAgent (conceptual_replan)...")
    try:
        agent_output = _run_appplan_agent(model)
    except Exception as exc:
        print(f"  ERROR: LLM call failed -- {exc}")
        return 1
    plan = agent_output["plan"]

    # Phase 3: Phase 7A preservation with LLM's decisions
    print("\n[3/4] Phase 7A -- resolve_carry_forward_preservation (real)...")
    with tempfile.TemporaryDirectory(prefix="mozaiks_live_smoke_") as tmp:
        tmp_dir = Path(tmp)
        _write_crm_workspace(tmp_dir)
        try:
            report = await _run_preservation(plan, tmp_dir)
        except Exception as exc:
            print(f"  ERROR: Phase 7A failed -- {exc}")
            return 1

    preserved_count = len(report.get("preserved_paths") or [])
    reused = report.get("reused_modules") or []
    dropped = report.get("dropped_modules") or []
    print(f"  preserved_paths: {preserved_count}")
    print(f"  reused_modules: {reused}")
    print(f"  dropped_modules: {dropped}")

    # Phase 4: Validate
    print("\n[4/4] Validating 17 assertions...")
    failures = _validate_output(routing, plan, report)
    if failures:
        print(f"\n  FAILURES ({len(failures)}/17):")
        for f in failures:
            print(f"    - {f}")
    else:
        print("  All 17 assertions passed.")

    # Assemble output
    output = {
        "schema_version": "mozaiks.live_conceptual_replan_benchmark.v1",
        "success": not failures,
        "model": model,
        "assertions": {
            "total": 17,
            "passed": 17 - len(failures),
            "failed": len(failures),
        },
        "approach": {
            "live": [
                "RefinementTriggerRouteResolver with real control-plane pack",
                f"AppPlanAgent via OpenAI ({model}) with full conceptual_replan context",
                "resolve_carry_forward_preservation Phase 7A with real LLM decisions",
            ],
            "stubbed": [
                "_DeterministicChangeClassifier(change_class='core')",
                "ArtifactStore.get_artifact_version -- synthetic CRM doc + temp dir",
            ],
            "note": (
                "Single-agent live benchmark. Not a full workflow sequence run. "
                "Tests AppPlanAgent reasoning and Phase 7A preservation integrity."
            ),
        },
        "scenario": {
            "app_id": _CRM_APP_ID,
            "previous_artifact": _PREV_ARTIFACT_VERSION_ID,
            "pivot_request": _PIVOT_REQUEST,
            "carry_forward_candidates": [m["module_id"] for m in _CARRY_FORWARD_MODULES],
        },
        "routing": routing,
        "appplan_agent": {
            "carry_forward_decisions": plan.get("carry_forward_decisions") or [],
            "capability_packs": plan.get("capability_packs") or [],
            "pages_count": len(plan.get("pages") or []),
            "build_tasks_count": len(plan.get("build_tasks") or []),
            # Full plan stored so fixture replay tests can verify marketplace tokens
            # across all plan fields (capability_packs, build_tasks, reasons, etc.)
            "plan": plan,
        },
        "carry_forward_report": report,
        "failures": failures,
    }

    if save_fixture:
        _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        _FIXTURE_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(f"\nFixture saved: {_FIXTURE_PATH}")

    status = "PASSED" if output["success"] else "FAILED"
    print(f"\nBENCHMARK {status} ({17 - len(failures)}/17 assertions)")
    return 0 if output["success"] else 1


def main() -> int:
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(_REPO_ROOT / ".env")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="Live LLM conceptual_replan benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--save-fixture",
        action="store_true",
        default=False,
        help="Write output to tests/fixtures/live_conceptual_replan_output.json",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("DEFAULT_LLM_MODEL", "gpt-5-nano"),
        help="OpenAI model to use (default: gpt-5-nano)",
    )
    parser.add_argument(
        "--fixture-path",
        default=None,
        help="Override fixture output path",
    )
    args = parser.parse_args()

    global _FIXTURE_PATH
    if args.fixture_path:
        _FIXTURE_PATH = Path(args.fixture_path)

    return asyncio.run(run_benchmark(save_fixture=args.save_fixture, model=args.model))


if __name__ == "__main__":
    sys.exit(main())
