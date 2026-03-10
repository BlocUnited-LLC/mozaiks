# ==============================================================================
# FILE: mozaikscore/core/routes/status.py
# DESCRIPTION: Operational status route — /__mozaiks/admin/status
# ORIGIN: Migrated from mozaiks-core-public/backend/core/routes/status.py
# ==============================================================================
import os

from fastapi import APIRouter, Depends

from mozaikscore.core.auth import require_admin_or_internal
from mozaikscore.core.module_manager import module_manager

router = APIRouter(
    prefix="/__mozaiks/admin",
    tags=["admin-status"],
    dependencies=[Depends(require_admin_or_internal)],
)

APP_ID = os.getenv("MOZAIKS_APP_ID", "dev_app")
ENV = os.getenv("ENV", "development")


@router.get("/status", response_model=dict)
async def get_status():
    """Operational status for admin dashboards."""
    return {
        "appId": APP_ID,
        "env": ENV,
        "ops": {
            "modules_loaded": len(module_manager.modules),
            "module_names": list(module_manager.modules.keys()),
        },
    }
