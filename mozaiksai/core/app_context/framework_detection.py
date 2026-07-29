"""Framework detection for App Intelligence source indexes."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FRAMEWORK_DETECTION_SCHEMA_VERSION: Literal["mozaiks.framework_detection.v1"] = "mozaiks.framework_detection.v1"

FrameworkCategory = Literal["app_runtime", "frontend", "backend", "fullstack", "build_tool", "test", "mozaiks"]
FrameworkEvidenceKind = Literal["manifest", "dependency", "script", "source", "path"]
FrameworkValidationKind = Literal["install", "lint", "test", "build", "typecheck", "dev", "start"]


class FrameworkEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FrameworkEvidenceKind
    path: str
    detail: str


class DetectedFramework(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework_id: str
    label: str
    category: FrameworkCategory
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[FrameworkEvidence] = Field(default_factory=list)


class FrameworkEntrypoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    path: str
    framework_id: str | None = None
    command: str | None = None


class FrameworkValidationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FrameworkValidationKind
    command: str
    working_directory: str = "."
    confidence: float = Field(ge=0.0, le=1.0)


class FrameworkDetectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.framework_detection.v1"] = FRAMEWORK_DETECTION_SCHEMA_VERSION
    primary_framework_id: str | None = None
    primary_framework_label: str | None = None
    languages: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    frameworks: list[DetectedFramework] = Field(default_factory=list)
    entrypoints: list[FrameworkEntrypoint] = Field(default_factory=list)
    validation_commands: list[FrameworkValidationCommand] = Field(default_factory=list)
    monorepo: bool = False
    warnings: list[str] = Field(default_factory=list)


def detect_frameworks_from_file_map(file_map: dict[str, str]) -> FrameworkDetectionResult:
    files = {
        str(path).replace("\\", "/").strip("/"): str(content or "")
        for path, content in (file_map or {}).items()
    }
    manifests = sorted(path for path in files if _is_manifest(path))
    evidence: dict[str, list[FrameworkEvidence]] = defaultdict(list)
    package_managers: set[str] = set()
    entrypoints: list[FrameworkEntrypoint] = []
    validation_commands: list[FrameworkValidationCommand] = []
    warnings: list[str] = []

    _detect_mozaiks(files, evidence, entrypoints)
    _detect_node(files, evidence, package_managers, entrypoints, validation_commands)
    _detect_python(files, evidence, package_managers, entrypoints, validation_commands)
    _detect_laravel(files, evidence, package_managers, entrypoints, validation_commands)

    frameworks = [_framework(framework_id, items) for framework_id, items in evidence.items()]
    frameworks.sort(key=lambda item: (-item.confidence, item.framework_id))
    if not frameworks:
        warnings.append("framework_detection_no_frameworks_detected")
    if not validation_commands:
        warnings.append("framework_detection_no_validation_commands_detected")

    primary = _primary_framework(frameworks)
    return FrameworkDetectionResult(
        primary_framework_id=primary.framework_id if primary else None,
        primary_framework_label=primary.label if primary else None,
        languages=sorted(_languages(files)),
        package_managers=sorted(package_managers),
        manifests=manifests[:80],
        frameworks=frameworks[:24],
        entrypoints=_dedupe_entrypoints(entrypoints)[:80],
        validation_commands=_dedupe_commands(validation_commands)[:24],
        monorepo=_looks_like_monorepo(files),
        warnings=warnings,
    )


def _detect_mozaiks(
    files: dict[str, str],
    evidence: dict[str, list[FrameworkEvidence]],
    entrypoints: list[FrameworkEntrypoint],
) -> None:
    if "app/app.json" in files:
        evidence["mozaiks_app"].append(_ev("manifest", "app/app.json", "Mozaiks app manifest"))
        entrypoints.append(FrameworkEntrypoint(kind="app_manifest", path="app/app.json", framework_id="mozaiks_app"))
    for path in files:
        if re.fullmatch(r"app/modules/[^/]+/module\.ya?ml", path):
            module_id = path.split("/")[2]
            evidence["mozaiks_app"].append(_ev("manifest", path, f"Mozaiks module manifest: {module_id}"))
            entrypoints.append(FrameworkEntrypoint(kind="module_manifest", path=path, framework_id="mozaiks_app"))
        if path == "workflows/AppGenerator/orchestrator.yaml" or path.startswith("factory_app/workflows/"):
            evidence["mozaiks_factory"].append(_ev("path", path, "Mozaiks factory workflow surface"))


def _detect_node(
    files: dict[str, str],
    evidence: dict[str, list[FrameworkEvidence]],
    package_managers: set[str],
    entrypoints: list[FrameworkEntrypoint],
    validation_commands: list[FrameworkValidationCommand],
) -> None:
    package_paths = [path for path in files if path.endswith("package.json")]
    if not package_paths:
        return
    if any(path.endswith("pnpm-lock.yaml") for path in files):
        package_managers.add("pnpm")
    elif any(path.endswith("yarn.lock") for path in files):
        package_managers.add("yarn")
    else:
        package_managers.add("npm")
    for package_path in sorted(package_paths):
        data = _parse_json(files.get(package_path))
        if not data:
            continue
        workdir = _dirname(package_path)
        scripts_raw = data.get("scripts")
        scripts = (
            {str(key): value for key, value in scripts_raw.items()}
            if isinstance(scripts_raw, dict)
            else {}
        )
        deps = _dependencies(data)
        evidence["node"].append(_ev("manifest", package_path, "Node package manifest"))
        _script_commands(scripts, workdir, _node_pm(package_managers), validation_commands)
        if "next" in deps:
            evidence["nextjs"].append(_ev("dependency", package_path, "Dependency: next"))
            entrypoints.extend(_next_entrypoints(files, workdir))
        if "react" in deps:
            evidence["react"].append(_ev("dependency", package_path, "Dependency: react"))
            entrypoints.extend(_frontend_entrypoints(files, workdir, "react"))
        if "vite" in deps or _has_under(files, workdir, "vite.config."):
            evidence["vite"].append(_ev("dependency", package_path, "Vite build surface"))
        if "vue" in deps:
            evidence["vue"].append(_ev("dependency", package_path, "Dependency: vue"))
            entrypoints.extend(_frontend_entrypoints(files, workdir, "vue"))
        if "express" in deps:
            evidence["express"].append(_ev("dependency", package_path, "Dependency: express"))
            entrypoints.extend(_server_entrypoints(files, workdir, "express"))


def _detect_python(
    files: dict[str, str],
    evidence: dict[str, list[FrameworkEvidence]],
    package_managers: set[str],
    entrypoints: list[FrameworkEntrypoint],
    validation_commands: list[FrameworkValidationCommand],
) -> None:
    pyproject_paths = [path for path in files if path.endswith("pyproject.toml")]
    requirements_paths = [path for path in files if path.endswith("requirements.txt")]
    if not pyproject_paths and not requirements_paths and not any(path.endswith(".py") for path in files):
        return
    package_managers.add("python")
    for path in sorted([*pyproject_paths, *requirements_paths]):
        evidence["python"].append(_ev("manifest", path, "Python project manifest"))
        workdir = _dirname(path)
        validation_commands.append(
            FrameworkValidationCommand(kind="test", command="python -m pytest", working_directory=workdir, confidence=0.65)
        )
        validation_commands.append(
            FrameworkValidationCommand(kind="lint", command="ruff check .", working_directory=workdir, confidence=0.55)
        )
        text = files.get(path, "").lower()
        for dep, framework_id in (("fastapi", "fastapi"), ("django", "django"), ("flask", "flask")):
            if dep in text:
                evidence[framework_id].append(_ev("dependency", path, f"Dependency: {dep}"))
    for path, content in files.items():
        if not path.endswith(".py"):
            continue
        lowered = content.lower()
        if "fastapi(" in lowered or "from fastapi" in lowered or "import fastapi" in lowered:
            evidence["fastapi"].append(_ev("source", path, "FastAPI application source"))
            entrypoints.append(FrameworkEntrypoint(kind="api_app", path=path, framework_id="fastapi"))
        if "django" in lowered and ("urlpatterns" in lowered or "settings" in PurePosixPath(path).name.lower()):
            evidence["django"].append(_ev("source", path, "Django route or settings source"))
            entrypoints.append(FrameworkEntrypoint(kind="django_config", path=path, framework_id="django"))
        if "flask(" in lowered or "from flask" in lowered:
            evidence["flask"].append(_ev("source", path, "Flask application source"))
            entrypoints.append(FrameworkEntrypoint(kind="api_app", path=path, framework_id="flask"))


def _detect_laravel(
    files: dict[str, str],
    evidence: dict[str, list[FrameworkEvidence]],
    package_managers: set[str],
    entrypoints: list[FrameworkEntrypoint],
    validation_commands: list[FrameworkValidationCommand],
) -> None:
    for path in sorted(file_path for file_path in files if file_path.endswith("composer.json")):
        data = _parse_json(files.get(path))
        deps = _dependencies(data)
        workdir = _dirname(path)
        if "laravel/framework" not in deps and not _has_under(files, workdir, "artisan"):
            continue
        package_managers.add("composer")
        evidence["laravel"].append(_ev("dependency", path, "Dependency: laravel/framework"))
        route_prefix = "" if workdir == "." else f"{workdir}/"
        entrypoints.extend(
            FrameworkEntrypoint(kind="route_file", path=route, framework_id="laravel")
            for route in files
            if route.startswith(f"{route_prefix}routes/") and route.endswith(".php")
        )
        validation_commands.append(
            FrameworkValidationCommand(kind="test", command="php artisan test", working_directory=workdir, confidence=0.75)
        )


def _framework(framework_id: str, evidence: list[FrameworkEvidence]) -> DetectedFramework:
    labels: dict[str, tuple[str, FrameworkCategory]] = {
        "mozaiks_app": ("Mozaiks App", "mozaiks"),
        "mozaiks_factory": ("Mozaiks Factory", "mozaiks"),
        "node": ("Node.js", "app_runtime"),
        "nextjs": ("Next.js", "fullstack"),
        "react": ("React", "frontend"),
        "vite": ("Vite", "build_tool"),
        "vue": ("Vue", "frontend"),
        "express": ("Express", "backend"),
        "python": ("Python", "app_runtime"),
        "fastapi": ("FastAPI", "backend"),
        "django": ("Django", "backend"),
        "flask": ("Flask", "backend"),
        "laravel": ("Laravel", "fullstack"),
    }
    label, category = labels.get(framework_id, (framework_id.replace("_", " ").title(), "app_runtime"))
    signal = sum({"manifest": 0.22, "dependency": 0.25, "script": 0.12, "source": 0.18, "path": 0.14}[item.kind] for item in evidence)
    confidence = min(0.98, 0.35 + signal)
    return DetectedFramework(framework_id=framework_id, label=label, category=category, confidence=confidence, evidence=evidence[:12])


def _primary_framework(frameworks: list[DetectedFramework]) -> DetectedFramework | None:
    order = ["mozaiks_app", "nextjs", "laravel", "fastapi", "django", "react", "vue", "express", "flask", "node", "python"]
    ranked = sorted(frameworks, key=lambda item: (order.index(item.framework_id) if item.framework_id in order else 999, -item.confidence))
    return ranked[0] if ranked else None


def _languages(files: dict[str, str]) -> set[str]:
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".php": "php",
        ".css": "css",
    }
    return {mapping[suffix] for path in files for suffix in [PurePosixPath(path).suffix.lower()] if suffix in mapping}


def _script_commands(
    scripts: dict[str, Any],
    workdir: str,
    package_manager: str,
    commands: list[FrameworkValidationCommand],
) -> None:
    script_map: dict[str, FrameworkValidationKind] = {
        "lint": "lint",
        "test": "test",
        "build": "build",
        "typecheck": "typecheck",
        "dev": "dev",
        "start": "start",
    }
    for script, kind in script_map.items():
        if script in scripts:
            commands.append(
                FrameworkValidationCommand(
                    kind=kind,
                    command=f"{package_manager} run {script}",
                    working_directory=workdir,
                    confidence=0.9,
                )
            )
    commands.append(
        FrameworkValidationCommand(kind="install", command=f"{package_manager} install", working_directory=workdir, confidence=0.7)
    )


def _next_entrypoints(files: dict[str, str], workdir: str) -> list[FrameworkEntrypoint]:
    prefixes = [_join_prefix(workdir, "app/"), _join_prefix(workdir, "pages/")]
    return [
        FrameworkEntrypoint(kind="route", path=path, framework_id="nextjs")
        for path in files
        if path.endswith((".tsx", ".jsx", ".ts", ".js")) and any(path.startswith(prefix) for prefix in prefixes)
    ][:40]


def _frontend_entrypoints(files: dict[str, str], workdir: str, framework_id: str) -> list[FrameworkEntrypoint]:
    roots = [_join_prefix(workdir, "src/"), _join_prefix(workdir, "app/ui/")]
    names = {"App.jsx", "App.tsx", "main.jsx", "main.tsx", "index.jsx", "index.tsx"}
    return [
        FrameworkEntrypoint(kind="frontend_entrypoint", path=path, framework_id=framework_id)
        for path in files
        if PurePosixPath(path).name in names and any(path.startswith(root) for root in roots)
    ]


def _server_entrypoints(files: dict[str, str], workdir: str, framework_id: str) -> list[FrameworkEntrypoint]:
    names = {"server.js", "server.ts", "index.js", "index.ts", "app.js", "app.ts"}
    return [
        FrameworkEntrypoint(kind="server_entrypoint", path=path, framework_id=framework_id)
        for path, content in files.items()
        if PurePosixPath(path).name in names and path.startswith(_join_prefix(workdir, "")) and "express" in content.lower()
    ]


def _dependencies(package_data: dict[str, Any]) -> set[str]:
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies", "require", "require-dev"):
        section = package_data.get(key)
        if isinstance(section, dict):
            deps.update(str(name).lower() for name in section)
    return deps


def _parse_json(content: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(content or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _node_pm(package_managers: set[str]) -> str:
    if "pnpm" in package_managers:
        return "pnpm"
    if "yarn" in package_managers:
        return "yarn"
    return "npm"


def _dirname(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "." if parent == "." else parent


def _has_under(files: dict[str, str], workdir: str, name_prefix: str) -> bool:
    prefix = "" if workdir == "." else f"{workdir}/"
    return any(path.startswith(prefix) and PurePosixPath(path).name.startswith(name_prefix) for path in files)


def _join_prefix(workdir: str, suffix: str) -> str:
    return suffix if workdir == "." else f"{workdir.rstrip('/')}/{suffix}"


def _looks_like_monorepo(files: dict[str, str]) -> bool:
    package_dirs = {_dirname(path) for path in files if path.endswith("package.json")}
    pyproject_dirs = {_dirname(path) for path in files if path.endswith("pyproject.toml")}
    workspace_markers = {"pnpm-workspace.yaml", "lerna.json", "turbo.json", "nx.json"}
    return len((package_dirs | pyproject_dirs) - {"."}) > 1 or any(path in files for path in workspace_markers)


def _is_manifest(path: str) -> bool:
    names = {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "composer.json",
        "vite.config.ts",
        "vite.config.js",
        "next.config.js",
        "next.config.mjs",
        "app.json",
        "module.yaml",
        "module.yml",
    }
    return PurePosixPath(path).name.lower() in names


def _ev(kind: FrameworkEvidenceKind, path: str, detail: str) -> FrameworkEvidence:
    return FrameworkEvidence(kind=kind, path=path, detail=detail)


def _dedupe_entrypoints(items: list[FrameworkEntrypoint]) -> list[FrameworkEntrypoint]:
    seen: set[tuple[str, str, str | None]] = set()
    out: list[FrameworkEntrypoint] = []
    for item in items:
        key = (item.kind, item.path, item.framework_id)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _dedupe_commands(items: list[FrameworkValidationCommand]) -> list[FrameworkValidationCommand]:
    seen: set[tuple[str, str, str]] = set()
    out: list[FrameworkValidationCommand] = []
    for item in items:
        key = (item.kind, item.command, item.working_directory)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


__all__ = [
    "FRAMEWORK_DETECTION_SCHEMA_VERSION",
    "DetectedFramework",
    "FrameworkDetectionResult",
    "FrameworkEntrypoint",
    "FrameworkEvidence",
    "FrameworkValidationCommand",
    "detect_frameworks_from_file_map",
]
