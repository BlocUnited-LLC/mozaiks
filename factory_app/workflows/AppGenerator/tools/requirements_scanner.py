"""
requirements_scanner — scan generated Python files for third-party imports.

Called by generate_and_download.py after the full files_map is assembled.
Uses stdlib ast only — no extra dependencies.

Output is written to requirements.txt in the bundle if the agents did not
already produce one.
"""

from __future__ import annotations

import ast
import sys

# sys.stdlib_module_names is Python 3.10+; fall back to a curated set.
try:
    _STDLIB: frozenset[str] = frozenset(sys.stdlib_module_names)  # type: ignore[attr-defined]
except AttributeError:
    _STDLIB: frozenset[str] = frozenset({  # type: ignore[no-redef]
        "__future__", "abc", "ast", "asyncio", "base64", "builtins", "collections",
        "contextlib", "copy", "dataclasses", "datetime", "enum", "functools", "hashlib",
        "hmac", "http", "importlib", "inspect", "io", "itertools", "json", "logging",
        "math", "os", "pathlib", "pickle", "platform", "queue", "random", "re",
        "shutil", "signal", "socket", "sqlite3", "string", "struct", "subprocess",
        "sys", "tempfile", "threading", "time", "traceback", "types", "typing",
        "unittest", "urllib", "uuid", "warnings", "weakref", "zipfile",
    })

# Packages installed by the mozaiks framework itself — no need to re-declare.
_FRAMEWORK_ROOTS: frozenset[str] = frozenset({
    "mozaiksai", "ag2", "factory_app", "workflows",
})

# Import name → PyPI package name when they differ.
_IMPORT_TO_PYPI: dict[str, str] = {
    "PIL":          "Pillow",
    "bs4":          "beautifulsoup4",
    "sklearn":      "scikit-learn",
    "cv2":          "opencv-python",
    "yaml":         "PyYAML",
    "dotenv":       "python-dotenv",
    "jose":         "python-jose",
    "dateutil":     "python-dateutil",
    "attr":         "attrs",
    "pkg_resources": "setuptools",
    "boto3":        "boto3",
    "botocore":     "botocore",
    "pymongo":      "pymongo",
    "motor":        "motor",
    "pydantic":     "pydantic",
    "fastapi":      "fastapi",
    "httpx":        "httpx",
    "aiohttp":      "aiohttp",
    "requests":     "requests",
    "openai":       "openai",
    "anthropic":    "anthropic",
    "sendgrid":     "sendgrid",
    "twilio":       "twilio",
    "jwt":          "PyJWT",
    "cryptography": "cryptography",
    "celery":       "celery",
    "redis":        "redis",
    "sqlalchemy":   "SQLAlchemy",
    "alembic":      "alembic",
    "elasticsearch": "elasticsearch",
    "google":       "google-cloud-core",
    "azure":        "azure-core",
}


def _extract_top_level_imports(source: str) -> set[str]:
    """Return top-level package names imported by source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # level > 0 means relative import — skip (internal module)
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def scan_requirements(files_map: dict[str, str]) -> str:
    """
    Scan every .py file in files_map and return requirements.txt content.

    Excludes:
    - stdlib modules
    - framework packages (mozaiksai, ag2, ...)
    - relative/internal imports
    - names starting with _ (private / test artifacts)

    Applies known import-name → PyPI-name mappings and sorts alphabetically.
    Returns a comment-only string when no third-party packages are detected.
    """
    packages: set[str] = set()

    for path, content in files_map.items():
        if not isinstance(path, str) or not path.endswith(".py"):
            continue
        if not content:
            continue

        for raw in _extract_top_level_imports(content):
            if not raw or raw.startswith("_"):
                continue
            if raw in _STDLIB:
                continue
            if raw in _FRAMEWORK_ROOTS or any(raw.startswith(r + ".") for r in _FRAMEWORK_ROOTS):
                continue
            packages.add(_IMPORT_TO_PYPI.get(raw, raw))

    # mozaiks is always required — it's the runtime that loads and serves the app bundle.
    packages.discard("mozaiks")
    extras = sorted(packages, key=str.lower)
    lines = ["mozaiks", *extras]
    return "\n".join(lines) + "\n"


__all__ = ["scan_requirements"]

