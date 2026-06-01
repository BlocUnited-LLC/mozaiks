from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
import yaml


WORKSPACE = Path(__file__).resolve().parents[1]
_PACKS_ROOT_ENV = os.getenv("MOZAIKS_HOSTED_PACKS_ROOT", "").strip()
PACKS_ROOT = Path(_PACKS_ROOT_ENV) if _PACKS_ROOT_ENV else WORKSPACE / ".missing-hosted-packs"
MOZAIKSPAY_ROOT = PACKS_ROOT / "mozaikspay"
MOZAIKSPAY_MANIFEST = MOZAIKSPAY_ROOT / "manifest.yaml"
MOZAIKSPAY_TEMPLATE = MOZAIKSPAY_ROOT / "service_templates" / "mozaikspay_client.py"


pytestmark = pytest.mark.skipif(
    not _PACKS_ROOT_ENV or not MOZAIKSPAY_MANIFEST.exists(),
    reason=(
        "Set MOZAIKS_HOSTED_PACKS_ROOT to a hosted capability_packs directory "
        "to validate the optional MozaiksPay hosted pack."
    ),
)


def _load_resolver():
    file_path = (
        WORKSPACE
        / "factory_app"
        / "workflows"
        / "AppGenerator"
        / "tools"
        / "resolve_hosted_pack_templates.py"
    )
    spec = importlib.util.spec_from_file_location("tests.mozaikspay_resolver", file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict:
    return yaml.safe_load(MOZAIKSPAY_MANIFEST.read_text(encoding="utf-8"))


def _template_source() -> str:
    return MOZAIKSPAY_TEMPLATE.read_text(encoding="utf-8")


def _non_comment_lines(source: str) -> list[str]:
    lines = []
    in_docstring = False
    for raw_line in source.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith('"""') or stripped.endswith('"""'):
            in_docstring = not in_docstring
            continue
        if in_docstring or not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def test_mozaikspay_pack_is_active_hosted_facade_over_wallet() -> None:
    manifest = _manifest()
    pack = manifest["pack"]

    assert pack["id"] == "mozaikspay"
    assert pack["status"] == "active"
    assert pack["visibility"] == "hosted"
    assert pack["capability_source"] == "hosted_pack"
    assert pack["implementation_mode"] == "external_integration"
    assert pack["backing_module"] == "wallet"
    assert "wallet" in pack.get("supersedes", [])


def test_mozaikspay_capabilities_map_to_wallet_module_actions() -> None:
    manifest = _manifest()
    capabilities = {item["capability_id"]: item for item in manifest["capabilities"]}

    assert capabilities["mozaikspay.view"]["action"] == "get_wallet_summary"
    assert capabilities["mozaikspay.request_payout"]["action"] == "request_payout"
    assert capabilities["mozaikspay.connect_account"]["action"] == "connect_stripe_account"
    assert capabilities["mozaikspay.list_transactions"]["action"] == "list_transactions"
    assert capabilities["mozaikspay.disconnect_account"]["action"] == "disconnect_stripe"
    assert {item["module"] for item in capabilities.values()} == {"wallet"}


def test_mozaikspay_backend_template_is_thin_client_only() -> None:
    source = _template_source()
    executable = "\n".join(_non_comment_lines(source)).lower()

    assert "class MozaiksPayClient" in source
    assert "_MODULE_PATH = \"/api/modules/wallet\"" in source
    assert "import httpx" in source
    assert "import stripe" not in executable
    assert "stripe_secret_key" not in executable
    assert "app.modules.wallet" not in executable
    assert "hosted.wallet" not in executable


def test_mozaikspay_template_resolves_to_backend_integrations() -> None:
    resolver = _load_resolver()
    pack_sources = [
        {
            "id": "mozaiks_app_hosted",
            "kind": "filesystem",
            "path": str(PACKS_ROOT),
            "capability_source": "hosted_pack",
        }
    ]
    task = {
        "task_type": "api_surface",
        "capability_pack_id": "mozaikspay",
        "owned_paths": ["services/integrations/mozaikspay_client.py"],
    }

    result = resolver.resolve_hosted_pack_templates(pack_sources, [task])

    assert len(result) == 1
    assert result[0]["filename"] == "services/integrations/mozaikspay_client.py"
    assert result[0]["content"] == _template_source()
    assert not result[0]["filename"].startswith("modules/mozaikspay/")
    assert "capability_packs" not in result[0]["filename"]
