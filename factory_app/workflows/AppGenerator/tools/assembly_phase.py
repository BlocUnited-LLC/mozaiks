"""
Assembly Phase Tool

Merges outputs from multiple feature-scoped AppGenerator runs into a single
workflow bundle. The bundle is a collection of workflow YAMLs, tools, and
config_impacts artifacts consumed by mozaiks-core.

Responsibilities:
1. Collect code_files from all feature generations
2. De-duplicate by filename (last writer wins)
3. Return merged code_files list for DownloadAgent
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_entries_from_payload,
)

logger = logging.getLogger(__name__)


def _merge_code_files(feature_outputs: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Merge code_files from feature outputs, deduping by filename."""
    file_map: Dict[str, str] = {}
    for output in feature_outputs:
        for item in extract_code_file_entries_from_payload(output):
            filename = item.get("filename")
            content = item.get("content")
            if not filename or content is None:
                continue
            file_map[str(filename)] = str(content)
    return [{"filename": name, "content": content} for name, content in sorted(file_map.items())]


async def assemble_features(
    app_id: str,
    feature_outputs: List[Dict[str, Any]],
    app_name: Optional[str] = None,
    primary_color: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Merge feature outputs into a single workflow bundle.

    Args:
        app_id: The parent application ID
        feature_outputs: List of outputs from AppGenerator(feature scope)
        app_name: Optional display name (unused here, reserved)
        primary_color: Optional branding color (unused here, reserved)

    Returns:
        {
            "success": bool,
            "code_files": [...],  # All merged files
            "message": str,
        }
    """
    try:
        merged_files = _merge_code_files(feature_outputs or [])
        logger.info(
            "Assembled %d feature outputs into %d files",
            len(feature_outputs or []),
            len(merged_files),
        )
        return {
            "success": True,
            "code_files": merged_files,
            "message": f"Assembled {len(merged_files)} files",
        }
    except Exception as exc:
        logger.error("Assembly failed: %s", exc, exc_info=True)
        return {
            "success": False,
            "code_files": [],
            "message": f"Assembly failed: {exc}",
        }
