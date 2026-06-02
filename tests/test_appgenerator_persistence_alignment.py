from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APPGEN = ROOT / "factory_app" / "workflows" / "AppGenerator"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _agent_block(agent_name: str) -> str:
    text = _read(APPGEN / "agents.yaml")
    marker = f"- name: {agent_name}"
    start = text.index(marker)
    next_start = text.find("\n- name: ", start + len(marker))
    if next_start == -1:
        return text[start:]
    return text[start:next_start]


def test_database_agent_uses_intent_artifacts_not_live_database_tools() -> None:
    block = _agent_block("DatabaseAgent")

    assert "data_contract" in block
    assert "config/data.json" in block
    assert "config/data_migrations/{migration_id}.json" in block
    assert "pending_schema_migration" in block

    for tool_name in (
        "provision_database",
        "apply_database_schema",
        "seed_database",
        "fetch_current_schema",
        "apply_schema_migration",
    ):
        assert tool_name not in block

    assert "backend/database/seed.json" not in block
    removed_schema_refs = [
        line.strip()
        for line in block.splitlines()
        if "backend/database/schema.json" in line
    ]
    assert removed_schema_refs == [
        "- Do not emit removed `backend/database/schema.json` or seed files."
    ]
    assert "ctx.persistence.collection(module_id, entity_name)" in block
    assert "Generated repo code must not assume `ctx.db` exists" not in block


def test_model_agent_targets_backend_schemas_py_not_backend_models() -> None:
    block = _agent_block("ModelAgent")

    assert "backend/schemas.py" in block
    assert "backend/models" not in block
    assert "models.py" not in block


def test_structured_outputs_align_with_persistence_contract() -> None:
    data = yaml.safe_load(_read(APPGEN / "structured_outputs.yaml"))
    database_output = data["models"]["DatabaseOutput"]["fields"]
    database_file = data["models"]["DatabaseArtifactFile"]["fields"]
    model_file = data["models"]["ModelFile"]["fields"]
    task_type_values = data["models"]["AppBuildTask"]["fields"]["task_type"]["values"]
    text = _read(APPGEN / "structured_outputs.yaml")

    assert "persistence_contract" in task_type_values
    assert "pending_schema_migration" in database_output
    assert "data_contract_json" in database_file["kind"]["values"]
    assert "data_migration_json" in database_file["kind"]["values"]
    assert "backend/schemas.py" in model_file["path"]["description"]

    assert "backend/models/" not in text
    assert "backend/database/schema.json" not in text
    assert "backend/database/seed.json" not in text
    assert "schema_json" not in text
    assert "seed_json" not in text
    assert "ctx.persistence.collection(module_id, entity_name)" in text
    assert "must not use ctx.db" in text
    assert "must not import get_mongo_client" in text


def test_file_contracts_define_canonical_persistence_and_ban_removed_paths() -> None:
    text = _read(APPGEN / "tools" / "file_contracts.yaml")
    data = yaml.safe_load(text)
    persistence = data["task_contracts"]["persistence_contract"]
    constraints = "\n".join(persistence["hard_constraints"])

    assert persistence["required_outputs"] == ["config/data.json"]
    assert "config/data_migrations/{migration_id}.json" in persistence["optional_outputs"]
    assert "modules/{module_id}/backend/repo.py" in persistence["downstream_python_defaults"]
    assert "modules/{module_id}/backend/schemas.py" in persistence["downstream_python_defaults"]
    assert "modules/{module_id}/backend/policy.py" in persistence["downstream_python_defaults"]

    assert "Do not emit backend/models.py." in constraints
    assert "Do not emit backend/models/*.py." in constraints
    assert "Do not emit backend/database/schema.json." in constraints
    assert "Do not emit backend/database/seed.json." in constraints
    assert "ctx.persistence.collection(module_id, entity_name)" in constraints
    assert "must not use ctx.db" in constraints
    assert "must not import or call get_mongo_client()" in constraints
    assert "must not hardcode database names" in constraints


def test_file_contracts_keep_repo_as_only_persistence_layer() -> None:
    data = yaml.safe_load(_read(APPGEN / "tools" / "file_contracts.yaml"))
    module_constraints = "\n".join(
        data["task_contracts"]["module_contract"]["hard_constraints"]
    )
    persistence_constraints = "\n".join(
        data["task_contracts"]["persistence_contract"]["hard_constraints"]
    )
    constraints = f"{module_constraints}\n{persistence_constraints}"

    assert "Do not put DB access in backend/handler.py." in constraints
    assert "backend/handler.py must not contain persistence logic." in constraints
    assert "Do not put raw persistence logic in backend/service.py." in constraints
    assert "backend/service.py calls repo.py" in constraints
    assert "backend/repo.py must use module_id/entity_name" in constraints


def test_service_agent_guides_repo_to_ctx_persistence() -> None:
    block = _agent_block("ServiceAgent")

    assert "context.persistence.collection(module_id, entity_name)" in block
    assert "context.persistence" in block
    assert "`backend/repo.py` is the only generated backend layer" in block
    assert "must not use `ctx.db` or `context.db`" in block
    assert "must not import or call `get_mongo_client()`" in block
    assert "must not hardcode database names" in block
    assert 'getattr(context, "db", None)' not in block
    assert "requires context.db" not in block
    assert "get_database()" in block


def test_service_agent_repo_example_uses_ctx_persistence() -> None:
    block = _agent_block("ServiceAgent")

    assert 'getattr(context, \\"persistence\\", None)' in block
    assert 'persistence.collection(\\"task_manager\\", \\"tasks\\")' in block
    assert "data_contract.surfaces[*].collections[*]" in block
    assert 'getattr(context, \\"db\\", None)' not in block
    assert "get_mongo_client" in block


def _generated_persistent_module_fixture() -> tuple[dict, dict[str, str]]:
    intent = {
        "schema_version": "mozaiks.data_contract.v1",
        "surfaces": [
            {
                "surface_id": "projects",
                "surface_kind": "module",
                "collections": [
                    {
                        "module_id": "projects",
                        "name": "projects",
                        "entity_name": "projects",
                        "indexes": [
                            {
                                "name": "owner_created_at",
                                "keys": [
                                    {"field": "owner_id", "order": 1},
                                    {"field": "created_at", "order": -1},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    files = {
        "modules/projects/backend/repo.py": """
class ProjectsRepo:
    async def _collection(self, ctx):
        persistence = getattr(ctx, "persistence", None)
        if persistence is None:
            raise RuntimeError("Persistence is not available for this app context.")
        return persistence.collection("projects", "projects")

    async def list_projects(self, ctx, *, query=None, limit=50):
        collection = await self._collection(ctx)
        return await collection.find_many(query or {}, limit=limit)
""",
        "modules/projects/backend/service.py": """
from .repo import ProjectsRepo


class ProjectsService:
    def __init__(self, repo=None):
        self.repo = repo or ProjectsRepo()

    async def list_projects(self, ctx, *, query=None):
        return await self.repo.list_projects(ctx, query=query)
""",
        "modules/projects/backend/handler.py": """
from .service import ProjectsService


class ProjectsHandler:
    def __init__(self):
        self.service = ProjectsService()

    async def list_projects(self, ctx, **payload):
        return await self.service.list_projects(ctx, query=payload)
""",
        "modules/projects/backend/schemas.py": """
from typing import TypedDict


class ProjectRecord(TypedDict):
    project_id: str
    owner_id: str
    created_at: str
""",
    }
    return intent, files


def test_generated_repo_fixture_uses_ctx_persistence_only() -> None:
    _, files = _generated_persistent_module_fixture()
    repo = files["modules/projects/backend/repo.py"]

    assert "persistence.collection" in repo
    assert 'collection("projects", "projects")' in repo
    assert "ctx.db" not in repo
    assert "context.db" not in repo
    assert "get_mongo_client" not in repo


def test_generated_repo_fixture_matches_data_contract_collection() -> None:
    intent, files = _generated_persistent_module_fixture()
    repo = files["modules/projects/backend/repo.py"]

    match = re.search(r'collection\("([^"]+)",\s*"([^"]+)"\)', repo)
    assert match is not None
    repo_key = match.group(1), match.group(2)

    collections = intent["surfaces"][0]["collections"]
    intent_keys = {
        (collection["module_id"], collection.get("entity_name") or collection["name"])
        for collection in collections
    }
    assert repo_key in intent_keys


def test_generated_service_and_handler_do_not_touch_persistence_directly() -> None:
    _, files = _generated_persistent_module_fixture()

    assert "persistence" not in files["modules/projects/backend/service.py"]
    assert "persistence" not in files["modules/projects/backend/handler.py"]
    assert "repo." in files["modules/projects/backend/service.py"]
    assert "service." in files["modules/projects/backend/handler.py"]


def test_generated_schema_fixture_remains_canonical_and_models_are_forbidden() -> None:
    _, files = _generated_persistent_module_fixture()

    assert "modules/projects/backend/schemas.py" in files
    assert all("backend/models.py" not in path for path in files)
    assert all("backend/models/" not in path for path in files)


def test_data_contract_structured_output_model_has_surfaces() -> None:
    """DataContract model must declare surfaces — the validator requires it."""
    data = yaml.safe_load(_read(APPGEN / "structured_outputs.yaml"))
    models = data["models"]

    assert "DataContract" in models, "DataContract model must exist"
    fields = models["DataContract"]["fields"]
    assert "surfaces" in fields, (
        "DataContract.surfaces is required — validator and runtime data contract loader "
        "both expect surfaces[].{surface_id, surface_kind, collections[]}"
    )
    assert "DataContractSurface" in models, "DataContractSurface model must exist"
    surface_fields = models["DataContractSurface"]["fields"]
    assert "surface_id" in surface_fields
    assert "surface_kind" in surface_fields
    assert "collections" in surface_fields

    assert "DataContractCollection" in models, "DataContractCollection model must exist"


def test_database_agent_output_example_contains_no_placeholder_content() -> None:
    """DatabaseAgent output format example must not use '{...}' as content.

    A literal '{...}' placeholder in the output example teaches the LLM to write
    '{...}' into data.json rather than real JSON. The example must show the actual
    DataContract JSON shape.
    """
    block = _agent_block("DatabaseAgent")
    output_section_start = block.index("[OUTPUT FORMAT]")
    output_section = block[output_section_start:]

    # Check that the greenfield content example isn't a bare placeholder
    assert '"{...}"' not in output_section, (
        "DatabaseAgent [OUTPUT FORMAT] must not use '{...}' as a content placeholder. "
        "Provide a real DataContract JSON example with surfaces, surface_id, collections."
    )
    # Verify the example contains the real shape keywords (escaped in YAML inline JSON)
    assert "surfaces" in output_section, (
        "DatabaseAgent [OUTPUT FORMAT] greenfield example must show the surfaces field "
        "so the LLM generates a valid DataContract."
    )
    assert "surface_id" in output_section
    assert "collections" in output_section


def test_module_archetypes_do_not_reference_removed_manifest_or_model_paths() -> None:
    text = _read(APPGEN / "tools" / "module_archetypes.yaml")
    hook_text = _read(APPGEN / "tools" / "hook_scope_transform.py")
    domain_hook_text = _read(APPGEN / "tools" / "hook_domain_catalog_context.py")

    assert "channels.yaml" not in text.replace("Do not emit channels.yaml", "")
    assert "states.yaml" not in text
    assert "transitions.yaml" not in text
    assert "backend/models/" not in text
    assert "models.py" not in text
    assert 'r"^backend/models/' not in hook_text
    assert "features/{feature_name}/models" not in hook_text
    assert '"channels.yaml"' not in domain_hook_text
    assert "Include channels.yaml only" not in domain_hook_text


def test_database_endpoint_tools_are_not_registered_or_required_by_prompts() -> None:
    tools = yaml.safe_load(_read(APPGEN / "tools.yaml"))["tools"]
    tool_names = {item["function"] for item in tools}

    database_block = _agent_block("DatabaseAgent")
    for name in ("provision_database", "apply_database_schema", "seed_database"):
        assert name not in tool_names
        assert name not in database_block


def test_schema_save_and_download_paths_are_canonical() -> None:
    save_schema = _read(APPGEN / "tools" / "save_app_schema.py")
    generate_download = _read(APPGEN / "tools" / "generate_and_download.py")

    assert '"config" / "data.json"' in save_schema
    assert "config/data.json" in save_schema
    assert "config/data_migrations" in generate_download


def test_docs_state_ctx_persistence_is_runtime_supported() -> None:
    docs = "\n".join(
        _read(path)
        for path in (
            ROOT / "docs" / "architecture" / "builder" / "data-contract-and-revision-contract.md",
            ROOT / "docs" / "architecture" / "modules-systems" / "module-system.md",
            ROOT / "docs" / "architecture" / "app" / "platform-authoring.md",
            ROOT / "docs" / "architecture" / "foundations" / "events-and-data" / "persistence-and-artifact-storage.md",
            ROOT / "AGENTS.md",
            ROOT / "CLAUDE.md",
        )
    )

    assert "`ctx.persistence`" in docs
    assert "ctx.persistence.collection(module_id, entity_name)" in docs
    assert "`ctx.db` remains absent and non-canonical" in docs
    assert "must not require `ctx.db`" in docs
    assert "backend/schemas.py" in docs
    assert "config/data.json" in docs
    assert "config/data_migrations/{migration_id}.json" in docs


def test_add_module_skill_repo_example_uses_ctx_persistence() -> None:
    skill = _read(ROOT / ".claude" / "skills" / "add-module" / "SKILL.md")
    start = skill.index("### 11. Write `backend/repo.py`")
    end = skill.index("### 12. Write `backend/service.py`", start)
    repo_section = skill[start:end]

    assert 'getattr(ctx, "persistence", None)' in repo_section
    assert 'persistence.collection("{name}", "{name}")' in repo_section
    assert "app/config/data.json" in repo_section
    assert "ctx.db" in repo_section
    assert "Do not use" in repo_section
    assert "get_mongo_client" in repo_section
    assert "from mozaiksai.core.core_config import get_mongo_client" not in repo_section
    assert 'getattr(ctx, "db", None)' not in repo_section

