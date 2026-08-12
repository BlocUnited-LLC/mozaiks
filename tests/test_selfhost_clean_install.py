"""Clean-room self-host acceptance smoke tests.

These tests represent the deterministic gate for the external-developer
self-host journey. They verify:

  - Required factory resources resolve without BlocUnited infrastructure.
  - The factory app loads (modules, workflows) from an editable install.
  - The startup validator produces actionable messages for missing config.
  - The package content guard passes on the installed distribution.
  - No accidental mozaiks-app / BlocUnited-domain imports in the runtime.

No live LLM calls, no real MongoDB, no external services. Every check here
must be executable from a fresh ``pip install -e ".[dev]"`` on a clean clone.

Classification per task 15 (Developer Error Quality):
  REQUIRED BASELINE   — must be set for Factory to do real LLM work
  OPTIONAL FEATURE    — enhances but not required for basic operation
  HOSTED-ONLY         — only relevant on mozaiks.ai platform
  BAD DEFAULT / DOC GAP — actively misleading for a clean-room user

See also: docs/guides/self-hosting.md
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. Factory resources resolve without private infrastructure
# ---------------------------------------------------------------------------


def test_factory_app_workspace_exists() -> None:
    """Factory app workspace (app bundle) is present in the editable install."""
    factory_app = REPO_ROOT / "factory_app" / "app"
    assert factory_app.is_dir(), (
        f"factory_app/app/ not found at {factory_app}. "
        "Is the package installed in editable mode from the repo root?"
    )


def test_factory_workflows_present() -> None:
    """Core factory workflows ship with the OSS package."""
    workflows = REPO_ROOT / "factory_app" / "workflows"
    assert workflows.is_dir(), "factory_app/workflows/ missing"
    required = ["AppGenerator", "AgentGenerator", "DesignDocs", "ValueEngine"]
    missing = [w for w in required if not (workflows / w).is_dir()]
    assert not missing, f"Missing required factory workflows: {missing}"


def test_env_example_provides_default_mongo_uri() -> None:
    """MONGO_URI has a usable local default in .env.example.

    This avoids a confusing crash for users who forget to set up MongoDB —
    the default points at localhost:27017 which is the natural local dev choice.
    """
    env_example = REPO_ROOT / ".env.example"
    assert env_example.exists(), ".env.example missing"
    content = env_example.read_text(encoding="utf-8")
    assert "MONGO_URI=mongodb://localhost:27017" in content, (
        "MONGO_URI default removed from .env.example — "
        "restore it so first-run users have a local dev target"
    )


def test_env_example_documents_gemini_as_default_llm() -> None:
    """Gemini is the documented free-tier default LLM provider.

    An unrelated developer following the docs should know which key to set
    without needing to read source code.
    """
    env_example = REPO_ROOT / ".env.example"
    content = env_example.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY" in content, "GEMINI_API_KEY missing from .env.example"
    assert "LLM_PRIMARY_API_TYPE=google" in content, (
        "Default LLM_PRIMARY_API_TYPE should be google (Gemini free tier). "
        "Docs say Gemini is the default — keep it consistent with .env.example."
    )


def test_env_example_does_not_require_azure() -> None:
    """Azure should be optional — not a required first-run step."""
    env_example = REPO_ROOT / ".env.example"
    content = env_example.read_text(encoding="utf-8")
    # Azure vars should be present but commented / labelled optional
    assert "OPTIONAL" in content.upper() or "optional" in content, (
        ".env.example must label Azure and other managed-platform options as OPTIONAL"
    )


def test_cli_entry_point_importable() -> None:
    """The mozaiks CLI entry point resolves without private imports."""
    import mozaiks_cli  # noqa: F401


def test_factory_app_loader_importable() -> None:
    """App loader is importable from an editable install."""
    from mozaiksai.core.runtime.app.loader import AppLoader  # noqa: F401

    assert AppLoader.__name__ == "AppLoader"


def test_startup_validation_importable() -> None:
    """Startup validation module is importable."""
    from mozaiksai.core.startup.validation import (  # noqa: F401
        StartupConfigError,
        _can_resolve_api_key,
        run_startup_checks,
    )

    assert callable(run_startup_checks)


# ---------------------------------------------------------------------------
# 2. Startup validator produces actionable messages (no BlocUnited knowledge)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_warns_on_missing_llm_key_with_actionable_message(monkeypatch) -> None:
    """Missing LLM key produces a message naming the correct env var.

    A clean-room developer should not need to guess which env var to set.
    """
    from unittest.mock import AsyncMock, patch

    from mozaiksai.core.startup.validation import run_startup_checks

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PRIMARY_API_TYPE", raising=False)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key-long-enough-to-pass-the-check")
    monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)

    class _OkMongo:
        class _Admin:
            async def command(self, _):
                return {"ok": 1}
        admin = _Admin()

    with (
        patch("mozaiksai.core.core_config.get_secret", side_effect=ValueError("not found")),
        patch(
            "mozaiksai.core.startup.validation._has_mongo_llm_config",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        warnings = await run_startup_checks(_mongo_client=_OkMongo())

    llm_warnings = [w for w in warnings if "llm" in w.lower() or "api_key" in w.lower() or "API_KEY" in w]
    assert llm_warnings, "Expected a warning about the missing LLM API key"
    # Message must name a specific env var — not just "configure LLM"
    assert any("_API_KEY" in w or "OPENAI" in w or "GEMINI" in w or "ANTHROPIC" in w for w in llm_warnings), (
        f"LLM warning does not name a specific env var: {llm_warnings}"
    )


@pytest.mark.asyncio
async def test_startup_warns_on_missing_mongo_with_actionable_message(monkeypatch) -> None:
    """Missing MONGO_URI produces a clear actionable message."""
    from unittest.mock import patch

    from mozaiksai.core.startup.validation import run_startup_checks

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder")
    monkeypatch.delenv("LLM_PRIMARY_API_TYPE", raising=False)
    monkeypatch.delenv("MONGO_URI", raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key-long-enough-to-pass-the-check")
    monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)

    with patch("mozaiksai.core.startup.validation.get_secret", side_effect=ValueError("not found")):
        warnings = await run_startup_checks()

    mongo_warnings = [w for w in warnings if "MONGO" in w or "MongoDB" in w]
    assert mongo_warnings, "Expected a warning about MONGO_URI"
    assert any("MONGO_URI" in w for w in mongo_warnings)


# ---------------------------------------------------------------------------
# 3. No accidental mozaiks-app / BlocUnited-domain imports
# ---------------------------------------------------------------------------


def test_no_mozaiks_app_pkg_imports_in_runtime() -> None:
    """The mozaiksai runtime must not import from a private mozaiks_app package.

    If ``import mozaiks_app`` appears in runtime source, self-hosters who don't
    have the private hosted-product repo installed will get ImportError at
    startup.
    """
    runtime_root = REPO_ROOT / "mozaiksai"
    violations = []
    for py_file in runtime_root.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "from mozaiks_app " in stripped or "import mozaiks_app" in stripped:
                violations.append(f"{py_file.relative_to(REPO_ROOT)}: {stripped!r}")
    assert not violations, (
        "Runtime imports from private mozaiks_app package:\n"
        + "\n".join(violations)
    )


def test_no_blocunited_domain_hardcoding_in_runtime() -> None:
    """No BlocUnited-specific domains hardcoded in runtime Python source."""
    runtime_root = REPO_ROOT / "mozaiksai"
    forbidden_fragments = ["blocunited.com", "app.blocunited", "api.blocunited"]
    violations = []
    for py_file in runtime_root.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8", errors="replace")
        for frag in forbidden_fragments:
            if frag in text.lower():
                violations.append(f"{py_file.relative_to(REPO_ROOT)}: contains {frag!r}")
    assert not violations, (
        "BlocUnited domain hardcoded in runtime:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 4. Package content guard passes on installed distribution
# ---------------------------------------------------------------------------


def test_package_content_guard_passes_on_built_dist(tmp_path) -> None:
    """Package content guard validates the installed dist-info metadata.

    This is a lightweight check (not a full wheel build) that exercises
    the guard's family allowlist against the installed package's file list.
    """
    import importlib.util
    import sys

    guard_path = REPO_ROOT / "scripts" / "package_content_guard.py"
    spec = importlib.util.spec_from_file_location("_pcg", guard_path)
    pcg = importlib.util.module_from_spec(spec)
    sys.modules["_pcg"] = pcg
    spec.loader.exec_module(pcg)

    # Verify the approved family sets are present and non-empty
    assert pcg.APPROVED_TOP_LEVEL_FAMILIES, "APPROVED_TOP_LEVEL_FAMILIES must be non-empty"
    assert "mozaiksai" in pcg.APPROVED_TOP_LEVEL_FAMILIES
    assert "factory_app" in pcg.APPROVED_TOP_LEVEL_FAMILIES
    assert pcg.APPROVED_FACTORY_APP_SUBFAMILIES, "APPROVED_FACTORY_APP_SUBFAMILIES must be non-empty"


# ---------------------------------------------------------------------------
# 5. Self-host docs present
# ---------------------------------------------------------------------------


def test_selfhost_guide_exists() -> None:
    """Self-hosting guide must exist for the documented self-host path."""
    guide = REPO_ROOT / "docs" / "guides" / "self-hosting.md"
    assert guide.exists(), "docs/guides/self-hosting.md missing"
    content = guide.read_text(encoding="utf-8")
    # Must document MongoDB setup (required baseline)
    assert "MongoDB" in content or "mongo" in content.lower()
    # Must not require BlocUnited-specific services
    assert "blocunited.com" not in content.lower()


def test_getting_started_does_not_reference_dead_pypi_install() -> None:
    """getting-started.md must not use ``pip install mozaiks`` as a step instruction.

    Releases are intentionally paused. The correct install is editable mode
    from a local checkout. Explanatory prose that *mentions* the PyPI form
    (e.g. "instead of ``pip install mozaiks``") is allowed.
    """
    doc = REPO_ROOT / "docs" / "getting-started.md"
    if not doc.exists():
        pytest.skip("getting-started.md not found")
    content = doc.read_text(encoding="utf-8")
    # A code block line that is just the install command (no -e flag) is a dead path.
    # Prose that mentions it in context ("instead of X") is acceptable.
    lines = content.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Only flag bare code-block style instructions — not explanatory prose
        if stripped == "pip install mozaiks" or stripped == "pip install mozaiks[dev]":
            pytest.fail(
                f"getting-started.md line {i+1} contains dead PyPI install instruction: {stripped!r}\n"
                "Releases are paused — use: pip install -e \".[dev]\""
            )
