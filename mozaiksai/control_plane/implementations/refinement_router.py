from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mozaiksai.core.session.model import SessionLifecycle, TriggerInput
from mozaiksai.core.session.trigger_routing import TriggerRoutingContribution
from mozaiksai.core.workflow.pack.config import (
    get_workflow_sequence,
    load_global_pack_graph,
    normalize_step_groups,
)

from ..contracts import ControlPlaneToolContext
from ..executor import resolve_control_plane_tool_entrypoint
from ..loader import load_selected_control_plane_pack
from ..schema import (
    ControlPlaneArtifactRoutingManifest,
    ControlPlaneChangeRouteManifest,
    LoadedControlPlanePack,
)
from .change_classifier import get_change_classifier

_logger = logging.getLogger("mozaiksai.control_plane.implementations.refinement_router")

# Topological order for stale-family priority: earliest dependency restarts first.
_STALE_PRIORITY: list[str] = [
    "concept",
    "brand",
    "design_docs",
    "experience_spec",
    "workflow_bundle",
    "app_bundle",
]

# Maps a stale artifact family to the canonical sequence that rebuilds it and its dependents.
_STALE_SEQUENCE_MAP: dict[str, str] = {
    "concept": "full_rebuild",
    "brand": "theme_revision",
    "design_docs": "design_revision",
    "experience_spec": "app_surface_revision",
    "workflow_bundle": "workflow_revision",
    "app_bundle": "app_revision",
}

_EXPERIENCE_NAVIGATION_TERMS = (
    "navigation",
    "nav",
    "shell",
    "chrome",
    "header",
    "footer",
    "menu",
    "sidebar",
    "tabs",
)

_MODULE_IMPACT_TERMS = (
    "module",
    "action",
    "api",
    "endpoint",
    "backend",
    "database",
    "schema",
    "event",
    "reaction",
    "notification",
    "admin panel",
    "permission",
    "handler",
    "service",
    "repo",
    "policy",
)

_MODULE_CONTRACT_ORDER = {
    "events.yaml": 0,
    "reactions.yaml": 1,
    "notifications.yaml": 2,
    "settings.yaml": 3,
    "admin.yaml": 4,
    "profile.yaml": 5,
}

_MODULE_BACKEND_ORDER = {
    "handler.py": 0,
    "service.py": 1,
    "repo.py": 2,
    "policy.py": 3,
    "schemas.py": 4,
    "settings.py": 5,
    "admin.py": 6,
}

_MANAGED_CAPABILITY_TERMS = (
    "managed capability",
    "external adapter",
    "integration client",
    "facade module",
    "façade module",
    "provider-backed",
    "external service",
    "analytics provider",
    "reporting provider",
    "audit provider",
    "notification provider",
)

_MANAGED_CAPABILITY_CATEGORY_TERMS = (
    "analytics",
    "reporting",
    "audit",
    "notification",
    "billing",
    "payments",
    "search",
    "email",
    "storage",
    "crm",
)

_MANAGED_GENERIC_PACK_TOKENS = {
    "pack",
    "provider",
    "external",
    "managed",
    "capability",
    "service",
}

_UI_SURFACE_TERMS = (
    "display",
    "dashboard",
    "page",
    "screen",
    "ui",
    "layout",
    "view",
    "panel",
    "metrics",
    "form",
    "table",
)

_INTEGRATION_IMPACT_TERMS = (
    "integration",
    "connector",
    "external api",
    "external service",
    "api key",
    "credential",
    "provider",
    "webhook",
    "sync",
    "analytics provider",
    "reporting provider",
    "search provider",
    "email provider",
    "storage provider",
    "crm provider",
)

_INTEGRATION_SECRET_PATH_TERMS = (
    ".env",
    "secret",
    "secrets",
    "credential",
    "credentials",
    "vault",
    "key",
    "keys",
    "token",
    "tokens",
    "password",
)

_DATA_MODEL_IMPACT_TERMS = (
    "schema",
    "field",
    "data model",
    "model",
    "database",
    "migration",
    "migrate",
    "collection",
    "table",
    "index",
    "unique",
    "required field",
    "optional field",
    "relation",
    "reference",
    "foreign key",
    "tenant scope",
    "workspace scope",
    "owner scope",
    "delete records",
    "rename field",
    "change type",
    "add column",
    "remove column",
    "backfill",
)

_DATA_MODEL_CONTEXTUAL_TERMS = (
    "archive",
    "soft delete",
)

_DATA_MODEL_CONTEXT_TERMS = (
    "schema",
    "field",
    "model",
    "database",
    "migration",
    "collection",
    "table",
    "record",
    "records",
    "persistence",
)

_DATA_MODEL_DESTRUCTIVE_TERMS = (
    "delete field",
    "remove field",
    "drop field",
    "drop table",
    "delete records",
    "purge",
    "rename field",
    "change type",
    "required field",
    "unique constraint",
    "irreversible",
    "destructive",
)


class ChangeClass(StrEnum):
    PATCH = "patch"
    DESIGN = "design"
    FEATURE = "feature"
    CORE = "core"


class ArtifactKind(StrEnum):
    APP_BUNDLE = "app_bundle"
    WORKFLOW_BUNDLE = "workflow_bundle"
    DESIGN_DOCS = "design_docs"
    EXPERIENCE_SPEC = "experience_spec"
    CONCEPT = "concept"


class RefinementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_kind: str = "refinement"
    declared_change_class: ChangeClass | None = None
    artifact_kind: str
    artifact_key: str | None = None
    artifact_version_id: str | None = None
    raw_user_request: str = ""
    source_surface: str | None = None
    app_id: str | None = None
    user_id: str | None = None
    requested_workflow_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifact_kind")
    @classmethod
    def _normalize_artifact_kind(cls, value: Any) -> str:
        if isinstance(value, Enum):
            value = value.value
        normalized = str(value or "").strip().lower()
        if not normalized:
            raise ValueError("artifact_kind is required")
        return normalized

    def normalized_artifact_key(self) -> str:
        return str(self.artifact_key or self.artifact_kind).strip() or self.artifact_kind

    @property
    def request_id(self) -> str:
        for key in ("request_id", "change_request_id", "revision_id"):
            value = str(self.extra.get(key) or "").strip()
            if value:
                return value
        seed = "|".join(
            [
                str(self.app_id or ""),
                str(self.user_id or ""),
                self.artifact_kind,
                self.normalized_artifact_key(),
                str(self.artifact_version_id or ""),
                self.raw_user_request,
            ]
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        return f"ref_{digest}"


class ChangeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_class: ChangeClass
    source: str = "llm"
    signals: list[str] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    requires_concept_revision: bool = False
    touches_app_bundle: bool = False
    touches_workflow_bundle: bool = False
    touches_design_docs: bool = False
    touches_concept: bool = False


class ImpactSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_sequence: str | None = None
    affected_workflows: list[str] = Field(default_factory=list)
    affected_bundle_paths: list[str] = Field(default_factory=list)
    affected_declarative_families: list[str] = Field(default_factory=list)
    requires_replanning: bool = False
    requires_rebuild: bool = True
    restart_from: str | None = None
    scope_summary: str = ""


class RefinementRoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    workflow_sequence: str | None = None
    refinement_request: RefinementRequest
    change_intent: ChangeIntent
    impact_set: ImpactSet
    context_seed: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""
    is_full_restart: bool = False

    @property
    def change_class(self) -> str:
        return self.change_intent.change_class.value


@dataclass(frozen=True)
class ArtifactRoutePolicy:
    artifact_kind: str
    label: str
    patch: ControlPlaneChangeRouteManifest
    design: ControlPlaneChangeRouteManifest
    feature: ControlPlaneChangeRouteManifest
    core: ControlPlaneChangeRouteManifest

    def route_for(self, change_class: ChangeClass) -> ControlPlaneChangeRouteManifest:
        return getattr(self, change_class.value)  # type: ignore[no-any-return]

class RefinementTriggerRouteResolver:
    def __init__(self, *, classifier=None, pack_loader=load_selected_control_plane_pack) -> None:
        self._classifier = classifier or get_change_classifier()
        self._pack_loader = pack_loader

    @staticmethod
    def _artifact_label(artifact_kind: str, policy: ArtifactRoutePolicy | None = None) -> str:
        return str(policy.label if policy is not None else artifact_kind).replace("_", " ")

    def _load_pack(self) -> LoadedControlPlanePack:
        loaded = self._pack_loader()
        return loaded if isinstance(loaded, LoadedControlPlanePack) else LoadedControlPlanePack.model_validate(loaded)

    def _policy_for(self, artifact_kind: str) -> ArtifactRoutePolicy:
        pack = self._load_pack()
        policy = pack.routing_for_artifact(artifact_kind)
        if policy is None:
            configured = ", ".join(sorted(artifact.artifact_kind for artifact in pack.manifest.routing.artifacts))
            raise RuntimeError(
                "No control-plane routing is configured for "
                f"artifact_kind '{artifact_kind}'. Add it to control_plane.yaml routing.artifacts. "
                f"Configured kinds: {configured or 'none'}."
            )
        return self._to_policy(policy)

    @staticmethod
    def _to_policy(policy: ControlPlaneArtifactRoutingManifest) -> ArtifactRoutePolicy:
        return ArtifactRoutePolicy(
            artifact_kind=policy.artifact_kind,
            label=str(policy.label or policy.artifact_kind).strip() or policy.artifact_kind,
            patch=policy.routes.patch,
            design=policy.routes.design,
            feature=policy.routes.feature,
            core=policy.routes.core,
        )

    def _families_for_route(self, route: ControlPlaneChangeRouteManifest, artifact_kind: str) -> list[str]:
        return self._sequence_families(route.workflow_sequence) or [artifact_kind]

    @staticmethod
    def _sequence_workflows(sequence_id: str | None) -> list[str]:
        sid = str(sequence_id or "").strip()
        if not sid:
            return []
        pack = load_global_pack_graph()
        if pack is None:
            raise RuntimeError(
                f"Control-plane route references workflow_sequence '{sid}', but no extension registry is loaded."
            )
        sequence = get_workflow_sequence(pack, sid)
        if sequence is None:
            raise RuntimeError(f"Control-plane route references unknown workflow_sequence '{sid}'.")
        workflows: list[str] = []
        for group in normalize_step_groups(sequence.steps):
            for workflow_id in group:
                if workflow_id not in workflows:
                    workflows.append(workflow_id)
        if not workflows:
            raise RuntimeError(f"Control-plane workflow_sequence '{sid}' does not contain workflow steps.")
        return workflows

    @staticmethod
    def _sequence_families(sequence_id: str | None) -> list[str]:
        sid = str(sequence_id or "").strip()
        if not sid:
            return []
        pack = load_global_pack_graph()
        if pack is None:
            raise RuntimeError(
                f"Control-plane route references workflow_sequence '{sid}', but no extension registry is loaded."
            )
        sequence = get_workflow_sequence(pack, sid)
        if sequence is None:
            raise RuntimeError(f"Control-plane route references unknown workflow_sequence '{sid}'.")
        return [
            str(item).strip()
            for item in getattr(sequence, "affected_declarative_families", [])
            if str(item).strip()
        ]

    def _route_workflow_id(self, route: ControlPlaneChangeRouteManifest) -> str:
        sequence_workflows = self._sequence_workflows(route.workflow_sequence)
        return sequence_workflows[0]

    def _affected_workflows_for_route(self, route: ControlPlaneChangeRouteManifest) -> list[str]:
        return self._sequence_workflows(route.workflow_sequence) or [self._route_workflow_id(route)]

    @staticmethod
    def _dedupe_paths(paths: list[str]) -> list[str]:
        result: list[str] = []
        for raw_path in paths:
            path = str(raw_path or "").strip().replace("\\", "/").lstrip("/")
            if path and path not in result:
                result.append(path)
        return result

    @staticmethod
    def _manifest_paths_from_extra(extra: dict[str, Any]) -> list[str]:
        for key in ("files_manifest", "file_manifest", "artifact_files_manifest"):
            raw_manifest = extra.get(key)
            if not isinstance(raw_manifest, list):
                continue
            paths: list[str] = []
            for entry in raw_manifest:
                if isinstance(entry, str):
                    paths.append(entry)
                elif isinstance(entry, dict):
                    paths.append(str(entry.get("path") or ""))
                else:
                    path = getattr(entry, "path", None)
                    if path is not None:
                        paths.append(str(path))
            return RefinementTriggerRouteResolver._dedupe_paths(paths)
        return []

    @staticmethod
    def _manifest_entries_from_extra(extra: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("files_manifest", "file_manifest", "artifact_files_manifest"):
            raw_manifest = extra.get(key)
            if not isinstance(raw_manifest, list):
                continue
            entries: list[dict[str, Any]] = []
            for entry in raw_manifest:
                if isinstance(entry, str):
                    entries.append({"path": entry})
                elif isinstance(entry, dict):
                    path = str(entry.get("path") or "").strip()
                    if path:
                        entries.append(dict(entry, path=path))
                else:
                    path = getattr(entry, "path", None)  # type: ignore[assignment]
                    if path is not None:
                        entries.append({"path": str(path)})
            return entries
        return []

    @staticmethod
    async def _manifest_paths_for_request(request: RefinementRequest) -> list[str]:
        paths = RefinementTriggerRouteResolver._manifest_paths_from_extra(request.extra)
        if paths:
            return paths
        if not request.app_id or not request.artifact_version_id:
            return []
        try:
            from mozaiksai.core.artifacts.store import ArtifactStore

            artifact = await ArtifactStore().get_artifact_version(
                app_id=request.app_id,
                artifact_version_id=request.artifact_version_id,
            )
        except Exception as exc:
            _logger.debug("ARTIFACT_FILES_LOOKUP_FAILED app=%s artifact=%s: %s", request.app_id, request.artifact_version_id, exc)
            return []
        if artifact is None:
            return []
        return RefinementTriggerRouteResolver._dedupe_paths(
            [entry.path for entry in getattr(artifact, "files_manifest", [])]
        )

    @staticmethod
    def _experience_requires_shell_update(
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
    ) -> bool:
        text = RefinementTriggerRouteResolver._request_impact_text(request=request, intent=intent)
        return any(term in text for term in _EXPERIENCE_NAVIGATION_TERMS)

    @staticmethod
    def _request_impact_text(
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
    ) -> str:
        return " ".join(
            [
                str(request.raw_user_request or ""),
                str(request.source_surface or ""),
                " ".join(str(signal or "") for signal in intent.signals),
            ]
        ).lower()

    @classmethod
    def _experience_spec_bundle_paths(
        cls,
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        manifest_paths: list[str],
    ) -> list[str]:
        if manifest_paths:
            paths = [
                path
                for path in manifest_paths
                if path.startswith("ui/pages/")
                and path.endswith((".yaml", ".yml"))
                and not path.startswith("ui/pages/custom/")
            ]
            if "ui/route_manifest.json" in manifest_paths:
                paths.append("ui/route_manifest.json")
            if cls._experience_requires_shell_update(request=request, intent=intent) and "config/shell.json" in manifest_paths:
                paths.append("config/shell.json")

            custom_paths = [
                path
                for path in manifest_paths
                if path.startswith("ui/pages/custom/")
                and path.endswith((".jsx", ".tsx", ".js", ".ts"))
            ]
            if custom_paths:
                if "ui/index.js" in manifest_paths:
                    paths.append("ui/index.js")
                paths.extend(custom_paths)
            return cls._dedupe_paths(paths)

        paths = ["ui/pages/*.yaml", "ui/route_manifest.json"]
        if cls._experience_requires_shell_update(request=request, intent=intent):
            paths.append("config/shell.json")
        return paths

    @staticmethod
    def _has_module_impact_signal(
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
    ) -> bool:
        text = RefinementTriggerRouteResolver._request_impact_text(request=request, intent=intent)
        for term in _MODULE_IMPACT_TERMS:
            variants = {term}
            if term == "api":
                variants.add("apis")
            elif term.endswith("y"):
                variants.add(f"{term[:-1]}ies")
            else:
                variants.add(f"{term}s")
            for variant in variants:
                pattern = rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])"
                if re.search(pattern, text):
                    return True
        return False

    @staticmethod
    def _module_ids_from_manifest(manifest_paths: list[str]) -> list[str]:
        module_ids: list[str] = []
        for path in manifest_paths:
            parts = path.split("/")
            if len(parts) >= 3 and parts[0] == "modules" and parts[1] and parts[1] not in {"*", "**"}:
                module_id = parts[1]
                if module_id not in module_ids:
                    module_ids.append(module_id)
        return module_ids

    @staticmethod
    def _mentioned_module_ids(
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        known_module_ids: list[str],
    ) -> list[str]:
        text = RefinementTriggerRouteResolver._request_impact_text(request=request, intent=intent)
        mentioned: list[str] = []
        for module_id in known_module_ids:
            variants = {
                module_id.lower(),
                module_id.lower().replace("_", "-"),
                module_id.lower().replace("_", " "),
                module_id.lower().replace("-", " "),
            }
            if module_id.lower().endswith("s") and len(module_id) > 1:
                singular = module_id.lower()[:-1]
                variants.update(
                    {
                        singular,
                        singular.replace("_", "-"),
                        singular.replace("_", " "),
                        singular.replace("-", " "),
                    }
                )
            if any(
                re.search(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])", text)
                for variant in variants
                if variant
            ):
                mentioned.append(module_id)
        return mentioned

    @staticmethod
    def _is_canonical_module_manifest_path(path: str, module_id: str) -> bool:
        prefix = f"modules/{module_id}/"
        if not path.startswith(prefix):
            return False
        relative = path.removeprefix(prefix)
        parts = relative.split("/")
        if relative in {"module.yaml", "runtime_extensions.yaml"}:
            return True
        if len(parts) == 2 and parts[0] == "contracts" and parts[1].endswith((".yaml", ".yml")):
            return True
        return len(parts) == 2 and parts[0] == "backend" and parts[1].endswith(".py")

    @staticmethod
    def _module_path_sort_key(path: str) -> tuple[str, int, int, str]:
        parts = path.split("/")
        module_id = parts[1] if len(parts) > 1 else ""
        relative = "/".join(parts[2:]) if len(parts) > 2 else ""
        if relative == "module.yaml":
            return (module_id, 0, 0, relative)
        if relative.startswith("contracts/"):
            filename = relative.split("/", 1)[1]
            return (module_id, 1, _MODULE_CONTRACT_ORDER.get(filename, 100), relative)
        if relative.startswith("backend/"):
            filename = relative.split("/", 1)[1]
            return (module_id, 2, _MODULE_BACKEND_ORDER.get(filename, 100), relative)
        if relative == "runtime_extensions.yaml":
            return (module_id, 3, 0, relative)
        return (module_id, 100, 0, relative)

    @staticmethod
    def _pack_ids_from_integration_clients(manifest_paths: list[str]) -> list[str]:
        pack_ids: list[str] = []
        for path in manifest_paths:
            match = re.fullmatch(r"services/integrations/([A-Za-z0-9_-]+)_client\.py", path)
            if match:
                pack_id = match.group(1)
                if pack_id not in pack_ids:
                    pack_ids.append(pack_id)
        return pack_ids

    @staticmethod
    def _has_integration_impact_signal(
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        known_connector_ids: list[str],
    ) -> bool:
        text = RefinementTriggerRouteResolver._request_impact_text(request=request, intent=intent)
        if re.search(r"(?<![a-z0-9])managed(?![a-z0-9])", text) or "provider-backed" in text:
            return False
        for term in _INTEGRATION_IMPACT_TERMS:
            variants = {term}
            if term.endswith("y"):
                variants.add(f"{term[:-1]}ies")
            else:
                variants.add(f"{term}s")
            for variant in variants:
                pattern = rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])"
                if re.search(pattern, text):
                    return True
        return bool(
            RefinementTriggerRouteResolver._mentioned_ids(
                request=request,
                intent=intent,
                known_ids=known_connector_ids,
            )
        )

    @staticmethod
    def _is_safe_integration_path(path: str) -> bool:
        normalized = path.lower()
        return not any(term in normalized for term in _INTEGRATION_SECRET_PATH_TERMS)

    @staticmethod
    def _is_integration_config_path(path: str) -> bool:
        return bool(re.fullmatch(r"config/integrations[^/]*\.json", path))

    @staticmethod
    def _is_integration_doc_path(path: str) -> bool:
        return bool(re.fullmatch(r"docs/integrations[^/]*\.md", path))

    @staticmethod
    def _adapter_path_matches_connector(path: str, connector_id: str) -> bool:
        adapter_scope = path.removeprefix("services/adapters/").lower().replace("-", "_")
        normalized_id = str(connector_id or "").lower().replace("-", "_")
        candidates = {normalized_id, normalized_id.replace("_", "")}
        candidates.update(
            token
            for token in normalized_id.split("_")
            if token and token not in {"api", "adapter", "client", "provider", "service"}
        )
        return any(candidate and candidate in adapter_scope for candidate in candidates)

    @staticmethod
    def _module_integration_content_signals(
        *,
        content: str,
        connector_ids: list[str],
    ) -> bool:
        lower_content = content.lower()
        if "backend.integrations" in lower_content or "connector_id" in lower_content or "integration" in lower_content:
            return True
        return any(
            connector_id.lower() in lower_content
            or connector_id.lower().replace("_", "-") in lower_content
            or connector_id.lower().replace("_", " ") in lower_content
            for connector_id in connector_ids
        )

    @classmethod
    def _module_ids_with_integration_content(
        cls,
        *,
        request: RefinementRequest,
        connector_ids: list[str],
    ) -> list[str]:
        module_ids: list[str] = []
        for entry in cls._manifest_entries_from_extra(request.extra):
            path = str(entry.get("path") or "").strip().replace("\\", "/").lstrip("/")
            parts = path.split("/")
            if len(parts) < 4 or parts[0] != "modules":
                continue
            content = cls._page_content_from_manifest_entry(entry)
            if content and cls._module_integration_content_signals(content=content, connector_ids=connector_ids):
                module_id = parts[1]
                if module_id not in module_ids:
                    module_ids.append(module_id)
        return module_ids

    @classmethod
    def _integration_bundle_paths(
        cls,
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        manifest_paths: list[str],
    ) -> list[str]:
        known_connector_ids = cls._pack_ids_from_integration_clients(manifest_paths)
        if not cls._has_integration_impact_signal(
            request=request,
            intent=intent,
            known_connector_ids=known_connector_ids,
        ):
            return []

        if not manifest_paths:
            paths = [
                "services/integrations/*_client.py",
                "services/adapters/**/*.py",
                "modules/*/backend/service.py",
                "modules/*/backend/schemas.py",
                "modules/*/module.yaml",
                "config/integrations*.json",
                "docs/integrations*.md",
            ]
            if cls._is_ui_facing_request(request=request, intent=intent):
                paths.append("ui/pages/*.yaml")
            return paths

        mentioned_connector_ids = cls._mentioned_ids(
            request=request,
            intent=intent,
            known_ids=known_connector_ids,
        )
        selected_connector_ids = mentioned_connector_ids or known_connector_ids

        paths = [
            path
            for path in manifest_paths
            if cls._is_safe_integration_path(path)
            and (
                re.fullmatch(r"services/integrations/[A-Za-z0-9_-]+_client\.py", path)
                or path.startswith("services/adapters/")
            )
            and (
                not mentioned_connector_ids
                or (
                    path.startswith("services/adapters/")
                    and any(
                        cls._adapter_path_matches_connector(path, connector_id)
                        for connector_id in selected_connector_ids
                    )
                )
                or path.removeprefix("services/integrations/").removesuffix("_client.py") in selected_connector_ids
            )
        ]

        known_module_ids = cls._module_ids_from_manifest(manifest_paths)
        module_ids = cls._module_ids_with_integration_content(
            request=request,
            connector_ids=selected_connector_ids,
        )
        for module_id in cls._mentioned_module_ids(
            request=request,
            intent=intent,
            known_module_ids=known_module_ids,
        ):
            if module_id not in module_ids:
                module_ids.append(module_id)

        module_path_set = {
            f"modules/{module_id}/module.yaml"
            for module_id in module_ids
            if f"modules/{module_id}/module.yaml" in manifest_paths
        }
        for module_id in module_ids:
            for filename in ("service.py", "schemas.py", "policy.py"):
                path = f"modules/{module_id}/backend/{filename}"
                if path in manifest_paths:
                    module_path_set.add(path)
        paths.extend(sorted(module_path_set, key=cls._module_path_sort_key))

        paths.extend(
            sorted(
                path
                for path in manifest_paths
                if cls._is_safe_integration_path(path)
                and (cls._is_integration_config_path(path) or cls._is_integration_doc_path(path))
            )
        )

        if cls._is_ui_facing_request(request=request, intent=intent):
            page_paths = [
                path
                for path in manifest_paths
                if path.startswith("ui/pages/")
                and path.endswith((".yaml", ".yml"))
                and not path.startswith("ui/pages/custom/")
            ]
            paths.extend(sorted(page_paths))

        return cls._dedupe_paths(paths)

    @staticmethod
    def _integration_readiness_path_hint_present(paths: list[str]) -> bool:
        return any(
            path.startswith("services/integrations/")
            or path.startswith("services/adapters/")
            or path.startswith("config/integrations")
            or path.startswith("docs/integrations")
            for path in paths
        )

    @staticmethod
    def _has_data_model_impact_signal(
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        known_module_ids: list[str],
    ) -> bool:
        text = RefinementTriggerRouteResolver._request_impact_text(request=request, intent=intent)
        for term in _DATA_MODEL_IMPACT_TERMS:
            variants = {term}
            if term.endswith("y"):
                variants.add(f"{term[:-1]}ies")
            else:
                variants.add(f"{term}s")
            for variant in variants:
                pattern = rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])"
                if re.search(pattern, text):
                    return True
        has_context = any(
            re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text)
            for term in _DATA_MODEL_CONTEXT_TERMS
        )
        if has_context and any(
            re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text)
            for term in _DATA_MODEL_CONTEXTUAL_TERMS
        ):
            return True
        return bool(
            RefinementTriggerRouteResolver._mentioned_module_ids(
                request=request,
                intent=intent,
                known_module_ids=known_module_ids,
            )
            and re.search(r"(?<![a-z0-9])(repo|policy|schemas|persistence)(?![a-z0-9])", text)
        )

    @staticmethod
    def _has_destructive_data_model_signal(
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
    ) -> bool:
        text = RefinementTriggerRouteResolver._request_impact_text(request=request, intent=intent)
        if any(
            re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text)
            for term in _DATA_MODEL_DESTRUCTIVE_TERMS
        ):
            return True
        return bool(re.search(r"(?<![a-z0-9])(required|unique|delete|remove|drop|rename|change type)\b.*\bfield(?![a-z0-9])", text))

    @staticmethod
    def _is_database_migration_path(path: str) -> bool:
        return bool(re.fullmatch(r"data/migrations/[^/]+\.json", path))

    @classmethod
    def _data_model_bundle_paths(
        cls,
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        manifest_paths: list[str],
    ) -> list[str]:
        known_module_ids = cls._module_ids_from_manifest(manifest_paths)
        if not cls._has_data_model_impact_signal(
            request=request,
            intent=intent,
            known_module_ids=known_module_ids,
        ):
            return []

        if not manifest_paths:
            paths = [
                "data/contract.json",
                "data/migrations/*.json",
                "modules/*/backend/schemas.py",
                "modules/*/backend/repo.py",
                "modules/*/backend/policy.py",
                "modules/*/module.yaml",
            ]
            if cls._is_ui_facing_request(request=request, intent=intent):
                paths.append("ui/pages/*.yaml")
            return paths

        mentioned_module_ids = cls._mentioned_module_ids(
            request=request,
            intent=intent,
            known_module_ids=known_module_ids,
        )

        paths: list[str] = ["data/contract.json"]  # type: ignore[no-redef]

        migration_paths = sorted(
            path
            for path in manifest_paths
            if cls._is_database_migration_path(path)
        )
        if migration_paths:
            paths.extend(migration_paths)
        else:
            paths.append("data/migrations/*.json")

        if mentioned_module_ids:
            module_paths: list[str] = []
            for module_id in mentioned_module_ids:
                for path in (
                    f"modules/{module_id}/module.yaml",
                    f"modules/{module_id}/contracts/events.yaml",
                    f"modules/{module_id}/contracts/admin.yaml",
                    f"modules/{module_id}/backend/repo.py",
                    f"modules/{module_id}/backend/policy.py",
                    f"modules/{module_id}/backend/schemas.py",
                ):
                    if path in manifest_paths:
                        module_paths.append(path)
            paths.extend(sorted(module_paths, key=cls._module_path_sort_key))
        else:
            paths.extend(
                [
                    "modules/*/backend/schemas.py",
                    "modules/*/backend/repo.py",
                    "modules/*/backend/policy.py",
                    "modules/*/module.yaml",
                ]
            )

        if cls._is_ui_facing_request(request=request, intent=intent):
            page_paths = [
                path
                for path in manifest_paths
                if path.startswith("ui/pages/")
                and path.endswith((".yaml", ".yml"))
                and not path.startswith("ui/pages/custom/")
            ]
            paths.extend(sorted(page_paths) or ["ui/pages/*.yaml"])

        if not paths:
            paths.extend(
                [
                    "data/contract.json",
                    "data/migrations/*.json",
                    "modules/*/backend/schemas.py",
                    "modules/*/backend/repo.py",
                    "modules/*/backend/policy.py",
                    "modules/*/module.yaml",
                ]
            )

        return cls._dedupe_paths(paths)

    @staticmethod
    def _data_model_path_hint_present(paths: list[str]) -> bool:
        return any(
            path == "data/contract.json"
            or path.startswith("data/migrations/")
            or path.startswith("modules/*/backend/schemas.py")
            or path.endswith("/backend/schemas.py")
            or path.endswith("/backend/repo.py")
            or path.endswith("/backend/policy.py")
            for path in paths
        )

    @staticmethod
    def _mentioned_ids(
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        known_ids: list[str],
    ) -> list[str]:
        text = RefinementTriggerRouteResolver._request_impact_text(request=request, intent=intent)
        mentioned: list[str] = []
        for known_id in known_ids:
            variants = {
                known_id.lower(),
                known_id.lower().replace("_", "-"),
                known_id.lower().replace("_", " "),
                known_id.lower().replace("-", " "),
            }
            if known_id.lower().endswith("s") and len(known_id) > 1:
                singular = known_id.lower()[:-1]
                variants.update(
                    {
                        singular,
                        singular.replace("_", "-"),
                        singular.replace("_", " "),
                        singular.replace("-", " "),
                    }
                )
            if any(
                re.search(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])", text)
                for variant in variants
                if variant
            ):
                mentioned.append(known_id)
        return mentioned

    @staticmethod
    def _has_managed_capability_signal(
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        known_pack_ids: list[str],
    ) -> bool:
        text = RefinementTriggerRouteResolver._request_impact_text(request=request, intent=intent)
        for term in _MANAGED_CAPABILITY_TERMS:
            pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
            if re.search(pattern, text):
                return True
        if re.search(r"(?<![a-z0-9])managed(?![a-z0-9])", text):
            for term in _MANAGED_CAPABILITY_CATEGORY_TERMS:
                pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
                if re.search(pattern, text):
                    return True
        return bool(
            RefinementTriggerRouteResolver._mentioned_ids(
                request=request,
                intent=intent,
                known_ids=known_pack_ids,
            )
        )

    @staticmethod
    def _is_ui_facing_request(
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
    ) -> bool:
        text = RefinementTriggerRouteResolver._request_impact_text(request=request, intent=intent)
        return any(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) for term in _UI_SURFACE_TERMS)

    @staticmethod
    def _pack_tokens(pack_ids: list[str]) -> set[str]:
        tokens: set[str] = set()
        for pack_id in pack_ids:
            for token in re.split(r"[_\-\s]+", pack_id.lower()):
                if token and token not in _MANAGED_GENERIC_PACK_TOKENS:
                    tokens.add(token)
        return tokens

    @classmethod
    def _facade_module_ids_for_managed_impact(
        cls,
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        manifest_paths: list[str],
        pack_ids: list[str],
    ) -> list[str]:
        known_module_ids = cls._module_ids_from_manifest(manifest_paths)
        mentioned_module_ids = cls._mentioned_module_ids(
            request=request,
            intent=intent,
            known_module_ids=known_module_ids,
        )
        facade_ids: list[str] = []
        pack_id_set = set(pack_ids)
        pack_tokens = cls._pack_tokens(pack_ids)
        for module_id in known_module_ids:
            if module_id in pack_id_set or module_id.startswith("managed_") or module_id.startswith("provider_"):
                continue
            module_tokens = set(re.split(r"[_\-\s]+", module_id.lower()))
            if module_id in mentioned_module_ids or (pack_tokens and pack_tokens.intersection(module_tokens)):
                facade_ids.append(module_id)
        return facade_ids

    @staticmethod
    def _page_content_from_manifest_entry(entry: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("content", "text", "source", "yaml", "json"):
            value = entry.get(key)
            if isinstance(value, str):
                parts.append(value)
        metadata = entry.get("metadata")
        if isinstance(metadata, dict):
            parts.extend(str(value) for value in metadata.values() if isinstance(value, str))
        return "\n".join(parts)

    @classmethod
    def _managed_capability_bundle_paths(
        cls,
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        manifest_paths: list[str],
    ) -> list[str]:
        known_pack_ids = cls._pack_ids_from_integration_clients(manifest_paths)
        if not cls._has_managed_capability_signal(
            request=request,
            intent=intent,
            known_pack_ids=known_pack_ids,
        ):
            return []

        if not manifest_paths:
            paths = [
                "services/integrations/*_client.py",
                "modules/*/module.yaml",
                "modules/*/contracts/*.yaml",
                "modules/*/backend/*.py",
            ]
            if cls._is_ui_facing_request(request=request, intent=intent):
                paths.append("ui/pages/*.yaml")
            return paths

        mentioned_pack_ids = cls._mentioned_ids(
            request=request,
            intent=intent,
            known_ids=known_pack_ids,
        )
        selected_pack_ids = mentioned_pack_ids or known_pack_ids
        paths = [
            path
            for path in manifest_paths
            if re.fullmatch(r"services/integrations/[A-Za-z0-9_-]+_client\.py", path)
            and (
                not mentioned_pack_ids
                or path.removeprefix("services/integrations/").removesuffix("_client.py") in selected_pack_ids
            )
        ]

        facade_module_ids = cls._facade_module_ids_for_managed_impact(
            request=request,
            intent=intent,
            manifest_paths=manifest_paths,
            pack_ids=selected_pack_ids,
        )
        module_paths = [
            path
            for module_id in facade_module_ids
            for path in manifest_paths
            if cls._is_canonical_module_manifest_path(path, module_id)
        ]
        paths.extend(sorted(module_paths, key=cls._module_path_sort_key))

        manifest_entries = cls._manifest_entries_from_extra(request.extra)
        page_paths: list[str] = []
        for entry in manifest_entries:
            page_path = str(entry.get("path") or "").strip().replace("\\", "/").lstrip("/")
            if not page_path.startswith("ui/pages/") or not page_path.endswith((".yaml", ".yml")):
                continue
            content = cls._page_content_from_manifest_entry(entry)
            if any(f"/api/modules/{module_id}/" in content for module_id in facade_module_ids):
                page_paths.append(page_path)

        if page_paths:
            paths.extend(sorted(page_paths))
        elif cls._is_ui_facing_request(request=request, intent=intent):
            paths.append("ui/pages/*.yaml")

        return cls._dedupe_paths(paths)

    @classmethod
    def _module_bundle_paths(
        cls,
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        manifest_paths: list[str],
    ) -> list[str]:
        if not cls._has_module_impact_signal(request=request, intent=intent):
            return []

        known_module_ids = cls._module_ids_from_manifest(manifest_paths)
        mentioned_module_ids = cls._mentioned_module_ids(
            request=request,
            intent=intent,
            known_module_ids=known_module_ids,
        )

        if mentioned_module_ids and manifest_paths:
            paths = [
                path
                for module_id in mentioned_module_ids
                for path in manifest_paths
                if cls._is_canonical_module_manifest_path(path, module_id)
            ]
            return cls._dedupe_paths(sorted(paths, key=cls._module_path_sort_key))

        if mentioned_module_ids:
            paths: list[str] = []  # type: ignore[no-redef]
            for module_id in mentioned_module_ids:
                paths.extend(
                    [
                        f"modules/{module_id}/module.yaml",
                        f"modules/{module_id}/contracts/*.yaml",
                        f"modules/{module_id}/backend/*.py",
                    ]
                )
            return cls._dedupe_paths(paths)

        return [
            "modules/*/module.yaml",
            "modules/*/contracts/*.yaml",
            "modules/*/backend/*.py",
        ]

    async def _affected_bundle_paths_for_impact(
        self,
        *,
        request: RefinementRequest,
        intent: ChangeIntent,
        families: list[str],
    ) -> list[str]:
        family_set = set(families)
        if "experience_spec" not in family_set and "app_bundle" not in family_set:
            return []
        manifest_paths = await self._manifest_paths_for_request(request)
        paths: list[str] = []
        if "experience_spec" in family_set:
            paths.extend(
                self._experience_spec_bundle_paths(
                    request=request,
                    intent=intent,
                    manifest_paths=manifest_paths,
                )
            )
        if "app_bundle" in family_set:
            integration_paths = self._integration_bundle_paths(
                request=request,
                intent=intent,
                manifest_paths=manifest_paths,
            )
            paths.extend(integration_paths)
            if integration_paths:
                return self._dedupe_paths(paths)
            managed_capability_paths = self._managed_capability_bundle_paths(
                request=request,
                intent=intent,
                manifest_paths=manifest_paths,
            )
            paths.extend(managed_capability_paths)
            if managed_capability_paths:
                return self._dedupe_paths(paths)
            data_model_paths = self._data_model_bundle_paths(
                request=request,
                intent=intent,
                manifest_paths=manifest_paths,
            )
            paths.extend(data_model_paths)
            if data_model_paths:
                return self._dedupe_paths(paths)
            paths.extend(
                self._module_bundle_paths(
                    request=request,
                    intent=intent,
                    manifest_paths=manifest_paths,
                )
            )
        return self._dedupe_paths(paths)

    async def _stale_route(self, request: RefinementRequest) -> RefinementRoutingDecision | None:
        """Return a deterministic restart decision if upstream artifacts are stale.

        Bypasses the LLM classifier entirely when stale families are detected —
        there is no point classifying the user's change request if the upstream
        artifacts it depends on are already out of date.
        """
        if not request.app_id:
            return None
        try:
            from mozaiksai.core.artifacts.store import (
                ArtifactStore,  # local import avoids circular dep
            )
            store = ArtifactStore()
            stale = await store.get_stale_artifact_families(app_id=request.app_id)
        except Exception as exc:
            _logger.debug("STALE_ARTIFACT_LOOKUP_FAILED app=%s: %s", request.app_id, exc)
            return None
        if not stale:
            return None

        stale_set = set(stale)
        restart_family = next((f for f in _STALE_PRIORITY if f in stale_set), None)
        if not restart_family:
            return None

        sequence_id = _STALE_SEQUENCE_MAP.get(restart_family)
        if not sequence_id:
            return None

        affected_workflows = self._sequence_workflows(sequence_id)
        families = self._sequence_families(sequence_id)
        workflow_id = affected_workflows[0] if affected_workflows else restart_family

        intent = ChangeIntent(
            change_class=ChangeClass.DESIGN,
            source="stale_upstream",
            signals=sorted(stale_set),
            rationale=(
                f"Upstream artifact family '{restart_family}' is stale; "
                f"rebuilding from {workflow_id} before processing the change request."
            ),
            confidence=1.0,
            requires_concept_revision=(restart_family == "concept"),
            touches_app_bundle="app_bundle" in families,
            touches_workflow_bundle="workflow_bundle" in families,
            touches_design_docs="design_docs" in families,
            touches_concept="concept" in families,
        )
        affected_bundle_paths = await self._affected_bundle_paths_for_impact(
            request=request,
            intent=intent,
            families=families,
        )
        impact = ImpactSet(
            workflow_sequence=sequence_id,
            affected_workflows=affected_workflows,
            affected_bundle_paths=affected_bundle_paths,
            affected_declarative_families=families,
            requires_replanning=True,
            requires_rebuild=True,
            restart_from=workflow_id,
            scope_summary=(
                f"Stale upstream detected: rebuilding from '{restart_family}' "
                f"before applying the requested change."
            ),
        )
        return RefinementRoutingDecision(
            workflow_id=workflow_id,
            workflow_sequence=sequence_id,
            refinement_request=request,
            change_intent=intent,
            impact_set=impact,
            explanation=(
                f"Stale upstream '{restart_family}' detected for app '{request.app_id}'; "
                f"routing to {workflow_id} to rebuild before processing the change."
            ),
            is_full_restart=(restart_family == "concept"),
        )

    async def _derive_change_intent(self, request: RefinementRequest) -> ChangeIntent:
        classification = await self._classifier.classify(
            artifact_kind=request.artifact_kind,
            artifact_key=request.artifact_key,
            raw_user_request=request.raw_user_request,
            declared_change_class=request.declared_change_class.value if request.declared_change_class else None,
            artifact_version_id=request.artifact_version_id,
            source_surface=request.source_surface,
            app_id=request.app_id,
            user_id=request.user_id,
            requested_workflow_id=request.requested_workflow_id,
            extra=request.extra,
        )
        change_class = ChangeClass(classification.change_class)
        source = "llm"
        signals = [str(signal).strip() for signal in classification.signals if str(signal).strip()]
        policy = self._policy_for(request.artifact_kind)
        route = policy.route_for(change_class)
        families = set(self._families_for_route(route, request.artifact_kind))
        label = self._artifact_label(request.artifact_kind, policy)

        if change_class == ChangeClass.CORE:
            return ChangeIntent(
                change_class=change_class,
                source=source,
                signals=signals,
                rationale=str(classification.rationale).strip()
                or f"Core revision requested for the {label}; reopen the upstream workflow and downstream dependent outputs.",
                confidence=classification.confidence,
                requires_concept_revision=True,
                touches_app_bundle="app_bundle" in families,
                touches_workflow_bundle="workflow_bundle" in families,
                touches_design_docs="design_docs" in families,
                touches_concept="concept" in families,
            )

        touches_app_bundle = "app_bundle" in families
        touches_workflow_bundle = "workflow_bundle" in families
        touches_design_docs = "design_docs" in families
        touches_concept = "concept" in families

        if change_class == ChangeClass.DESIGN:
            rationale = str(classification.rationale).strip() or (
                f"Design-scoped revision requested for the {label}; preserve the current concept while revisiting the structured owning workflow."
            )
        elif change_class == ChangeClass.FEATURE:
            rationale = str(classification.rationale).strip() or (
                f"Feature extension requested for the {label}; preserve the current concept while widening the owned implementation scope."
            )
        else:
            rationale = str(classification.rationale).strip() or (
                f"Scoped patch requested for the {label}; stay within the current artifact boundary unless validation forces wider scope."
            )

        return ChangeIntent(
            change_class=change_class,
            source=source,
            signals=signals,
            rationale=rationale,
            confidence=classification.confidence,
            requires_concept_revision=(change_class == ChangeClass.CORE) or (touches_concept and change_class != ChangeClass.PATCH),
            touches_app_bundle=touches_app_bundle,
            touches_workflow_bundle=touches_workflow_bundle,
            touches_design_docs=touches_design_docs,
            touches_concept=touches_concept,
        )

    async def _derive_impact_set(self, request: RefinementRequest, intent: ChangeIntent) -> ImpactSet:
        policy = self._policy_for(request.artifact_kind)
        route = policy.route_for(intent.change_class)
        families = self._families_for_route(route, request.artifact_kind)
        label = self._artifact_label(request.artifact_kind, policy)
        workflow_id = self._route_workflow_id(route)
        affected_workflows = self._affected_workflows_for_route(route)
        if intent.change_class == ChangeClass.CORE:
            scope_summary = f"Restart from {workflow_id} and invalidate downstream outputs that depend on the {label}."
        elif intent.change_class == ChangeClass.DESIGN:
            scope_summary = f"Reopen structured planning surfaces for the {label} and rebuild the affected downstream outputs."
        elif intent.change_class == ChangeClass.FEATURE:
            scope_summary = f"Extend the existing {label} within the approved concept using {workflow_id}."
        else:
            scope_summary = f"Apply a local patch to the current {label} without widening upstream scope."
        affected_bundle_paths = await self._affected_bundle_paths_for_impact(
            request=request,
            intent=intent,
            families=families,
        )
        if self._integration_readiness_path_hint_present(affected_bundle_paths):
            scope_summary = f"{scope_summary} Integration readiness may need to be rechecked."
        if self._data_model_path_hint_present(affected_bundle_paths):
            scope_summary = f"{scope_summary} Data model migration impact detected."
            if self._has_destructive_data_model_signal(request=request, intent=intent):
                scope_summary = f"{scope_summary} Destructive changes require explicit review."
        return ImpactSet(
            workflow_sequence=route.workflow_sequence,
            affected_workflows=affected_workflows,
            affected_bundle_paths=affected_bundle_paths,
            affected_declarative_families=families,
            requires_replanning=intent.change_class != ChangeClass.PATCH,
            requires_rebuild=True,
            restart_from=workflow_id,
            scope_summary=scope_summary,
        )

    def _derive_route(
        self,
        request: RefinementRequest,
        *,
        change_intent: ChangeIntent,
        impact_set: ImpactSet,
    ) -> RefinementRoutingDecision:
        policy = self._policy_for(request.artifact_kind)
        route = policy.route_for(change_intent.change_class)
        label = self._artifact_label(request.artifact_kind, policy)
        workflow_id = self._route_workflow_id(route)

        if change_intent.change_class == ChangeClass.CORE:
            return RefinementRoutingDecision(
                workflow_id=workflow_id,
                workflow_sequence=route.workflow_sequence,
                refinement_request=request,
                change_intent=change_intent,
                impact_set=impact_set,
                explanation=f"Core concept change detected for {label}; restarting from {workflow_id}.",
                is_full_restart=True,
            )

        if change_intent.change_class == ChangeClass.DESIGN:
            return RefinementRoutingDecision(
                workflow_id=workflow_id,
                workflow_sequence=route.workflow_sequence,
                refinement_request=request,
                change_intent=change_intent,
                impact_set=impact_set,
                explanation=f"Re-entering {workflow_id} to revise design-owned aspects of the {label}.",
                is_full_restart=False,
            )

        if change_intent.change_class == ChangeClass.FEATURE:
            return RefinementRoutingDecision(
                workflow_id=workflow_id,
                workflow_sequence=route.workflow_sequence,
                refinement_request=request,
                change_intent=change_intent,
                impact_set=impact_set,
                explanation=f"Re-entering {workflow_id} to extend the {label} within the current concept.",
                is_full_restart=False,
            )

        return RefinementRoutingDecision(
            workflow_id=workflow_id,
            workflow_sequence=route.workflow_sequence,
            refinement_request=request,
            change_intent=change_intent,
            impact_set=impact_set,
            explanation=f"Re-entering {workflow_id} to apply a scoped patch to the {label}.",
            is_full_restart=False,
        )

    # Validation: alphanumeric, underscore, hyphen only; max 80 chars; no path components.
    _MODULE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
    _MODULE_ID_MAX_LEN = 80

    @staticmethod
    def _validate_carry_forward_module_id(module_id: str) -> tuple[bool, str]:
        """Return ``(is_valid, reason)`` for a single auto-extracted module id.

        Rejects:
        - empty string
        - "." or ".."
        - contains "/" or "\\"
        - contains any character outside ``[a-zA-Z0-9_-]``
        - length > 80 characters
        """
        if not module_id:
            return False, "empty"
        if module_id in (".", ".."):
            return False, f"reserved name {module_id!r}"
        if "/" in module_id or "\\" in module_id:
            return False, "contains path separator"
        if len(module_id) > RefinementTriggerRouteResolver._MODULE_ID_MAX_LEN:
            return False, f"exceeds {RefinementTriggerRouteResolver._MODULE_ID_MAX_LEN} characters"
        if not RefinementTriggerRouteResolver._MODULE_ID_RE.match(module_id):
            return False, "contains disallowed characters (only a-z, A-Z, 0-9, _, - permitted)"
        return True, ""

    @staticmethod
    def _sanitize_carry_forward_module_ids(
        module_ids: list[str],
    ) -> tuple[list[str], list[str]]:
        """Validate and deduplicate auto-extracted module ids.

        Returns ``(valid_ids, rejection_warnings)``. Order is preserved;
        duplicates are removed keeping the first occurrence. Each rejected id
        produces one warning string logged by the caller.
        """
        valid: list[str] = []
        seen: set[str] = set()
        rejection_warnings: list[str] = []
        for mid in module_ids:
            is_valid, reason = RefinementTriggerRouteResolver._validate_carry_forward_module_id(mid)
            if not is_valid:
                msg = f"carry_forward_invalid_module_id: {mid!r} rejected ({reason})"
                rejection_warnings.append(msg)
                _logger.warning("carry_forward module_id rejected for auto-extraction: %s", msg)
                continue
            if mid in seen:
                continue
            seen.add(mid)
            valid.append(mid)
        return valid, rejection_warnings

    async def _auto_carry_forward_resolution(
        self,
        *,
        request: RefinementRequest,
        previous_app_bundle_ref: str,
    ) -> tuple[list[str], list[str]]:
        """Auto-extract carry-forward module ids using the registered tool.

        Calls the ``get_carry_forward_candidates`` control-plane tool via the
        pack executor mechanism — no direct import from factory_app.

        Returns ``(module_ids, warnings)``. Never raises. Returns
        ``([], [warning])`` on any failure so the router can degrade gracefully.
        """
        try:
            pack = self._load_pack()
            tool_def = pack.tool_by_id("get_carry_forward_candidates")
            if tool_def is None:
                return [], [
                    "carry_forward_tool_not_registered: "
                    "get_carry_forward_candidates is not declared in the pack tools; "
                    "carry_forward_modules defaults to []"
                ]
            fn = resolve_control_plane_tool_entrypoint(tool_def.entrypoint)
            ctx = ControlPlaneToolContext(
                checkpoint="route_requested",
                app_id=request.app_id,
                extra={"previous_app_bundle_ref": previous_app_bundle_ref},
            )
            result = await fn(context=ctx)
            if not isinstance(result, dict):
                return [], ["carry_forward_invalid_result: tool returned a non-dict value"]
            modules: list[Any] = result.get("modules") or []
            warnings: list[str] = [str(w) for w in (result.get("warnings") or [])]
            raw_ids = [
                str(m.get("module_id", "")).strip()
                for m in modules
                if isinstance(m, dict) and str(m.get("module_id", "")).strip()
            ]
            module_ids, rejection_warnings = self._sanitize_carry_forward_module_ids(raw_ids)
            warnings.extend(rejection_warnings)
            return module_ids, warnings
        except Exception as exc:
            _logger.warning(
                "carry_forward auto-extraction failed for app_id=%s version=%s: %s",
                request.app_id,
                previous_app_bundle_ref,
                exc,
            )
            return [], [f"carry_forward_extraction_error: {exc}"]

    async def _build_context_seed(
        self,
        *,
        request: RefinementRequest,
        change_intent: ChangeIntent,
        impact_set: ImpactSet,
    ) -> dict[str, Any]:
        context_seed: dict[str, Any] = {
            "build_mode": "revision",
            "revision_scope": change_intent.change_class.value,
            "artifact_kind": request.artifact_kind,
            "refinement_request": request.raw_user_request,
            "refinement_request_meta": request.model_dump(mode="python"),
            "screen": request.source_surface,
            "change_intent": change_intent.model_dump(mode="python"),
            "impact_set": impact_set.model_dump(mode="python"),
            "revision_origin_workflow": request.requested_workflow_id,
        }
        if request.artifact_version_id:
            context_seed["artifact_version_id"] = request.artifact_version_id
        # When the app is in 'review' (pre-promotion), revisions must operate on
        # the generated bundle path rather than the active workspace root.
        lifecycle_state = request.extra.get("lifecycle_state")
        bundle_path = request.extra.get("bundle_path")
        if lifecycle_state == "review" and isinstance(bundle_path, str) and bundle_path.strip():
            context_seed["artifact_root"] = bundle_path.strip()
            context_seed["lifecycle_state"] = "review"
        if impact_set.workflow_sequence:
            context_seed["workflow_sequence"] = impact_set.workflow_sequence
        change_request_id = request.extra.get("change_request_id")
        if isinstance(change_request_id, str) and change_request_id.strip():
            context_seed["change_request_id"] = change_request_id.strip()
        revision_id = request.extra.get("revision_id")
        if isinstance(revision_id, str) and revision_id.strip():
            context_seed["revision_id"] = revision_id.strip()
        # Inject the parent theme config so ThemeCapture can use it as a
        # starting point on design/feature routes instead of generating from
        # scratch. Sent by the workbench inside refinement_request.extra.
        parent_theme_config = request.extra.get("parent_theme_config")
        if isinstance(parent_theme_config, dict) and parent_theme_config:
            context_seed["parent_theme_config"] = parent_theme_config
        # Signal a stronger reasoning model for architecture-level sequences.
        # This is advisory context plumbing — the runner reads llm_profile and
        # selects the declared model when a consumer is wired up. The classifier
        # and coding worker configs are not affected.
        if impact_set.workflow_sequence in ("conceptual_replan", "full_rebuild"):
            context_seed["llm_profile"] = "architecture"
        # Inject conceptual-replan context when the sequence is a concept-level
        # pivot. These fields let downstream workflows distinguish a pivot from a
        # blind full_rebuild and carry forward reusable context.
        if impact_set.workflow_sequence == "conceptual_replan":
            context_seed["pivot_description"] = request.raw_user_request
            existing_concept_ref = request.extra.get("existing_concept_ref")
            if isinstance(existing_concept_ref, str) and existing_concept_ref.strip():
                context_seed["existing_concept_ref"] = existing_concept_ref.strip()
            preserve_families = request.extra.get("preserve_families")
            context_seed["preserve_families"] = (
                list(preserve_families)
                if isinstance(preserve_families, list)
                else ["brand"]
            )
            previous_brand_ref = request.extra.get("previous_brand_ref")
            if isinstance(previous_brand_ref, str) and previous_brand_ref.strip():
                context_seed["previous_brand_ref"] = previous_brand_ref.strip()
            # Normalize the previous_app_bundle_ref into a single variable used
            # both for the context seed and carry-forward extraction.
            previous_app_bundle_ref_raw = request.extra.get("previous_app_bundle_ref")
            previous_app_bundle_ref = (
                previous_app_bundle_ref_raw.strip()
                if isinstance(previous_app_bundle_ref_raw, str) and previous_app_bundle_ref_raw.strip()
                else None
            )
            if previous_app_bundle_ref:
                context_seed["previous_app_bundle_ref"] = previous_app_bundle_ref
            # carry_forward_modules: advisory list of module ids for AppPlanAgent.
            # Explicit client override always wins. When absent, attempt deterministic
            # extraction from the previous app_bundle artifact workspace via the
            # registered get_carry_forward_candidates tool. No file copy or merge
            # occurs — this is context plumbing only.
            carry_forward_modules = request.extra.get("carry_forward_modules")
            if isinstance(carry_forward_modules, list):
                # Client supplied an explicit list — use as-is, skip extraction.
                context_seed["carry_forward_modules"] = list(carry_forward_modules)
            elif previous_app_bundle_ref:
                # Auto-populate from the previous artifact workspace.
                module_ids, warnings = await self._auto_carry_forward_resolution(
                    request=request,
                    previous_app_bundle_ref=previous_app_bundle_ref,
                )
                context_seed["carry_forward_modules"] = module_ids
                if warnings:
                    context_seed["carry_forward_warnings"] = warnings
            else:
                context_seed["carry_forward_modules"] = []
        return context_seed

    async def route(self, request: RefinementRequest) -> RefinementRoutingDecision:
        # Stale-upstream check: bypass LLM if upstream artifacts need rebuilding first.
        stale_decision = await self._stale_route(request)
        if stale_decision is not None:
            stale_decision.context_seed = await self._build_context_seed(
                request=request,
                change_intent=stale_decision.change_intent,
                impact_set=stale_decision.impact_set,
            )
            _logger.info(
                "Stale-upstream routing: families=%s -> workflow=%s sequence=%s",
                stale_decision.change_intent.signals,
                stale_decision.workflow_id,
                stale_decision.workflow_sequence,
            )
            return stale_decision

        change_intent = await self._derive_change_intent(request)
        impact_set = await self._derive_impact_set(request, change_intent)
        decision = self._derive_route(
            request,
            change_intent=change_intent,
            impact_set=impact_set,
        )
        decision.context_seed = await self._build_context_seed(
            request=request,
            change_intent=change_intent,
            impact_set=impact_set,
        )
        _logger.info(
            "Routing decision: change_class=%s artifact_kind=%s -> workflow=%s restart=%s",
            change_intent.change_class,
            request.artifact_kind,
            decision.workflow_id,
            decision.is_full_restart,
        )
        return decision

    def request_from_payload(
        self,
        *,
        payload: dict[str, Any],
        app_id: str | None = None,
        user_id: str | None = None,
        requested_workflow_id: str | None = None,
        default_source_surface: str | None = None,
    ) -> RefinementRequest | None:
        nested_request = payload.get("refinement_request")
        if not isinstance(nested_request, dict):
            return None
        request_payload = dict(nested_request)
        request_payload.setdefault("extra", {})
        if not isinstance(request_payload["extra"], dict):
            request_payload["extra"] = {}
        harness_action = payload.get("harness_action")
        if isinstance(harness_action, dict):
            request_payload["extra"]["harness_action"] = dict(harness_action)
        change_request_id = payload.get("change_request_id")
        if isinstance(change_request_id, str) and change_request_id.strip():
            request_payload["extra"]["change_request_id"] = change_request_id.strip()
        revision_id = payload.get("revision_id")
        if isinstance(revision_id, str) and revision_id.strip():
            request_payload["extra"]["revision_id"] = revision_id.strip()

        default_artifact_kind = self._load_pack().manifest.routing.default_artifact_kind
        request_payload.setdefault("artifact_kind", default_artifact_kind)
        request_payload["artifact_key"] = (
            str(request_payload.get("artifact_key") or request_payload.get("artifact_kind") or default_artifact_kind)
            .strip()
            or default_artifact_kind
        )
        request_payload["app_id"] = str(app_id or "").strip() or None
        request_payload["user_id"] = str(user_id or "").strip() or None
        request_payload["requested_workflow_id"] = str(requested_workflow_id or "").strip() or None
        if default_source_surface and not request_payload.get("source_surface"):
            request_payload["source_surface"] = default_source_surface
        return RefinementRequest.model_validate(request_payload)

    def request_from_trigger(self, trigger: TriggerInput) -> RefinementRequest | None:
        trigger_source = str(trigger.trigger_source or "").strip().lower()
        if trigger_source != "refinement":
            return None
        default_source_surface = None
        screen = trigger.context_variables.get("screen")
        if isinstance(screen, str) and screen.strip():
            default_source_surface = screen.strip()
        payload = dict(trigger.trigger_payload or {})
        # Forward lifecycle state + bundle path from context_variables so
        # _build_context_seed can set artifact_root when the app is pre-promotion.
        payload.setdefault("extra", {})
        if not isinstance(payload["extra"], dict):
            payload["extra"] = {}
        lifecycle_state = trigger.context_variables.get("lifecycle_state")
        if isinstance(lifecycle_state, str) and lifecycle_state.strip():
            payload["extra"].setdefault("lifecycle_state", lifecycle_state.strip())
        bundle_path = trigger.context_variables.get("bundle_path")
        if isinstance(bundle_path, str) and bundle_path.strip():
            payload["extra"].setdefault("bundle_path", bundle_path.strip())
        return self.request_from_payload(
            payload=payload,
            app_id=trigger.app_id,
            user_id=trigger.user_id,
            requested_workflow_id=trigger.workflow_id,
            default_source_surface=default_source_surface,
        )

    async def resolve(self, trigger: TriggerInput) -> TriggerRoutingContribution | None:
        request = self.request_from_trigger(trigger)
        if request is None:
            return None

        decision = await self.route(request)
        return TriggerRoutingContribution(
            workflow_id=decision.workflow_id,
            journey_id=decision.workflow_sequence,
            context_seed=decision.context_seed,
            explanation=decision.explanation,
            is_full_restart=decision.is_full_restart,
            lifecycle_state=SessionLifecycle.STALE if decision.is_full_restart else SessionLifecycle.ACTIVE,
        )

    def supported_artifact_kinds(self) -> list[str]:
        pack = self._load_pack()
        configured = [artifact.artifact_kind for artifact in pack.manifest.routing.artifacts]
        return sorted(configured or [ArtifactKind.APP_BUNDLE.value])

    def supported_change_classes(self) -> list[str]:
        return sorted(change_class.value for change_class in ChangeClass)


_resolver: RefinementTriggerRouteResolver | None = None


def get_refinement_trigger_route_resolver() -> RefinementTriggerRouteResolver:
    global _resolver
    if _resolver is None:
        _resolver = RefinementTriggerRouteResolver()
    return _resolver

