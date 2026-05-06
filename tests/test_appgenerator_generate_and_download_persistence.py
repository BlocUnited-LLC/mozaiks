from __future__ import annotations

import asyncio
import importlib.util
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


def test_persist_pending_schema_migration_records_staged_history(monkeypatch) -> None:
    fake_store = _FakeStore()
    monkeypatch.setattr(generate_and_download_module, "BuilderArtifactStore", lambda: fake_store)

    context = _Context({"artifact_version_id": "artifact_123", "change_class": "feature"})
    record = asyncio.run(
        generate_and_download_module._persist_pending_schema_migration(
            pending_migration={"migration_id": "m_1", "changes": {"new_collections": ["users"]}},
            app_id="app_123",
            build_id="build_123",
            workflow_name="AppGenerator",
            chat_id="chat_123",
            context_variables=context,
            generated_app_dir="C:/tmp/generated/apps/app_123/build_123/app",
        )
    )

    assert record["migration_id"] == "m_1"
    assert fake_store.calls[0]["artifact_version_id"] == "artifact_123"
    assert fake_store.calls[0]["change_class"] == "feature"
    assert context.data["persisted_database_migration"]["status"] == "staged"
