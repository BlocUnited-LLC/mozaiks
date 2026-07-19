from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_gate_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = workspace / "scripts" / "production_readiness_gate.py"
    spec = importlib.util.spec_from_file_location("tests.production_readiness_gate", file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_production_readiness_gate_includes_core_offline_targets() -> None:
    gate = _load_gate_module()

    targets = set(gate.PYTEST_GATE_TARGETS)

    assert "tests/test_appgenerator_canonical_generation.py" in targets
    assert "tests/test_appgenerator_save_app_schema.py" in targets
    assert "tests/test_managed_capability_artifact_replay.py" in targets
    assert "tests/test_mozaikspay_managed_capability_contract.py" in targets
    assert "tests/test_generated_saas_subscription_runtime_acceptance.py" in targets
    assert "tests/test_subscription_contract_designer.py" in targets
    assert "tests/test_subscriptions_loader.py" in targets
    assert "tests/test_token_usage_guard.py" in targets
    assert "tests/test_token_wallet_ledger.py" in targets
    assert "tests/test_token_wallet_usage_ingest.py" in targets
    assert "tests/test_app_loader.py" in targets
    assert "tests/test_studio_host_smoke.py" in targets
    # Security and runtime contracts
    assert "tests/test_security_hardening.py" in targets
    assert "tests/test_rate_limit_helpers.py" in targets
    assert "tests/test_rate_limit_middleware.py" in targets
    assert "tests/test_startup_validation.py" in targets
    assert "tests/test_module_executor_permission_enforcement.py" in targets


def test_production_readiness_gate_includes_core_monetization_targets() -> None:
    gate = _load_gate_module()

    expected = {
        "tests/test_monetization_taxonomy.py",
        "tests/test_appgenerator_integration_manifest.py",
        "tests/test_workspace_integrations_module.py",
        "tests/test_appgenerator_capability_pack_selection.py",
        "tests/test_generated_bundle_scanner.py",
        "tests/test_mozaikspay_managed_capability_contract.py",
        "tests/test_managed_capability_artifact_replay.py",
        "tests/test_generated_saas_subscription_runtime_acceptance.py",
    }

    assert set(gate.CORE_MONETIZATION_GATE_TARGETS) == expected
    assert expected <= set(gate.PYTEST_GATE_TARGETS)
    assert expected <= set(gate.QUICK_PYTEST_TARGETS)


def test_production_readiness_gate_can_list_without_running() -> None:
    gate = _load_gate_module()

    assert gate.main(["--quick", "--skip-frontend", "--list"]) == 0


def test_production_readiness_gate_sets_test_env_defaults() -> None:
    gate = _load_gate_module()

    env = gate._base_env()

    assert env["ENV"] == "test"
    assert env["AUTH_ENABLED"] == "false"
    assert env["RATE_LIMIT_ENABLED"] == "false"
    assert env["OPENAI_API_KEY"] == "sk-test-key"


def test_production_readiness_gate_can_include_cash_to_token_docker_smoke() -> None:
    gate = _load_gate_module()

    steps = gate._build_steps(gate.argparse.Namespace(
        quick=True,
        skip_frontend=True,
        include_docker_smoke=True,
    ))

    command = steps[0].command
    env = steps[0].env
    assert "tests/test_subscription_token_runtime_real_mongo.py" in command
    assert env["MOZAIKS_RUN_SUBSCRIPTION_TOKEN_DOCKER_SMOKE"] == "1"


def test_source_hygiene_scan_passes_current_repo() -> None:
    gate = _load_gate_module()

    assert gate.run_source_hygiene_scan() == []


def test_source_hygiene_excludes_release_local_virtualenv() -> None:
    gate = _load_gate_module()

    assert ".release-local-venv" in gate.SOURCE_HYGIENE_EXCLUDED_DIRS


def test_source_hygiene_excludes_local_agent_worktrees_but_keeps_repo_rules() -> None:
    gate = _load_gate_module()

    assert gate._source_hygiene_excluded(
        gate.REPO_ROOT / ".claude" / "worktrees" / "agent-a" / "tests" / "example.py"
    )
    assert not gate._source_hygiene_excluded(
        gate.REPO_ROOT / ".claude" / "rules" / "testing.md"
    )

