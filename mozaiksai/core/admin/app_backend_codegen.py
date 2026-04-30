from __future__ import annotations

from pprint import pformat
from typing import Any, Dict, List

from mozaiksai.core.admin.app_backend_contract import validate_app_backend_admin_config


_ADMIN_CONFIG_PATH = "backend/admin_config.py"
_ADMIN_ROUTE_PATH = "backend/routes/admin.py"
_ADMIN_ROUTE_MODULE = (
    "from mozaiksai.core.admin import build_app_backend_admin_router\n\n"
    "from backend.admin_config import get_admin_config\n\n\n"
    "router = build_app_backend_admin_router(get_admin_config)\n"
)


def _indent_block(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else line for line in text.splitlines())


def _render_admin_config_module(payload: Dict[str, Any]) -> str:
    rendered_payload = pformat(payload, sort_dicts=False, width=100)
    return (
        "from mozaiksai.core.admin import validate_app_backend_admin_config\n\n\n"
        "def get_admin_config():\n"
        "    return validate_app_backend_admin_config(\n"
        f"{_indent_block(rendered_payload, 8)}\n"
        "    )\n"
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
