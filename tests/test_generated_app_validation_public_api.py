from __future__ import annotations

from mozaiksai.core.validation import (
    GeneratedAppValidationRequest,
    validate_generated_app_bundle,
)


def test_validate_generated_app_bundle_returns_structured_scanner_diagnostics() -> None:
    result = validate_generated_app_bundle(
        GeneratedAppValidationRequest(
            files={
                "modules/checkout/backend/service.py": "import payment_provider\n",
            }
        )
    )

    assert result.passed is False
    assert result.diagnostics
    assert result.diagnostics[0].code == "generated_bundle_contract_failed"
    assert result.diagnostics[0].path == "modules/checkout/backend/service.py"


def test_validate_generated_app_bundle_reports_page_contract_warnings() -> None:
    result = validate_generated_app_bundle(
        GeneratedAppValidationRequest(
            files={},
            pages=[
                {
                    "name": "orders",
                    "page_type": "record_list",
                    "sections": [
                        {"primitive": "DataTable", "api_endpoint": "/api/modules/orders/list?bad=1"}
                    ],
                }
            ],
        )
    )

    assert any(
        diagnostic.code == "page_schema_contract_warning"
        and "api_endpoint" in diagnostic.message
        for diagnostic in result.diagnostics
    )


def test_validate_generated_app_bundle_reports_build_task_dependency_errors() -> None:
    result = validate_generated_app_bundle(
        GeneratedAppValidationRequest(
            files={},
            build_tasks=[
                {
                    "task_id": "checkout",
                    "task_type": "module_contract",
                    "depends_on": ["missing"],
                }
            ],
        )
    )

    assert result.passed is False
    assert any(
        diagnostic.code == "build_task_dependency_invalid"
        and "missing" in diagnostic.message
        for diagnostic in result.diagnostics
    )
