import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.sandbox import private_documents
from tests.test_artifact_preview_sessions import IDENTITY, FakeSandboxAdapter, _create, _manager


async def test_document_uses_real_owner_quota_and_cannot_be_recovered_or_changed_from_studio(monkeypatch):
    from mozaiksai.core.sandbox.preview_store import PreviewCapacityError

    adapter = FakeSandboxAdapter()
    manager = _manager(adapter, provider="e2b")
    manager._max_owner_sessions = 2
    monkeypatch.setenv("MOZAIKS_PREVIEW_ENV_SENTINEL", "private-preview-test-value")
    studio = await _create(manager)
    doc = await _create(manager, "document-a", document_only=True)
    acquisition = [kwargs for name, kwargs in adapter.calls if name == "create_session"][-1]
    assert acquisition["metadata"]["preview_mode"] == "document"
    assert "SENTINEL" not in acquisition["envs"]
    owner = {key: IDENTITY[key] for key in ("app_id", "user_id")}
    assert [s.sandbox_id for s in await manager.list_for_owner(**owner)] == [studio.sandbox_id]
    with pytest.raises(KeyError):
        await manager.require_owner(doc.sandbox_id, **owner)
    with pytest.raises(ValueError, match="no app host"):
        await manager.start(doc.sandbox_id)
    with pytest.raises(ValueError, match="identity changed"):
        await _create(manager, "document-a")
    with pytest.raises(PreviewCapacityError):
        await _create(manager, "third", document_only=True)
    await manager.stop(doc.sandbox_id)
    assert (await manager.require_owner(studio.sandbox_id, **owner)).sandbox_id == studio.sandbox_id
    await manager.stop(studio.sandbox_id)


@pytest.mark.parametrize("success", [True, False])
async def test_document_compiler_limits_inputs_and_releases_its_own_allocation(monkeypatch, success):
    manager = SimpleNamespace(create_or_reuse=AsyncMock(return_value=SimpleNamespace(
        sandbox_id="owned", provider="e2b", session_id="provider-id",
    )), stop=AsyncMock())
    port = SimpleNamespace(write_files=AsyncMock(), run_command=AsyncMock(return_value=SimpleNamespace(success=success)),
                           read_file=AsyncMock(return_value="<!doctype html>ok"))
    monkeypatch.setattr(private_documents, "get_artifact_preview_sessions", lambda: manager)
    monkeypatch.setattr(private_documents, "resolve_preview_provider", lambda **_: ("e2b", port))
    page = "ui/pages/Calendar.jsx"
    bundle = {page: "import './calendar.css'", "ui/pages/calendar.css": "body {}", "modules/key.py": "private"}

    async def compare():
        async with private_documents.private_document_renderer(**IDENTITY) as render:
            assert await render(bundle, page) == "<!doctype html>ok"
            assert await render({**bundle, page: "export default () => 'Revised'"}, page) == "<!doctype html>ok"

    if success:
        await compare()
    else:
        with pytest.raises(private_documents.DocumentCompileError):
            await compare()
    files = [call.kwargs["files"] for call in port.write_files.await_args_list]
    paths = [next(iter(batch)) for batch in files]
    assert len(set(paths)) == len(paths)
    assert set(json.loads(files[1][paths[1]])["files"]) == {page, "ui/pages/calendar.css"}
    assert manager.create_or_reuse.await_args.kwargs == {**IDENTITY, "document_only": True}
    manager.stop.assert_awaited_once_with("owned")
