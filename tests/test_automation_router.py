from __future__ import annotations

from tests.import_utils import import_module_directly

_contracts = import_module_directly("mozaiksai.core.automation.contracts")
_router_module = import_module_directly("mozaiksai.core.automation.router")


def _config():
    return _contracts.AutomationConfigBundle.model_validate(
        {
            "events": [
                {
                    "event_type": "report.requested",
                    "source_event": "report_requested",
                    "description": "Report request event",
                    "post_commit_only": True,
                }
            ],
            "routes": [
                {
                    "route_id": "greenroom-report-request",
                    "event_type": "report.requested",
                    "when": {"payload.workflow": "GreenRoom"},
                    "effect": {
                        "kind": "workflow.run",
                        "workflow": "GreenRoom",
                        "surface": "background",
                        "message_template": "A report was requested for {payload.workflow}.",
                    },
                    "bindings": {
                        "app_id": "tenant.app_id",
                        "user_id": "tenant.user_id",
                    },
                }
            ],
        }
    )


def _event(event_type: str = "report.requested", workflow: str = "GreenRoom"):
    return _contracts.SubstrateEventEnvelope.model_validate(
        {
            "event_type": event_type,
            "tenant": {
                "app_id": "app_greenroom",
                "user_id": "user_123",
            },
            "actor": {
                "id": "user_123",
                "type": "user",
            },
            "source": {
                "layer": "substrate",
                "component": "cross_substrate_bridge",
                "transport": "http",
                "internal_event": "report_requested",
            },
            "payload": {
                "workflow": workflow,
                "report_type": "brief",
            },
        }
    )


def test_router_matches_declared_route() -> None:
    router = _router_module.AutomationRouter(_config())
    decision = router.evaluate(_event())

    assert decision.status == _contracts.AutomationDecisionStatus.MATCHED
    assert decision.route == "workflow.run:GreenRoom"
    assert decision.payload["app_id"] == "app_greenroom"
    assert decision.payload["user_id"] == "user_123"
    assert decision.payload["message"] == "A report was requested for GreenRoom."


def test_router_ignores_non_matching_conditions() -> None:
    router = _router_module.AutomationRouter(_config())
    decision = router.evaluate(_event(workflow="MainStage"))

    assert decision.status == _contracts.AutomationDecisionStatus.IGNORED
    assert "no automation route matched" in decision.detail["reason"]


def test_router_rejects_unknown_event_type() -> None:
    router = _router_module.AutomationRouter(_config())
    decision = router.evaluate(_event(event_type="unknown.event"))

    assert decision.status == _contracts.AutomationDecisionStatus.INVALID
    assert "unknown event_type" in decision.detail["reason"]
