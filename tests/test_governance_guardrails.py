from __future__ import annotations

import importlib.util
import subprocess
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


def _init_git_repo(repo: Path) -> None:
    """Initialise a bare git repo suitable for governance tests."""
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_governance_guardrail_rejects_removed_permission_list_token(tmp_path: Path) -> None:
    """Any reintroduction of the removed permission-list dispatch contract in
    runtime source is an error, regardless of the value assigned."""
    guardrails = _load_guardrails_module()
    for candidate in (
        "mozaiksai/core/runtime/composition/new_dispatch.py",
        "mozaiksai/hosts/routers/modules.py",
    ):
        path = _write(
            tmp_path,
            candidate,
            "request = ModuleRequest(granted_permissions=['x'])\n",
        )
        errors, notices = guardrails.scan_paths([path], repo_root=tmp_path)
        assert notices == []
        assert [error.code for error in errors] == ["authority_bypass_not_reviewed"], candidate


def test_governance_guardrail_ignores_token_outside_runtime_source(tmp_path: Path) -> None:
    guardrails = _load_guardrails_module()
    path = _write(
        tmp_path,
        "CHANGELOG.md",
        "Removed granted_permissions from ModuleRequest.\n",
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


def test_governance_guardrail_ignored_and_untracked_local_settings_do_not_fail_all_scan(tmp_path: Path) -> None:
    """Ignored or untracked settings.local.json on disk must NOT produce a finding.

    The check must use Git's index authority, not working-tree presence.
    A contributor whose correctly gitignored file exists locally must not see a
    spurious error when running --all.
    """
    guardrails = _load_guardrails_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    # Commit a .gitignore that covers the local settings file.
    _write(repo, ".gitignore", ".claude/settings.local.json\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "init")

    # Write the file to disk — it is gitignored and NOT staged.
    _write(repo, ".claude/settings.local.json", '{"permissions": {"allow": ["Bash(curl:*)"]}}\n')

    errors, _notices = guardrails.run_scan(all_files=True, repo_root=repo)

    assert not any(e.code == "agent_local_config_committed" for e in errors), (
        "gitignored/untracked settings.local.json must not trigger agent_local_config_committed"
    )


def test_governance_guardrail_force_tracked_local_settings_fail(tmp_path: Path) -> None:
    """settings.local.json that is force-added to the index must trigger an error.

    A contributor who force-adds an agent-local permission file (overriding
    .gitignore) must be blocked by the governance check.  Git's index is the
    authority; the check fires because the file IS tracked, not merely present.
    """
    guardrails = _load_guardrails_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    # Initial commit so HEAD exists.
    _write(repo, ".gitignore", ".claude/settings.local.json\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "init")

    # Force-add the file despite .gitignore — it is now tracked.
    _write(repo, ".claude/settings.local.json", '{"permissions": {"allow": ["Bash(taskkill:*)"]}}\n')
    _git(repo, "add", "-f", ".claude/settings.local.json")

    errors, _notices = guardrails.run_scan(all_files=True, repo_root=repo)

    assert any(e.code == "agent_local_config_committed" for e in errors), (
        "force-tracked settings.local.json must trigger agent_local_config_committed"
    )


def test_governance_guardrail_shared_agent_guidance_stays_tracked_and_clean(tmp_path: Path) -> None:
    """Shared project guidance under .claude/rules/**, .claude/skills/**, and
    .claude/settings.json must never trigger agent_local_config_committed even
    when tracked in git.  Exact-path matching must not widen to .claude/**.
    """
    guardrails = _load_guardrails_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    _write(repo, ".claude/rules/runtime.md", "# Runtime Rules\n")
    _write(repo, ".claude/skills/add-module/SKILL.md", "# Add Module\n")
    _write(repo, ".claude/settings.json", '{"model": "opus"}\n')
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")

    errors, _notices = guardrails.run_scan(all_files=True, repo_root=repo)

    assert not any(e.code == "agent_local_config_committed" for e in errors), (
        "shared .claude guidance paths must not match agent_local_config_committed"
    )


def test_governance_guardrail_default_and_all_agree_on_tracked_authority(tmp_path: Path) -> None:
    """default (changed-file) mode and --all mode must both flag a newly staged
    settings.local.json.

    Both modes use the same git-index authority: 'is the file tracked?'  Neither
    should depend on whether the file physically exists without being in the index.
    """
    guardrails = _load_guardrails_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    # Create HEAD so git diff has a base.
    _write(repo, ".gitignore", "")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "init")

    # Stage settings.local.json: it is now in the index but not yet committed.
    _write(repo, ".claude/settings.local.json", '{"permissions": {"allow": ["Bash(curl:*)"]}}\n')
    _git(repo, "add", ".claude/settings.local.json")

    errors_all, _ = guardrails.run_scan(all_files=True, repo_root=repo)
    errors_changed, _ = guardrails.run_scan(all_files=False, repo_root=repo)

    assert any(e.code == "agent_local_config_committed" for e in errors_all), (
        "--all scan must flag a staged settings.local.json"
    )
    assert any(e.code == "agent_local_config_committed" for e in errors_changed), (
        "default (changed-file) scan must flag a staged settings.local.json"
    )


def test_governance_guardrail_passes_current_repo_all_scan() -> None:
    guardrails = _load_guardrails_module()

    errors, _notices = guardrails.run_scan(all_files=True)

    assert errors == []
