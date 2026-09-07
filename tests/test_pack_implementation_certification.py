"""Real canonical capability-pack implementation certification.

Every workspace_handler_split pack module certifies through the bounded
CANONICAL_BASE_HANDLER mode: the preserved workspace-owned ``handler.py``
leaf directly subclasses the regenerated template-owned ``base_handler.py``
class, which explicitly defines each declared ``handler_method``. Exactly
those two verified sources participate — no general Python source closure —
and the certified implementation identity covers BOTH source digests.

The corpus is discovered independently from the checked-in factory packs and
pinned: 10 modules, 63 actions, all certifying end to end through
``resolve_module_action_implementation`` with exact verified bytes, the #484
closed request import, and the pack contract's workspace_handler_split
ownership authority.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.artifacts.content_store import LocalArtifactContentStore
from mozaiksai.core.runtime.app.layout_registry import PathScope
from mozaiksai.core.semantics.composition_ledger import AccountedArtifact, ArtifactAddress
from mozaiksai.core.semantics.implementation_artifacts import (
    HandlerCertificationMode,
    ImplementationArtifactError,
    SelectedContractArtifact,
    resolve_module_action_implementation,
    workspace_handler_split_authority_from_pack_contract,
)
from mozaiksai.core.semantics.refs import ChildContractRef, ExecutionAccessScopeRef

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"

SCOPE = ExecutionAccessScopeRef(tenant_id="tenant", workspace_id="workspace")

EXPECTED_MODULE_COUNT = 10
EXPECTED_ACTION_COUNT = 63


def _discover_split_modules() -> list[tuple[str, str]]:
    """Independently discover the canonical workspace_handler_split corpus."""
    discovered: list[tuple[str, str]] = []
    for base_handler in sorted(BUILD_CONTEXT.glob("*/templates/modules/*/backend/base_handler.py")):
        module_dir = base_handler.parent.parent
        pack_dir = base_handler.parents[4]
        discovered.append((pack_dir.name, module_dir.name))
    return discovered


SPLIT_MODULES = _discover_split_modules()
SPLIT_IDS = [f"{pack}/{module}" for pack, module in SPLIT_MODULES]


def _module_root(pack: str, module: str) -> Path:
    return BUILD_CONTEXT / pack / "templates" / "modules" / module


def _pack_contract(pack: str) -> dict:
    return yaml.safe_load((BUILD_CONTEXT / pack / "contract.yaml").read_text(encoding="utf-8")) or {}


@pytest.fixture
def content_store(tmp_path):
    return LocalArtifactContentStore(root=tmp_path)


async def _put(content_store, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    await content_store.put_blob(data, expected_digest=digest)
    return digest


# Selections use APP_BUNDLE_ROOT: the canonical layout registry registers the
# module_backend_base_handler family at app scope (the module-relative sibling
# row does not exist yet — a registry asymmetry outside this slice's scope).
def _module_selection(*, digest: str, module: str) -> SelectedContractArtifact:
    return SelectedContractArtifact(
        contract_ref=ChildContractRef(
            subject_id="pack-app",
            subject_version=1,
            content_digest=digest,
            scope=SCOPE,
            artifact_family="module_manifest",
            canonical_relative_path=f"modules/{module}/module.yaml",
            contract_schema_version="mozaiks.module.v1",
        ),
        address=ArtifactAddress(
            path_scope=PathScope.APP_BUNDLE_ROOT,
            placeholder_values=(),
            path=f"modules/{module}/module.yaml",
        ),
    )


def _backend_artifact(*, digest: str | None, module: str, file_name: str) -> AccountedArtifact:
    return AccountedArtifact(
        address=ArtifactAddress(
            path_scope=PathScope.APP_BUNDLE_ROOT,
            placeholder_values=(),
            path=f"modules/{module}/backend/{file_name}",
        ),
        content_digest=digest,
    )


async def _stage_module(content_store, pack: str, module: str):
    """Load the real pack module into the content store and build selections."""
    root = _module_root(pack, module)
    manifest_bytes = (root / "module.yaml").read_bytes()
    handler_bytes = (root / "backend" / "handler.py").read_bytes()
    base_bytes = (root / "backend" / "base_handler.py").read_bytes()
    manifest_digest = await _put(content_store, manifest_bytes)
    handler_digest = await _put(content_store, handler_bytes)
    base_digest = await _put(content_store, base_bytes)
    authority = workspace_handler_split_authority_from_pack_contract(
        _pack_contract(pack), module_instance=module
    )
    return {
        "module_selection": _module_selection(digest=manifest_digest, module=module),
        "handler_artifact": _backend_artifact(
            digest=handler_digest, module=module, file_name="handler.py"
        ),
        "base_artifact": _backend_artifact(
            digest=base_digest, module=module, file_name="base_handler.py"
        ),
        "authority": authority,
        "handler_digest": handler_digest,
        "base_digest": base_digest,
    }


def test_census_is_pinned() -> None:
    """The canonical corpus is exactly 10 split modules carrying 63 actions."""
    assert len(SPLIT_MODULES) == EXPECTED_MODULE_COUNT, SPLIT_MODULES
    total_actions = 0
    for pack, module in SPLIT_MODULES:
        manifest = yaml.safe_load(
            (_module_root(pack, module) / "module.yaml").read_text(encoding="utf-8")
        )
        total_actions += len(manifest.get("actions") or [])
    assert total_actions == EXPECTED_ACTION_COUNT


@pytest.mark.parametrize("pack,module", SPLIT_MODULES, ids=SPLIT_IDS)
async def test_every_real_pack_action_certifies(content_store, pack: str, module: str) -> None:
    staged = await _stage_module(content_store, pack, module)
    manifest = yaml.safe_load(
        (_module_root(pack, module) / "module.yaml").read_text(encoding="utf-8")
    )
    action_ids = [action["id"] for action in manifest.get("actions") or []]
    assert action_ids
    for action_id in action_ids:
        resolved = await resolve_module_action_implementation(
            staged["module_selection"],
            staged["handler_artifact"],
            action_id=action_id,
            content_store=content_store,
            requesting_scope=SCOPE,
            base_handler_artifact=staged["base_artifact"],
            split_authority=staged["authority"],
        )
        proof = resolved.export_proof
        assert proof.mode is HandlerCertificationMode.CANONICAL_BASE_HANDLER, (
            f"{pack}/{module}:{action_id} certified as {proof.mode}"
        )
        assert proof.content_digest == staged["handler_digest"]
        assert proof.base_content_digest == staged["base_digest"]
        assert proof.base_handler_class
        assert resolved.request_contract is not None
        assert resolved.base_handler is not None
        assert len(proof.implementation_digest) == 64


async def test_implementation_identity_covers_both_sources(content_store) -> None:
    """Changing ONLY the base — or ONLY the preserved leaf — changes identity."""
    staged = await _stage_module(content_store, "files", "files")
    baseline = await resolve_module_action_implementation(
        staged["module_selection"],
        staged["handler_artifact"],
        action_id="get_file",
        content_store=content_store,
        requesting_scope=SCOPE,
        base_handler_artifact=staged["base_artifact"],
        split_authority=staged["authority"],
    )
    root = _module_root("files", "files")

    changed_base = (root / "backend" / "base_handler.py").read_text(encoding="utf-8") + "\n# regenerated\n"
    changed_base_digest = await _put(content_store, changed_base.encode("utf-8"))
    with_new_base = await resolve_module_action_implementation(
        staged["module_selection"],
        staged["handler_artifact"],
        action_id="get_file",
        content_store=content_store,
        requesting_scope=SCOPE,
        base_handler_artifact=_backend_artifact(
            digest=changed_base_digest, module="files", file_name="base_handler.py"
        ),
        split_authority=staged["authority"],
    )
    changed_leaf = (root / "backend" / "handler.py").read_text(encoding="utf-8") + "\n# workspace note\n"
    changed_leaf_digest = await _put(content_store, changed_leaf.encode("utf-8"))
    with_new_leaf = await resolve_module_action_implementation(
        staged["module_selection"],
        _backend_artifact(digest=changed_leaf_digest, module="files", file_name="handler.py"),
        action_id="get_file",
        content_store=content_store,
        requesting_scope=SCOPE,
        base_handler_artifact=staged["base_artifact"],
        split_authority=staged["authority"],
    )
    identities = {
        baseline.export_proof.implementation_digest,
        with_new_base.export_proof.implementation_digest,
        with_new_leaf.export_proof.implementation_digest,
    }
    assert len(identities) == 3


# ---------------------------------------------------------------------------
# Split authority hostile matrix
# ---------------------------------------------------------------------------


def test_split_authority_requires_canonical_ownership() -> None:
    contract = _pack_contract("files")
    # Wrong leaf ownership cannot establish the split.
    tampered = yaml.safe_load(yaml.safe_dump(contract))
    for entry in tampered["required_outputs"]:
        if entry.get("path") == "modules/files/backend/handler.py":
            entry["owner"] = "templates"
    with pytest.raises(ImplementationArtifactError, match="workspace-owned"):
        workspace_handler_split_authority_from_pack_contract(tampered, module_instance="files")
    # Workspace-owned base cannot establish the split.
    tampered = yaml.safe_load(yaml.safe_dump(contract))
    for entry in tampered["required_outputs"]:
        if entry.get("path") == "modules/files/backend/base_handler.py":
            entry["owner"] = "workspace"
    with pytest.raises(ImplementationArtifactError, match="template-owned"):
        workspace_handler_split_authority_from_pack_contract(tampered, module_instance="files")
    # Absent declarations reject.
    with pytest.raises(ImplementationArtifactError, match="absent"):
        workspace_handler_split_authority_from_pack_contract(
            contract, module_instance="undeclared_module"
        )
    # Non-pack contract types carry no split authority.
    with pytest.raises(ImplementationArtifactError, match="build_pack_instructions"):
        workspace_handler_split_authority_from_pack_contract(
            {"contract_type": "provider_api_contract", "contract_id": "x"},
            module_instance="files",
        )


async def test_base_selection_without_split_authority_rejects(content_store) -> None:
    staged = await _stage_module(content_store, "files", "files")
    with pytest.raises(ImplementationArtifactError, match="split authority"):
        await resolve_module_action_implementation(
            staged["module_selection"],
            staged["handler_artifact"],
            action_id="get_file",
            content_store=content_store,
            requesting_scope=SCOPE,
            base_handler_artifact=staged["base_artifact"],
            split_authority=None,
        )


async def test_without_base_selection_the_single_source_rejection_is_preserved(
    content_store,
) -> None:
    staged = await _stage_module(content_store, "files", "files")
    with pytest.raises(ImplementationArtifactError, match="not explicitly defined"):
        await resolve_module_action_implementation(
            staged["module_selection"],
            staged["handler_artifact"],
            action_id="get_file",
            content_store=content_store,
            requesting_scope=SCOPE,
        )


async def test_base_content_hostiles_reject(content_store, tmp_path) -> None:
    staged = await _stage_module(content_store, "files", "files")

    async def _resolve(base_artifact, authority=None):
        return await resolve_module_action_implementation(
            staged["module_selection"],
            staged["handler_artifact"],
            action_id="get_file",
            content_store=content_store,
            requesting_scope=SCOPE,
            base_handler_artifact=base_artifact,
            split_authority=authority or staged["authority"],
        )

    # Missing digest.
    with pytest.raises(ImplementationArtifactError, match="non-null content digest"):
        await _resolve(_backend_artifact(digest=None, module="files", file_name="base_handler.py"))

    # Missing bytes (stale ref after base regeneration).
    absent = "e" * 64
    with pytest.raises(ImplementationArtifactError, match="did not resolve exactly"):
        await _resolve(
            _backend_artifact(digest=absent, module="files", file_name="base_handler.py")
        )

    # Tampered bytes under a valid digest path.
    digest = hashlib.sha256(b"real base bytes").hexdigest()
    blob_path = tmp_path / "sha256" / digest[:2] / digest
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(b"tampered base bytes")
    with pytest.raises(ImplementationArtifactError, match="did not resolve exactly"):
        await _resolve(
            _backend_artifact(digest=digest, module="files", file_name="base_handler.py")
        )

    # Base from another module instance.
    with pytest.raises(ImplementationArtifactError, match="cannot implement module"):
        await _resolve(
            _backend_artifact(
                digest=staged["base_digest"], module="messages", file_name="base_handler.py"
            )
        )

    # Base path outside the canonical family.
    with pytest.raises(ImplementationArtifactError, match="not 'module_backend_base_handler'"):
        await _resolve(
            _backend_artifact(
                digest=staged["base_digest"], module="files", file_name="service.py"
            )
        )

    # Authority borrowed from another module.
    other_authority = workspace_handler_split_authority_from_pack_contract(
        _pack_contract("messaging"), module_instance="messages"
    )
    with pytest.raises(ImplementationArtifactError, match="cannot certify module"):
        await _resolve(staged["base_artifact"], authority=other_authority)

    # Scope mismatch between leaf and base selections: the module-relative
    # base row is unregistered today, so the mismatch rejects at the layout
    # proof; the explicit same-scope rule remains as defense-in-depth.
    module_scope_base = AccountedArtifact(
        address=ArtifactAddress(
            path_scope=PathScope.MODULE_RELATIVE,
            placeholder_values=(("module_id", "files"),),
            path="backend/base_handler.py",
        ),
        content_digest=staged["base_digest"],
    )
    with pytest.raises(
        ImplementationArtifactError, match="no canonical layout row|same module scope"
    ):
        await _resolve(module_scope_base)


# ---------------------------------------------------------------------------
# Bounded AST hostile matrix for the split grammar
# ---------------------------------------------------------------------------


BASE_SOURCE = (
    "class FilesBaseHandler:\n"
    "    async def get_file(self, ctx, file_id=None):\n"
    "        return {\"file\": None}\n"
)


async def _certify_sources(
    content_store, leaf_source: str, base_source: str = BASE_SOURCE, *, action_id: str = "get_file"
):
    staged = await _stage_module(content_store, "files", "files")
    leaf_digest = await _put(content_store, leaf_source.encode("utf-8"))
    base_digest = await _put(content_store, base_source.encode("utf-8"))
    return await resolve_module_action_implementation(
        staged["module_selection"],
        _backend_artifact(digest=leaf_digest, module="files", file_name="handler.py"),
        action_id=action_id,
        content_store=content_store,
        requesting_scope=SCOPE,
        base_handler_artifact=_backend_artifact(
            digest=base_digest, module="files", file_name="base_handler.py"
        ),
        split_authority=staged["authority"],
    )


CANONICAL_LEAF = (
    "from .base_handler import FilesBaseHandler\n\n\n"
    "class FilesHandler(FilesBaseHandler):\n"
    "    \"\"\"Preserved workspace leaf.\"\"\"\n"
)


async def test_canonical_minimal_split_certifies(content_store) -> None:
    resolved = await _certify_sources(content_store, CANONICAL_LEAF)
    assert resolved.export_proof.mode is HandlerCertificationMode.CANONICAL_BASE_HANDLER
    assert resolved.export_proof.base_handler_class == "FilesBaseHandler"


async def test_leaf_override_certifies_as_explicit_handler(content_store) -> None:
    leaf = (
        "from .base_handler import FilesBaseHandler\n\n\n"
        "class FilesHandler(FilesBaseHandler):\n"
        "    async def get_file(self, ctx, file_id=None):\n"
        "        return {\"file\": {\"overridden\": True}}\n"
    )
    resolved = await _certify_sources(content_store, leaf)
    assert resolved.export_proof.mode is HandlerCertificationMode.EXPLICIT_HANDLER
    assert resolved.export_proof.base_content_digest is None


@pytest.mark.parametrize(
    "leaf,reason",
    [
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "class OtherMixin:\n    pass\n"
            "class FilesHandler(FilesBaseHandler, OtherMixin):\n    pass\n",
            "exactly one base|multiple inheritance",
            id="multiple-inheritance-mixin",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler.__class__):\n    pass\n",
            "plain imported name",
            id="non-name-base",
        ),
        pytest.param(
            "from .other_module import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "from .base_handler import",
            id="base-imported-from-wrong-module",
        ),
        pytest.param(
            "from .base_handler import WrongBase as FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "from .base_handler import",
            id="aliased-import",
        ),
        pytest.param(
            "from .base_handler import *\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "star imports",
            id="star-import",
        ),
        pytest.param(
            "import importlib\n"
            "FilesBaseHandler = importlib.import_module('x').Base\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "rebound outside its canonical import",
            id="dynamic-import",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "if True:\n"
            "    class FilesHandler(FilesBaseHandler):\n        pass\n",
            "conditionally",
            id="conditional-class",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            "FilesHandler.get_file = None\n",
            "rebound|referenced dynamically",
            id="monkeypatch",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            "setattr(FilesHandler, 'get_file', None)\n",
            "not statically provable|referenced dynamically",
            id="setattr",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "def __getattr__(name):\n    raise AttributeError(name)\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "__getattr__",
            id="module-getattr",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            "class _Shadow:\n    pass\n"
            "FilesHandler = _Shadow\n",
            "more than once|rebound",
            id="class-rebinding",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "AliasBase = FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "referenced only as the leaf's single base",
            id="base-alias-reference",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n"
            "    get_file = FilesBaseHandler\n",
            "bound dynamically|referenced only",
            id="leaf-method-rebinding",
        ),
    ],
)
async def test_leaf_split_grammar_hostiles_reject(content_store, leaf: str, reason: str) -> None:
    with pytest.raises(ImplementationArtifactError, match=reason):
        await _certify_sources(content_store, leaf)


@pytest.mark.parametrize(
    "base,reason",
    [
        pytest.param(
            "class WrongBase:\n"
            "    async def get_file(self, ctx):\n        return {}\n",
            "absent from the selected base-handler source",
            id="base-class-missing",
        ),
        pytest.param(
            "class FilesBaseHandler:\n    pass\n"
            "class Helper:\n"
            "    async def get_file(self, ctx):\n        return {}\n",
            "not explicitly defined on canonical base class",
            id="method-on-wrong-class",
        ),
        pytest.param(
            "class FilesBaseHandler:\n    pass\n",
            "not explicitly defined on canonical base class",
            id="base-method-missing",
        ),
        pytest.param(
            "class Grandparent:\n"
            "    async def get_file(self, ctx):\n        return {}\n"
            "class FilesBaseHandler(Grandparent):\n    pass\n",
            "must not inherit",
            id="transitive-inheritance",
        ),
        pytest.param(
            "class FilesBaseHandler:\n"
            "    async def get_file(self, ctx):\n        return {}\n"
            "FilesBaseHandler.get_file = None\n",
            "rebound|referenced dynamically",
            id="base-monkeypatch",
        ),
        pytest.param(
            "class FilesBaseHandler:\n"
            "    def __getattr__(self, name):\n        raise AttributeError(name)\n"
            "    async def get_file(self, ctx):\n        return {}\n",
            "__getattr__",
            id="base-class-getattr",
        ),
        pytest.param(
            "class FilesBaseHandler:\n"
            "    if True:\n"
            "        async def get_file(self, ctx):\n            return {}\n",
            "conditionally",
            id="base-conditional-method",
        ),
    ],
)
async def test_base_split_grammar_hostiles_reject(content_store, base: str, reason: str) -> None:
    with pytest.raises(ImplementationArtifactError, match=reason):
        await _certify_sources(content_store, CANONICAL_LEAF, base)
