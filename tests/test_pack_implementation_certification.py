"""Real canonical capability-pack implementation certification.

Every workspace_handler_split pack module certifies through the bounded
CANONICAL_BASE_HANDLER mode: the preserved workspace-owned ``handler.py``
leaf directly subclasses the regenerated template-owned ``base_handler.py``
class.  Exactly those two verified sources participate — no general Python
source closure — and the certified implementation identity covers BOTH
source digests plus the exact pack-contract digest that authorized the
split.

The split authority itself is content-resolved: the caller supplies only the
exact scope-bound pack-contract selection, and every ownership fact is
derived from digest-verified bytes.  There is no caller-constructible
authority token anywhere on the public resolution API.

The corpus is discovered independently from the checked-in factory packs and
pinned: 10 modules, 63 actions, all certifying end to end through
``resolve_module_action_implementation`` with exact verified bytes for the
manifest, leaf, base, AND pack contract (the blob-read count proves the
contract bytes are actually read), and the #484 closed request import.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.artifacts.content_store import LocalArtifactContentStore
from mozaiksai.core.runtime.app.layout_registry import PathScope
from mozaiksai.core.semantics.composition_ledger import AccountedArtifact, ArtifactAddress
from mozaiksai.core.semantics.implementation_artifacts import (
    HandlerCertificationMode,
    HandlerMethodSource,
    ImplementationArtifactError,
    SelectedAccountedArtifact,
    SelectedContractArtifact,
    resolve_module_action_implementation,
    resolve_module_base_handler_source,
    resolve_module_manifest_artifact,
    resolve_workspace_handler_split_authority,
)
from mozaiksai.core.semantics.refs import ChildContractRef, ExecutionAccessScopeRef

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"

SCOPE = ExecutionAccessScopeRef(tenant_id="tenant", workspace_id="workspace")
OTHER_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant", workspace_id="elsewhere")
OTHER_TENANT = ExecutionAccessScopeRef(tenant_id="rival", workspace_id="workspace")

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


def _pack_contract_bytes(pack: str) -> bytes:
    return (BUILD_CONTEXT / pack / "contract.yaml").read_bytes()


class _CountingContentStore(LocalArtifactContentStore):
    """Records every verified blob read so tests can prove reads happen."""

    def __init__(self, *, root) -> None:
        super().__init__(root=root)
        self.verified_reads: list[str] = []

    async def get_verified_blob(self, digest: str) -> bytes:
        self.verified_reads.append(digest)
        return await super().get_verified_blob(digest)


@pytest.fixture
def content_store(tmp_path):
    return _CountingContentStore(root=tmp_path)


async def _put(content_store, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    await content_store.put_blob(data, expected_digest=digest)
    return digest


# Source selections use APP_BUNDLE_ROOT: the canonical layout registry
# registers the module_backend_base_handler family at app scope (the
# module-relative sibling row does not exist yet — a registry asymmetry
# outside this slice's scope).  Pack-contract selections are bounded
# workspace-root build-context inputs.
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


def _source_selection(
    *,
    digest: str | None,
    module: str,
    file_name: str,
    scope: ExecutionAccessScopeRef = SCOPE,
) -> SelectedAccountedArtifact:
    return SelectedAccountedArtifact(
        scope=scope,
        artifact=AccountedArtifact(
            address=ArtifactAddress(
                path_scope=PathScope.APP_BUNDLE_ROOT,
                placeholder_values=(),
                path=f"modules/{module}/backend/{file_name}",
            ),
            content_digest=digest,
        ),
    )


def _contract_selection(
    *,
    digest: str | None,
    pack: str,
    scope: ExecutionAccessScopeRef = SCOPE,
    path: str | None = None,
    path_scope: PathScope = PathScope.WORKSPACE_ROOT,
) -> SelectedAccountedArtifact:
    return SelectedAccountedArtifact(
        scope=scope,
        artifact=AccountedArtifact(
            address=ArtifactAddress(
                path_scope=path_scope,
                placeholder_values=(),
                path=path or f"build_context/{pack}/contract.yaml",
            ),
            content_digest=digest,
        ),
    )


async def _stage_module(content_store, pack: str, module: str):
    """Load the real pack module AND its pack contract into the content store."""
    root = _module_root(pack, module)
    manifest_digest = await _put(content_store, (root / "module.yaml").read_bytes())
    handler_digest = await _put(content_store, (root / "backend" / "handler.py").read_bytes())
    base_digest = await _put(content_store, (root / "backend" / "base_handler.py").read_bytes())
    contract_digest = await _put(content_store, _pack_contract_bytes(pack))
    return {
        "module_selection": _module_selection(digest=manifest_digest, module=module),
        "handler_selection": _source_selection(
            digest=handler_digest, module=module, file_name="handler.py"
        ),
        "base_selection": _source_selection(
            digest=base_digest, module=module, file_name="base_handler.py"
        ),
        "contract_selection": _contract_selection(digest=contract_digest, pack=pack),
        "handler_digest": handler_digest,
        "base_digest": base_digest,
        "contract_digest": contract_digest,
    }


async def _resolved_module_for(content_store, staged):
    return await resolve_module_manifest_artifact(
        staged["module_selection"],
        content_store=content_store,
        requesting_scope=SCOPE,
    )


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


def test_public_resolution_accepts_no_authority_token() -> None:
    """The public resolution APIs take pack-contract selections, never a
    preconstructed ownership result — fabricated authority strings have no
    entry point."""
    aggregate = inspect.signature(resolve_module_action_implementation)
    assert "pack_contract_selection" in aggregate.parameters
    assert "split_authority" not in aggregate.parameters
    base = inspect.signature(resolve_module_base_handler_source)
    assert "pack_contract_selection" in base.parameters
    assert "split_authority" not in base.parameters


def test_public_surface_has_no_fabricated_authority_entry_point() -> None:
    """No exported function accepts a ResolvedWorkspaceHandlerSplitAuthority:
    the split-capable certifier is internal, prove_module_action_export stays
    the only standalone proof helper (zero-base EXPLICIT_HANDLER), and
    resolve_module_action_implementation is the public authority boundary."""
    import mozaiksai.core.semantics.implementation_artifacts as impl

    assert "certify_module_action_export" not in impl.__all__
    assert not hasattr(impl, "certify_module_action_export")
    assert "prove_module_action_export" in impl.__all__
    assert "resolve_module_action_implementation" in impl.__all__
    for name in impl.__all__:
        exported = getattr(impl, name)
        if callable(exported) and not isinstance(exported, type):
            for parameter in inspect.signature(exported).parameters.values():
                assert "ResolvedWorkspaceHandlerSplitAuthority" not in str(
                    parameter.annotation
                ), (name, parameter.name)


@pytest.mark.parametrize("pack,module", SPLIT_MODULES, ids=SPLIT_IDS)
async def test_every_real_pack_action_certifies(content_store, pack: str, module: str) -> None:
    staged = await _stage_module(content_store, pack, module)
    manifest = yaml.safe_load(
        (_module_root(pack, module) / "module.yaml").read_text(encoding="utf-8")
    )
    action_ids = [action["id"] for action in manifest.get("actions") or []]
    assert action_ids
    for action_id in action_ids:
        reads_before = len(content_store.verified_reads)
        resolved = await resolve_module_action_implementation(
            staged["module_selection"],
            staged["handler_selection"],
            action_id=action_id,
            content_store=content_store,
            requesting_scope=SCOPE,
            base_handler_selection=staged["base_selection"],
            pack_contract_selection=staged["contract_selection"],
        )
        proof = resolved.export_proof
        assert proof.mode is HandlerCertificationMode.CANONICAL_BASE_HANDLER, (
            f"{pack}/{module}:{action_id} certified as {proof.mode}"
        )
        # No checked-in pack leaf overrides an action today.
        assert proof.method_source is HandlerMethodSource.BASE_HANDLER
        assert proof.content_digest == staged["handler_digest"]
        assert proof.base_content_digest == staged["base_digest"]
        assert proof.split_contract_content_digest == staged["contract_digest"]
        assert proof.base_handler_class
        assert resolved.request_contract is not None
        assert resolved.base_handler is not None
        assert resolved.split_authority is not None
        assert resolved.split_authority.contract_id == pack
        assert resolved.split_authority.contract_content_digest == staged["contract_digest"]
        assert len(proof.implementation_digest) == 64
        # The certification actually read the pack-contract bytes, plus the
        # manifest, leaf, and base bytes, through the verified blob store.
        reads = content_store.verified_reads[reads_before:]
        assert staged["contract_digest"] in reads
        assert staged["handler_digest"] in reads
        assert staged["base_digest"] in reads


async def test_implementation_identity_covers_both_sources_and_authority(content_store) -> None:
    """Changing ONLY the base, ONLY the preserved leaf, or ONLY the pack
    contract changes the certified identity."""
    staged = await _stage_module(content_store, "files", "files")

    async def _resolve(
        *, handler_selection=None, base_selection=None, contract_selection=None
    ):
        return await resolve_module_action_implementation(
            staged["module_selection"],
            handler_selection or staged["handler_selection"],
            action_id="get_file",
            content_store=content_store,
            requesting_scope=SCOPE,
            base_handler_selection=base_selection or staged["base_selection"],
            pack_contract_selection=contract_selection or staged["contract_selection"],
        )

    baseline = await _resolve()
    root = _module_root("files", "files")

    changed_base = (
        (root / "backend" / "base_handler.py").read_text(encoding="utf-8") + "\n# regenerated\n"
    )
    changed_base_digest = await _put(content_store, changed_base.encode("utf-8"))
    with_new_base = await _resolve(
        base_selection=_source_selection(
            digest=changed_base_digest, module="files", file_name="base_handler.py"
        )
    )

    changed_leaf = (
        (root / "backend" / "handler.py").read_text(encoding="utf-8") + "\n# workspace note\n"
    )
    changed_leaf_digest = await _put(content_store, changed_leaf.encode("utf-8"))
    with_new_leaf = await _resolve(
        handler_selection=_source_selection(
            digest=changed_leaf_digest, module="files", file_name="handler.py"
        )
    )

    changed_contract = _pack_contract_bytes("files") + b"\n# authority revision\n"
    changed_contract_digest = await _put(content_store, changed_contract)
    with_new_contract = await _resolve(
        contract_selection=_contract_selection(digest=changed_contract_digest, pack="files")
    )

    identities = {
        baseline.export_proof.implementation_digest,
        with_new_base.export_proof.implementation_digest,
        with_new_leaf.export_proof.implementation_digest,
        with_new_contract.export_proof.implementation_digest,
    }
    assert len(identities) == 4


# ---------------------------------------------------------------------------
# Content-resolved split authority hostile matrix
# ---------------------------------------------------------------------------


async def _resolve_authority(content_store, staged, contract_selection):
    module = await _resolved_module_for(content_store, staged)
    return await resolve_workspace_handler_split_authority(
        contract_selection,
        module=module,
        content_store=content_store,
        requesting_scope=SCOPE,
    )


async def _stage_tampered_contract(content_store, pack: str, mutate) -> str:
    document = yaml.safe_load(_pack_contract_bytes(pack).decode("utf-8"))
    mutate(document)
    return await _put(
        content_store, yaml.safe_dump(document, sort_keys=False).encode("utf-8")
    )


async def test_split_authority_resolves_from_exact_contract_bytes(content_store) -> None:
    staged = await _stage_module(content_store, "files", "files")
    authority = await _resolve_authority(content_store, staged, staged["contract_selection"])
    assert authority.contract_id == "files"
    assert authority.module_instance == "files"
    assert authority.contract_content_digest == staged["contract_digest"]
    assert authority.handler_path == "modules/files/backend/handler.py"
    assert authority.base_handler_path == "modules/files/backend/base_handler.py"
    assert staged["contract_digest"] in content_store.verified_reads


async def test_split_authority_ownership_hostiles_reject(content_store) -> None:
    staged = await _stage_module(content_store, "files", "files")

    def _set_owner(path: str, owner: str):
        def mutate(document):
            for entry in document["required_outputs"]:
                if entry.get("path") == path:
                    entry["owner"] = owner

        return mutate

    # Wrong leaf ownership cannot establish the split.
    digest = await _stage_tampered_contract(
        content_store, "files", _set_owner("modules/files/backend/handler.py", "templates")
    )
    with pytest.raises(ImplementationArtifactError, match="workspace-owned"):
        await _resolve_authority(
            content_store, staged, _contract_selection(digest=digest, pack="files")
        )

    # Workspace-owned base cannot establish the split.
    digest = await _stage_tampered_contract(
        content_store,
        "files",
        _set_owner("modules/files/backend/base_handler.py", "workspace"),
    )
    with pytest.raises(ImplementationArtifactError, match="template-owned"):
        await _resolve_authority(
            content_store, staged, _contract_selection(digest=digest, pack="files")
        )

    # A contract that does not declare the module manifest owns nothing here.
    def _drop_manifest(document):
        document["required_outputs"] = [
            entry
            for entry in document["required_outputs"]
            if entry.get("path") != "modules/files/module.yaml"
        ]

    digest = await _stage_tampered_contract(content_store, "files", _drop_manifest)
    with pytest.raises(ImplementationArtifactError, match="module.yaml.*absent"):
        await _resolve_authority(
            content_store, staged, _contract_selection(digest=digest, pack="files")
        )

    # An unrelated pack contract does not own this module at all.
    messaging_digest = await _put(content_store, _pack_contract_bytes("messaging"))
    with pytest.raises(ImplementationArtifactError, match="absent"):
        await _resolve_authority(
            content_store,
            staged,
            _contract_selection(digest=messaging_digest, pack="messaging"),
        )

    # Non-pack contract types carry no split authority.
    non_pack = yaml.safe_dump(
        {"contract_type": "provider_api_contract", "contract_id": "files"}
    ).encode("utf-8")
    digest = await _put(content_store, non_pack)
    with pytest.raises(ImplementationArtifactError, match="build_pack_instructions"):
        await _resolve_authority(
            content_store, staged, _contract_selection(digest=digest, pack="files")
        )


async def test_split_authority_content_hostiles_reject(content_store, tmp_path) -> None:
    staged = await _stage_module(content_store, "files", "files")

    # Absent digest: the selection carries no content identity.
    with pytest.raises(ImplementationArtifactError, match="non-null content digest"):
        await _resolve_authority(
            content_store, staged, _contract_selection(digest=None, pack="files")
        )

    # Correct filename, no exact bytes behind the digest.
    with pytest.raises(ImplementationArtifactError, match="did not resolve exactly"):
        await _resolve_authority(
            content_store, staged, _contract_selection(digest="e" * 64, pack="files")
        )

    # Tampered bytes under a valid digest path.
    digest = hashlib.sha256(b"real contract bytes").hexdigest()
    blob_path = tmp_path / "sha256" / digest[:2] / digest
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(b"tampered contract bytes")
    with pytest.raises(ImplementationArtifactError, match="did not resolve exactly"):
        await _resolve_authority(
            content_store, staged, _contract_selection(digest=digest, pack="files")
        )

    # Correct filename whose bytes are not a pack contract document.
    with pytest.raises(
        ImplementationArtifactError, match="not valid YAML|mapping|build_pack_instructions"
    ):
        await _resolve_authority(
            content_store,
            staged,
            _contract_selection(digest=staged["base_digest"], pack="files"),
        )

    # The address must equal the canonical path for the contract id the bytes
    # declare — the files contract cannot be smuggled under another pack path.
    with pytest.raises(ImplementationArtifactError, match="lives at"):
        await _resolve_authority(
            content_store,
            staged,
            _contract_selection(
                digest=staged["contract_digest"],
                pack="files",
                path="build_context/messaging/contract.yaml",
            ),
        )

    # Pack contracts are workspace-root build-context inputs, not app-bundle
    # artifacts.
    with pytest.raises(ImplementationArtifactError, match="workspace-root build-context"):
        await _resolve_authority(
            content_store,
            staged,
            _contract_selection(
                digest=staged["contract_digest"],
                pack="files",
                path_scope=PathScope.APP_BUNDLE_ROOT,
            ),
        )


async def test_standalone_base_resolver_derives_authority_from_contract_bytes(
    content_store,
) -> None:
    """The public base resolver takes the pack-contract selection and derives
    authority internally — and refuses a contract smuggled under another
    pack's canonical path."""
    staged = await _stage_module(content_store, "files", "files")
    module = await _resolved_module_for(content_store, staged)
    from mozaiksai.core.semantics.implementation_artifacts import (
        resolve_module_handler_source,
    )

    handler = await resolve_module_handler_source(
        staged["handler_selection"],
        module=module,
        content_store=content_store,
        requesting_scope=SCOPE,
    )
    base = await resolve_module_base_handler_source(
        staged["base_selection"],
        module=module,
        handler=handler,
        pack_contract_selection=staged["contract_selection"],
        content_store=content_store,
        requesting_scope=SCOPE,
    )
    assert base.module_instance == "files"
    assert base.content_digest == staged["base_digest"]
    assert staged["contract_digest"] in content_store.verified_reads
    with pytest.raises(ImplementationArtifactError, match="lives at"):
        await resolve_module_base_handler_source(
            staged["base_selection"],
            module=module,
            handler=handler,
            pack_contract_selection=_contract_selection(
                digest=staged["contract_digest"],
                pack="files",
                path="build_context/messaging/contract.yaml",
            ),
            content_store=content_store,
            requesting_scope=SCOPE,
        )


async def test_ambiguous_or_implicit_required_outputs_reject(content_store) -> None:
    """The verified pack-contract bytes must be unambiguous at this boundary:
    duplicate required_outputs paths reject globally, and authority-relevant
    ownership must be explicit — omitted, null, or empty owners never default."""
    staged = await _stage_module(content_store, "files", "files")
    handler_path = "modules/files/backend/handler.py"
    base_path = "modules/files/backend/base_handler.py"
    manifest_path = "modules/files/module.yaml"

    def _append_dup(path: str, owner: str):
        def mutate(document):
            document["required_outputs"].append({"path": path, "owner": owner})

        return mutate

    def _prepend_dup(path: str, owner: str):
        def mutate(document):
            document["required_outputs"].insert(0, {"path": path, "owner": owner})

        return mutate

    duplicate_cases = [
        _append_dup(handler_path, "templates"),  # workspace then templates
        _prepend_dup(handler_path, "templates"),  # templates then workspace
        _append_dup(base_path, "workspace"),
        _append_dup(manifest_path, "templates"),
        # ANY duplicate path is structurally ambiguous, relevant or not.
        _append_dup("modules/files/backend/service.py", "templates"),
    ]
    for mutate in duplicate_cases:
        digest = await _stage_tampered_contract(content_store, "files", mutate)
        with pytest.raises(ImplementationArtifactError, match="more than once"):
            await _resolve_authority(
                content_store, staged, _contract_selection(digest=digest, pack="files")
            )

    def _strip_owner(path: str):
        def mutate(document):
            for entry in document["required_outputs"]:
                if entry.get("path") == path:
                    entry.pop("owner", None)

        return mutate

    def _set_owner_value(path: str, value):
        def mutate(document):
            for entry in document["required_outputs"]:
                if entry.get("path") == path:
                    entry["owner"] = value

        return mutate

    implicit_cases = [
        _strip_owner(handler_path),
        _strip_owner(base_path),
        _strip_owner(manifest_path),
        _set_owner_value(handler_path, None),
        _set_owner_value(base_path, ""),
    ]
    for mutate in implicit_cases:
        digest = await _stage_tampered_contract(content_store, "files", mutate)
        with pytest.raises(ImplementationArtifactError, match="explicit owner"):
            await _resolve_authority(
                content_store, staged, _contract_selection(digest=digest, pack="files")
            )

    # The manifest owner must be the exact canonical owner, not merely present.
    digest = await _stage_tampered_contract(
        content_store, "files", _set_owner_value(manifest_path, "workspace")
    )
    with pytest.raises(ImplementationArtifactError, match="canonical manifest owner"):
        await _resolve_authority(
            content_store, staged, _contract_selection(digest=digest, pack="files")
        )


async def test_split_certification_requires_both_selections_together(content_store) -> None:
    staged = await _stage_module(content_store, "files", "files")
    with pytest.raises(ImplementationArtifactError, match="together"):
        await resolve_module_action_implementation(
            staged["module_selection"],
            staged["handler_selection"],
            action_id="get_file",
            content_store=content_store,
            requesting_scope=SCOPE,
            base_handler_selection=staged["base_selection"],
        )
    with pytest.raises(ImplementationArtifactError, match="together"):
        await resolve_module_action_implementation(
            staged["module_selection"],
            staged["handler_selection"],
            action_id="get_file",
            content_store=content_store,
            requesting_scope=SCOPE,
            pack_contract_selection=staged["contract_selection"],
        )


async def test_without_base_selection_the_standalone_rejection_is_preserved(
    content_store,
) -> None:
    staged = await _stage_module(content_store, "files", "files")
    with pytest.raises(ImplementationArtifactError, match="zero bases"):
        await resolve_module_action_implementation(
            staged["module_selection"],
            staged["handler_selection"],
            action_id="get_file",
            content_store=content_store,
            requesting_scope=SCOPE,
        )


async def test_cross_scope_source_selections_reject(content_store) -> None:
    """Same module id and same bytes under another scope fail closed for the
    leaf, the base, and the pack contract."""
    staged = await _stage_module(content_store, "files", "files")

    async def _resolve(
        *, handler_selection=None, base_selection=None, contract_selection=None
    ):
        return await resolve_module_action_implementation(
            staged["module_selection"],
            handler_selection or staged["handler_selection"],
            action_id="get_file",
            content_store=content_store,
            requesting_scope=SCOPE,
            base_handler_selection=base_selection or staged["base_selection"],
            pack_contract_selection=contract_selection or staged["contract_selection"],
        )

    scope_error = "cross-scope|module selection's execution scope"
    for hostile_scope in (OTHER_SCOPE, OTHER_TENANT):
        with pytest.raises(ImplementationArtifactError, match=scope_error):
            await _resolve(
                handler_selection=_source_selection(
                    digest=staged["handler_digest"],
                    module="files",
                    file_name="handler.py",
                    scope=hostile_scope,
                )
            )
        # Leaf in the correct scope, base selected from another scope.
        with pytest.raises(ImplementationArtifactError, match=scope_error):
            await _resolve(
                base_selection=_source_selection(
                    digest=staged["base_digest"],
                    module="files",
                    file_name="base_handler.py",
                    scope=hostile_scope,
                )
            )
        # Correct sources, pack contract selected from another scope.
        with pytest.raises(ImplementationArtifactError, match=scope_error):
            await _resolve(
                contract_selection=_contract_selection(
                    digest=staged["contract_digest"], pack="files", scope=hostile_scope
                )
            )


async def test_base_content_hostiles_reject(content_store, tmp_path) -> None:
    staged = await _stage_module(content_store, "files", "files")

    async def _resolve(base_selection, contract_selection=None):
        return await resolve_module_action_implementation(
            staged["module_selection"],
            staged["handler_selection"],
            action_id="get_file",
            content_store=content_store,
            requesting_scope=SCOPE,
            base_handler_selection=base_selection,
            pack_contract_selection=contract_selection or staged["contract_selection"],
        )

    # Missing digest.
    with pytest.raises(ImplementationArtifactError, match="non-null content digest"):
        await _resolve(
            _source_selection(digest=None, module="files", file_name="base_handler.py")
        )

    # Missing bytes (stale ref after base regeneration).
    with pytest.raises(ImplementationArtifactError, match="did not resolve exactly"):
        await _resolve(
            _source_selection(digest="e" * 64, module="files", file_name="base_handler.py")
        )

    # Tampered bytes under a valid digest path.
    digest = hashlib.sha256(b"real base bytes").hexdigest()
    blob_path = tmp_path / "sha256" / digest[:2] / digest
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(b"tampered base bytes")
    with pytest.raises(ImplementationArtifactError, match="did not resolve exactly"):
        await _resolve(
            _source_selection(digest=digest, module="files", file_name="base_handler.py")
        )

    # Base from another module instance.
    with pytest.raises(ImplementationArtifactError, match="cannot implement module"):
        await _resolve(
            _source_selection(
                digest=staged["base_digest"], module="messages", file_name="base_handler.py"
            )
        )

    # Base path outside the canonical family.
    with pytest.raises(ImplementationArtifactError, match="not 'module_backend_base_handler'"):
        await _resolve(
            _source_selection(
                digest=staged["base_digest"], module="files", file_name="service.py"
            )
        )

    # Authority borrowed from another module's pack contract.
    messaging_digest = await _put(content_store, _pack_contract_bytes("messaging"))
    with pytest.raises(ImplementationArtifactError, match="absent"):
        await _resolve(
            staged["base_selection"],
            contract_selection=_contract_selection(digest=messaging_digest, pack="messaging"),
        )

    # Scope mismatch between leaf and base selections: the module-relative
    # base row is unregistered today, so the mismatch rejects at the layout
    # proof; the explicit same-scope rule remains as defense-in-depth.
    module_scope_base = SelectedAccountedArtifact(
        scope=SCOPE,
        artifact=AccountedArtifact(
            address=ArtifactAddress(
                path_scope=PathScope.MODULE_RELATIVE,
                placeholder_values=(("module_id", "files"),),
                path="backend/base_handler.py",
            ),
            content_digest=staged["base_digest"],
        ),
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
        _source_selection(digest=leaf_digest, module="files", file_name="handler.py"),
        action_id=action_id,
        content_store=content_store,
        requesting_scope=SCOPE,
        base_handler_selection=_source_selection(
            digest=base_digest, module="files", file_name="base_handler.py"
        ),
        pack_contract_selection=staged["contract_selection"],
    )


CANONICAL_LEAF = (
    "from .base_handler import FilesBaseHandler\n\n\n"
    "class FilesHandler(FilesBaseHandler):\n"
    "    \"\"\"Preserved workspace leaf.\"\"\"\n"
)

OVERRIDE_LEAF = (
    "from .base_handler import FilesBaseHandler\n\n\n"
    "class FilesHandler(FilesBaseHandler):\n"
    "    async def get_file(self, ctx, file_id=None):\n"
    "        return {\"file\": {\"overridden\": True}}\n"
)


async def test_canonical_minimal_split_certifies(content_store) -> None:
    resolved = await _certify_sources(content_store, CANONICAL_LEAF)
    proof = resolved.export_proof
    assert proof.mode is HandlerCertificationMode.CANONICAL_BASE_HANDLER
    assert proof.method_source is HandlerMethodSource.BASE_HANDLER
    assert proof.base_handler_class == "FilesBaseHandler"
    assert proof.split_contract_content_digest is not None


async def test_split_leaf_override_stays_two_source(content_store) -> None:
    """A canonical split leaf override is still a CANONICAL_BASE_HANDLER
    certification: both source digests and the pack-contract digest stay
    meaning-bearing; only the method source moves to the leaf."""
    resolved = await _certify_sources(content_store, OVERRIDE_LEAF)
    proof = resolved.export_proof
    assert proof.mode is HandlerCertificationMode.CANONICAL_BASE_HANDLER
    assert proof.method_source is HandlerMethodSource.HANDLER
    assert proof.base_content_digest is not None
    assert proof.split_contract_content_digest is not None
    assert resolved.base_handler is not None
    assert resolved.split_authority is not None


async def test_leaf_override_certifies_even_when_base_lacks_the_method(content_store) -> None:
    base_without_method = "class FilesBaseHandler:\n    pass\n"
    resolved = await _certify_sources(content_store, OVERRIDE_LEAF, base_without_method)
    proof = resolved.export_proof
    assert proof.mode is HandlerCertificationMode.CANONICAL_BASE_HANDLER
    assert proof.method_source is HandlerMethodSource.HANDLER


async def test_override_and_inherited_certifications_have_distinct_identity(
    content_store,
) -> None:
    inherited = await _certify_sources(content_store, CANONICAL_LEAF)
    overridden = await _certify_sources(content_store, OVERRIDE_LEAF)
    assert (
        inherited.export_proof.implementation_digest
        != overridden.export_proof.implementation_digest
    )


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
            "if True:\n"
            "    from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "unconditional top-level",
            id="conditional-import",
        ),
        pytest.param(
            "try:\n"
            "    from .base_handler import FilesBaseHandler\n"
            "finally:\n"
            "    pass\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "unconditional top-level",
            id="try-import",
        ),
        pytest.param(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "unconditional top-level",
            id="type-checking-import",
        ),
        pytest.param(
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            "from .base_handler import FilesBaseHandler\n",
            "before the handler class definition",
            id="import-after-class",
        ),
        pytest.param(
            "def _load():\n"
            "    from .base_handler import FilesBaseHandler\n"
            "    return FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "exactly one",
            id="nested-function-import",
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
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "try:\n    pass\n"
            "except Exception as FilesBaseHandler:\n    pass\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "rebound outside its canonical import",
            id="except-capture-of-base",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            "match {}:\n"
            "    case {\"x\": FilesHandler}:\n        pass\n    case _:\n        pass\n",
            "rebound|referenced dynamically",
            id="match-capture-of-handler",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "match []:\n"
            "    case [*FilesBaseHandler]:\n        pass\n    case _:\n        pass\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "rebound outside its canonical import",
            id="match-star-capture-of-base",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "match {}:\n"
            "    case {\"x\": _v, **FilesBaseHandler}:\n        pass\n    case _:\n        pass\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n",
            "rebound outside its canonical import",
            id="match-mapping-rest-capture-of-base",
        ),
        pytest.param(
            "import builtins\n"
            "from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            'builtins.exec("FilesHandler = 1")\n',
            "accesses the builtins module",
            id="leaf-builtins-exec",
        ),
        pytest.param(
            "from builtins import exec as e\n"
            "from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            'e("FilesHandler = 1")\n',
            "imports 'exec' from builtins",
            id="leaf-aliased-builtins-exec-import",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n"
            '    exec("get_file = 1")\n',
            "dynamic export primitive",
            id="leaf-class-body-exec-of-method",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "def mutate():\n"
            '    globals()["FilesHandler"] = None\n'
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            "mutate()\n",
            "locally-defined callable",
            id="leaf-construction-call-of-local-function",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "class Mutator:\n"
            "    def __init__(self):\n"
            '        globals()["FilesHandler"] = None\n'
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            "Mutator()\n",
            "locally-defined callable",
            id="leaf-local-class-construction",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "def mutate():\n    pass\n"
            "alias_one = mutate\n"
            "alias_two = alias_one\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            "alias_two()\n",
            "locally-defined callable",
            id="leaf-alias-chain-invocation",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            '@(lambda c: exec("FilesHandler = None", globals()) or c)\n'
            "class Sibling:\n    pass\n",
            "lambda decorator",
            id="leaf-lambda-decorator-on-sibling",
        ),
        pytest.param(
            "from .base_handler import FilesBaseHandler\n"
            "def decorate(c):\n"
            '    globals()["FilesHandler"] = None\n'
            "    return c\n"
            "class FilesHandler(FilesBaseHandler):\n    pass\n"
            "@decorate\n"
            "class Sibling:\n    pass\n",
            "locally-defined callable",
            id="leaf-local-decorator-on-sibling",
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
        pytest.param(
            "class FilesBaseHandler:\n"
            "    async def get_file(self, ctx):\n        return {}\n"
            "    try:\n        pass\n"
            "    except Exception as get_file:\n        pass\n",
            "rebound",
            id="base-except-capture-of-method",
        ),
        pytest.param(
            "class FilesBaseHandler:\n"
            "    async def get_file(self, ctx):\n        return {}\n"
            "match []:\n"
            "    case [*FilesBaseHandler]:\n        pass\n    case _:\n        pass\n",
            "rebound|referenced dynamically",
            id="base-match-star-capture-of-class",
        ),
        pytest.param(
            "e = eval\n"
            "class FilesBaseHandler:\n"
            "    async def get_file(self, ctx):\n        return {}\n",
            "dynamic export primitive",
            id="base-rebound-eval",
        ),
        pytest.param(
            "import builtins as b\n"
            "class FilesBaseHandler:\n"
            "    async def get_file(self, ctx):\n        return {}\n"
            'b.setattr(FilesBaseHandler, "get_file", None)\n',
            "accesses the builtins module|referenced dynamically",
            id="base-builtins-setattr",
        ),
        pytest.param(
            "class FilesBaseHandler:\n"
            "    async def get_file(self, ctx):\n        return {}\n"
            '    exec("get_file = 1")\n',
            "dynamic export primitive",
            id="base-class-body-exec-of-method",
        ),
        pytest.param(
            "def mutate():\n"
            '    globals()["FilesBaseHandler"] = None\n'
            "class FilesBaseHandler:\n"
            "    async def get_file(self, ctx):\n        return {}\n"
            "mutate()\n",
            "locally-defined callable",
            id="base-construction-call-of-local-function",
        ),
        pytest.param(
            "class FilesBaseHandler:\n"
            "    async def get_file(self, ctx):\n        return {}\n"
            "    def _mutate():\n        pass\n"
            "    _mutate()\n",
            "locally-defined callable",
            id="base-class-scope-local-call",
        ),
        pytest.param(
            "class FilesBaseHandler:\n"
            "    async def get_file(self, ctx):\n        return {}\n"
            '@(lambda c: exec("FilesBaseHandler = None", globals()) or c)\n'
            "class Sibling:\n    pass\n",
            "lambda decorator",
            id="base-lambda-decorator-on-sibling",
        ),
        pytest.param(
            "class FilesBaseHandler:\n"
            "    async def get_file(self, ctx):\n        return {}\n"
            'locals()["FilesBaseHandler"] = None\n',
            "dynamic export primitive",
            id="base-locals-mutation",
        ),
    ],
)
async def test_base_split_grammar_hostiles_reject(content_store, base: str, reason: str) -> None:
    with pytest.raises(ImplementationArtifactError, match=reason):
        await _certify_sources(content_store, CANONICAL_LEAF, base)


async def test_deferred_base_helper_remains_certifiable(content_store) -> None:
    """A local helper used only from a deferred method body stays certifiable:
    runtime behavior is outside the import-time effective-export proof."""
    base = (
        "def _normalize(value):\n"
        "    return value or {}\n"
        "class FilesBaseHandler:\n"
        "    async def get_file(self, ctx, file_id=None):\n"
        '        return {"file": _normalize(file_id)}\n'
    )
    resolved = await _certify_sources(content_store, CANONICAL_LEAF, base)
    assert resolved.export_proof.mode is HandlerCertificationMode.CANONICAL_BASE_HANDLER
    assert resolved.export_proof.method_source is HandlerMethodSource.BASE_HANDLER
