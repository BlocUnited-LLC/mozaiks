"""
Hosted pack template expansion tests.

Verifies:
 1. OSS mode with pack_sources null does not attempt template expansion.
 2. OSS mode with pack_sources empty list does not attempt template expansion.
 3. Hosted wallet adapter task resolves wallet template from pack_sources.
 4. Template content matches hosted wallet template source (integration, skipped when
    mozaiks-app is not present).
 5. No modules/wallet path is generated.
 6. No app/capability_packs path is generated.
 7. Missing template raises HostedPackTemplateError.
 8. Placeholder pack is not expanded.
 9. Unsafe pack_id (path traversal) raises HostedPackTemplateError.
10. Template path with ../ in manifest raises HostedPackTemplateError.
11. OSS code does not import mozaiks-app.
12. Existing hosted_pack module_contract rejection still passes.
13. Existing hosted_pack adapter task validation still passes.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
_TOOLS_DIR = WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools"

# Optional integration path — exists only in mozaiks-app workspace
_MOZAIKS_APP_ROOT = WORKSPACE.parent / "mozaiks-app"
_WALLET_PACKS_ROOT = _MOZAIKS_APP_ROOT / "app_generator" / "capability_packs"
_WALLET_TEMPLATE_PATH = (
    _WALLET_PACKS_ROOT / "wallet" / "backend_templates" / "wallet_client.py"
)

# ---------------------------------------------------------------------------
# Module loaders
# ---------------------------------------------------------------------------

def _load_module(relative_path: str, module_name: str):
    file_path = WORKSPACE / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_resolver():
    return _load_module(
        "factory_app/workflows/AppGenerator/tools/resolve_hosted_pack_templates.py",
        "tests.resolve_hosted_pack_templates",
    )


def _load_app_build_plan():
    return _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def resolver():
    return _load_resolver()


@pytest.fixture()
def pack_root(tmp_path: Path) -> Path:
    """
    Create a minimal capability_packs directory structure with an active wallet pack.

    Layout::

        {tmp_path}/
          wallet/
            manifest.yaml   ← backend_templates: [backend_templates/wallet_client.py]
            backend_templates/
              wallet_client.py  ← placeholder template content
    """
    wallet_dir = tmp_path / "wallet"
    tpl_dir = wallet_dir / "backend_templates"
    tpl_dir.mkdir(parents=True)

    manifest = {
        "schema_version": "mozaiks.capability_pack",
        "pack": {
            "id": "wallet",
            "display_name": "Wallet",
            "version": "1.0.0",
            "status": "active",
            "capability_source": "hosted_pack",
        },
        "backend_templates": [
            "backend_templates/wallet_client.py"
        ],
    }
    (wallet_dir / "manifest.yaml").write_text(
        yaml.dump(manifest), encoding="utf-8"
    )
    (tpl_dir / "wallet_client.py").write_text(
        "# wallet adapter template\nclass HostedWalletClient: pass\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def pack_sources_from(pack_root: Path) -> list[dict]:
    return [
        {
            "id": "mozaiks_app_hosted",
            "kind": "filesystem",
            "path": str(pack_root),
            "capability_source": "hosted_pack",
        }
    ]


@pytest.fixture()
def wallet_adapter_task() -> dict[str, Any]:
    return {
        "task_id": "task_wallet_adapter",
        "task_type": "api_surface",
        "capability_pack_id": "wallet",
        "surface_kind": "external_integration",
        "initial_agent": "ControllerAgent",
        "owned_paths": ["backend/integrations/wallet_client.py"],
    }


# ---------------------------------------------------------------------------
# 1 & 2 — OSS mode no-op
# ---------------------------------------------------------------------------

class TestOSSModeNoOp:
    def test_null_pack_sources_returns_empty(self, resolver):
        result = resolver.resolve_hosted_pack_templates(None, [{"task_type": "api_surface"}])
        assert result == []

    def test_empty_pack_sources_returns_empty(self, resolver, wallet_adapter_task):
        result = resolver.resolve_hosted_pack_templates([], [wallet_adapter_task])
        assert result == []

    def test_null_build_tasks_returns_empty(self, resolver, pack_sources_from):
        result = resolver.resolve_hosted_pack_templates(pack_sources_from, None)
        assert result == []

    def test_empty_build_tasks_returns_empty(self, resolver, pack_sources_from):
        result = resolver.resolve_hosted_pack_templates(pack_sources_from, [])
        assert result == []

    def test_non_adapter_task_is_skipped(self, resolver, pack_sources_from):
        module_task = {
            "task_id": "task_mod",
            "task_type": "module_contract",
            "capability_pack_id": "billing",
            "owned_paths": ["modules/billing/module.yaml"],
        }
        result = resolver.resolve_hosted_pack_templates(pack_sources_from, [module_task])
        assert result == []


# ---------------------------------------------------------------------------
# 3 — Template resolved from pack_sources
# ---------------------------------------------------------------------------

class TestTemplateResolution:
    def test_wallet_adapter_task_resolves_template(
        self, resolver, pack_sources_from, wallet_adapter_task
    ):
        result = resolver.resolve_hosted_pack_templates(
            pack_sources_from, [wallet_adapter_task]
        )
        assert len(result) == 1
        assert result[0]["filename"] == "backend/integrations/wallet_client.py"
        assert result[0]["content"].strip()

    def test_resolved_filename_is_exact_owned_path(
        self, resolver, pack_sources_from, wallet_adapter_task
    ):
        result = resolver.resolve_hosted_pack_templates(
            pack_sources_from, [wallet_adapter_task]
        )
        assert result[0]["filename"] == "backend/integrations/wallet_client.py"

    def test_template_content_is_nonempty(
        self, resolver, pack_sources_from, wallet_adapter_task
    ):
        result = resolver.resolve_hosted_pack_templates(
            pack_sources_from, [wallet_adapter_task]
        )
        assert len(result[0]["content"]) > 0


# ---------------------------------------------------------------------------
# 4 — Content matches real wallet template (integration, skipped if absent)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _WALLET_TEMPLATE_PATH.exists(),
    reason="mozaiks-app wallet template not present in this environment",
)
class TestRealWalletTemplateContent:
    def test_template_content_matches_hosted_wallet_source(self, resolver):
        pack_sources = [
            {
                "id": "mozaiks_app_hosted",
                "kind": "filesystem",
                "path": str(_WALLET_PACKS_ROOT),
                "capability_source": "hosted_pack",
            }
        ]
        task = {
            "task_type": "api_surface",
            "capability_pack_id": "wallet",
            "owned_paths": ["backend/integrations/wallet_client.py"],
        }
        result = resolver.resolve_hosted_pack_templates(pack_sources, [task])
        assert len(result) == 1
        expected = _WALLET_TEMPLATE_PATH.read_text(encoding="utf-8")
        assert result[0]["content"] == expected


# ---------------------------------------------------------------------------
# 5 & 6 — Generated paths are safe
# ---------------------------------------------------------------------------

class TestGeneratedPaths:
    def test_no_modules_wallet_path(
        self, resolver, pack_sources_from, wallet_adapter_task
    ):
        result = resolver.resolve_hosted_pack_templates(
            pack_sources_from, [wallet_adapter_task]
        )
        for entry in result:
            assert not entry["filename"].startswith("modules/wallet"), \
                f"Generated path '{entry['filename']}' must not be under modules/wallet/"

    def test_no_app_capability_packs_path(
        self, resolver, pack_sources_from, wallet_adapter_task
    ):
        result = resolver.resolve_hosted_pack_templates(
            pack_sources_from, [wallet_adapter_task]
        )
        for entry in result:
            assert "capability_packs" not in entry["filename"], \
                f"Generated path '{entry['filename']}' must not reference capability_packs"


# ---------------------------------------------------------------------------
# 7 — Missing template raises HostedPackTemplateError
# ---------------------------------------------------------------------------

class TestMissingTemplate:
    def test_missing_template_file_raises(self, resolver, tmp_path):
        wallet_dir = tmp_path / "wallet"
        wallet_dir.mkdir()
        manifest = {
            "pack": {"id": "wallet", "status": "active"},
            "backend_templates": ["backend_templates/wallet_client.py"],
        }
        (wallet_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        # No backend_templates/ directory — file is missing

        pack_sources = [{"id": "x", "kind": "filesystem", "path": str(tmp_path)}]
        task = {
            "task_type": "api_surface",
            "capability_pack_id": "wallet",
            "owned_paths": ["backend/integrations/wallet_client.py"],
        }
        with pytest.raises(resolver.HostedPackTemplateError):
            resolver.resolve_hosted_pack_templates(pack_sources, [task])

    def test_missing_manifest_raises(self, resolver, tmp_path):
        # No manifest.yaml at all
        pack_sources = [{"id": "x", "kind": "filesystem", "path": str(tmp_path)}]
        task = {
            "task_type": "api_surface",
            "capability_pack_id": "wallet",
            "owned_paths": ["backend/integrations/wallet_client.py"],
        }
        with pytest.raises(resolver.HostedPackTemplateError, match="manifest"):
            resolver.resolve_hosted_pack_templates(pack_sources, [task])

    def test_owned_path_with_no_matching_template_raises(
        self, resolver, pack_sources_from
    ):
        # Task asks for nonexistent_client.py but manifest only has wallet_client.py
        task = {
            "task_type": "api_surface",
            "capability_pack_id": "wallet",
            "owned_paths": ["backend/integrations/nonexistent_client.py"],
        }
        with pytest.raises(resolver.HostedPackTemplateError):
            resolver.resolve_hosted_pack_templates(pack_sources_from, [task])


# ---------------------------------------------------------------------------
# 8 — Placeholder pack is not expanded
# ---------------------------------------------------------------------------

class TestPlaceholderPack:
    def test_placeholder_pack_not_expanded(self, resolver, tmp_path):
        wallet_dir = tmp_path / "mozaikspay"
        tpl_dir = wallet_dir / "backend_templates"
        tpl_dir.mkdir(parents=True)
        manifest = {
            "pack": {"id": "mozaikspay", "status": "placeholder"},
            "backend_templates": ["backend_templates/mozaikspay_client.py"],
        }
        (wallet_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (tpl_dir / "mozaikspay_client.py").write_text("# placeholder")

        pack_sources = [{"id": "x", "kind": "filesystem", "path": str(tmp_path)}]
        task = {
            "task_type": "api_surface",
            "capability_pack_id": "mozaikspay",
            "owned_paths": ["backend/integrations/mozaikspay_client.py"],
        }
        result = resolver.resolve_hosted_pack_templates(pack_sources, [task])
        assert result == [], "Placeholder pack must not produce template files"


# ---------------------------------------------------------------------------
# 9 & 10 — Path traversal prevention
# ---------------------------------------------------------------------------

class TestPathTraversalPrevention:
    def test_unsafe_pack_id_with_slash_raises(self, resolver, pack_sources_from):
        task = {
            "task_type": "api_surface",
            "capability_pack_id": "../evil",
            "owned_paths": ["backend/integrations/evil_client.py"],
        }
        with pytest.raises(resolver.HostedPackTemplateError, match="Unsafe pack_id"):
            resolver.resolve_hosted_pack_templates(pack_sources_from, [task])

    def test_unsafe_pack_id_with_backslash_raises(self, resolver, pack_sources_from):
        task = {
            "task_type": "api_surface",
            "capability_pack_id": "..\\evil",
            "owned_paths": ["backend/integrations/evil_client.py"],
        }
        with pytest.raises(resolver.HostedPackTemplateError, match="Unsafe pack_id"):
            resolver.resolve_hosted_pack_templates(pack_sources_from, [task])

    def test_template_path_traversal_in_manifest_raises(self, resolver, tmp_path):
        wallet_dir = tmp_path / "wallet"
        wallet_dir.mkdir()
        manifest = {
            "pack": {"id": "wallet", "status": "active"},
            # Attempt to escape pack directory via template path
            "backend_templates": ["../../secret.py"],
        }
        (wallet_dir / "manifest.yaml").write_text(yaml.dump(manifest))

        pack_sources = [{"id": "x", "kind": "filesystem", "path": str(tmp_path)}]
        task = {
            "task_type": "api_surface",
            "capability_pack_id": "wallet",
            "owned_paths": ["backend/integrations/secret.py"],
        }
        with pytest.raises(resolver.HostedPackTemplateError, match="traversal|manifest|matching"):
            resolver.resolve_hosted_pack_templates(pack_sources, [task])


# ---------------------------------------------------------------------------
# 11 — OSS resolver does not import mozaiks-app
# ---------------------------------------------------------------------------

class TestNoMozaiksAppImport:
    def test_resolver_source_does_not_import_mozaiks_app(self):
        src = (
            WORKSPACE
            / "factory_app"
            / "workflows"
            / "AppGenerator"
            / "tools"
            / "resolve_hosted_pack_templates.py"
        ).read_text(encoding="utf-8")
        # Only check actual import statement lines, not docstring or comment text.
        import_lines = [
            ln.strip() for ln in src.splitlines()
            if ln.strip().startswith(("import ", "from "))
        ]
        bad = [
            ln for ln in import_lines
            if (
                ln.startswith("from mozaiks_app") or
                ln.startswith("import mozaiks_app") or
                "app_generator.hosted_build_context" in ln
            )
        ]
        assert not bad, f"OSS resolver imports mozaiks-app: {bad}"

    def test_assemble_tasks_source_does_not_import_mozaiks_app(self):
        src = (
            WORKSPACE
            / "factory_app"
            / "workflows"
            / "AppGenerator"
            / "tools"
            / "assemble_app_tasks.py"
        ).read_text(encoding="utf-8")
        lines = [ln.strip() for ln in src.splitlines()]
        bad = [
            ln for ln in lines
            if (
                ln.startswith("from mozaiks_app") or
                ln.startswith("import mozaiks_app") or
                "app_generator.hosted_build_context" in ln
            )
        ]
        assert not bad, f"assemble_app_tasks imports mozaiks-app: {bad}"


# ---------------------------------------------------------------------------
# 12 — Existing module_contract rejection guard still passes
# ---------------------------------------------------------------------------

class TestModuleContractRejectionStillPasses:
    def test_hosted_pack_module_contract_rejected(self):
        mod = _load_app_build_plan()
        build_tasks = [
            {
                "task_id": "bad_task",
                "task_type": "module_contract",
                "capability_pack_id": "wallet",
                "initial_agent": "ConfigMiddlewareAgent",
                "owned_paths": ["modules/wallet/module.yaml"],
            }
        ]
        hosted_pack_ids: frozenset = frozenset({"wallet"})
        with pytest.raises(ValueError, match="hosted pack"):
            mod._validate_build_tasks(build_tasks, hosted_pack_ids)

    def test_generated_module_contract_still_passes(self):
        mod = _load_app_build_plan()
        build_tasks = [
            {
                "task_id": "task_billing",
                "task_type": "module_contract",
                "capability_pack_id": "billing",
                "initial_agent": "ConfigMiddlewareAgent",
                "owned_paths": ["modules/billing/module.yaml"],
            }
        ]
        # wallet is hosted, billing is not — should pass
        hosted_pack_ids: frozenset = frozenset({"wallet"})
        mod._validate_build_tasks(build_tasks, hosted_pack_ids)  # no exception


# ---------------------------------------------------------------------------
# 13 — Existing adapter task validation still passes
# ---------------------------------------------------------------------------

class TestAdapterTaskValidationStillPasses:
    def test_adapter_task_with_integrations_path_is_valid(self):
        mod = _load_app_build_plan()
        build_tasks = [
            {
                "task_id": "task_wallet_adapter",
                "task_type": "api_surface",
                "capability_pack_id": "wallet",
                "initial_agent": "ControllerAgent",
                "owned_paths": ["backend/integrations/wallet_client.py"],
            }
        ]
        hosted_pack_ids: frozenset = frozenset({"wallet"})
        # Should not raise
        mod._validate_build_tasks(build_tasks, hosted_pack_ids)

    def test_adapter_task_must_not_own_modules_path(self):
        mod = _load_app_build_plan()
        build_tasks = [
            {
                "task_id": "bad",
                "task_type": "api_surface",
                "capability_pack_id": "wallet",
                "initial_agent": "ControllerAgent",
                "owned_paths": ["modules/wallet/backend/handler.py"],
            }
        ]
        hosted_pack_ids: frozenset = frozenset({"wallet"})
        with pytest.raises(ValueError, match="modules/wallet"):
            mod._validate_build_tasks(build_tasks, hosted_pack_ids)
