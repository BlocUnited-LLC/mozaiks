from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_guardrails_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = workspace / "scripts" / "governance_guardrails.py"
    spec = importlib.util.spec_from_file_location("tests.governance_guardrails", file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_governance_guardrail_rejects_new_permission_bypass(tmp_path: Path) -> None:
    guardrails = _load_guardrails_module()
    path = _write(
        tmp_path,
        "mozaiksai/core/runtime/composition/new_dispatch.py",
        "request = ModuleRequest(granted_permissions=None)\n",
    )

    errors, notices = guardrails.scan_paths([path], repo_root=tmp_path)

    assert notices == []
    assert [error.code for error in errors] == ["authority_bypass_not_reviewed"]


def test_governance_guardrail_keeps_current_permission_allowlist(tmp_path: Path) -> None:
    guardrails = _load_guardrails_module()
    path = _write(
        tmp_path,
        "mozaiksai/hosts/routers/modules.py",
        "request.granted_permissions = None\n",
    )

    errors, notices = guardrails.scan_paths([path], repo_root=tmp_path)

    assert errors == []
    assert notices == []


def test_governance_guardrail_rejects_raw_public_artifact_secret(tmp_path: Path) -> None:
    guardrails = _load_guardrails_module()
    secret_value = "sk_" + "live_" + ("a" * 18)
    path = _write(
        tmp_path,
        "generated/apps/example/app/env.example",
        f"STRIPE_SECRET_KEY={secret_value}\n",
    )

    errors, notices = guardrails.scan_paths([path], repo_root=tmp_path)

    assert notices == []
    assert [error.code for error in errors] == ["raw_provider_secret"]


def test_governance_guardrail_rejects_unreviewed_provider_mutation(tmp_path: Path) -> None:
    guardrails = _load_guardrails_module()
    path = _write(
        tmp_path,
        "app/services/adapters/dns/cloudflare.py",
        "def update_dns(record):\n    return record\n",
    )

    errors, notices = guardrails.scan_paths([path], repo_root=tmp_path)

    assert notices == []
    assert [error.code for error in errors] == ["provider_mutation_review_missing"]


def test_governance_guardrail_marks_review_surfaces_without_blocking(tmp_path: Path) -> None:
    guardrails = _load_guardrails_module()
    path = _write(
        tmp_path,
        "factory_app/workflows/AppGenerator/agents.yaml",
        "agents:\n  - id: app_agent\n",
    )

    errors, notices = guardrails.scan_paths([path], repo_root=tmp_path)

    assert errors == []
    assert [notice.code for notice in notices] == ["publication_review_surface"]


def test_governance_guardrail_marks_unversioned_public_contracts_without_blocking(tmp_path: Path) -> None:
    guardrails = _load_guardrails_module()
    path = _write(
        tmp_path,
        "app/contracts/events.yaml",
        "events:\n  - type: domain.created\n",
    )

    errors, notices = guardrails.scan_paths([path], repo_root=tmp_path)

    assert errors == []
    assert [notice.code for notice in notices] == ["public_schema_needs_classification"]


def test_governance_guardrail_rejects_committed_agent_local_config(tmp_path: Path) -> None:
    guardrails = _load_guardrails_module()
    path = _write(
        tmp_path,
        ".claude/settings.local.json",
        '{"permissions": {"allow": ["Bash(curl:*)"]}}\n',
    )

    errors, _notices = guardrails.scan_paths([path], repo_root=tmp_path)

    assert [error.code for error in errors] == ["agent_local_config_committed"]


def test_governance_guardrail_keeps_shared_agent_guidance_tracked(tmp_path: Path) -> None:
    guardrails = _load_guardrails_module()
    paths = [
        _write(tmp_path, ".claude/rules/runtime.md", "# Runtime Rules\n"),
        _write(tmp_path, ".claude/skills/add-module/SKILL.md", "# Add Module\n"),
        _write(tmp_path, ".claude/settings.json", '{"model": "opus"}\n'),
    ]

    errors, notices = guardrails.scan_paths(paths, repo_root=tmp_path)

    findings = [finding.code for finding in [*errors, *notices]]
    assert "agent_local_config_committed" not in findings


def test_governance_guardrail_passes_current_repo_all_scan() -> None:
    guardrails = _load_guardrails_module()

    errors, _notices = guardrails.run_scan(all_files=True)

    assert errors == []
