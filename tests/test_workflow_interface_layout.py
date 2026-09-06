"""The interface has exactly two offline compiler-owned layout representations."""

from mozaiksai.core.runtime.app.layout_registry import build_app_layout_registry


def test_exact_interface_layout_rows_have_no_runtime_or_agent_authority() -> None:
    registry = build_app_layout_registry(())
    rows = [row for row in registry.families if row.kind.value == "workflow_module_interface"]
    assert len(rows) == 2
    expected_templates = {
        "workspace_root": "workflows/{workflow_id}/module_interface.yaml",
        "workflow_relative": "module_interface.yaml",
    }
    assert {row.path_scope.value for row in rows} == set(expected_templates)
    for row in rows:
        assert row.identity_payload == {
            "kind": "workflow_module_interface",
            "owner": "workflow",
            "requirement": "conditional",
            "multiplicity": "many",
            "condition": "when_workflow_declared",
            "path_scope": row.path_scope.value,
            "path_template": expected_templates[row.path_scope.value],
            "materializer": "workflow_interface_executor",
            "disposition": "render",
            "validator": "generated_app_validator",
            "runtime_consumer": "none",
            "security_class": "internal_contract",
            "assignment_kinds": [],
            "allowed_stub_kinds": [],
            "dependency_families": ["workflow_manifest"],
            "semantic_input_kinds": [
                "module", "workflow", "workflow_capability",
                "workflow_capability_binding", "workflow_result",
            ],
        }
    assert all(row.kind.value != "app_workflow_registry" for row in registry.families)
    assert all(row.path_template != "workflows/workflow_registry.json" for row in registry.families)
