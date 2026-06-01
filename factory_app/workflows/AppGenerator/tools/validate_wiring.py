"""
validate_wiring - Cross-reference page api_endpoint fields against known module actions.

Runs at IntegrationTestAgent time, after AppSchemaAgent and assembly are complete.

Algorithm
---------
1. Extract every api_endpoint string from page sections in context_variables.app_pages
   (recursively, including nested children in compositional sections).
2. Build a registry of valid "{module_id}/{action_id}" paths from two sources:
     a. context_variables.app_build_plan.capability_packs  (planned actions)
     b. modules/*/module.yaml files on disk in the generated app directory (actual actions)
3. Cross-reference every referenced endpoint against the registry.
4. Report:
     wired            — endpoints that resolved correctly
     orphaned_pages   — endpoints with no matching module action (BLOCKING)
     orphaned_actions — module actions with no page referencing them (advisory warning)

The check is only blocking when a module registry is available. If neither
capability_packs nor module.yaml files can be found, the check is advisory
(cannot confirm or deny validity without a registry).
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

import yaml

_logger = logging.getLogger("tools.validate_wiring")


# ---------------------------------------------------------------------------
# Helpers — context access
# ---------------------------------------------------------------------------

def _context_get(context_variables: Optional[Dict[str, Any]], key: str) -> Optional[Any]:
    if context_variables is None:
        return None
    if hasattr(context_variables, "get"):
        try:
            v = context_variables.get(key)
            if v is not None:
                return v
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    if isinstance(context_variables, dict):
        return context_variables.get(key)
    return None


def _generated_files_from_context(context_variables: Optional[Dict[str, Any]]) -> Dict[str, str]:
    raw = _context_get(context_variables, "generated_files")
    if not isinstance(raw, dict):
        return {}
    files: Dict[str, str] = {}
    for path, content in raw.items():
        if not isinstance(path, str):
            continue
        normalized = path.replace("\\", "/").strip()
        if normalized and not normalized.startswith("/") and ".." not in normalized.split("/"):
            files[normalized] = str(content)
    return files


def _pages_from_generated_files(files: Dict[str, str]) -> List[Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []
    for path, content in sorted(files.items()):
        if not path.startswith("ui/pages/") or not path.endswith((".yaml", ".yml")):
            continue
        try:
            parsed = yaml.safe_load(content) or {}
        except Exception as exc:
            _logger.warning("validate_wiring: failed to parse generated page %s: %s", path, exc)
            continue
        if isinstance(parsed, dict):
            pages.append(parsed)
    return pages


# ---------------------------------------------------------------------------
# Helpers — filesystem
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def _resolve_generated_artifacts_root() -> Path:
    raw = os.getenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", "generated").strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate.resolve()


# ---------------------------------------------------------------------------
# Endpoint extraction — pages
# ---------------------------------------------------------------------------

def _collect_endpoints_from_config(
    page_name: str,
    section_id: str,
    config: Any,
    out: List[Tuple[str, str, str]],
) -> None:
    """Extract api_endpoint from config dict and recurse into children."""
    if not isinstance(config, dict):
        return
    ep = config.get("api_endpoint")
    if isinstance(ep, str) and ep.strip():
        out.append((page_name, section_id, ep.strip()))
    href = config.get("href")
    if isinstance(href, str) and href.strip().startswith("/api/"):
        out.append((page_name, section_id, href.strip()))
    submit_action = config.get("submit_action")
    if isinstance(submit_action, dict):
        _collect_endpoints_from_config(page_name, f"{section_id}/submit", submit_action, out)
    for action in config.get("actions") or []:
        if isinstance(action, dict):
            action_id = str(action.get("id") or "action").strip()
            _collect_endpoints_from_config(page_name, f"{section_id}/{action_id}", action, out)
    for child in config.get("children") or []:
        if isinstance(child, dict):
            child_id = str(child.get("id") or f"{section_id}/child")
            _collect_endpoints_from_config(page_name, child_id, child.get("config"), out)


def _extract_endpoint_refs(pages: List[Any]) -> List[Tuple[str, str, str]]:
    """
    Walk all pages and return (page_name, section_id, api_endpoint) triples
    for every api_endpoint found anywhere in the section tree.
    """
    results: List[Tuple[str, str, str]] = []
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        page_name = str(page.get("name") or "<unnamed>")
        for section in page.get("sections") or []:
            if not isinstance(section, dict):
                continue
            section_id = str(section.get("id") or "<no-id>")
            _collect_endpoints_from_config(page_name, section_id, section.get("config"), results)
    return results


def _endpoint_to_action_key(endpoint: str) -> tuple[Optional[str], Optional[str]]:
    """Normalize a page api_endpoint to the module action registry key.

    Canonical app pages call module actions at /api/modules/{module}/{action}.
    Query strings are intentionally rejected because durable page parameters
    belong in page_size, payload, form input, or module action input schemas.
    """
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme or parsed.netloc:
        return None, "api_endpoint must be an app-relative path, not an absolute URL"
    if parsed.query or parsed.fragment:
        return None, "api_endpoint must not include query strings or fragments"

    path = parsed.path.strip()
    if path.startswith("/api/modules/"):
        parts = path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "api" or parts[1] != "modules" or not parts[2] or not parts[3]:
            return None, "api_endpoint must use /api/modules/{module_id}/{action_id}"
        return f"{parts[2]}/{parts[3]}", None

    return path.lstrip("/"), None


# ---------------------------------------------------------------------------
# Module action registry — two sources
# ---------------------------------------------------------------------------

def _actions_from_capability_packs(capability_packs: List[Any]) -> Set[str]:
    """
    Build action registry from app_build_plan.capability_packs.

    Accepted pack shapes:
      { "id": "wallet",    "actions": ["get_wallet_summary", ...] }
      { "module_id": "wallet", "actions": [{"id": "get_wallet_summary"}, ...] }
    """
    valid: Set[str] = set()
    for pack in capability_packs or []:
        if not isinstance(pack, dict):
            continue
        module_id = str(pack.get("module_id") or pack.get("id") or "").strip()
        if not module_id:
            continue
        action_list = (
            pack.get("actions")
            or pack.get("action_ids")
            or pack.get("action_names")
            or []
        )
        if not isinstance(action_list, list):
            continue
        for action in action_list:
            if isinstance(action, dict):
                action_id = str(action.get("id") or action.get("name") or "").strip()
            else:
                action_id = str(action).strip()
            if action_id:
                valid.add(f"{module_id}/{action_id}")
                valid.add(action_id)  # bare match (for pages that omit module prefix)
    return valid


def _actions_from_module_yamls(app_dir: Path) -> Set[str]:
    """
    Scan app_dir/modules/*/module.yaml and build action registry.

    Returns a set containing both:
      "{module_id}/{action_id}"  (canonical form)
      "{action_id}"              (bare form, for pages that omit the module prefix)
    """
    valid: Set[str] = set()
    modules_dir = app_dir / "modules"
    if not modules_dir.is_dir():
        return valid
    for module_dir in sorted(modules_dir.iterdir()):
        if not module_dir.is_dir():
            continue
        module_yaml_path = module_dir / "module.yaml"
        if not module_yaml_path.exists():
            continue
        try:
            with open(module_yaml_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            module_id = str(data.get("id") or module_dir.name).strip()
            for action in data.get("actions") or []:
                if isinstance(action, dict):
                    action_id = str(action.get("id") or "").strip()
                elif isinstance(action, str):
                    action_id = action.strip()
                else:
                    continue
                if action_id:
                    valid.add(f"{module_id}/{action_id}")
                    valid.add(action_id)
        except Exception as exc:
            _logger.warning("validate_wiring: failed to parse %s: %s", module_yaml_path, exc)
    return valid


def _actions_from_generated_module_files(files: Dict[str, str]) -> Set[str]:
    valid: Set[str] = set()
    for path, content in sorted(files.items()):
        if not path.startswith("modules/") or not path.endswith("/module.yaml"):
            continue
        try:
            data = yaml.safe_load(content) or {}
        except Exception as exc:
            _logger.warning("validate_wiring: failed to parse generated module %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            continue
        module_block = data.get("module") if isinstance(data.get("module"), dict) else data
        module_id = str(module_block.get("id") if isinstance(module_block, dict) else "").strip()
        if not module_id:
            parts = path.split("/")
            module_id = parts[1] if len(parts) > 1 else ""
        if not module_id:
            continue
        for action in data.get("actions") or []:
            if isinstance(action, dict):
                action_id = str(action.get("id") or "").strip()
            elif isinstance(action, str):
                action_id = action.strip()
            else:
                continue
            if action_id:
                valid.add(f"{module_id}/{action_id}")
                valid.add(action_id)
    return valid


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------

async def validate_wiring(
    context_variables: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Cross-reference page api_endpoint references against known module actions.

    Returns a wiring report compatible with run_integration_tests output shape:
    {
      "contract_version": "1.0",
      "passed": bool,
      "wired": [{"page": str, "section": str, "endpoint": str}, ...],
      "orphaned_pages": [...],     # endpoints with no known module action — BLOCKING
      "orphaned_actions": [...],   # module actions never referenced by a page — warning
      "checks": [...],
      "failed_tests": [...],
      "warnings": [...],
    }
    """
    # ── Read context ──────────────────────────────────────────────────────
    generated_files = _generated_files_from_context(context_variables)
    app_pages: List[Any] = _context_get(context_variables, "app_pages") or []
    if not app_pages:
        app_pages = _pages_from_generated_files(generated_files)
    app_build_plan: Dict[str, Any] = _context_get(context_variables, "app_build_plan") or {}
    generated_app_dir_str: Optional[str] = _context_get(context_variables, "generated_app_dir")

    # ── 1. Collect endpoint references from pages ─────────────────────────
    endpoint_refs = _extract_endpoint_refs(app_pages)
    referenced_set: Set[str] = set()
    endpoint_keys: Dict[Tuple[str, str, str], Optional[str]] = {}
    invalid_refs: Set[Tuple[str, str, str]] = set()
    invalid_endpoints: List[Dict[str, str]] = []
    for page_name, section_id, endpoint in endpoint_refs:
        action_key, error = _endpoint_to_action_key(endpoint)
        endpoint_keys[(page_name, section_id, endpoint)] = action_key
        if action_key:
            referenced_set.add(action_key)
            if "/" in action_key:
                referenced_set.add(action_key.split("/", 1)[1])
        if error:
            invalid_refs.add((page_name, section_id, endpoint))
            invalid_endpoints.append({
                "page": page_name,
                "section": section_id,
                "endpoint": endpoint,
                "error": error,
            })

    # ── 2. Build module action registry ──────────────────────────────────
    known_actions: Set[str] = set()

    # Source A: capability_packs
    capability_packs = app_build_plan.get("capability_packs") or []
    known_actions.update(_actions_from_capability_packs(capability_packs))

    # Source B: modules/*/module.yaml on disk
    app_dir: Optional[Path] = None
    if generated_app_dir_str:
        app_dir = Path(generated_app_dir_str)
    else:
        app_id = _context_get(context_variables, "app_id")
        build_id = (
            _context_get(context_variables, "build_id")
            or _context_get(context_variables, "chat_id")
        )
        if app_id and build_id:
            app_dir = (
                _resolve_generated_artifacts_root()
                / "apps"
                / str(app_id)
                / str(build_id)
                / "app"
            )

    if app_dir and app_dir.is_dir():
        known_actions.update(_actions_from_module_yamls(app_dir))
    if generated_files:
        known_actions.update(_actions_from_generated_module_files(generated_files))

    # ── 3. Cross-reference ────────────────────────────────────────────────
    wired: List[Dict[str, str]] = []
    orphaned_pages: List[Dict[str, str]] = []

    for page_name, section_id, endpoint in endpoint_refs:
        action_key = endpoint_keys.get((page_name, section_id, endpoint))
        if (page_name, section_id, endpoint) in invalid_refs:
            continue
        if action_key in known_actions:
            wired.append({"page": page_name, "section": section_id, "endpoint": endpoint})
        else:
            orphaned_pages.append({"page": page_name, "section": section_id, "endpoint": endpoint})

    # Orphaned actions: defined but never referenced by any page
    # Only consider the canonical "module/action" form to avoid bare-id duplication noise.
    canonical_actions: Set[str] = {a for a in known_actions if "/" in a}
    orphaned_actions: List[str] = sorted(
        a for a in canonical_actions
        if a not in referenced_set and a.split("/")[1] not in referenced_set
    )

    # ── 4. Determine pass/fail ─────────────────────────────────────────────
    no_endpoints = not endpoint_refs
    no_registry = not known_actions
    has_orphaned_pages = bool(orphaned_pages)
    has_invalid_endpoints = bool(invalid_endpoints)

    # Block only when we have a registry to validate against.
    blocking_pass: bool = not has_invalid_endpoints and (not has_orphaned_pages or no_registry)

    # ── 5. Build human-readable output ───────────────────────────────────
    warnings: List[str] = []
    failed_tests: List[Dict[str, Any]] = []

    for item in invalid_endpoints:
        failed_tests.append({
            "test": "wiring_invalid_endpoint",
            "page": item["page"],
            "section": item["section"],
            "endpoint": item["endpoint"],
            "error": (
                f"Page '{item['page']}' section '{item['section']}' has invalid "
                f"api_endpoint '{item['endpoint']}': {item['error']}."
            ),
            "fix_suggestion": (
                "Use the canonical /api/modules/{module_id}/{action_id} path and put "
                "filters, limits, and selected row data in page_size, payload, form "
                "state, or the module action input schema."
            ),
        })

    if has_orphaned_pages and not no_registry:
        for item in orphaned_pages:
            failed_tests.append({
                "test": "wiring_orphaned_endpoint",
                "page": item["page"],
                "section": item["section"],
                "endpoint": item["endpoint"],
                "error": (
                    f"Page '{item['page']}' section '{item['section']}' references "
                    f"api_endpoint '{item['endpoint']}' which has no matching module action."
                ),
                "fix_suggestion": (
                    f"Either add action '{item['endpoint']}' to the appropriate module.yaml "
                    "or correct the api_endpoint value in the page schema."
                ),
            })

    if no_endpoints:
        warnings.append(
            "No api_endpoint fields found in any page section. "
            "If this app uses module actions, ensure AppSchemaAgent wires them."
        )

    if no_registry:
        warnings.append(
            "No module action registry available (capability_packs is empty and no "
            "module.yaml files found on disk). Wiring check is advisory — cannot "
            "confirm or deny endpoint validity."
        )

    if orphaned_actions:
        warnings.append(
            f"{len(orphaned_actions)} module action(s) not referenced by any page: "
            f"{orphaned_actions}. These may be subscription handlers or admin-only "
            "actions — review intent."
        )

    if has_invalid_endpoints:
        message = f"{len(invalid_endpoints)} page endpoint(s) have invalid api_endpoint syntax."
    elif not blocking_pass:
        message = (
            f"{len(orphaned_pages)} page endpoint(s) reference unknown module actions."
        )
    elif not no_endpoints and not no_registry:
        message = (
            f"All {len(wired)} page api_endpoint reference(s) resolve to known module actions."
        )
    else:
        message = "Wiring check passed (advisory — registry or endpoints unavailable)."

    check: Dict[str, Any] = {
        "id": "module_action_wiring",
        "passed": blocking_pass,
        "message": message,
        "details": {
            "blocking": not no_registry,
            "total_endpoints_referenced": len(endpoint_refs),
            "wired_count": len(wired),
            "invalid_endpoint_count": len(invalid_endpoints),
            "orphaned_page_count": len(orphaned_pages),
            "orphaned_action_count": len(orphaned_actions),
            "module_registry_available": not no_registry,
            "wired": wired,
            "invalid_endpoints": invalid_endpoints,
            "orphaned_pages": orphaned_pages,
            "orphaned_actions": orphaned_actions,
            **(
                {"fix_suggestions": [f["fix_suggestion"] for f in failed_tests]}
                if failed_tests
                else {}
            ),
        },
    }

    result: Dict[str, Any] = {
        "contract_version": "1.0",
        "passed": blocking_pass,
        "wired": wired,
        "invalid_endpoints": invalid_endpoints,
        "orphaned_pages": orphaned_pages,
        "orphaned_actions": orphaned_actions,
        "checks": [check],
        "failed_tests": failed_tests,
        "warnings": warnings,
    }

    # ── Persist into context for routing ──────────────────────────────────
    try:
        if context_variables is not None and hasattr(context_variables, "set"):
            context_variables.set("wiring_validation_passed", blocking_pass)
            context_variables.set("wiring_validation_result", result)
    except Exception as exc:
        _logger.debug("validate_wiring: failed to persist results: %s", exc)

    return result


__all__ = ["validate_wiring"]
