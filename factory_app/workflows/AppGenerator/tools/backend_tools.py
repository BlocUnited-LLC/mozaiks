"""
Backend Tools for AppGenerator Workflow.

Wraps AppGeneratorBackendClient methods as callable tools for agents.
"""

from typing import Annotated, Dict, List, Optional, Any

from autogen.tools.dependency_injection import Field

from workflows.AppGenerator.tools.backend_client import app_gen_backend_client
from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("backend_tools")

async def generate_scaffold(
    app_id: Annotated[str, Field(description="Application ID.")],
    dependencies: Annotated[Dict[str, List[str]], Field(description="Dependencies for frontend and backend.")],
    tech_stack_override: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Optional tech stack override."),
    ] = None,
    user_id: Annotated[Optional[str], Field(description="User ID.")] = None
) -> Dict[str, Any]:
    """
    Generate app scaffold files (boilerplate, config, dockerfiles) via the backend.
    Returns a dictionary containing the generated files.
    """
    logger.info(f"Generating scaffold for app {app_id}")
    try:
        result = await app_gen_backend_client.generate_scaffold(
            app_id=app_id,
            dependencies=dependencies,
            tech_stack_override=tech_stack_override,
            user_id=user_id
        )
        return result
    except Exception as e:
        logger.error(f"Failed to generate scaffold: {e}")
        return {"error": str(e)}

async def provision_database(
    app_id: Annotated[str, Field(description="Application ID.")],
    user_id: Annotated[Optional[str], Field(description="User ID.")] = None
) -> Dict[str, Any]:
    """
    Provision a database for the application via the backend.
    """
    logger.info(f"Provisioning database for app {app_id}")
    try:
        result = await app_gen_backend_client.provision_database(app_id=app_id, user_id=user_id)
        return result
    except Exception as e:
        logger.error(f"Failed to provision database: {e}")
        return {"error": str(e)}

async def apply_database_schema(
    app_id: Annotated[str, Field(description="Application ID.")],
    database_schema: Annotated[Dict[str, Any], Field(description="Database schema definition.")],
    user_id: Annotated[Optional[str], Field(description="User ID.")] = None
) -> Dict[str, Any]:
    """
    Apply the database schema via the backend.
    """
    logger.info(f"Applying schema for app {app_id}")
    try:
        result = await app_gen_backend_client.apply_database_schema(
            app_id=app_id,
            schema=database_schema,
            user_id=user_id
        )
        return result
    except Exception as e:
        logger.error(f"Failed to apply schema: {e}")
        return {"error": str(e)}

async def seed_database(
    app_id: Annotated[str, Field(description="Application ID.")],
    seed_data: Annotated[Dict[str, Any], Field(description="Seed data to insert.")],
    user_id: Annotated[Optional[str], Field(description="User ID.")] = None
) -> Dict[str, Any]:
    """
    Seed the database with initial data via the backend.
    """
    logger.info(f"Seeding database for app {app_id}")
    try:
        result = await app_gen_backend_client.seed_database(
            app_id=app_id,
            seed_data=seed_data,
            user_id=user_id
        )
        return result
    except Exception as e:
        logger.error(f"Failed to seed database: {e}")
        return {"error": str(e)}
