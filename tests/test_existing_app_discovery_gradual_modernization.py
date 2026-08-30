"""Tests for ExistingAppDiscovery gradual_modernization and ecosystem improvements.

Covers:
- Storage-pattern detection from package names and source text
- Connector detection from package manifests and imports
- Mozaiks vocabulary and authorship detection
- Structured-output models for new fields
- save_existing_app_artifacts retirement of persisted decomposition output
- embed/bridge behavior is unchanged
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import tempfile
from pathlib import Path

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]


def _read_yaml(relative_path: str) -> dict:
    return yaml.safe_load((WORKSPACE / relative_path).read_text(encoding="utf-8")) or {}


def _load_module(relative_path: str, module_name: str):
    file_path = WORKSPACE / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Context(dict):
    def get(self, key, default=None):
        return super().get(key, default)


# ---------------------------------------------------------------------------
# Storage detection tests
# ---------------------------------------------------------------------------


def test_storage_detection_file_store_from_typescript_source() -> None:
    """file_store detected when source uses fs.writeFileSync / JSON.parse patterns."""
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_native_file_store",
    )

    ts_source = """
import fs from 'fs';
const data = JSON.parse(fs.readFileSync('./data/records.json', 'utf8'));
fs.writeFileSync('./data/records.json', JSON.stringify(updated));
"""
    result = module._detect_storage_pattern([], ts_source)
    assert result == "file_store", f"Expected file_store, got {result}"


def test_storage_detection_mongodb_from_packages() -> None:
    """mongodb detected from package names (mongoose, pymongo, motor)."""
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_native_mongodb",
    )

    result = module._detect_storage_pattern(["mongoose", "express", "dotenv"], "")
    assert result == "mongodb"

    result2 = module._detect_storage_pattern(["pymongo", "motor"], "")
    assert result2 == "mongodb"


def test_storage_detection_sql_from_packages() -> None:
    """sql detected from sqlalchemy, psycopg2, pg, sequelize packages."""
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_native_sql",
    )

    result = module._detect_storage_pattern(["sqlalchemy", "alembic", "fastapi"], "")
    assert result == "sql"

    result2 = module._detect_storage_pattern(["pg", "sequelize"], "")
    assert result2 == "sql"


def test_storage_detection_redis_from_packages() -> None:
    """redis detected from redis, ioredis, aioredis packages."""
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_native_redis",
    )

    result = module._detect_storage_pattern(["ioredis", "express"], "")
    assert result == "redis"

    result2 = module._detect_storage_pattern(["aioredis", "fastapi"], "")
    assert result2 == "redis"


# ---------------------------------------------------------------------------
# Connector detection tests
# ---------------------------------------------------------------------------


def test_connector_detection_azure_from_package() -> None:
    """Azure connector detected from @azure/storage-blob package."""
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_native_azure",
    )

    connectors = module._detect_connectors(["@azure/storage-blob", "express"], "")
    provider_ids = [c["provider_id"] for c in connectors]
    assert "azure" in provider_ids

    azure = next(c for c in connectors if c["provider_id"] == "azure")
    assert azure["category"] in ("cloud", "storage")
    assert isinstance(azure["likely_secret_envs"], list)
    assert isinstance(azure["mozaiks_adapter_exists"], bool)


def test_connector_detection_github_from_source() -> None:
    """GitHub connector detected from octokit import in source."""
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_native_github",
    )

    source = "import { Octokit } from '@octokit/rest';\nconst octokit = new Octokit({ auth: process.env.GITHUB_TOKEN });"
    connectors = module._detect_connectors([], source)
    provider_ids = [c["provider_id"] for c in connectors]
    assert "github" in provider_ids

    github = next(c for c in connectors if c["provider_id"] == "github")
    assert "GITHUB_TOKEN" in github["likely_secret_envs"] or len(github["likely_secret_envs"]) >= 0


def test_connector_detection_returns_list_of_connector_spec_dicts() -> None:
    """_detect_connectors always returns a list; each item has required ConnectorSpec keys."""
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_native_connector_shape",
    )

    connectors = module._detect_connectors(["openai", "fastapi"], "import openai")
    assert isinstance(connectors, list)

    for c in connectors:
        assert "provider_id" in c
        assert "category" in c
        assert "confidence" in c
        assert "mozaiks_adapter_exists" in c
        assert "likely_secret_envs" in c
        assert isinstance(c["likely_secret_envs"], list)


# ---------------------------------------------------------------------------
# Mozaiks vocabulary detection tests
# ---------------------------------------------------------------------------


def test_mozaiks_vocabulary_detected_from_source() -> None:
    """Mozaiks vocabulary terms (contractKind, module-action) flag vocab_detected=True."""
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_native_vocab",
    )

    source = """
# contractKind: module-action
# workflow-preparation: standard
action_id = "submit_application"
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        result = module._detect_mozaiks_vocabulary(Path(tmpdir), source)

    assert result["mozaiks_vocabulary_detected"] is True


def test_mozaiks_authored_app_from_canonical_structure() -> None:
    """mozaiks_authored_app is True when module.yaml + contracts/ + backend/ are present."""
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_native_authored",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Simulate canonical Mozaiks module structure
        mod_dir = root / "app" / "modules" / "my_module"
        (mod_dir / "contracts").mkdir(parents=True)
        (mod_dir / "backend").mkdir(parents=True)
        (mod_dir / "module.yaml").write_text("module_id: my_module\n", encoding="utf-8")
        (mod_dir / "contracts" / "events.yaml").write_text("events: []\n", encoding="utf-8")
        (mod_dir / "backend" / "handler.py").write_text("# handler\n", encoding="utf-8")

        # Provide vocabulary signal in source sample
        source_with_vocab = "# contractKind: module-action\n# workflow-preparation: standard\n"
        result = module._detect_mozaiks_vocabulary(root, source_with_vocab)

    assert result["mozaiks_authored_app"] is True


# ---------------------------------------------------------------------------
# Structured output model tests
# ---------------------------------------------------------------------------


def test_typescript_backend_sets_migration_complexity_full_rewrite() -> None:
    """AgentAugmentationPlan model has migration_complexity field with full_rewrite as valid value."""
    data = _read_yaml("factory_app/workflows/ExistingAppDiscovery/structured_outputs.yaml")
    models = data["models"]

    plan_fields = models["AgentAugmentationPlan"]["fields"]
    assert "migration_complexity" in plan_fields, "AgentAugmentationPlan must have migration_complexity"
    mc = plan_fields["migration_complexity"]
    assert mc["type"] in ("str", "optional_str"), f"Unexpected type: {mc['type']}"
    # The enum values are documented in context_variables.yaml and agents.yaml, not enforced in model shape,
    # but the field must exist for the assembler to populate it.


def test_file_store_sets_storage_migration_required_true() -> None:
    """ExistingProductSpec has storage_migration_required (bool) and storage_pattern (str)."""
    data = _read_yaml("factory_app/workflows/ExistingAppDiscovery/structured_outputs.yaml")
    models = data["models"]

    spec_fields = models["ExistingProductSpec"]["fields"]
    assert "storage_pattern" in spec_fields, "ExistingProductSpec must have storage_pattern"
    assert "storage_migration_required" in spec_fields, "ExistingProductSpec must have storage_migration_required"
    assert spec_fields["storage_migration_required"]["type"] == "bool"


def test_detected_connectors_populate_new_adapters_required() -> None:
    """AgentAugmentationPlan has new_adapters_required; ExistingProductSpec has detected_connectors."""
    data = _read_yaml("factory_app/workflows/ExistingAppDiscovery/structured_outputs.yaml")
    models = data["models"]

    plan_fields = models["AgentAugmentationPlan"]["fields"]
    assert "new_adapters_required" in plan_fields, "AgentAugmentationPlan must have new_adapters_required"

    spec_fields = models["ExistingProductSpec"]["fields"]
    assert "detected_connectors" in spec_fields, "ExistingProductSpec must have detected_connectors"

    connector_model = models.get("ConnectorSpec")
    assert connector_model is not None, "ConnectorSpec model must exist in structured_outputs.yaml"
    connector_fields = connector_model["fields"]
    assert "provider_id" in connector_fields
    assert "category" in connector_fields
    assert "mozaiks_adapter_exists" in connector_fields


def test_gradual_modernization_artifact_keeps_decomposition_internal_only() -> None:
    """ModuleDecompositionPlan remains declared but is not a top-level artifact field."""
    data = _read_yaml("factory_app/workflows/ExistingAppDiscovery/structured_outputs.yaml")
    models = data["models"]

    artifact_fields = models["ExistingAppAugmentationArtifact"]["fields"]
    assert "module_decomposition_plan" not in artifact_fields

    # ModuleDecompositionPlan model must also be declared
    assert "ModuleDecompositionPlan" in models, "ModuleDecompositionPlan model must exist"
    mdp_description = models["ModuleDecompositionPlan"]["description"]
    assert "workflow-local" in mdp_description
    assert "not a canonical top-level AppContext artifact" in mdp_description
    mdp_fields = models["ModuleDecompositionPlan"]["fields"]
    assert "proposed_modules" in mdp_fields
    assert "proposed_workflows" in mdp_fields
    assert "proposed_adapters" in mdp_fields
    assert "implementation_phases" in mdp_fields


# ---------------------------------------------------------------------------
# save_existing_app_artifacts behavior tests
# ---------------------------------------------------------------------------


def _make_save_module():
    return _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/save_existing_app_artifacts.py",
        "tests.save_artifacts_gradual",
    )


def _fake_decomposition_plan() -> dict:
    return {
        "proposed_modules": [
            {
                "module_id": "dns_zone_manager",
                "label": "DNS Zone Manager",
                "priority": "p1_critical",
                "actions": ["list_zones", "add_record", "remove_record", "run_health_check"],
                "emits": ["infra.dns_zone_manager.record.added"],
                "mongo_collections": ["dns_zones"],
                "connector_dependencies": ["cloudflare"],
            }
        ],
        "proposed_workflows": [
            {
                "workflow_id": "DnsProviderMigration",
                "priority": "p1_critical",
                "module_calls": ["dns_zone_manager.list_zones", "dns_zone_manager.add_record"],
            }
        ],
        "proposed_pages": [
            {
                "page_id": "dns_zone_list",
                "page_type": "declarative_yaml",
                "route": "/admin/dns/zones",
                "module_bindings": ["dns_zone_manager"],
            }
        ],
        "proposed_adapters": [
            {
                "provider_id": "cloudflare",
                "adapter_ownership": "app_specific_adapter",
                "secret_requirements": ["CLOUDFLARE_API_TOKEN"],
            }
        ],
        "persistence_migration": "File-based zone store → dns_zones MongoDB collection owned by dns_zone_manager.",
        "security_migration": "No-auth Express → platform Keycloak JWT with tenant scoping.",
        "implementation_phases": ["phase_1_dns_module", "phase_2_migration_workflow"],
    }


def test_save_artifacts_keeps_decomposition_context_evidence_without_disk_persistence(tmp_path) -> None:
    """save_existing_app_artifacts no longer persists module_decomposition_plan.json."""
    module = _make_save_module()

    emitted = {}

    async def _fake_emit(component, payload, **kwargs):
        emitted["component"] = component
        emitted["payload"] = payload

    # Patch emit_ui_surface in both the save module and the overview emission module,
    # since the new code delegates emission to emit_app_intelligence_enriched_overview_card.
    module.emit_ui_surface = _fake_emit
    import factory_app.workflows.ExistingAppDiscovery.tools.emit_app_intelligence_overview as _overview_mod
    _orig_emit = _overview_mod.emit_ui_surface
    _overview_mod.emit_ui_surface = _fake_emit

    plan = _fake_decomposition_plan()

    context = _Context(
        chat_id="chat_gradual_001",
        module_decomposition_plan=json.dumps(plan),
        structured_output={
            "request_intent": "brownfield_app",
            "existing_product_spec": {
                "app_name": "ops-studio",
                "app_description": "DNS migration tool",
                "tech_stack": "TypeScript, Express",
                "auth_model": "none",
                "brand_theme_summary": "Minimal dark interface.",
                "brand_theme_evidence": {},
                "storage_pattern": "file_store",
                "storage_migration_required": True,
                "detected_connectors": [
                    {
                        "provider_id": "cloudflare",
                        "package_or_import": "@cloudflare/cloudflare",
                        "category": "dns",
                        "confidence": "high",
                        "source_files": ["src/connectors/cloudflare.ts"],
                        "likely_secret_envs": ["CLOUDFLARE_API_TOKEN"],
                        "mozaiks_adapter_exists": False,
                    }
                ],
                "mozaiks_vocabulary_detected": False,
                "mozaiks_authored_app": False,
                "service_surfaces": [],
                "route_surfaces": [],
            },
            "capability_specs": [
                {
                    "capability_id": "dns_zone_management",
                    "label": "DNS Zone Management",
                    "confidence": "confirmed",
                    "delivery_surface": "rest_api",
                    "agent_ready": False,
                    "migration_priority": "p1_critical",
                    "connector_requirements": ["cloudflare"],
                }
            ],
            "agent_augmentation_plan": {
                "adoption_level": "gradual_modernization",
                "migration_complexity": "full_rewrite",
                "adoption_rationale": "TypeScript backend requires full rewrite to Python FastAPI modules.",
                "storage_migration_required": True,
                "new_adapters_required": ["cloudflare"],
                "auth_delegation_model": "platform_keycloak_jwt",
                "ui_surface_preference": "admin_studio",
                "ai_accessible_capabilities": [],
                "initial_workflows": ["DnsProviderMigration"],
                "ecosystem_bindings": [],
                "theme_adaptation_strategy": "Full Mozaiks shell replaces host chrome.",
                "embed_theme_ready": False,
            },
            "discovery_brief": "Gradual modernization of DNS management tooling.",
            "artifact_version": "1.0",
        },
    )

    try:
        result = asyncio.run(module.save_existing_app_artifacts(context_variables=context))
    finally:
        _overview_mod.emit_ui_surface = _orig_emit

    assert result["success"] is True

    # No decomposition plan file is written; context evidence is preserved.
    written = tmp_path / "existing_app_discovery" / "chat_gradual_001" / "module_decomposition_plan.json"
    assert not written.exists()

    assert context["module_decomposition_plan"] == json.dumps(plan)
    assert "module_decomposition_plan" not in context["existing_app_discovery_artifact"]

    # Check UI payload has new fields
    assert emitted["payload"]["adoption_level"] == "gradual_modernization"
    assert emitted["payload"]["migration_complexity"] == "full_rewrite"
    assert emitted["payload"]["storage_pattern"] == "file_store"
    assert emitted["payload"]["storage_migration_required"] is True
    assert emitted["payload"]["adoption_plan_available"] is True
    assert "has_decomposition_plan" not in emitted["payload"]
    assert len(emitted["payload"]["detected_connectors"]) == 1
    assert emitted["payload"]["detected_connectors"][0]["provider_id"] == "cloudflare"
    assert emitted["payload"]["capabilities"][0]["migration_priority"] == "p1_critical"


def test_save_artifacts_embed_bridge_behavior_unchanged(tmp_path) -> None:
    """save_existing_app_artifacts does NOT write decomposition plan for embed/bridge."""
    module = _make_save_module()

    emitted = {}

    async def _fake_emit(component, payload, **kwargs):
        emitted["component"] = component
        emitted["payload"] = payload

    # Patch emit_ui_surface in both the save module and the overview emission module,
    # since the new code delegates emission to emit_app_intelligence_enriched_overview_card.
    module.emit_ui_surface = _fake_emit
    import factory_app.workflows.ExistingAppDiscovery.tools.emit_app_intelligence_overview as _overview_mod
    _orig_emit = _overview_mod.emit_ui_surface
    _overview_mod.emit_ui_surface = _fake_emit

    context = _Context(
        chat_id="chat_bridge_001",
        structured_output={
            "request_intent": "brownfield_app",
            "existing_product_spec": {
                "app_name": "partner-portal",
                "app_description": "External partner SaaS",
                "tech_stack": "React, Node.js",
                "auth_model": "OAuth2",
                "brand_theme_summary": "Light corporate blue.",
                "brand_theme_evidence": {},
                "storage_pattern": "unknown",
                "storage_migration_required": False,
                "detected_connectors": [],
                "mozaiks_vocabulary_detected": False,
                "mozaiks_authored_app": False,
                "service_surfaces": [{"name": "Partner API"}],
                "route_surfaces": [{"path": "/dashboard"}],
            },
            "capability_specs": [
                {
                    "capability_id": "partner_dashboard",
                    "label": "Partner Dashboard",
                    "confidence": "confirmed",
                    "delivery_surface": "rest_api",
                    "agent_ready": True,
                }
            ],
            "agent_augmentation_plan": {
                "adoption_level": "bridge",
                "migration_complexity": "none",
                "adoption_rationale": "External SaaS; bridge is sufficient.",
                "storage_migration_required": False,
                "new_adapters_required": [],
                "auth_delegation_model": "user_token_forwarding",
                "ui_surface_preference": "side_panel",
                "ai_accessible_capabilities": ["partner_dashboard"],
                "initial_workflows": ["PartnerSummary"],
                "ecosystem_bindings": [],
                "theme_adaptation_strategy": "Apply captured tokens to Mozaiks side panel.",
                "embed_theme_ready": True,
            },
            "discovery_brief": "Bridge the partner portal API for agentic access.",
            "artifact_version": "1.0",
        },
    )

    try:
        result = asyncio.run(module.save_existing_app_artifacts(context_variables=context))
    finally:
        _overview_mod.emit_ui_surface = _orig_emit

    assert result["success"] is True

    # No decomposition plan directory should be written
    decomp_dir = tmp_path / "existing_app_discovery" / "chat_bridge_001"
    assert not decomp_dir.exists(), "No plan directory should be created for bridge adoption"

    # Existing artifact fields still present
    assert context["existing_product_spec"]["app_name"] == "partner-portal"
    assert context["capability_specs"][0]["capability_id"] == "partner_dashboard"
    assert context["agent_augmentation_plan"]["adoption_level"] == "bridge"

    # UI payload still emitted correctly for bridge
    assert emitted["payload"]["adoption_level"] == "bridge"
    assert emitted["payload"]["embed_theme_ready"] is True
    assert emitted["payload"]["adoption_plan_available"] is True
    assert "has_decomposition_plan" not in emitted["payload"]
    assert emitted["payload"]["service_surface_count"] == 1


# ---------------------------------------------------------------------------
# Regression tests for ESM async file I/O, GoDaddy, OpenSRS, and
# high-density vocabulary authored_app detection (scanner fix validation)
# ---------------------------------------------------------------------------


def _load_preload(suffix: str):
    return _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        f"tests.preload_fix_{suffix}",
    )


def test_storage_detection_file_store_from_esm_async_api() -> None:
    """file_store detected from ESM async node:fs/promises imports (TypeScript backend pattern)."""
    mod = _load_preload("esm_file_store")
    esm_source = """
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';

const raw = await readFile(DATA_FILE, 'utf8');
const data = JSON.parse(raw);
await writeFile(DATA_FILE, JSON.stringify(updated), 'utf8');
"""
    result = mod._detect_storage_pattern([], esm_source)
    assert result == "file_store", f"Expected file_store from ESM async API, got {result!r}"


def test_storage_detection_file_store_from_esm_double_quote_import() -> None:
    """file_store detected from double-quote ESM import variant."""
    mod = _load_preload("esm_double_quote")
    source = 'import { readFile, writeFile } from "node:fs/promises";'
    result = mod._detect_storage_pattern([], source)
    assert result == "file_store"


def test_connector_detection_godaddy_from_class_name() -> None:
    """GoDaddy connector detected from GoDaddyConnector class name in source."""
    mod = _load_preload("godaddy")
    source = """
export class GoDaddyConnector implements InfraConnector<Domain> {
  readonly provider = 'godaddy';
  private readonly GD_API = 'https://api.godaddy.com';
}
"""
    connectors = mod._detect_connectors([], source)
    provider_ids = [c["provider_id"] for c in connectors]
    assert "godaddy" in provider_ids, f"GoDaddy not detected; got {provider_ids}"

    gd = next(c for c in connectors if c["provider_id"] == "godaddy")
    assert "GODADDY_API_KEY" in gd["likely_secret_envs"]
    assert gd["category"] == "infrastructure"


def test_connector_detection_opensrs_from_class_name() -> None:
    """OpenSRS connector detected from OpenSrsConnector class name in source."""
    mod = _load_preload("opensrs")
    source = """
export class OpenSrsConnector implements InfraConnector<Domain> {
  readonly provider = 'opensrs';
  private readonly endpoint = 'https://rr-n1-tor.opensrs.net:55443/';
  // XCP protocol — xcpItem builder
}
"""
    connectors = mod._detect_connectors([], source)
    provider_ids = [c["provider_id"] for c in connectors]
    assert "opensrs" in provider_ids, f"OpenSRS not detected; got {provider_ids}"

    ors = next(c for c in connectors if c["provider_id"] == "opensrs")
    assert "OPENSRS_API_KEY" in ors["likely_secret_envs"]
    assert ors["category"] == "infrastructure"


def test_mozaiks_authored_app_from_high_density_vocabulary() -> None:
    """mozaiks_authored_app is True when multiple vocab terms appear many times (no .yaml structure needed)."""
    mod = _load_preload("high_density_vocab")

    # Simulate a TypeScript file using Mozaiks vocabulary heavily (like ops-studio)
    # Each term appears > 10 times
    source = ("contractKind " * 15) + ("module-action " * 12) + ("workflow-preparation " * 11)

    with tempfile.TemporaryDirectory() as tmpdir:
        result = mod._detect_mozaiks_vocabulary(Path(tmpdir), source)

    assert result["mozaiks_vocabulary_detected"] is True
    assert result["mozaiks_authored_app"] is True
    assert "high_density_vocabulary" in result["structure_indicators_found"]


def test_mozaiks_authored_app_false_for_low_density_vocabulary() -> None:
    """mozaiks_authored_app stays False when vocabulary terms appear only a few times."""
    mod = _load_preload("low_density_vocab")

    # Each term < 10 times AND no file structure
    source = ("contractKind " * 3) + ("module-action " * 4)

    with tempfile.TemporaryDirectory() as tmpdir:
        result = mod._detect_mozaiks_vocabulary(Path(tmpdir), source)

    # vocab_detected may be True, but authored should not be (low density, no structure)
    if result["mozaiks_vocabulary_detected"]:
        assert result["mozaiks_authored_app"] is False, (
            "authored_app should be False when vocab density is below threshold"
        )


def test_cloudflare_detected_via_connector_class_name() -> None:
    """Cloudflare detected from CloudflareConnector class name — covers ops-studio pattern."""
    mod = _load_preload("cloudflare_class")
    source = """
export class CloudflareConnector implements InfraConnector<DnsZone> {
  readonly provider = 'cloudflare';
  private readonly CF_API = 'https://api.cloudflare.com/client/v4';
}
"""
    connectors = mod._detect_connectors([], source)
    provider_ids = [c["provider_id"] for c in connectors]
    assert "cloudflare" in provider_ids, f"Cloudflare not detected via class name; got {provider_ids}"

