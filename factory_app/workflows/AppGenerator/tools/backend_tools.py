"""
Backend Tools for AppGenerator Workflow.

Wraps AppGeneratorBackendClient methods as callable tools for agents.
"""

from typing import Annotated, Any

from pydantic import Field

from factory_app.workflows.AppGenerator.tools.backend_client import app_gen_backend_client
from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("backend_tools")

async def generate_scaffold(
    app_id: Annotated[str, Field(description="Application ID.")],
    dependencies: Annotated[dict[str, list[str]], Field(description="Dependencies for frontend and backend.")],
    tech_stack_override: Annotated[
        dict[str, Any] | None,
        Field(description="Optional tech stack override."),
    ] = None,
    include_dockerfiles: Annotated[
        bool,
        Field(description="Whether scaffold output should include deterministic Dockerfile artifacts."),
    ] = True,
    include_workflow: Annotated[
        bool,
        Field(description="Whether scaffold output should include deterministic deploy workflow artifacts."),
    ] = True,
    include_compose: Annotated[
        bool,
        Field(description="Whether scaffold output should include docker-compose artifact."),
    ] = False,
    deployment_profile: Annotated[
        str | None,
        Field(
            description=(
                "Optional provider-neutral deployment profile id. Production profiles such as "
                "production_container force Dockerfile/env/manifest/readiness handoff artifacts."
            )
        ),
    ] = None,
    user_id: Annotated[str | None, Field(description="User ID.")] = None
) -> dict[str, Any]:
    """
    Generate app scaffold files (boilerplate, config, dockerfiles) via the backend.
    Returns a dictionary containing the generated files.
    """
    logger.info("Generating scaffold for app %s", app_id)
    try:
        result = await app_gen_backend_client.generate_scaffold(
            app_id=app_id,
            dependencies=dependencies,
            tech_stack_override=tech_stack_override,
            include_dockerfiles=include_dockerfiles,
            include_workflow=include_workflow,
            include_compose=include_compose,
            deployment_profile=deployment_profile,
            user_id=user_id
        )
        return result
    except Exception as e:
        logger.error("Failed to generate scaffold: %s", e)
        return {"error": str(e)}

async def fetch_current_schema(
    app_id: Annotated[str, Field(description="Application ID.")],
    artifact_version_id: Annotated[str | None, Field(description="Artifact version ID of the bundle being refined. If null, returns null.")] = None,
) -> dict[str, Any]:
    """
    Fetch the prior data contract that was recorded for an existing app bundle
    artifact. Returns {"schema": <dict>} when prior intent exists, or
    {"schema": null} when no prior intent is available. DatabaseAgent prompts should prefer
    data_contract context and staged migration artifacts.
    """
    if not artifact_version_id:
        logger.debug("fetch_current_schema: no artifact_version_id — returning null schema (greenfield)")
        return {"schema": None, "artifact_version_id": None}
    logger.info("Fetching current schema for app %s artifact %s", app_id, artifact_version_id)
    try:
        result = await app_gen_backend_client.get_artifact_schema(
            app_id=app_id,
            artifact_version_id=artifact_version_id,
        )
        return result
    except Exception as e:
        logger.warning("fetch_current_schema failed (non-fatal): %s", e)
        return {"schema": None, "error": str(e)}


async def apply_schema_migration(
    app_id: Annotated[str, Field(description="Application ID.")],
    migration: Annotated[dict[str, Any], Field(description="Migration document produced by schema_migration.generate_migration().")],
    user_id: Annotated[str | None, Field(description="User ID.")] = None,
) -> dict[str, Any]:
    """
    Apply safe migration ops (additive only) to the existing database.
    Destructive ops in the migration are logged and skipped unless explicitly
    cleared by the safety gate in schema_migration.apply_migration_safe().
    """
    migration_id = (migration or {}).get("migration_id", "unknown")
    logger.info("Applying schema migration %s for app %s", migration_id, app_id)
    try:
        result = await app_gen_backend_client.apply_schema_migration(
            app_id=app_id,
            migration=migration,
            user_id=user_id,
        )
        return result
    except Exception as e:
        logger.error("Failed to apply migration %s: %s", migration_id, e)
        return {"error": str(e), "migration_id": migration_id}


