"""Regression tests for the live-Genesis persistence root causes.

A real greenfield build produced only a concept artifact: two workflows
terminated through the compiled graph's no_transition_matched default and
were reported as clean completions, and the tool wrapper logged failure
returns as success. (The companion structured_output shape defect is fixed at
the runtime overlay boundary and covered by
tests/test_structured_output_exact_shape.py.)
"""

from __future__ import annotations

from tests.import_utils import import_module_directly

_runner = import_module_directly("mozaiksai.core.adapters.ag2_network_runner")
_tools = import_module_directly("mozaiksai.core.workflow.agents.tools")
_ports = import_module_directly("mozaiksai.core.ports.orchestration")

RunStatus = _ports.RunStatus


class TestNoTransitionMatchedIsFailure:
    """RC4 — graph exhaustion must not masquerade as clean completion."""

    def test_no_transition_matched_maps_to_failed(self):
        status, error = _runner._resolve_close_status(RunStatus.COMPLETED, "no_transition_matched", None)
        assert status is RunStatus.FAILED
        assert error == "no_transition_matched"

    def test_workflow_failed_maps_to_failed(self):
        status, error = _runner._resolve_close_status(RunStatus.COMPLETED, "workflow_failed", None)
        assert status is RunStatus.FAILED
        assert error == "workflow_failed"

    def test_clean_reasons_pass_through(self):
        status, error = _runner._resolve_close_status(RunStatus.COMPLETED, "workflow_complete", None)
        assert status is RunStatus.COMPLETED
        assert error is None
        status, _ = _runner._resolve_close_status(RunStatus.PAUSED, "awaiting_user_input", None)
        assert status is RunStatus.PAUSED


class TestLiveUserContextElevation:
    """A live user turn carrying a declared user_text trigger write must
    attribute to the deterministic user-decision writer — otherwise the
    closed routing key rejects the re-application and the resume turn fails
    (observed live: the builder's approval set workflow_review_approved via
    the trigger, then the live-context lane errored the run re-applying it).
    """

    def _policy(self):
        _authority = import_module_directly("mozaiksai.core.workflow.context.authority")
        definitions = {
            "workflow_review_approved": {
                "type": "boolean",
                "source": {
                    "type": "state",
                    "default": False,
                    "triggers": [
                        {"type": "user_text", "match": {"regex": r"(?i)\bapproved?\b"}}
                    ],
                },
            }
        }
        rules = [
            {
                "source_agent": "user",
                "target_agent": "PackBuildCoordinator",
                "transition_type": "condition",
                "condition_type": "context_equals",
                "condition_key": "workflow_review_approved",
                "condition_value": True,
            }
        ]
        return _authority.build_context_authority_policy(
            workflow_name="ElevationSmoke", definitions=definitions, transition_rules=rules
        )

    def test_live_user_context_elevates_to_declared_user_text_writer(self):
        _authority = import_module_directly("mozaiksai.core.workflow.context.authority")
        policy = self._policy()
        safe = _runner._authorized_context_updates(
            {"workflow_review_approved": True},
            writer_id=_authority.LIVE_USER_CONTEXT_WRITER,
            context_authority_policy=policy,
            elevated_writer_id=_authority.USER_TEXT_TRIGGER_WRITER,
        )
        assert safe == {"workflow_review_approved": True}

    def test_unelevated_live_user_context_still_rejected(self):
        import pytest

        _authority = import_module_directly("mozaiksai.core.workflow.context.authority")
        policy = self._policy()
        with pytest.raises(_authority.ContextAuthorityError):
            _runner._authorized_context_updates(
                {"workflow_review_approved": True},
                writer_id=_authority.LIVE_USER_CONTEXT_WRITER,
                context_authority_policy=policy,
            )

    def test_untrusted_elevation_pairs_still_raise(self):
        import pytest

        _authority = import_module_directly("mozaiksai.core.workflow.context.authority")
        policy = self._policy()
        with pytest.raises(_authority.ContextAuthorityError, match="untrusted_writer_attribution"):
            _runner._authorized_context_updates(
                {"workflow_review_approved": True},
                writer_id=_authority.LIVE_USER_CONTEXT_WRITER,
                context_authority_policy=policy,
                elevated_writer_id=_authority.SENTINEL_TEXT_TRIGGER_WRITER,
            )


class TestToolWrapperFailureLogging:
    """RC5 — an ok:False / success:False return is not a success."""

    def test_failure_markers_detected(self):
        reason = _tools._returned_failure_reason({"ok": False, "reason": "missing_design_docs_bundle"})
        assert reason == "missing_design_docs_bundle"
        reason = _tools._returned_failure_reason({"success": False, "error": "No structured output"})
        assert reason == "No structured output"
        assert _tools._returned_failure_reason({"success": False}) == "success=False"

    def test_success_and_non_dict_results_pass(self):
        assert _tools._returned_failure_reason({"ok": True}) is None
        assert _tools._returned_failure_reason({"success": True, "error": None}) is None
        assert _tools._returned_failure_reason({"status": "error"}) is None  # no explicit False marker
        assert _tools._returned_failure_reason("text result") is None
        assert _tools._returned_failure_reason(None) is None
