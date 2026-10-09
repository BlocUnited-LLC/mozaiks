"""Closed candidate preview boot over the existing preview manager and Docker port."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import pytest

from mozaiksai.core.adapters.docker_sandbox import DockerSandboxAdapter
from mozaiksai.core.sandbox.preview_sessions import ArtifactPreviewSessionManager
from mozaiksai.core.semantics.archive import (
    ArchiveEntry,
    archive_digest,
    build_deterministic_archive,
)
from tests.test_artifact_preview_sessions import FakeSandboxAdapter, _manager, _store

IMAGE_ID = "sha256:" + "a" * 64
E2B_BUILD_REF = "preview:f47ac10b-58cc-4372-a567-0e02b2c3d479"
IDENTITY = dict(
    artifact_id="candidate-a", app_id="factory", user_id="owner",
    target_app_id="preview-app", build_registry_id="build-a",
)
APP_JSON = b'{"appId":"preview-app","appName":"Preview","authRequired":false}'


def _archive(**files: bytes) -> bytes:
    return build_deterministic_archive(
        [ArchiveEntry(path=path, content=content) for path, content in files.items()]
    )


class _SealedAdapter(FakeSandboxAdapter):
    async def stage_sealed_files(self, **kwargs):
        self.calls.append(("stage_sealed_files", kwargs))


@pytest.mark.asyncio
async def test_sealed_cleanup_after_restart_uses_stored_docker_provider(monkeypatch):
    import mozaiksai.core.adapters.docker_sandbox as docker_sandbox

    adapter = _SealedAdapter()
    monkeypatch.setattr(docker_sandbox, "docker_available", lambda: True)
    monkeypatch.setattr(docker_sandbox, "get_docker_sandbox", lambda: adapter)
    monkeypatch.setenv("MOZAIKS_PREVIEW_PROVIDER", "docker")
    store = _store()
    first = ArtifactPreviewSessionManager(store=store, startup_timeout_seconds=0)
    data = _archive(**{"app/app.json": APP_JSON})
    state = await first.create_sealed_candidate(
        **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=IMAGE_ID,
    )

    # The new selection is unconfigured; the old Docker session still needs
    # a confirmed stop through its persisted provider identity.
    monkeypatch.setenv("MOZAIKS_PREVIEW_PROVIDER", "e2b")
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    restarted = ArtifactPreviewSessionManager(store=store)
    await restarted.cleanup(expired_only=False)

    assert await store.get(state.sandbox_id) is None
    assert [name for name, _ in adapter.calls].count("terminate_session") == 1


@pytest.mark.asyncio
async def test_legacy_sealed_image_record_fails_new_admission_but_still_cleans_up(monkeypatch):
    import mozaiksai.core.adapters.docker_sandbox as docker_sandbox

    adapter = _SealedAdapter()
    monkeypatch.setattr(docker_sandbox, "docker_available", lambda: True)
    monkeypatch.setattr(docker_sandbox, "get_docker_sandbox", lambda: adapter)
    monkeypatch.setenv("MOZAIKS_PREVIEW_PROVIDER", "docker")
    store = _store()
    first = ArtifactPreviewSessionManager(store=store, startup_timeout_seconds=0)
    data = _archive(**{"app/app.json": APP_JSON})
    state = await first.create_sealed_candidate(
        **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=IMAGE_ID,
    )
    ledger = next(iter(store._ledger_collection().documents.values()))
    entry = next(item for item in ledger["entries"] if item["sandbox_id"] == state.sandbox_id)
    entry["sealed_image_id"] = entry.pop("sealed_runtime_ref")

    with pytest.raises(ValueError, match="identity"):
        await first.create_sealed_candidate(
            **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=IMAGE_ID,
        )
    monkeypatch.setenv("MOZAIKS_PREVIEW_PROVIDER", "e2b")
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    await ArtifactPreviewSessionManager(store=store).cleanup(expired_only=False)
    assert await store.get(state.sandbox_id) is None
    assert [name for name, _ in adapter.calls].count("terminate_session") == 1


@pytest.mark.asyncio
async def test_sealed_e2b_boot_uses_exact_runtime_ref_without_url_or_mutable_write():
    adapter = _SealedAdapter(provider="e2b")
    manager = _manager(adapter, provider="e2b")
    data = _archive(**{"app/app.json": APP_JSON})
    state = await manager.create_sealed_candidate(
        **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=E2B_BUILD_REF,
    )
    assert state.provider == "e2b" and state.sealed_runtime_ref == E2B_BUILD_REF
    assert state.status == "running" and state.preview_url is None
    creates = [args for name, args in adapter.calls if name == "create_session"]
    assert len(creates) == 1 and creates[0]["template"] == E2B_BUILD_REF
    assert creates[0]["envs"] == {}
    assert not any(name in {"get_preview_url", "write_files"} for name, _ in adapter.calls)
    await manager.stop(state.sandbox_id)
    assert await manager._store.get(state.sandbox_id) is None


@pytest.mark.asyncio
async def test_sealed_e2b_cleanup_after_restart_uses_stored_provider(monkeypatch):
    import mozaiksai.core.adapters.e2b_sandbox as e2b_sandbox

    adapter = _SealedAdapter(provider="e2b")
    monkeypatch.setattr(e2b_sandbox, "get_e2b_sandbox", lambda: adapter)
    monkeypatch.setenv("MOZAIKS_PREVIEW_PROVIDER", "e2b")
    monkeypatch.setenv("E2B_API_KEY", "test-key")
    store = _store()
    first = ArtifactPreviewSessionManager(store=store, startup_timeout_seconds=0)
    data = _archive(**{"app/app.json": APP_JSON})
    state = await first.create_sealed_candidate(
        **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=E2B_BUILD_REF,
    )
    monkeypatch.setenv("MOZAIKS_PREVIEW_PROVIDER", "docker")
    restarted = ArtifactPreviewSessionManager(store=store)
    await restarted.cleanup(expired_only=False)
    assert await store.get(state.sandbox_id) is None
    assert [name for name, _ in adapter.calls].count("terminate_session") == 1


@pytest.mark.asyncio
async def test_sealed_e2b_rejects_wrong_runtime_ref_or_missing_stager_before_allocation():
    adapter = _SealedAdapter(provider="e2b")
    manager = _manager(adapter, provider="e2b")
    data = _archive(**{"app/app.json": APP_JSON})
    with pytest.raises(ValueError, match="does not match"):
        await manager.create_sealed_candidate(
            **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=IMAGE_ID,
        )
    assert adapter.calls == []
    ordinary_adapter = FakeSandboxAdapter(provider="e2b")
    manager = _manager(ordinary_adapter, provider="e2b")
    with pytest.raises(RuntimeError, match="immutable staging"):
        await manager.create_sealed_candidate(
            **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=E2B_BUILD_REF,
        )
    assert ordinary_adapter.calls == []


@pytest.mark.asyncio
async def test_sealed_e2b_rejects_provider_mismatch_and_cleans_up():
    adapter = _SealedAdapter(provider="docker")
    manager = _manager(adapter, provider="e2b")
    data = _archive(**{"app/app.json": APP_JSON})
    with pytest.raises(RuntimeError, match="did not match"):
        await manager.create_sealed_candidate(
            **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=E2B_BUILD_REF,
        )
    assert [name for name, _ in adapter.calls].count("stage_sealed_files") == 0
    assert [name for name, _ in adapter.calls].count("terminate_session") == 1
    assert await manager._store.list() == []


@pytest.mark.asyncio
async def test_sealed_boot_binds_digest_runtime_ref_and_owner_without_url_or_sync(monkeypatch):
    monkeypatch.setenv("MOZAIKS_PREVIEW_ENV_OPENAI_API_KEY", "must-not-forward")
    adapter = _SealedAdapter()
    manager = _manager(adapter)
    data = _archive(**{
        "app/app.json": APP_JSON,
        "app/host.py": b"from mozaiksai.hosts.studio import app\n",
        "app/config/onboarding.yaml": b"enabled: true\n",
        "workflows/Example/orchestrator.yaml": b"name: Example\n",
        "requirements.txt": b"mozaiksai==0.2.0\n",
    })
    state = await manager.create_sealed_candidate(
        **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=IMAGE_ID,
    )
    assert state.status == "running"
    assert state.preview_url is None
    assert state.sealed_archive_sha256 == archive_digest(data)
    assert state.sealed_runtime_ref == IMAGE_ID
    assert state.manifest is None and state.paths == []
    stored = await manager._store.get(state.sandbox_id)
    assert stored["manifest"] is None and stored["paths"] == []
    assert APP_JSON.decode() not in str(stored)
    assert (await manager.require_owner(state.sandbox_id, app_id="factory", user_id="owner")).sandbox_id == state.sandbox_id
    with pytest.raises(KeyError):
        await manager.require_owner(state.sandbox_id, app_id="factory", user_id="other")
    creates = [kwargs for name, kwargs in adapter.calls if name == "create_session"]
    assert len(creates) == 1
    assert creates[0]["template"] == IMAGE_ID
    assert creates[0]["envs"] == {}
    assert creates[0]["metadata"]["purpose"] == "sealed_candidate_preview"
    assert [name for name, _ in adapter.calls].count("stage_sealed_files") == 1
    staged = next(kwargs["files"] for name, kwargs in adapter.calls if name == "stage_sealed_files")
    assert staged["requirements.txt"] == b"mozaiksai==0.2.0\n"
    assert "get_preview_url" not in [name for name, _ in adapter.calls]
    with pytest.raises(ValueError, match="cannot be synchronized"):
        await manager.sync(state.sandbox_id, [{"path": "app.json", "content": "{}"}], [])
    with pytest.raises(ValueError, match="cannot be restarted"):
        await manager.start(state.sandbox_id)
    with pytest.raises(ValueError, match="identity changed"):
        await manager.create_sealed_candidate(
            **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref="sha256:" + "b" * 64,
        )
    await manager.stop(state.sandbox_id)
    assert [name for name, _ in adapter.calls].count("terminate_session") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("stage_timeout", "ttl_minutes"), [(0.001, 30), (180, 0.0001)])
async def test_sealed_stage_timeout_kills_session_and_releases_reservation(stage_timeout, ttl_minutes):
    class SlowStager(_SealedAdapter):
        async def stage_sealed_files(self, **kwargs):
            self.calls.append(("stage_sealed_files", kwargs))
            await asyncio.sleep(1)

    adapter = SlowStager()
    manager = _manager(adapter)
    manager._sealed_stage_timeout_seconds = stage_timeout
    manager._ttl_minutes = ttl_minutes
    data = _archive(**{"app/app.json": APP_JSON})
    with pytest.raises(TimeoutError):
        await manager.create_sealed_candidate(
            **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=IMAGE_ID,
        )
    assert [name for name, _ in adapter.calls].count("terminate_session") == 1
    assert await manager._store.list() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [
    {"app/app.json": b'{"appId":"wrong"}'},
    {"app/app.json": APP_JSON, "other.py": b"pass"},
    {"app/app.json": APP_JSON, "other/unknown.py": b"pass"},
    {"app/app.json": b'{"appId":"wrong","appId":"preview-app"}'},
])
async def test_sealed_archive_rejects_wrong_app_and_unknown_paths_before_provider(files):
    data = _archive(**files)
    manager = _manager(_SealedAdapter())
    manager._provider_resolver = lambda: pytest.fail("Invalid archive contacted provider")
    with pytest.raises(ValueError):
        await manager.create_sealed_candidate(
            **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=IMAGE_ID,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["malformed", "oversize", "digest"])
async def test_sealed_archive_rejects_malformed_oversize_or_wrong_digest_before_provider(monkeypatch, case):
    data = b"not a zip" if case == "malformed" else _archive(**{"app/app.json": APP_JSON})
    digest = archive_digest(data) if case != "digest" else "sha256:" + "0" * 64
    if case == "oversize":
        monkeypatch.setattr("mozaiksai.core.sandbox.preview_sessions._SEALED_ARCHIVE_MAX_BYTES", len(data) - 1)
    manager = _manager(_SealedAdapter())
    manager._provider_resolver = lambda: pytest.fail("Invalid archive contacted provider")
    with pytest.raises(ValueError):
        await manager.create_sealed_candidate(
            **IDENTITY, archive_bytes=data, archive_sha256=digest, sealed_runtime_ref=IMAGE_ID,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", ["count", "entry", "total"])
async def test_sealed_zip_budget_rejects_before_canonical_parser_or_provider(monkeypatch, budget):
    data = _archive(**{"app/app.json": APP_JSON, "requirements.txt": b"mozaiksai\n"})
    limits = {
        "count": ("_SEALED_ARCHIVE_MAX_FILES", 1),
        "entry": ("_SEALED_FILE_MAX_BYTES", len(APP_JSON) - 1),
        "total": ("_SEALED_TOTAL_FILE_MAX_BYTES", len(APP_JSON)),
    }
    name, value = limits[budget]
    monkeypatch.setattr("mozaiksai.core.sandbox.preview_sessions." + name, value)
    monkeypatch.setattr(
        "mozaiksai.core.sandbox.preview_sessions.read_archive_manifest",
        lambda _data: pytest.fail("Canonical parser ran before budget check"),
    )
    manager = _manager(_SealedAdapter())
    manager._provider_resolver = lambda: pytest.fail("Oversize archive contacted provider")
    with pytest.raises(ValueError, match="file limits"):
        await manager.create_sealed_candidate(
            **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=IMAGE_ID,
        )


@pytest.mark.asyncio
async def test_docker_sealed_session_is_offline_pinned_read_only_and_without_logs_or_ports():
    adapter = DockerSandboxAdapter()
    commands = []

    async def fake_run(args, *, timeout=30.0, input_data=None):
        commands.append((args, input_data))
        if args[1:3] == ["image", "inspect"]:
            return 0, "", ""
        if args[1] == "inspect":
            return 0, "true\n", ""
        if args[1] == "run":
            return 0, "sealed-container\n", ""
        return 0, "", ""

    with patch.object(adapter, "_run", side_effect=fake_run):
        session = await adapter.create_session(
            template=IMAGE_ID, metadata={"purpose": "sealed_candidate_preview"}, envs={},
        )
        await adapter.stage_sealed_files(
            session_id=session.session_id, files={"app/app.json": APP_JSON},
        )
    args = commands[1][0]
    assert args[args.index("--network") + 1] == "none"
    assert "--user=10001:10001" in args
    assert "--log-driver=none" in args
    assert "--pull=never" in args and "--read-only" in args
    assert "mozaiks.preview.sealed=true" in args
    assert "--tmpfs" in args and "-p" not in args and "-e" not in args
    assert "--mount" not in args and "-v" not in args
    assert args[-4] == IMAGE_ID
    assert all("-u" in command for command, _ in commands[3:])
    assert any(input_data and APP_JSON in input_data for _, input_data in commands)


@pytest.mark.asyncio
async def test_docker_teardown_confirms_absence_after_lag_or_force_removal():
    adapter = DockerSandboxAdapter()
    calls = []
    probe = iter(["still-here", "still-here", "still-here", ""])

    async def fake_run(args, *, timeout=30.0, input_data=None):
        calls.append(args)
        if args[1] == "ps":
            return 0, next(probe), ""
        return 0, "", ""

    with patch.object(adapter, "_run", side_effect=fake_run):
        assert await adapter.terminate_session(session_id="sealed-container")
    assert [args[1] for args in calls] == ["stop", "ps", "ps", "ps", "rm", "ps"]


@pytest.mark.asyncio
async def test_sealed_stage_rejects_unlabelled_container_before_root_exec():
    adapter = DockerSandboxAdapter()
    calls = []

    async def fake_run(args, *, timeout=30.0, input_data=None):
        calls.append(args)
        return 0, "<no value>\n", ""

    with patch.object(adapter, "_run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="not a sealed"):
            await adapter.stage_sealed_files(
                session_id="ordinary-container", files={"app/app.json": APP_JSON},
            )
    assert len(calls) == 1 and calls[0][1] == "inspect"


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("MOZAIKS_RUN_SEALED_DOCKER_SMOKE") != "1", reason="opt-in real Docker smoke")
async def test_real_docker_sealed_public_app_boots_and_tears_down():
    runtime_ref = os.environ["MOZAIKS_SEALED_PREVIEW_IMAGE_ID"]
    adapter = DockerSandboxAdapter()
    manager = _manager(adapter)
    manager._startup_timeout_seconds = 120
    data = _archive(**{"app/app.json": APP_JSON})
    state = await manager.create_sealed_candidate(
        **IDENTITY, archive_bytes=data, archive_sha256=archive_digest(data), sealed_runtime_ref=runtime_ref,
    )
    try:
        assert state.status == "running" and state.preview_url is None
        assert (await manager.status(state.sandbox_id)).status == "running"
        assert await adapter.get_preview_url(session_id=state.session_id, port=3000) is None
        immutable = await adapter.run_command(
            session_id=state.session_id,
            command="printf tamper >> /workspace/app/app.json",
        )
        assert not immutable.success
    finally:
        await manager.stop(state.sandbox_id)
