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
    assert "tests/test_mozaikspay_hosted_pack_contract.py" in targets
    assert "tests/test_studio_host_smoke.py" in targets


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


def test_source_hygiene_scan_passes_current_repo() -> None:
    gate = _load_gate_module()

    assert gate.run_source_hygiene_scan() == []

