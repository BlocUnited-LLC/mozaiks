from __future__ import annotations

from pprint import pformat
from typing import Any, Dict, List

from factory_app.workflows.AppGenerator.tools.app_backend_admin_contract import (
    validate_app_backend_admin_config,
)


_ADMIN_CONFIG_PATH = "backend/admin_config.py"
_ADMIN_ROUTE_PATH = "backend/routes/admin.py"

# Self-contained route — no mozaiksai import required in the generated app.
_ADMIN_ROUTE_MODULE = (
    "from fastapi import APIRouter, HTTPException\n"
    "from inspect import isawaitable\n\n"
    "from backend.admin_config import get_admin_config\n\n"
    "router = APIRouter(prefix=\"/api/admin\", tags=[\"app-backend-admin\"])\n\n\n"
    "@router.get(\"/config\")\n"
    "async def _get_admin_config():\n"
    "    try:\n"
    "        result = get_admin_config()\n"
    "        if isawaitable(result):\n"
    "            result = await result\n"
    "        return result\n"
    "    except HTTPException:\n"
    "        raise\n"
    "    except Exception as exc:\n"
    "        raise HTTPException(status_code=500, detail=str(exc)) from exc\n"
)


def _indent_block(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else line for line in text.splitlines())


def _render_admin_config_module(payload: Dict[str, Any]) -> str:
    # Config is already validated at codegen time — emit as a plain dict literal.
    rendered_payload = pformat(payload, sort_dicts=False, width=100)
    return (
        "_ADMIN_CONFIG = "
        f"{_indent_block(rendered_payload, 0)}\n\n\n"
        "def get_admin_config():\n"
        "    return _ADMIN_CONFIG\n"
    )


def build_app_backend_admin_code_files(raw: Any) -> List[Dict[str, str]]:
    """Render the canonical split app-backend admin surface from typed config."""

    config = validate_app_backend_admin_config(raw)
    payload = config.model_dump(mode="python", exclude_none=True)
    return [
        {
            "filename": _ADMIN_CONFIG_PATH,
            "content": _render_admin_config_module(payload),
        },
        {
            "filename": _ADMIN_ROUTE_PATH,
            "content": _ADMIN_ROUTE_MODULE,
        },
    ]


__all__ = ["build_app_backend_admin_code_files"]
