from __future__ import annotations

from mozaiksai.core.runtime.readiness import (
    ReadinessCheck,
    checks_from_readiness_requirements,
    evaluate_readiness_checks,
    evaluate_readiness_requirements,
    non_false_env,
    truthy_env,
)


def _env(values: dict[str, str]):
    def _reader(name: str) -> str | None:
        return values.get(name)

    return _reader


def test_readiness_evaluator_reports_names_not_values() -> None:
    result = evaluate_readiness_checks(
        [
            ReadinessCheck(
                id="runtime_environment",
                category="runtime",
                label="Runtime environment",
                implemented_score=7,
                required_env=("OPENAI_API_KEY", "MONGO_URI"),
                required_evidence=("APP_IMAGE_SMOKE_VERIFIED_AT",),
            )
        ],
        env=_env({"OPENAI_API_KEY": "sk-test-secret-value"}),
    )

    assert result["ready"] is False
    check = result["checks"][0]
    assert check["missing_required_env"] == ["MONGO_URI"]
    assert check["missing_required_evidence"] == ["APP_IMAGE_SMOKE_VERIFIED_AT"]
    rendered = repr(result)
    assert "sk-test-secret-value" not in rendered


def test_readiness_evaluator_scores_10_when_required_names_are_present() -> None:
    result = evaluate_readiness_checks(
        [
            ReadinessCheck(
                id="runtime_environment",
                category="runtime",
                label="Runtime environment",
                implemented_score=7,
                required_env=("OPENAI_API_KEY", "MONGO_URI"),
                required_evidence=("APP_IMAGE_SMOKE_VERIFIED_AT",),
            ),
            ReadinessCheck(
                id="healthcheck",
                category="deployment",
                label="Healthcheck",
                implemented_score=8,
                required_evidence=("APP_HEALTHCHECK_VERIFIED_AT",),
            ),
        ],
        env=_env(
            {
                "OPENAI_API_KEY": "present",
                "MONGO_URI": "present",
                "APP_IMAGE_SMOKE_VERIFIED_AT": "run-123",
                "APP_HEALTHCHECK_VERIFIED_AT": "run-124",
            }
        ),
    )

    assert result["ready"] is True
    assert result["overall_score"] == 10
    assert result["blocking_checks"] == []
    assert result["categories"]["runtime"]["ready"] is True
    assert result["categories"]["deployment"]["ready"] is True


def test_readiness_evaluator_supports_custom_env_validators() -> None:
    result = evaluate_readiness_checks(
        [
            ReadinessCheck(
                id="runtime_environment",
                category="runtime",
                label="Runtime environment",
                implemented_score=7,
                required_env=("AUTH_ENABLED", "FEATURE_ENABLED"),
            )
        ],
        env=_env({"AUTH_ENABLED": "false", "FEATURE_ENABLED": "off"}),
        validators={"AUTH_ENABLED": non_false_env, "FEATURE_ENABLED": truthy_env},
    )

    check = result["checks"][0]
    assert check["score"] == 6
    assert check["missing_required_env"] == ["AUTH_ENABLED", "FEATURE_ENABLED"]


def test_readiness_requirements_manifest_shape_evaluates() -> None:
    requirements = {
        "checks": [
            {
                "id": "runtime_environment",
                "category": "runtime",
                "label": "Runtime environment",
                "implemented_score": 7,
                "required_env": ["OPENAI_API_KEY", "MONGO_URI", "MONGO_URI"],
                "required_evidence": [],
                "canonical_paths": ["env.example"],
            }
        ]
    }

    checks = checks_from_readiness_requirements(requirements)
    assert checks[0].required_env == ("OPENAI_API_KEY", "MONGO_URI")

    result = evaluate_readiness_requirements(
        requirements,
        env=_env({"OPENAI_API_KEY": "present", "MONGO_URI": "present"}),
    )
    assert result["ready"] is True


def test_readiness_requirements_parser_tolerates_malformed_optional_fields() -> None:
    requirements = {
        "checks": [
            {
                "id": "runtime_environment",
                "category": "runtime",
                "label": "Runtime environment",
                "implemented_score": "not-a-number",
                "required_env": "OPENAI_API_KEY",
                "required_evidence": None,
                "canonical_paths": "env.example",
            }
        ]
    }

    checks = checks_from_readiness_requirements(requirements)
    assert len(checks) == 1
    assert checks[0].implemented_score == 0
    assert checks[0].required_env == ()
