"""
Deterministic before_chat collector for ExistingAppDiscovery.

This tool does not ask the user anything and does not make product decisions.
It only reads explicit discovery inputs already provided to the workflow and
preloads context from deterministic sources:

- local repo path (`repo_path`)
- GitHub repo identifier (`github_repo`, optional `github_ref`)
- backend base URL (`backend_base_url`)
- explicit OpenAPI URL (`openapi_url`)
- uploaded OpenAPI file path (`uploaded_openapi_path`)

The collector mutates context_variables in place because lifecycle tool return
values are not merged by the runtime.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import logging
import os
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx
import yaml

logger = logging.getLogger(__name__)

_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "bin",
    "obj",
    "site",
    "__pycache__",
}
_OPENAPI_CANDIDATE_PATHS = [
    "/openapi.json",
    "/swagger/v1/swagger.json",
    "/swagger.json",
]
_THEME_CONFIG_RELATIVE_CANDIDATES = [
    "app/config/theme_config.json",
    "brand/theme_config.json",
    "config/theme_config.json",
    "theme_config.json",
]
_SHELL_CONFIG_RELATIVE_CANDIDATES = [
    "app/config/shell.json",
    "config/shell.json",
    "shell.json",
]
_THEME_SNAPSHOT_RELATIVE_CANDIDATES = [
    "tailwind.config.js",
    "tailwind.config.ts",
    "src/index.css",
    "src/App.css",
    "src/app.css",
    "src/styles/index.css",
    "src/styles.css",
]


def _ctx_store(context_variables: Any) -> Any:
    if context_variables is None:
        return {}
    if isinstance(context_variables, dict):
        return context_variables
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data
    return context_variables


def _ctx_get(context_variables: Any, key: str, default: Any = None) -> Any:
    store = _ctx_store(context_variables)
    if isinstance(store, dict):
        return store.get(key, default)
    getter = getattr(store, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:
            return default
    return default


def _ctx_set(context_variables: Any, key: str, value: Any) -> None:
    store = _ctx_store(context_variables)
    if isinstance(store, dict):
        store[key] = value
        return
    try:
        store[key] = value
    except Exception:
        setattr(store, key, value)


def _coerce_mapping(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _first_nonempty(*values: Any) -> Optional[Any]:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _append_unique(items: List[str], value: Optional[str]) -> None:
    if value and value not in items:
        items.append(value)


def _normalise_base_url(value: Optional[str]) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    return value.rstrip("/")


def _first_existing_dir(*candidates: Optional[Path]) -> Optional[Path]:
    for candidate in candidates:
        if candidate is None:
            continue
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return None


def _workspace_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def _default_workspace_app_inputs() -> Dict[str, Any]:
    workspace = _workspace_root()
    sibling_root = workspace.parent

    configured_repo = os.getenv("MOZAIKS_WORKSPACE_APP_PATH")
    configured_repo_path = Path(configured_repo).expanduser() if configured_repo else None
    workspace_app_repo = _first_existing_dir(
        configured_repo_path,
        sibling_root / "mozaiks-app",
    )

    data: Dict[str, Any] = {
        "repo_path": str(workspace_app_repo) if workspace_app_repo else None,
        "discovery_mode": "guided",
    }

    env_backend_base = os.getenv("MOZAIKS_WORKSPACE_APP_BACKEND_BASE_URL")
    env_openapi_url = os.getenv("MOZAIKS_WORKSPACE_APP_OPENAPI_URL")
    if env_backend_base:
        data["backend_base_url"] = env_backend_base
    if env_openapi_url:
        data["openapi_url"] = env_openapi_url

    return {key: value for key, value in data.items() if value}


def _resolve_host_app_source_inputs(host_app_source: Optional[str]) -> Dict[str, Any]:
    source = str(host_app_source or "").strip()
    if not source:
        return {}
    if source == "workspace_app":
        return _default_workspace_app_inputs()
    return {}


def _load_theme_capture_preloader():
    file_path = Path(__file__).resolve().parents[2] / "ThemeCapture" / "tools" / "preload_theme_capture_context.py"
    spec = importlib.util.spec_from_file_location("mozaiks_theme_capture_preloader", file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load ThemeCapture preloader from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find_theme_config_path(repo_path: Optional[str]) -> Optional[str]:
    if not repo_path:
        return None
    root = Path(repo_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return None
    for relative in _THEME_CONFIG_RELATIVE_CANDIDATES:
        candidate = root / relative
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


def _find_shell_config_path(repo_path: Optional[str]) -> Optional[str]:
    if not repo_path:
        return None
    root = Path(repo_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return None
    for relative in _SHELL_CONFIG_RELATIVE_CANDIDATES:
        candidate = root / relative
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


def _collect_theme_css_snapshot(repo_path: Optional[str], max_chars: int = 24000) -> Optional[str]:
    if not repo_path:
        return None
    root = Path(repo_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return None

    ordered_files: List[Path] = []
    seen: set[Path] = set()
    for relative in _THEME_SNAPSHOT_RELATIVE_CANDIDATES:
        candidate = root / relative
        if candidate.exists() and candidate.is_file():
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                ordered_files.append(candidate)

    src_root = root / "src"
    if src_root.exists() and src_root.is_dir():
        for candidate in sorted(src_root.rglob("*.css"))[:6]:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                ordered_files.append(candidate)

    if not ordered_files:
        return None

    chunks: List[str] = []
    total_chars = 0
    for path in ordered_files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if not text.strip():
            continue
        relative = path.relative_to(root).as_posix()
        chunk = f"/* file: {relative} */\n{text.strip()}\n"
        if total_chars + len(chunk) > max_chars:
            remaining = max_chars - total_chars
            if remaining <= 0:
                break
            chunk = f"/* file: {relative} */\n{text.strip()[: max(0, remaining - len(relative) - 20)]}\n"
        chunks.append(chunk)
        total_chars += len(chunk)
        if total_chars >= max_chars:
            break

    return "\n".join(chunks) if chunks else None


def _summarize_theme_evidence(theme_evidence: Dict[str, Any]) -> Optional[str]:
    if not theme_evidence:
        return None
    appearance = theme_evidence.get("appearance")
    colors = [str(item) for item in (theme_evidence.get("colors") or [])[:4] if item]
    fonts = [str(item) for item in (theme_evidence.get("fonts") or [])[:4] if item]
    layout_hints = [str(item) for item in (theme_evidence.get("layout_hints") or [])[:4] if item]

    parts: List[str] = []
    if appearance:
        parts.append(f"{appearance} appearance")
    if colors:
        parts.append(f"colors {', '.join(colors)}")
    if fonts:
        parts.append(f"fonts {', '.join(fonts)}")
    if layout_hints:
        parts.append(f"layout hints {', '.join(layout_hints)}")

    if not parts:
        return None
    return "Host brand evidence suggests " + "; ".join(parts) + "."


def _iter_repo_files(repo_root: Path, limit: int = 5000) -> Iterable[Path]:
    count = 0
    for path in repo_root.rglob("*"):
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        yield path
        count += 1
        if count >= limit:
            break


def _infer_stack_from_signals(languages: List[str], frameworks: List[str]) -> str:
    parts = []
    for item in languages + frameworks:
        if item not in parts:
            parts.append(item)
    return ", ".join(parts)


def _parse_package_json(raw_text: str, frameworks: List[str], languages: List[str]) -> None:
    try:
        data = json.loads(raw_text)
    except Exception:
        return
    deps = {}
    deps.update(data.get("dependencies", {}) or {})
    deps.update(data.get("devDependencies", {}) or {})
    if deps:
        _append_unique(languages, "Node.js")
    if "react" in deps:
        _append_unique(frameworks, "React")
    if "next" in deps:
        _append_unique(frameworks, "Next.js")
    if "vite" in deps:
        _append_unique(frameworks, "Vite")
    if "vue" in deps:
        _append_unique(frameworks, "Vue")


def _parse_pyproject(raw_bytes: bytes, frameworks: List[str], languages: List[str]) -> None:
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except Exception:
        return
    project = data.get("project", {}) if isinstance(data, dict) else {}
    deps = project.get("dependencies", []) or []
    if deps:
        _append_unique(languages, "Python")
    dep_blob = " ".join(str(dep).lower() for dep in deps)
    if "fastapi" in dep_blob:
        _append_unique(frameworks, "FastAPI")
    if "django" in dep_blob:
        _append_unique(frameworks, "Django")
    if "flask" in dep_blob:
        _append_unique(frameworks, "Flask")


def _parse_csproj(raw_text: str, frameworks: List[str], languages: List[str]) -> List[str]:
    target_frameworks: List[str] = []
    try:
        root = ET.fromstring(raw_text)
    except Exception:
        return target_frameworks
    _append_unique(languages, "C#")
    _append_unique(frameworks, ".NET")
    for node_name in ["TargetFramework", "TargetFrameworks"]:
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag != node_name:
                continue
            value = (elem.text or "").strip()
            if not value:
                continue
            for item in value.split(";"):
                item = item.strip()
                if item:
                    target_frameworks.append(item)
    return target_frameworks


def _summarise_file_tree(file_paths: Iterable[str]) -> Dict[str, Any]:
    extension_counts: Dict[str, int] = {}
    manifest_paths: List[str] = []
    csproj_paths: List[str] = []
    route_files: List[str] = []
    service_entrypoints: List[str] = []
    hub_files: List[str] = []
    total_files = 0

    for rel_path in file_paths:
        total_files += 1
        lower_path = rel_path.lower()
        suffix = Path(rel_path).suffix.lower()
        if suffix:
            extension_counts[suffix] = extension_counts.get(suffix, 0) + 1
        name = Path(rel_path).name.lower()
        if name in {"package.json", "pyproject.toml", "requirements.txt", "dockerfile", "docker-compose.yml", "docker-compose.yaml"}:
            manifest_paths.append(rel_path)
        if lower_path.endswith(".csproj") or lower_path.endswith(".sln"):
            manifest_paths.append(rel_path)
        if lower_path.endswith(".csproj"):
            csproj_paths.append(rel_path)
        if name.endswith("routes.js") or name.endswith("routes.jsx") or name.endswith("routes.tsx") or name in {
            "app.js",
            "app.tsx",
            "router.js",
            "router.tsx",
        }:
            route_files.append(rel_path)
        if name in {"program.cs", "startup.cs"} or lower_path.endswith(".api.csproj"):
            service_entrypoints.append(rel_path)
        if name.endswith("hub.cs"):
            hub_files.append(rel_path)

    languages: List[str] = []
    frameworks: List[str] = []
    if extension_counts.get(".py"):
        _append_unique(languages, "Python")
    if extension_counts.get(".js") or extension_counts.get(".jsx") or extension_counts.get(".ts") or extension_counts.get(".tsx"):
        _append_unique(languages, "JavaScript/TypeScript")
    if extension_counts.get(".cs") or csproj_paths:
        _append_unique(languages, "C#")
        _append_unique(frameworks, ".NET")

    return {
        "total_files_scanned": total_files,
        "extension_counts": dict(sorted(extension_counts.items(), key=lambda item: item[1], reverse=True)[:12]),
        "manifest_paths": manifest_paths[:20],
        "csproj_paths": csproj_paths[:10],
        "route_files": route_files[:20],
        "service_entrypoints": service_entrypoints[:20],
        "hub_files": hub_files[:20],
        "languages": languages,
        "frameworks": frameworks,
    }


def _infer_service_surfaces(repo_summary: Dict[str, Any], api_inventory: Dict[str, Any], runtime_observations: Dict[str, Any]) -> List[Dict[str, Any]]:
    surfaces: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for rel_path in repo_summary.get("service_entrypoints") or []:
        location = str(rel_path)
        name = Path(location).parent.name or Path(location).stem
        kind = "rest_api" if location.lower().endswith(".csproj") or location.lower().endswith("program.cs") else "service"
        key = (name, location)
        if key in seen:
            continue
        seen.add(key)
        surfaces.append(
            {
                "name": name,
                "kind": kind,
                "location": location,
                "description": f"Repo-discovered service entrypoint at {location}",
            }
        )

    for rel_path in repo_summary.get("hub_files") or []:
        location = str(rel_path)
        name = Path(location).stem
        key = (name, location)
        if key in seen:
            continue
        seen.add(key)
        surfaces.append(
            {
                "name": name,
                "kind": "signalr_hub",
                "location": location,
                "description": f"Realtime hub discovered in repo at {location}",
            }
        )

    spec_location = api_inventory.get("spec_location")
    if api_inventory.get("success") and spec_location:
        key = ("OpenAPI Surface", str(spec_location))
        if key not in seen:
            seen.add(key)
            surfaces.append(
                {
                    "name": api_inventory.get("title") or "OpenAPI Surface",
                    "kind": "rest_api",
                    "location": str(spec_location),
                    "description": f"Discovered OpenAPI surface with {api_inventory.get('path_count', 0)} paths",
                }
            )

    health_url = runtime_observations.get("health_url")
    if runtime_observations.get("success") and health_url:
        key = ("Reachable Backend", str(health_url))
        if key not in seen:
            seen.add(key)
            surfaces.append(
                {
                    "name": "Reachable Backend",
                    "kind": "runtime_probe",
                    "location": str(health_url),
                    "description": "Backend reachable from deterministic runtime probe",
                }
            )

    return surfaces


def _infer_route_surfaces(repo_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    surfaces: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for rel_path in repo_summary.get("route_files") or []:
        location = str(rel_path)
        if location in seen:
            continue
        seen.add(location)
        surfaces.append(
            {
                "path": location,
                "module": Path(location).parent.name or Path(location).stem,
                "description": f"Repo-discovered route or shell entry file at {location}",
            }
        )

    return surfaces


def _scan_local_repo(repo_path: str) -> Dict[str, Any]:
    root = Path(repo_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {"success": False, "error": f"Repo path does not exist: {root}"}

    files = list(_iter_repo_files(root))
    relative_paths = [str(path.relative_to(root)).replace("\\", "/") for path in files]
    summary = _summarise_file_tree(relative_paths)
    languages = list(summary["languages"])
    frameworks = list(summary["frameworks"])
    target_frameworks: List[str] = []

    for rel_path in summary["manifest_paths"][:20]:
        full_path = root / rel_path
        try:
            if rel_path.endswith("package.json"):
                _parse_package_json(full_path.read_text(encoding="utf-8"), frameworks, languages)
            elif rel_path.endswith("pyproject.toml"):
                _parse_pyproject(full_path.read_bytes(), frameworks, languages)
            elif rel_path.endswith("requirements.txt"):
                _append_unique(languages, "Python")
            elif rel_path.lower().endswith(".csproj"):
                target_frameworks.extend(_parse_csproj(full_path.read_text(encoding="utf-8"), frameworks, languages))
        except Exception as exc:
            logger.debug("[ExistingAppDiscovery] Failed to parse manifest %s: %s", rel_path, exc)

    summary.update(
        {
            "success": True,
            "source": "local_repo",
            "repo_path": str(root),
            "repo_name": root.name,
            "languages": languages,
            "frameworks": frameworks,
            "target_frameworks": sorted(set(target_frameworks)),
            "inferred_tech_stack": _infer_stack_from_signals(languages, frameworks + target_frameworks),
        }
    )
    return summary


async def _github_request(url: str, token: Optional[str]) -> httpx.Response:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=10.0)) as client:
        return await client.get(url, headers=headers)


async def _fetch_github_file(owner: str, repo: str, path: str, ref: str, token: Optional[str]) -> Optional[bytes]:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    resp = await _github_request(url, token)
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
        content = data.get("content", "")
        encoding = data.get("encoding")
        if encoding == "base64" and content:
            return base64.b64decode(content)
    except Exception:
        return None
    return None


async def _scan_github_repo(github_repo: str, github_ref: Optional[str]) -> Dict[str, Any]:
    if "/" not in github_repo:
        return {"success": False, "error": f"Invalid github_repo '{github_repo}'"}

    owner, repo = github_repo.split("/", 1)
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    repo_resp = await _github_request(f"https://api.github.com/repos/{owner}/{repo}", token)
    if repo_resp.status_code != 200:
        return {
            "success": False,
            "error": f"GitHub repo lookup failed with HTTP {repo_resp.status_code}",
        }

    repo_info = repo_resp.json()
    ref = github_ref or repo_info.get("default_branch") or "main"
    tree_resp = await _github_request(
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1",
        token,
    )
    if tree_resp.status_code != 200:
        return {
            "success": False,
            "error": f"GitHub tree lookup failed with HTTP {tree_resp.status_code}",
        }

    tree = tree_resp.json().get("tree", []) or []
    file_paths = [item.get("path", "") for item in tree if item.get("type") == "blob"]
    summary = _summarise_file_tree(file_paths)
    languages = list(summary["languages"])
    frameworks = list(summary["frameworks"])
    target_frameworks: List[str] = []

    for rel_path in summary["manifest_paths"][:10]:
        raw_bytes = await _fetch_github_file(owner, repo, rel_path, ref, token)
        if not raw_bytes:
            continue
        try:
            if rel_path.endswith("package.json"):
                _parse_package_json(raw_bytes.decode("utf-8"), frameworks, languages)
            elif rel_path.endswith("pyproject.toml"):
                _parse_pyproject(raw_bytes, frameworks, languages)
            elif rel_path.endswith("requirements.txt"):
                _append_unique(languages, "Python")
            elif rel_path.lower().endswith(".csproj"):
                target_frameworks.extend(_parse_csproj(raw_bytes.decode("utf-8"), frameworks, languages))
        except Exception as exc:
            logger.debug("[ExistingAppDiscovery] Failed to parse GitHub manifest %s: %s", rel_path, exc)

    summary.update(
        {
            "success": True,
            "source": "github_repo_scan",
            "github_repo": github_repo,
            "github_ref": ref,
            "repo_name": repo_info.get("name") or repo,
            "default_branch": repo_info.get("default_branch"),
            "languages": languages,
            "frameworks": frameworks,
            "target_frameworks": sorted(set(target_frameworks)),
            "inferred_tech_stack": _infer_stack_from_signals(languages, frameworks + target_frameworks),
        }
    )
    return summary


async def _scan_repo_source(local_repo_path: Optional[str], github_repo: Optional[str], github_ref: Optional[str]) -> Dict[str, Any]:
    if local_repo_path:
        return _scan_local_repo(str(local_repo_path))
    if github_repo:
        return await _scan_github_repo(str(github_repo), str(github_ref) if github_ref else None)
    return {}


def _combine_repo_summaries(*summaries: Dict[str, Any]) -> Dict[str, Any]:
    valid = [item for item in summaries if item and item.get("success")]
    if not valid:
        return {}

    repo_names = [item.get("repo_name") for item in valid if item.get("repo_name")]
    languages: List[str] = []
    frameworks: List[str] = []
    target_frameworks: List[str] = []
    route_files: List[str] = []
    service_entrypoints: List[str] = []
    hub_files: List[str] = []
    total_files_scanned = 0

    for summary in valid:
        total_files_scanned += int(summary.get("total_files_scanned") or 0)
        for value in summary.get("languages") or []:
            _append_unique(languages, value)
        for value in summary.get("frameworks") or []:
            _append_unique(frameworks, value)
        for value in summary.get("target_frameworks") or []:
            _append_unique(target_frameworks, value)
        for value in summary.get("route_files") or []:
            _append_unique(route_files, value)
        for value in summary.get("service_entrypoints") or []:
            _append_unique(service_entrypoints, value)
        for value in summary.get("hub_files") or []:
            _append_unique(hub_files, value)

    return {
        "success": True,
        "source": "multi_repo",
        "repo_name": " + ".join(repo_names) if repo_names else "existing_app_sources",
        "repo_names": repo_names,
        "languages": languages,
        "frameworks": frameworks,
        "target_frameworks": target_frameworks,
        "route_files": route_files,
        "service_entrypoints": service_entrypoints,
        "hub_files": hub_files,
        "total_files_scanned": total_files_scanned,
        "inferred_tech_stack": _infer_stack_from_signals(languages, frameworks + target_frameworks),
    }


def _load_spec_from_file(path_value: str) -> Optional[Dict[str, Any]]:
    path = Path(path_value).expanduser().resolve()
    if not path.exists() or not path.is_file():
        return None
    raw_text = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw_text)
    except Exception:
        try:
            parsed = yaml.safe_load(raw_text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


async def _load_spec_from_url(url: str) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=10.0)) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except Exception:
        try:
            parsed = yaml.safe_load(resp.text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def _auth_summary_from_security_schemes(security_schemes: Dict[str, Any]) -> str:
    if not security_schemes:
        return "unknown"
    auth_kinds: List[str] = []
    for scheme in security_schemes.values():
        if not isinstance(scheme, dict):
            continue
        scheme_type = str(scheme.get("type") or "").lower()
        if scheme_type == "oauth2":
            _append_unique(auth_kinds, "OAuth2")
        elif scheme_type == "http" and str(scheme.get("scheme") or "").lower() == "bearer":
            _append_unique(auth_kinds, "JWT Bearer")
        elif scheme_type == "apikey":
            _append_unique(auth_kinds, "API Key")
        elif scheme_type:
            _append_unique(auth_kinds, scheme_type)
    return ", ".join(auth_kinds) if auth_kinds else "unknown"


def _summarise_openapi_spec(spec: Dict[str, Any], source: str) -> Dict[str, Any]:
    paths = spec.get("paths", {}) if isinstance(spec, dict) else {}
    methods = set()
    for item in paths.values():
        if not isinstance(item, dict):
            continue
        for method_name in item.keys():
            methods.add(str(method_name).upper())
    security_schemes = ((spec.get("components") or {}).get("securitySchemes") or {}) if isinstance(spec, dict) else {}
    return {
        "success": True,
        "source": source,
        "title": ((spec.get("info") or {}).get("title")) if isinstance(spec, dict) else None,
        "version": ((spec.get("info") or {}).get("version")) if isinstance(spec, dict) else None,
        "path_count": len(paths),
        "sample_paths": list(paths.keys())[:15],
        "methods": sorted(methods),
        "security_schemes": list(security_schemes.keys()),
        "auth_summary": _auth_summary_from_security_schemes(security_schemes),
    }


async def _collect_openapi(openapi_url: Optional[str], backend_base_url: Optional[str], uploaded_openapi_path: Optional[str]) -> Dict[str, Any]:
    if uploaded_openapi_path:
        spec = _load_spec_from_file(uploaded_openapi_path)
        if spec:
            summary = _summarise_openapi_spec(spec, "uploaded_openapi")
            summary["spec_location"] = uploaded_openapi_path
            return summary

    if openapi_url:
        spec = await _load_spec_from_url(openapi_url)
        if spec:
            summary = _summarise_openapi_spec(spec, "openapi_url")
            summary["spec_location"] = openapi_url
            return summary

    base_url = _normalise_base_url(backend_base_url)
    if not base_url:
        return {"success": False, "error": "No OpenAPI input provided"}

    for path in _OPENAPI_CANDIDATE_PATHS:
        candidate = f"{base_url}{path}"
        spec = await _load_spec_from_url(candidate)
        if spec:
            summary = _summarise_openapi_spec(spec, "backend_probe")
            summary["spec_location"] = candidate
            return summary

    return {"success": False, "error": "OpenAPI spec not found from configured sources"}


async def _probe_backend(backend_base_url: Optional[str]) -> Dict[str, Any]:
    base_url = _normalise_base_url(backend_base_url)
    if not base_url:
        return {"success": False, "error": "No backend_base_url provided"}

    candidates = ["/health", "/healthz", "/api/health", "/"]
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        for suffix in candidates:
            url = f"{base_url}{suffix}"
            try:
                resp = await client.get(url)
            except Exception as exc:
                logger.debug("[ExistingAppDiscovery] Health probe failed for %s: %s", url, exc)
                continue
            if resp.status_code >= 400:
                continue
            details: Dict[str, Any]
            try:
                details = resp.json()
            except Exception:
                details = {"raw": resp.text[:500]}
            return {
                "success": True,
                "health_url": url,
                "status_code": resp.status_code,
                "details": details,
            }

    return {"success": False, "error": f"Backend probe failed for {base_url}"}


def _merge_unresolved(existing: List[Dict[str, Any]], question: str, context: str, priority: str = "medium") -> None:
    if any(item.get("question") == question for item in existing):
        return
    existing.append({"question": question, "context": context, "priority": priority})


async def collect_prechat_discovery_context(context_variables: Optional[Any] = None) -> Dict[str, Any]:
    """Populate discovery context from deterministic pre-chat sources."""
    ctx = context_variables or {}
    discovery_inputs = _coerce_mapping(_ctx_get(ctx, "discovery_inputs", {}))
    host_app_source = _first_nonempty(
        discovery_inputs.get("host_app_source"),
        _ctx_get(ctx, "host_app_source"),
        "external",
    )
    host_source_inputs = _resolve_host_app_source_inputs(str(host_app_source) if host_app_source else None)
    if host_source_inputs:
        merged_inputs = dict(host_source_inputs)
        merged_inputs.update({key: value for key, value in discovery_inputs.items() if value is not None})
        discovery_inputs = merged_inputs

    discovery_mode = _first_nonempty(_ctx_get(ctx, "discovery_mode"), discovery_inputs.get("discovery_mode"), "guided")

    _ctx_set(ctx, "host_app_source", host_app_source)

    repo_path = _first_nonempty(_ctx_get(ctx, "repo_path"), discovery_inputs.get("repo_path"))
    frontend_repo_path = _first_nonempty(_ctx_get(ctx, "frontend_repo_path"), discovery_inputs.get("frontend_repo_path"))
    backend_repo_path = _first_nonempty(_ctx_get(ctx, "backend_repo_path"), discovery_inputs.get("backend_repo_path"))
    github_repo = _first_nonempty(_ctx_get(ctx, "github_repo"), discovery_inputs.get("github_repo"))
    frontend_github_repo = _first_nonempty(_ctx_get(ctx, "frontend_github_repo"), discovery_inputs.get("frontend_github_repo"))
    backend_github_repo = _first_nonempty(_ctx_get(ctx, "backend_github_repo"), discovery_inputs.get("backend_github_repo"))
    github_ref = _first_nonempty(_ctx_get(ctx, "github_ref"), discovery_inputs.get("github_ref"))
    backend_base_url = _first_nonempty(_ctx_get(ctx, "backend_base_url"), discovery_inputs.get("backend_base_url"), _ctx_get(ctx, "app_url"))
    openapi_url = _first_nonempty(_ctx_get(ctx, "openapi_url"), discovery_inputs.get("openapi_url"))
    uploaded_openapi_path = _first_nonempty(_ctx_get(ctx, "uploaded_openapi_path"), discovery_inputs.get("uploaded_openapi_path"))

    evidence_sources: List[Dict[str, Any]] = []
    unresolved_questions: List[Dict[str, Any]] = list(_ctx_get(ctx, "unresolved_questions", []) or [])
    repo_summary: Dict[str, Any] = {}
    frontend_repo_summary: Dict[str, Any] = {}
    backend_repo_summary: Dict[str, Any] = {}
    api_inventory: Dict[str, Any] = {}
    runtime_observations: Dict[str, Any] = {}
    auth_hypothesis: Dict[str, Any] = {}
    theme_capture_ready = False
    theme_capture_status = "none"
    theme_capture_summary: Optional[str] = None
    theme_capture_evidence: Dict[str, Any] = {}

    if frontend_repo_path or frontend_github_repo:
        frontend_repo_summary = await _scan_repo_source(frontend_repo_path, frontend_github_repo, github_ref)
        evidence_sources.append({
            "kind": "frontend_repo_scan",
            "location": frontend_repo_path or frontend_github_repo,
            "success": bool(frontend_repo_summary.get("success")),
        })
        if not frontend_repo_summary.get("success"):
            _merge_unresolved(
                unresolved_questions,
                "Frontend repo could not be scanned.",
                frontend_repo_summary.get("error", "Frontend repo scan failed."),
                "high",
            )

    if backend_repo_path or backend_github_repo:
        backend_repo_summary = await _scan_repo_source(backend_repo_path, backend_github_repo, github_ref)
        evidence_sources.append({
            "kind": "backend_repo_scan",
            "location": backend_repo_path or backend_github_repo,
            "success": bool(backend_repo_summary.get("success")),
        })
        if not backend_repo_summary.get("success"):
            _merge_unresolved(
                unresolved_questions,
                "Backend repo could not be scanned.",
                backend_repo_summary.get("error", "Backend repo scan failed."),
                "high",
            )

    if repo_path and not frontend_repo_summary and not backend_repo_summary:
        repo_summary = _scan_local_repo(str(repo_path))
        evidence_sources.append({
            "kind": "local_repo",
            "location": str(repo_path),
            "success": bool(repo_summary.get("success")),
        })
        if not repo_summary.get("success"):
            _merge_unresolved(
                unresolved_questions,
                "Local repo path could not be scanned.",
                repo_summary.get("error", "Repo scan failed."),
                "high",
            )
    elif github_repo and not frontend_repo_summary and not backend_repo_summary:
        repo_summary = await _scan_github_repo(str(github_repo), str(github_ref) if github_ref else None)
        evidence_sources.append({
            "kind": "github_repo_scan",
            "location": str(github_repo),
            "success": bool(repo_summary.get("success")),
        })
        if not repo_summary.get("success"):
            _merge_unresolved(
                unresolved_questions,
                "GitHub repo could not be scanned.",
                repo_summary.get("error", "GitHub scan failed. Private repos require a GitHub token or app installation."),
                "high",
            )

    if not repo_summary:
        repo_summary = _combine_repo_summaries(frontend_repo_summary, backend_repo_summary)

    if openapi_url or uploaded_openapi_path or backend_base_url:
        api_inventory = await _collect_openapi(
            str(openapi_url) if openapi_url else None,
            str(backend_base_url) if backend_base_url else None,
            str(uploaded_openapi_path) if uploaded_openapi_path else None,
        )
        evidence_sources.append({
            "kind": "openapi",
            "location": api_inventory.get("spec_location") or openapi_url or uploaded_openapi_path or backend_base_url,
            "success": bool(api_inventory.get("success")),
        })
        if api_inventory.get("success"):
            auth_hypothesis = {
                "source": api_inventory.get("source"),
                "summary": api_inventory.get("auth_summary", "unknown"),
                "security_schemes": api_inventory.get("security_schemes", []),
            }
        else:
            _merge_unresolved(
                unresolved_questions,
                "API schema could not be loaded.",
                api_inventory.get("error", "Provide an OpenAPI URL/file or a backend base URL with a discoverable swagger endpoint."),
                "high",
            )

    if backend_base_url:
        runtime_observations = await _probe_backend(str(backend_base_url))
        evidence_sources.append({
            "kind": "runtime_probe",
            "location": str(backend_base_url),
            "success": bool(runtime_observations.get("success")),
        })
        if not runtime_observations.get("success"):
            _merge_unresolved(
                unresolved_questions,
                "Backend health probe failed.",
                runtime_observations.get("error", "Verify the backend base URL and that it is reachable from the Mozaiks runtime."),
                "medium",
            )

    successful_sources = [item for item in evidence_sources if item.get("success")]
    preload_status = "ready" if successful_sources else "none"
    if evidence_sources and successful_sources and len(successful_sources) < len(evidence_sources):
        preload_status = "partial"

    if not evidence_sources:
        _merge_unresolved(
            unresolved_questions,
            "No pre-chat evidence source was provided.",
            "Provide at least one deterministic source such as repo_path, github_repo, backend_base_url, openapi_url, or uploaded_openapi_path.",
            "high",
        )

    if repo_summary.get("success") and not _ctx_get(ctx, "tech_stack") and repo_summary.get("inferred_tech_stack"):
        _ctx_set(ctx, "tech_stack", repo_summary["inferred_tech_stack"])
    if repo_summary.get("success") and not _ctx_get(ctx, "app_name"):
        inferred_name = repo_summary.get("repo_name")
        if inferred_name:
            _ctx_set(ctx, "app_name", inferred_name)

    if api_inventory.get("success"):
        _ctx_set(ctx, "api_docs_available", True)
        if not _ctx_get(ctx, "api_surface"):
            _ctx_set(ctx, "api_surface", f"OpenAPI, {api_inventory.get('path_count', 0)} paths")
        if not _ctx_get(ctx, "auth_model") and auth_hypothesis.get("summary"):
            _ctx_set(ctx, "auth_model", auth_hypothesis["summary"])
        if not _ctx_get(ctx, "app_name") and api_inventory.get("title"):
            _ctx_set(ctx, "app_name", api_inventory["title"])

    theme_app_url = _ctx_get(ctx, "app_url")
    if backend_base_url and theme_app_url and str(theme_app_url).rstrip("/") == str(backend_base_url).rstrip("/"):
        theme_app_url = None

    frontend_theme_path = _find_theme_config_path(str(frontend_repo_path)) if frontend_repo_path else None
    frontend_shell_path = _find_shell_config_path(str(frontend_repo_path)) if frontend_repo_path else None
    frontend_css_snapshot = _collect_theme_css_snapshot(str(frontend_repo_path)) if frontend_repo_path else None
    if not frontend_css_snapshot and repo_path and not frontend_repo_path and not backend_repo_path:
        frontend_css_snapshot = _collect_theme_css_snapshot(str(repo_path))
    if not frontend_theme_path and repo_path and not frontend_repo_path and not backend_repo_path:
        frontend_theme_path = _find_theme_config_path(str(repo_path))
    if not frontend_shell_path and repo_path and not frontend_repo_path and not backend_repo_path:
        frontend_shell_path = _find_shell_config_path(str(repo_path))

    theme_seed_available = any(
        [
            theme_app_url,
            frontend_theme_path,
            frontend_css_snapshot,
            frontend_repo_summary,
        ]
    )
    if theme_seed_available:
        try:
            theme_module = _load_theme_capture_preloader()
            theme_context: Dict[str, Any] = {
                "app_url": theme_app_url,
                "parent_theme_config": frontend_theme_path,
                "parent_shell_config": frontend_shell_path,
                "css_snapshot": frontend_css_snapshot,
                "frontend_repo_summary": frontend_repo_summary or {},
                "app_name": _ctx_get(ctx, "app_name"),
                "app_description": _ctx_get(ctx, "app_description"),
                "tech_stack": _ctx_get(ctx, "tech_stack"),
            }
            await theme_module.collect_prechat_theme_context(theme_context)
            theme_capture_status = str(theme_context.get("preload_status") or "none")
            theme_capture_ready = bool(theme_context.get("preloaded_context_ready"))
            theme_capture_evidence = theme_context.get("theme_capture_evidence") or {}
            theme_capture_summary = _summarize_theme_evidence(theme_capture_evidence) or theme_context.get("preload_summary")
            if not _ctx_get(ctx, "app_name") and theme_context.get("app_name"):
                _ctx_set(ctx, "app_name", theme_context["app_name"])
        except Exception as exc:
            logger.warning("[ExistingAppDiscovery] Theme evidence preload failed: %s", exc)
            theme_capture_status = "partial"
            theme_capture_summary = "Theme evidence preload failed before chat; refinement may be needed."

        evidence_sources.append(
            {
                "kind": "theme_capture",
                "location": frontend_theme_path or frontend_repo_path or theme_app_url,
                "success": bool(theme_capture_ready),
            }
        )

    service_surface_summary = backend_repo_summary or repo_summary
    route_surface_summary = frontend_repo_summary or repo_summary
    inferred_service_surfaces = _infer_service_surfaces(service_surface_summary, api_inventory, runtime_observations)
    inferred_route_surfaces = _infer_route_surfaces(route_surface_summary)

    if inferred_service_surfaces and not _ctx_get(ctx, "service_surfaces"):
        _ctx_set(ctx, "service_surfaces", inferred_service_surfaces)
    if inferred_route_surfaces and not _ctx_get(ctx, "route_surfaces"):
        _ctx_set(ctx, "route_surfaces", inferred_route_surfaces)
    if not _ctx_get(ctx, "existing_experience_summary") and inferred_route_surfaces:
        route_labels = ", ".join(item.get("module", "surface") for item in inferred_route_surfaces[:4])
        _ctx_set(
            ctx,
            "existing_experience_summary",
            f"Current experience appears to be organized around route/module surfaces such as {route_labels}.",
        )

    summary_lines = []
    if repo_summary.get("success"):
        summary_lines.append(
            f"Repo scan loaded from {repo_summary.get('source')}: {repo_summary.get('inferred_tech_stack') or 'stack not inferred'}"
        )
        if inferred_route_surfaces:
            summary_lines.append(
                f"Route surfaces detected: {len(inferred_route_surfaces)}"
            )
        if inferred_service_surfaces:
            summary_lines.append(
                f"Service surfaces detected: {len(inferred_service_surfaces)}"
            )
    if api_inventory.get("success"):
        summary_lines.append(
            f"API schema loaded: {api_inventory.get('path_count', 0)} paths, auth={api_inventory.get('auth_summary', 'unknown')}"
        )
    if runtime_observations.get("success"):
        summary_lines.append(
            f"Backend probe succeeded at {runtime_observations.get('health_url')}"
        )
    if theme_capture_status != "none" and theme_capture_summary:
        summary_lines.append(f"Theme evidence: {theme_capture_summary}")
    if unresolved_questions:
        summary_lines.append(
            f"Open questions remaining: {len(unresolved_questions)}"
        )

    _ctx_set(ctx, "repo_path", repo_path)
    _ctx_set(ctx, "discovery_mode", discovery_mode)
    _ctx_set(ctx, "github_repo", github_repo)
    _ctx_set(ctx, "frontend_repo_path", frontend_repo_path)
    _ctx_set(ctx, "backend_repo_path", backend_repo_path)
    _ctx_set(ctx, "frontend_github_repo", frontend_github_repo)
    _ctx_set(ctx, "backend_github_repo", backend_github_repo)
    _ctx_set(ctx, "github_ref", github_ref)
    _ctx_set(ctx, "backend_base_url", backend_base_url)
    _ctx_set(ctx, "openapi_url", openapi_url)
    _ctx_set(ctx, "uploaded_openapi_path", uploaded_openapi_path)
    _ctx_set(ctx, "evidence_sources", evidence_sources)
    _ctx_set(ctx, "repo_summary", repo_summary if repo_summary else {})
    _ctx_set(ctx, "frontend_repo_summary", frontend_repo_summary if frontend_repo_summary else {})
    _ctx_set(ctx, "backend_repo_summary", backend_repo_summary if backend_repo_summary else {})
    _ctx_set(ctx, "api_inventory", api_inventory if api_inventory else {})
    _ctx_set(ctx, "runtime_observations", runtime_observations if runtime_observations else {})
    _ctx_set(ctx, "auth_hypothesis", auth_hypothesis if auth_hypothesis else {})
    _ctx_set(ctx, "theme_capture_ready", theme_capture_ready)
    _ctx_set(ctx, "theme_capture_status", theme_capture_status)
    _ctx_set(ctx, "theme_capture_summary", theme_capture_summary)
    _ctx_set(ctx, "theme_capture_evidence", theme_capture_evidence if theme_capture_evidence else {})
    _ctx_set(ctx, "unresolved_questions", unresolved_questions)
    _ctx_set(ctx, "preloaded_context_ready", bool(successful_sources))
    _ctx_set(ctx, "preload_status", preload_status)
    _ctx_set(ctx, "preload_summary", "\n".join(summary_lines) if summary_lines else "No deterministic evidence was preloaded.")

    logger.info(
        "[ExistingAppDiscovery] before_chat preload complete: status=%s, sources=%s",
        preload_status,
        ", ".join(item.get("kind", "unknown") for item in evidence_sources) or "none",
    )

    return {
        "success": True,
        "preload_status": preload_status,
        "successful_sources": len(successful_sources),
        "total_sources": len(evidence_sources),
    }
