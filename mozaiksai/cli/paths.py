"""Project root resolution and shared config loading for CLI generators."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

# Sentinel files that identify the project root
_ROOT_MARKERS = ("pyproject.toml", "app")


def find_project_root(start: Optional[Path] = None) -> Path:
    """
    Walk upward from *start* (default: cwd) looking for the project root.

    The root is identified by containing ``pyproject.toml`` and the ``app/``
    directory.  Falls back to cwd if detection fails.
    """
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        if all((directory / m).exists() for m in _ROOT_MARKERS):
            return directory
    # Fallback
    return Path.cwd().resolve()


def load_json(path: Path, label: str) -> dict:
    """Load a JSON file or exit with a clear error."""
    if not path.is_file():
        print(f"ERROR: {label} not found at {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
