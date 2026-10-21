"""Opt-in authenticated UI receipt to generated app storage acceptance."""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from mozaiksai.core.data import persistence
from mozaiksai.core.runtime.persistence import (
    MongoPersistenceContext,
    PersistencePrincipal,
    apply_data_migrations,
    load_data_migrations,
)
from mozaiksai.core.workflow.outcome_feedback import resolve_workflow_feedback
from tests import test_ui_response_ownership as response_ownership
from tests.test_workflow_outcome_feedback import _compile_feedback_app, _render_http

harness = response_ownership.harness

pytestmark = pytest.mark.skipif(
    os.getenv("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1",
    reason="set MOZAIKS_RUN_REAL_MONGO_TESTS=1 for authenticated feedback storage acceptance",
)


@pytest.fixture
async def generated_feedback(monkeypatch, tmp_path):
    app_root = tmp_path / "app"
    files, contract = await _compile_feedback_app()
    for path, content in files.items():
        target = app_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    package = ModuleType("generated_outcome_feedback")
    package.__path__ = [str(app_root / "modules/outcome_feedback/backend")]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    try:
        yield SimpleNamespace(
            app_root=app_root,
            contract=contract,
            service=importlib.import_module(f"{package.__name__}.service").OutcomeFeedbackService(),
            account=importlib.import_module(f"{package.__name__}.account_data_handler").AccountDataHandler,
        )
    finally:
        for name in list(sys.modules):
            if name.startswith(package.__name__ + "."):
                sys.modules.pop(name, None)


async def test_authenticated_receipt_survives_manager_restart_and_generated_storage_is_private(
    harness, generated_feedback, monkeypatch,
):
    database_name = f"mozaiks_feedback_test_{uuid4().hex}"
    client = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=3000)
    try:
        sessions = client[database_name]["ChatSessions"]
        await sessions.insert_one(dict(harness.session))
        manager_type = persistence.AG2PersistenceManager
        manager = manager_type()
        monkeypatch.setattr(manager, "_coll", AsyncMock(return_value=sessions))
        harness.transport._get_or_create_persistence_manager = Mock(return_value=manager)
        response_ownership._pending(harness)
        harness.transport._ui_tool_metadata["evt-owned"].update(
            workflow_primitive="outcome_feedback", workflow_name="AnswerFlow",
            feedback_agent_name="AnswerAgent", outcome_id="result-1",
        )
        harness.app.post("/api/workflow-feedback/rendered")(harness.runtime.acknowledge_workflow_feedback_render)
        acknowledgements = await asyncio.gather(*(_render_http(harness) for _ in range(8)))
        assert all(result.status_code == 200 for result in acknowledgements)
        render_receipt = await manager.get_workflow_feedback_render_receipt(
            app_id="app-owner", chat_id="chat-owner", user_id="owner", ui_event_id="evt-owned",
        )
        assert render_receipt["evidence_kind"] == "client_visible_ack"
        assert render_receipt["outcome_id"] == "result-1"
        assert await manager.save_workflow_feedback_render_receipt({
            **render_receipt, "observed_at": "2099-01-01T00:00:00+00:00",
        })
        assert not await manager.save_workflow_feedback_render_receipt({
            **render_receipt, "outcome_id": "another-result",
        })
        assert await manager.get_workflow_feedback_render_receipt(
            app_id="app-owner", chat_id="chat-owner", user_id="owner", ui_event_id="evt-owned",
        ) == render_receipt
        assert await manager.get_workflow_feedback_receipt(
            app_id="app-owner", chat_id="chat-owner", user_id="owner", ui_event_id="evt-owned",
        ) is None
        assert (await response_ownership._http(
            harness, response_data={"status": "submitted", "rating": 3, "helpful": False},
        )).status_code == 200
        assert harness.transport.pending_tool_call_responses["evt-owned"].result()["rating"] == 3

        # A fresh manager resolves the receipt without in-memory transport state.
        restored = manager_type()
        monkeypatch.setattr(restored, "_coll", AsyncMock(return_value=sessions))
        monkeypatch.setattr(persistence, "AG2PersistenceManager", lambda: restored)
        assert await restored.get_workflow_feedback_render_receipt(
            app_id="app-owner", chat_id="chat-owner", user_id="owner", ui_event_id="evt-owned",
        ) == render_receipt
        assert await restored.get_workflow_feedback_render_receipt(
            app_id="app-owner", chat_id="chat-owner", user_id="other", ui_event_id="evt-owned",
        ) is None
        scope = dict(app_id="app-owner", chat_id="chat-owner", user_id="owner", ui_event_id="evt-owned", outcome_id="result-1")
        receipt = await resolve_workflow_feedback(**scope)
        assert receipt.response.helpful is False and receipt.response.outcome is None
        assert not await restored.save_workflow_feedback_receipt({
            **receipt.model_dump(mode="json"), "response": {"status": "submitted", "rating": 5},
        })
        assert await resolve_workflow_feedback(**{**scope, "user_id": "other"}) is None
        assert await resolve_workflow_feedback(**{**scope, "app_id": "other"}) is None
        assert await resolve_workflow_feedback(**{**scope, "outcome_id": "other"}) is None

        # Startup materializes the exact shipped migration before user-scoped access.
        startup = MongoPersistenceContext(app_id="app-owner", client=client, database_name=database_name)
        migrations = load_data_migrations(generated_feedback.app_root)
        assert await apply_data_migrations(
            app_id="app-owner", migrations=migrations, persistence=startup,
            history_client=client, history_database=database_name,
        ) == 1

        def context(user="owner", app="app-owner", surface="workflow_tool"):
            return SimpleNamespace(
                app_id=app, user_id=user,
                dispatch_authority=SimpleNamespace(kind="workflow" if surface == "workflow_tool" else "http"),
                dispatch_provenance=SimpleNamespace(surface=surface, workflow_run_id="chat-owner", workflow_name="AnswerFlow"),
                persistence=MongoPersistenceContext(
                    app_id=app, user_id=user, client=client, database_name=database_name,
                    data_contract=generated_feedback.contract, principal=PersistencePrincipal(user),
                ),
            )

        service = generated_feedback.service
        args = dict(ui_event_id="evt-owned", outcome_id="result-1")
        results = await asyncio.gather(*(service.record_workflow_feedback(context(), **args) for _ in range(8)))
        assert sum(not result["duplicate"] for result in results) == 1
        records = (await service.list_my_feedback(context()))["records"]
        assert len(records) == 1 and records[0]["response"]["rating"] == 3
        assert records[0]["ui_event_id"] == "evt-owned"
        assert (await service.list_my_feedback(context(user="other")))["records"] == []
        assert (await service.list_my_feedback(context(app="other")))["records"] == []
        with pytest.raises(PermissionError, match="authenticated workflow"):
            await service.record_workflow_feedback(context(surface="http"), **args)
        with pytest.raises(PermissionError, match="receipt not found"):
            await service.record_workflow_feedback(context(user="other"), **args)
        with pytest.raises(PermissionError, match="receipt not found"):
            await service.record_workflow_feedback(context(), ui_event_id="invented", outcome_id="result-1")
        account = generated_feedback.account(context().persistence)
        exported = await account.export_user_data(app_id="app-owner", user_id="owner")
        assert exported["outcome_feedback_records"][0]["response"]["rating"] == 3
        assert (await account.delete_user_data(app_id="app-owner", user_id="owner"))["deleted_count"] == 1
        assert (await service.list_my_feedback(context()))["records"] == []
    finally:
        await client.drop_database(database_name)
        client.close()
