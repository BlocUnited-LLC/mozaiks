"""Public API contract smoke tests.

These tests verify that every symbol the ``mozaiks`` public API promises
is actually importable from its canonical path for downstream consumers.

If any import here breaks, it means a public contract regression.
"""

from __future__ import annotations

import importlib


# ── helpers ──────────────────────────────────────────────────────────────────

def _assert_importable(module_path: str, symbols: list[str]) -> None:
    """Import *module_path* and assert every *symbol* is present."""
    mod = importlib.import_module(module_path)
    for sym in symbols:
        assert hasattr(mod, sym), (
            f"{module_path} is missing expected symbol '{sym}'"
        )


# ── 1. mozaiks top-level ─────────────────────────────────────────────────────

class TestTopLevel:
    def test_version(self) -> None:
        from mozaiks import __version__
        assert isinstance(__version__, str)

    def test_event_envelope(self) -> None:
        from mozaiks import EventEnvelope
        assert EventEnvelope is not None

    def test_domain_event(self) -> None:
        from mozaiks import DomainEvent
        assert DomainEvent is not None

    def test_create_app(self) -> None:
        from mozaiks import create_app
        assert callable(create_app)

    def test_kernel_ai_workflow_runner(self) -> None:
        from mozaiks import KernelAIWorkflowRunner
        assert KernelAIWorkflowRunner is not None


# ── 2. mozaiks.contracts ─────────────────────────────────────────────────────

class TestContracts:
    def test_core_symbols(self) -> None:
        _assert_importable("mozaiks.contracts", [
            "EventEnvelope",
            "DomainEvent",
            "RunRequest",
            "ResumeRequest",
            "ArtifactRef",
            "ArtifactCreatedPayload",
            "ArtifactUpdatedPayload",
            "ArtifactStatePatchedPayload",
            "ArtifactStateReplacedPayload",
            "ToolExecutionRequest",
            "ToolExecutionResult",
            "SecretRef",
            "SandboxExecutionResult",
            "ReplayBoundaryPayload",
            "SnapshotEventPayload",
            "CANONICAL_EVENT_TAXONOMY",
        ])

    def test_ports(self) -> None:
        _assert_importable("mozaiks.contracts.ports", [
            "AIWorkflowRunnerPort",
            "OrchestrationPort",
            "SandboxPort",
            "SecretsPort",
            "ToolExecutionPort",
            "ArtifactPort",
            "ClockPort",
            "ControlPlanePort",
            "LedgerPort",
            "LoggerPort",
        ])


# ── 3. mozaiks.core.auth ─────────────────────────────────────────────────────

class TestCoreAuth:
    """Consumer expectation: all middleware + JWT symbols importable."""

    def test_jwt_primitives(self) -> None:
        _assert_importable("mozaiks.core.auth", [
            "JWTValidator",
            "JWTValidatorConfig",
            "JWTValidationError",
            "TokenExpiredError",
            "SigningKeyError",
            "OIDCResolutionError",
            "JWKSClient",
            "OIDCDiscoveryClient",
            "OIDCDiscoveryDocument",
        ])

    def test_middleware_principals(self) -> None:
        _assert_importable("mozaiks.core.auth", [
            "AuthConfig",
            "WebSocketUser",
            "UserPrincipal",
            "ServicePrincipal",
        ])

    def test_middleware_config(self) -> None:
        _assert_importable("mozaiks.core.auth", [
            "get_auth_config",
        ])

    def test_middleware_websocket_auth(self) -> None:
        _assert_importable("mozaiks.core.auth", [
            "authenticate_websocket",
            "authenticate_websocket_with_path_user",
            "authenticate_websocket_with_path_binding",
        ])

    def test_middleware_resource_ownership(self) -> None:
        _assert_importable("mozaiks.core.auth", [
            "verify_user_owns_resource",
            "require_resource_ownership",
            "require_user_scope",
        ])

    def test_middleware_guards(self) -> None:
        _assert_importable("mozaiks.core.auth", [
            "require_user",
            "require_any_auth",
            "require_internal",
            "require_role",
            "optional_user",
        ])

    def test_ws_close_constant(self) -> None:
        from mozaiks.core.auth import WS_CLOSE_POLICY_VIOLATION
        assert WS_CLOSE_POLICY_VIOLATION == 1008


# ── 4. mozaiks.core.artifacts ─────────────────────────────────────────────────

class TestCoreArtifacts:
    """Consumer expectation: artifact attachment helpers."""

    def test_symbols(self) -> None:
        _assert_importable("mozaiks.core.artifacts", [
            "inject_bundle_attachments_into_payload",
            "iter_bundle_attachment_files",
            "handle_chat_upload",
        ])


# ── 5. mozaiks.core.persistence ───────────────────────────────────────────────

class TestCorePersistence:
    """Consumer expectation: manager facades + store ports."""

    def test_store_ports(self) -> None:
        _assert_importable("mozaiks.core.persistence", [
            "EventStorePort",
            "CheckpointStorePort",
            "EventSinkPort",
            "PersistedEvent",
            "RunRecordView",
            "ArtifactRecordView",
        ])

    def test_store_implementations(self) -> None:
        _assert_importable("mozaiks.core.persistence", [
            "InMemoryEventStore",
            "SqlAlchemyEventStore",
            "InMemoryCheckpointStore",
            "SqlAlchemyCheckpointStore",
        ])

    def test_manager_facades(self) -> None:
        _assert_importable("mozaiks.core.persistence", [
            "PersistenceManager",
            "AG2PersistenceManager",
        ])


# ── 6. mozaiks.core.streaming ─────────────────────────────────────────────────

class TestCoreStreaming:
    """Consumer expectation: SimpleTransport + RunStreamHub."""

    def test_symbols(self) -> None:
        _assert_importable("mozaiks.core.streaming", [
            "RunStreamHub",
            "SimpleTransport",
        ])


# ── 7. mozaiks.core.tools ─────────────────────────────────────────────────────

class TestCoreTools:
    """Consumer expectation: ui tool helpers."""

    def test_catalog(self) -> None:
        _assert_importable("mozaiks.core.tools", [
            "ToolCatalog",
            "ToolSpec",
            "register_builtin_tools",
        ])

    def test_ui_tool_helpers(self) -> None:
        _assert_importable("mozaiks.core.tools.ui", [
            "use_ui_tool",
            "emit_tool_progress_event",
        ])


# ── 8. mozaiks.orchestration ──────────────────────────────────────────────────

class TestOrchestration:
    """Consumer expectation: KernelAIWorkflowRunner."""

    def test_runner(self) -> None:
        _assert_importable("mozaiks.orchestration", [
            "KernelAIWorkflowRunner",
            "create_ai_workflow_runner",
            "create_runner",
        ])

    def test_core_domain(self) -> None:
        _assert_importable("mozaiks.orchestration", [
            "RunRequest",
            "RunStarted",
            "RunCompleted",
            "RunFailed",
            "TaskDAG",
            "TaskNode",
            "ToolRegistry",
        ])


# ── 9. mozaiks.core (top-level) ──────────────────────────────────────────────

class TestCore:
    def test_create_app(self) -> None:
        _assert_importable("mozaiks.core", [
            "create_app",
        ])


# ── 10. Cross-module: downstream full import path smoke test ──────────────────

class TestConsumerImportPaths:
    """Verify representative downstream import paths work."""

    def test_auth_full_import(self) -> None:
        from mozaiks.core.auth import (
            AuthConfig,
            get_auth_config,
            authenticate_websocket,
            authenticate_websocket_with_path_user,
            authenticate_websocket_with_path_binding,
            verify_user_owns_resource,
            require_resource_ownership,
            require_user_scope,
            WebSocketUser,
            UserPrincipal,
            ServicePrincipal,
            require_user,
            require_any_auth,
            require_internal,
            require_role,
            optional_user,
            WS_CLOSE_POLICY_VIOLATION,
        )

    def test_artifacts_full_import(self) -> None:
        from mozaiks.core.artifacts import (
            inject_bundle_attachments_into_payload,
            iter_bundle_attachment_files,
            handle_chat_upload,
        )

    def test_persistence_full_import(self) -> None:
        from mozaiks.core.persistence import (
            PersistenceManager,
            AG2PersistenceManager,
        )

    def test_streaming_full_import(self) -> None:
        from mozaiks.core.streaming import SimpleTransport

    def test_ui_tools_full_import(self) -> None:
        from mozaiks.core.tools.ui import use_ui_tool
        from mozaiks.core.tools.ui import emit_tool_progress_event

    def test_workflow_runner_full_import(self) -> None:
        from mozaiks.orchestration import KernelAIWorkflowRunner
