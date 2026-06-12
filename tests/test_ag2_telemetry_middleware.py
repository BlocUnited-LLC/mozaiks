from __future__ import annotations

import importlib

telemetry_mod = importlib.import_module("mozaiksai.core.observability.performance_manager")
AG2TelemetryConfig = telemetry_mod.AG2TelemetryConfig
build_ag2_span_attributes = telemetry_mod.build_ag2_span_attributes
build_ag2_telemetry_middleware = telemetry_mod.build_ag2_telemetry_middleware


class _ContextBridge:
    data = {
        "app_id": "app-1",
        "chat_id": "chat-1",
        "user_id": "user-1",
        "session_router_session_id": "session_router::app-1::user-1",
        "journey_instance_id": "journey-1",
    }


def test_ag2_telemetry_config_defaults_to_safe_content_capture(monkeypatch):
    monkeypatch.delenv("MOZAIKS_AG2_TELEMETRY_ENABLED", raising=False)
    monkeypatch.delenv("MOZAIKS_AG2_TELEMETRY_CAPTURE_CONTENT", raising=False)

    config = AG2TelemetryConfig.from_env()

    assert config.enabled is True
    assert config.capture_content is False


def test_ag2_span_attributes_include_run_scope_without_content():
    attrs = build_ag2_span_attributes(
        agent_name="AppPlanAgent",
        workflow_name="AppGenerator",
        context_variables=_ContextBridge(),
        config=AG2TelemetryConfig(
            enabled=True,
            capture_content=False,
            service_name="mozaiks-test",
            environment="test",
        ),
    )

    assert attrs == {
        "service.name": "mozaiks-test",
        "deployment.environment": "test",
        "mozaiks.workflow.name": "AppGenerator",
        "mozaiks.agent.name": "AppPlanAgent",
        "mozaiks.app.id": "app-1",
        "mozaiks.chat.id": "chat-1",
        "mozaiks.user.id": "user-1",
        "mozaiks.workflow.run.id": "chat-1",
        "mozaiks.session_router.session_id": "session_router::app-1::user-1",
        "mozaiks.journey.instance_id": "journey-1",
    }


def test_ag2_telemetry_middleware_can_be_disabled():
    middleware = build_ag2_telemetry_middleware(
        agent_name="AppPlanAgent",
        workflow_name="AppGenerator",
        context_variables={},
        config=AG2TelemetryConfig(enabled=False),
    )

    assert middleware is None


def test_ag2_telemetry_middleware_factory_returns_ag2_middleware():
    middleware = build_ag2_telemetry_middleware(
        agent_name="AppPlanAgent",
        workflow_name="AppGenerator",
        context_variables=_ContextBridge(),
        provider_name="openai",
        model_name="gpt-4o-mini",
        config=AG2TelemetryConfig(
            enabled=True,
            capture_content=False,
            service_name="mozaiks-test",
            environment="test",
        ),
    )

    assert middleware is not None
    assert middleware.__class__.__name__ == "TelemetryMiddleware"
    assert middleware._capture_content is False
    assert middleware._agent_name == "AppPlanAgent"
    assert middleware._provider_name == "openai"
    assert middleware._model_name == "gpt-4o-mini"
    assert middleware._span_attributes["mozaiks.chat.id"] == "chat-1"


def test_ag2_telemetry_middleware_factory_accepts_ag2_event_context_call_shape():
    middleware = build_ag2_telemetry_middleware(
        agent_name="AppPlanAgent",
        workflow_name="AppGenerator",
        context_variables=_ContextBridge(),
        config=AG2TelemetryConfig(enabled=True, capture_content=False),
    )

    instance = middleware(object(), object())

    assert instance.__class__.__name__ == "_TelemetryMiddlewareInstance"
