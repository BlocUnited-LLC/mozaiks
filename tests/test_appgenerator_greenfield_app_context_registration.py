from __future__ import annotations

import asyncio
import importlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from mozaiksai.core.app_context.models import AppContextMode, OwnershipClass
from mozaiksai.core.app_context.store import (
    APP_CONTEXT_VERSION_ARTIFACT_KIND,
    GREENFIELD_APP_CONTEXT_ARTIFACT_KINDS,
    get_current_app_context_version,
)
from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)

ROOT = Path(__file__).resolve().parents[1]


def _load_generate_and_download_module():
    file_path = (
        ROOT
        / "factory_app"
        / "workflows"
        / "AppGenerator"
        / "tools"
        / "generate_and_download.py"
    )
    module_name = "tests.appgenerator_greenfield_context_direct"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate_and_download_module = _load_generate_and_download_module()


class _Context:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.data = dict(initial or {})

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class _MemoryArtifactStore:
    def __init__(self) -> None:
        self.versions: dict[str, ArtifactVersionDoc] = {}
        self.create_calls: list[dict[str, Any]] = []
        self._counter = 0

    async def create_artifact_version(self, **kwargs: Any) -> ArtifactVersionDoc:
        self._counter += 1
        artifact_kind = kwargs["artifact_kind"]
        version_id = (
            "av_app_bundle_1"
            if artifact_kind == "app_bundle"
            else f"av_{artifact_kind}_{self._counter}"
        )
        self.create_calls.append(dict(kwargs))
        doc = ArtifactVersionDoc(
            _id=version_id,
            app_id=kwargs["app_id"],
            artifact_kind=artifact_kind,
            artifact_key=kwargs["artifact_key"],
            version_number=self._counter,
            parent_version_id=kwargs.get("parent_version_id"),
            lineage_root_id=version_id,
            source_workflow=kwargs.get("source_workflow"),
            source_chat_id=kwargs.get("source_chat_id"),
            canonical_inputs_version=kwargs.get("canonical_inputs_version") or {},
            lifecycle_status=kwargs.get("lifecycle_status", ArtifactLifecycleStatus.DRAFT),
            validation_status=kwargs.get("validation_status", ArtifactValidationStatus.PENDING),
            files_manifest=kwargs.get("files_manifest") or [],
            commit_metadata=kwargs.get("commit_metadata") or {},
        )
        if doc.lifecycle_status is ArtifactLifecycleStatus.CURRENT:
            self._supersede_current_siblings(doc)
        self.versions[doc.id] = doc
        return doc

    async def get_artifact_version(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
    ) -> ArtifactVersionDoc | None:
        doc = self.versions.get(artifact_version_id)
        if doc is None or doc.app_id != app_id:
            return None
        return doc

    async def accept_artifact_version(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
        commit_metadata: dict[str, Any] | None = None,
    ) -> ArtifactVersionDoc | None:
        doc = await self.get_artifact_version(
            app_id=app_id,
            artifact_version_id=artifact_version_id,
        )
        if doc is None:
            return None
        self._supersede_current_siblings(doc)
        doc.lifecycle_status = ArtifactLifecycleStatus.CURRENT
        if commit_metadata is not None:
            doc.commit_metadata = commit_metadata
        return doc

    async def list_artifact_versions(
        self,
        *,
        app_id: str,
        artifact_kind: str | None = None,
        artifact_key: str | None = None,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> list[ArtifactVersionDoc]:
        rows = [doc for doc in self.versions.values() if doc.app_id == app_id]
        if artifact_kind is not None:
            rows = [doc for doc in rows if doc.artifact_kind == artifact_kind]
        if artifact_key is not None:
            rows = [doc for doc in rows if doc.artifact_key == artifact_key]
        if lifecycle_status is not None:
            rows = [doc for doc in rows if doc.lifecycle_status is lifecycle_status]
        return sorted(rows, key=lambda doc: doc.version_number, reverse=True)[:limit]

    def _supersede_current_siblings(self, target: ArtifactVersionDoc) -> None:
        for doc in self.versions.values():
            if (
                doc.app_id == target.app_id
                and doc.artifact_kind == target.artifact_kind
                and doc.artifact_key == target.artifact_key
                and doc.id != target.id
                and doc.lifecycle_status is ArtifactLifecycleStatus.CURRENT
            ):
                doc.lifecycle_status = ArtifactLifecycleStatus.SUPERSEDED


def _patch_artifact_store(monkeypatch, store: _MemoryArtifactStore) -> None:
    artifacts_mod = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts_mod, "get_artifact_store", lambda: store)
    monkeypatch.setattr(
        artifacts_mod,
        "resolve_latest_artifact_version_refs",
        lambda **_kwargs: asyncio.sleep(
            0,
            result={
                "concept": "av_concept_1",
                "build_plan": "av_build_plan_1",
                "design_docs": "av_design_docs_1",
                "theme_capture": "av_theme_capture_1",
            },
        ),
    )


def _write_generated_app(app_dir: Path) -> list[str]:
    files = {
        "app/app.json": '{"app_id": "field_service"}\n',
        "app/ui/route_manifest.json": '{"routes": []}\n',
        "app/ui/pages/home.yaml": "title: Home\n",
        "app/modules/work_orders/module.yaml": "id: work_orders\n",
        "app/services/integrations/email_gateway_client.py": "class EmailGatewayClient: pass\n",
        "app/config/integrations/email_gateway.json": '{"provider": "email_gateway"}\n',
    }
    for rel_path, content in files.items():
        path = app_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return list(files)


async def test_appgenerator_app_bundle_save_registers_greenfield_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = _MemoryArtifactStore()
    _patch_artifact_store(monkeypatch, store)

    app_dir = tmp_path / "GeneratedApp"
    written_paths = _write_generated_app(app_dir)
    original_contents = {
        rel_path: (app_dir / rel_path).read_text(encoding="utf-8")
        for rel_path in written_paths
    }
    zip_path = tmp_path / "GeneratedApp.zip"
    zip_path.write_bytes(b"fake bundle bytes")
    context = _Context({"app_bundle_acceptance_status": "passed"})

    app_bundle = await generate_and_download_module._register_app_bundle_artifact_version(
        app_id="field_service",
        user_id="user_123",
        workflow_name="AppGenerator",
        chat_id="chat_greenfield",
        bundle_name="GeneratedApp",
        zip_path=zip_path,
        app_dir=app_dir,
        written_paths=written_paths,
        context_variables=context,
    )
    current_context = await get_current_app_context_version(
        app_id="field_service",
        artifact_store=store,
    )

    persisted_kinds = {call["artifact_kind"] for call in store.create_calls}
    assert persisted_kinds == {
        "app_bundle",
        *GREENFIELD_APP_CONTEXT_ARTIFACT_KINDS,
        APP_CONTEXT_VERSION_ARTIFACT_KIND,
    }
    assert app_bundle.id == "av_app_bundle_1"
    assert app_bundle.lifecycle_status is ArtifactLifecycleStatus.CURRENT
    assert current_context is not None
    assert current_context.mode is AppContextMode.GREENFIELD
    assert any(
        ref.artifact_kind == "app_bundle"
        and ref.artifact_version_id == "av_app_bundle_1"
        for ref in current_context.artifact_refs
    )
    assert context.data["artifact_version_id"] == "av_app_bundle_1"
    assert context.data["greenfield_app_context_registered"] is True
    assert context.data["app_context_version_id"] == current_context.context_version_id

    inventory_payload = next(
        doc.commit_metadata.metadata["summary_payload"]
        for doc in store.versions.values()
        if doc.artifact_kind == "application_inventory"
    )
    assert any(page["location"] == "ui/pages/home.yaml" for page in inventory_payload["pages"])
    assert any(
        module["location"] == "modules/work_orders/module.yaml"
        for module in inventory_payload["modules"]
    )
    assert inventory_payload["integrations"][0]["integration_id"] == "email_gateway"
    assert inventory_payload["integrations"][0]["secret_required"] is False
    assert inventory_payload["integrations"][0]["frontend_safe_config"] == {}

    ownership_payload = next(
        doc.commit_metadata.metadata["summary_payload"]
        for doc in store.versions.values()
        if doc.artifact_kind == "ownership_boundary"
    )
    file_ownership = {
        boundary["ownership"]
        for boundary in ownership_payload["ownership_boundaries"]
        if not boundary["path_or_artifact"].startswith("integration:")
    }
    assert file_ownership == {OwnershipClass.GENERATED_OVERLAY.value}

    app_bundle_manifest_paths = {
        entry.path for entry in store.versions["av_app_bundle_1"].files_manifest
    }
    assert "GeneratedApp/GeneratedApp.zip" in app_bundle_manifest_paths
    assert "GeneratedApp/app/ui/pages/home.yaml" in app_bundle_manifest_paths
    assert store.versions["av_app_bundle_1"].commit_metadata.metadata["artifact_path"] == str(
        zip_path.resolve()
    )
    for rel_path, content in original_contents.items():
        assert (app_dir / rel_path).read_text(encoding="utf-8") == content


async def test_appgenerator_greenfield_context_registration_failure_is_nonfatal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = _MemoryArtifactStore()
    _patch_artifact_store(monkeypatch, store)

    async def _fail_registration(**_kwargs: Any) -> None:
        raise RuntimeError("context store unavailable")

    monkeypatch.setattr(
        generate_and_download_module,
        "register_greenfield_app_context_version",
        _fail_registration,
    )

    app_dir = tmp_path / "GeneratedApp"
    written_paths = _write_generated_app(app_dir)
    zip_path = tmp_path / "GeneratedApp.zip"
    zip_path.write_bytes(b"fake bundle bytes")
    context = _Context({"app_bundle_acceptance_status": "passed"})

    app_bundle = await generate_and_download_module._register_app_bundle_artifact_version(
        app_id="field_service",
        user_id="user_123",
        workflow_name="AppGenerator",
        chat_id="chat_greenfield",
        bundle_name="GeneratedApp",
        zip_path=zip_path,
        app_dir=app_dir,
        written_paths=written_paths,
        context_variables=context,
    )

    assert app_bundle.id == "av_app_bundle_1"
    assert context.data["artifact_version_id"] == "av_app_bundle_1"
    assert "context store unavailable" in context.data["app_context_registration_warning"]
    assert {call["artifact_kind"] for call in store.create_calls} == {"app_bundle"}


async def test_appgenerator_greenfield_context_contract_errors_can_be_strict() -> None:
    store = _MemoryArtifactStore()

    with pytest.raises(ValueError, match="app_id is required"):
        await generate_and_download_module._register_greenfield_app_context_for_bundle(
            app_bundle_artifact=object(),
            artifact_store=store,
            files_manifest=[],
            workflow_name="AppGenerator",
            chat_id="chat_greenfield",
            context_variables=_Context(),
            raise_on_contract_error=True,
        )


def test_appgenerator_context_registration_has_no_graph_database_or_proprietary_dependency() -> None:
    paths = [
        ROOT / "factory_app/workflows/AppGenerator/tools/generate_and_download.py",
        ROOT / "tests/test_appgenerator_greenfield_app_context_registration.py",
    ]
    forbidden_terms = (
        "Fal" + "kor",
        "app " + "zero",
        "app_" + "zero",
        "mozaiks" + "-app",
        "mozaiks" + "pay",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            assert term.lower() not in text

