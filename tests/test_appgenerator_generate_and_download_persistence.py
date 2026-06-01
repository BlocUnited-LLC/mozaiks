from __future__ import annotations

import asyncio
import importlib.util
import importlib
from pathlib import Path


def _load_generate_and_download_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = (
        workspace
        / "factory_app"
        / "workflows"
        / "AppGenerator"
        / "tools"
        / "generate_and_download.py"
    )
    module_name = "tests.appgenerator_generate_and_download_persistence_direct"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate_and_download_module = _load_generate_and_download_module()


class _Context:
    def __init__(self, initial=None) -> None:
        self.data = dict(initial or {})

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


class _FakeStore:
    def __init__(self) -> None:
        self.calls = []

    async def save_database_migration(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "migration_id": kwargs["migration"]["migration_id"],
            "app_id": kwargs["app_id"],
            "build_id": kwargs["build_id"],
            "status": kwargs["status"],
        }


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.calls = []

    async def create_artifact_version(self, **kwargs):
        self.calls.append(dict(kwargs))
        return type("ArtifactVersion", (), {"id": "av_bundle_1"})()


def test_persist_pending_schema_migration_records_staged_history(monkeypatch, tmp_path: Path) -> None:
    fake_store = _FakeStore()
    monkeypatch.setattr(generate_and_download_module, "BuilderArtifactStore", lambda: fake_store)

    context = _Context({"artifact_version_id": "artifact_123", "revision_scope": "feature"})
    record = asyncio.run(
        generate_and_download_module._persist_pending_schema_migration(
            pending_migration={"migration_id": "m_1", "changes": {"new_collections": ["users"]}},
            app_id="app_123",
            build_id="build_123",
            workflow_name="AppGenerator",
            chat_id="chat_123",
            context_variables=context,
            generated_app_dir=str(tmp_path),
        )
    )

    assert record["migration_id"] == "m_1"
    assert fake_store.calls[0]["artifact_version_id"] == "artifact_123"
    assert fake_store.calls[0]["change_class"] == "feature"
    assert context.data["persisted_database_migration"]["status"] == "staged"
    assert context.data["staged_database_migration_path"] == "config/data_migrations/m_1.json"
    assert (tmp_path / "config" / "data_migrations" / "m_1.json").exists()


def test_register_app_bundle_artifact_version_sets_context_and_parent(monkeypatch, tmp_path: Path) -> None:
    fake_artifact_store = _FakeArtifactStore()
    artifacts_mod = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts_mod, "get_artifact_store", lambda: fake_artifact_store)
    monkeypatch.setattr(
        artifacts_mod,
        "resolve_latest_artifact_version_refs",
        lambda **kwargs: asyncio.sleep(0, result={
            "concept": "av_concept_1",
            "build_plan": "av_build_plan_1",
            "design_docs": "av_design_docs_1",
            "theme_capture": "av_theme_capture_1",
        }),
    )

    zip_path = tmp_path / "GeneratedApp.zip"
    zip_path.write_bytes(b"fake bundle bytes")
    context = _Context({"artifact_version_id": "av_parent_1", "app_validation_status": "passed"})

    artifact_version = asyncio.run(
        generate_and_download_module._register_app_bundle_artifact_version(
            app_id="app_123",
            user_id="user_123",
            workflow_name="AppGenerator",
            chat_id="chat_123",
            bundle_name="GeneratedApp",
            zip_path=zip_path,
            context_variables=context,
        )
    )

    assert artifact_version.id == "av_bundle_1"
    assert fake_artifact_store.calls[0]["artifact_kind"] == "app_bundle"
    assert fake_artifact_store.calls[0]["artifact_key"] == "app_bundle"
    assert fake_artifact_store.calls[0]["parent_version_id"] == "av_parent_1"
    assert fake_artifact_store.calls[0]["canonical_inputs_version"] == {
        "concept": "av_concept_1",
        "build_plan": "av_build_plan_1",
        "design_docs": "av_design_docs_1",
        "theme_capture": "av_theme_capture_1",
    }
    assert fake_artifact_store.calls[0]["lifecycle_status"].value == "draft"
    assert fake_artifact_store.calls[0]["validation_status"].value == "passed"
    assert context.data["artifact_version_id"] == "av_bundle_1"
