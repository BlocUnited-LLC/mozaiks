from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_contributing_has_human_first_quickstart() -> None:
    contributing = _read("CONTRIBUTING.md")

    assert "## Quickstart: Your First Pull Request" in contributing
    assert "**Fork** the repository" in contributing
    assert "**Create a branch**" in contributing
    assert 'pip install -e ".[dev]"' in contributing
    assert "**Make a focused change.**" in contributing
    assert "**Run the relevant tests**" in contributing
    assert "**Open a pull request** against `main` using the pull request template." in contributing


def test_contributing_explains_no_extra_setup_paths() -> None:
    contributing = _read("CONTRIBUTING.md")

    assert "## What You Can Contribute Without Extra Setup" in contributing
    assert "MongoDB" in contributing
    assert "Node.js" in contributing
    assert "an LLM API key" in contributing
    assert 'pip install -e ".[docs]"' in contributing
    assert "python -m mkdocs serve" in contributing
    assert "tests/conftest.py" in contributing
    assert "mozaiks_cli" in contributing


def test_ai_agent_guidance_is_clearly_optional_and_preserved() -> None:
    contributing = _read("CONTRIBUTING.md")

    assert "## Working With an AI Coding Agent (Optional)" in contributing
    assert "nothing below is required to complete the Quickstart above" in contributing
    assert "### Start Here" in contributing
    assert "### Common Task Map" in contributing
    assert "### Build And Refinement Truth" in contributing
    assert "### Final Report Requirements" in contributing
    assert "### Focused Tests" in contributing

    # These must no longer be top-level headings once nested under the
    # AI-agent-optional section.
    assert "\n## Build And Refinement Truth" not in contributing
    assert "\n## Final Report Requirements" not in contributing
    assert "\n## Focused Tests" not in contributing
    assert "\n## Development Setup" not in contributing


def test_contributing_links_to_new_community_health_files() -> None:
    contributing = _read("CONTRIBUTING.md")

    assert "[Code of Conduct](CODE_OF_CONDUCT.md)" in contributing
    assert "[SECURITY.md](SECURITY.md)" in contributing
    assert "pull request template" in contributing


def test_readme_and_docs_index_point_to_same_contribution_path() -> None:
    readme = _read("README.md")
    docs_index = _read("docs/contributing/index.md")

    assert "[CONTRIBUTING.md](CONTRIBUTING.md)" in readme
    assert "[Code of Conduct](CODE_OF_CONDUCT.md)" in readme
    assert "#quickstart-your-first-pull-request" in docs_index
    assert "Contributor Guidance Readiness" in docs_index


def test_security_md_prefers_private_reporting_and_invents_no_email() -> None:
    security = _read("SECURITY.md")

    assert "private vulnerability reporting" in security
    assert "github.com/BlocUnited-LLC/mozaiks/security" in security
    assert "Report a vulnerability" in security
    assert "@" not in security


def test_code_of_conduct_is_contributor_covenant_with_no_individual_contact() -> None:
    coc = _read("CODE_OF_CONDUCT.md")

    assert "Contributor Covenant" in coc
    assert "project maintainers" in coc
    # No invented individual person or email address as the enforcement contact.
    assert "@" not in coc


def test_issue_templates_cover_bug_feature_and_docs() -> None:
    template_dir = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"

    bug = yaml.safe_load((template_dir / "bug_report.yml").read_text(encoding="utf-8"))
    feature = yaml.safe_load((template_dir / "feature_request.yml").read_text(encoding="utf-8"))
    docs = yaml.safe_load((template_dir / "documentation.yml").read_text(encoding="utf-8"))
    config = yaml.safe_load((template_dir / "config.yml").read_text(encoding="utf-8"))

    assert bug["name"] == "Bug Report"
    assert any(field.get("id") == "repro" for field in bug["body"])

    assert feature["name"] == "Feature Request"
    assert any(field.get("id") == "problem" for field in feature["body"])

    assert docs["name"] == "Documentation Improvement"
    assert any(field.get("id") == "location" for field in docs["body"])

    assert config["blank_issues_enabled"] is False
    assert any(
        "security" in link["url"]
        for link in config["contact_links"]
    )


def test_pull_request_template_requests_required_fields() -> None:
    pr_template = _read(".github/PULL_REQUEST_TEMPLATE.md")

    assert "## Related issue" in pr_template
    assert "## Summary" in pr_template
    assert "## Tests run" in pr_template
    assert "## Screenshots" in pr_template
    assert "## Boundary confirmation" in pr_template
    assert "private hosted-product logic" in pr_template
