"""What every other installed pack holds in an app, by physical destination.

Packs hold app files and workflow files whole, files they seeded, and entries or
data surfaces inside shared documents. Those records live in different parts of
the provenance and are relative to different roots, yet they can name one file:
the workflow root may sit inside the app root, and a generator pack may own a
document whole that a library contributes entries to. :class:`Holdings` resolves
every record to the file it names, so both lifecycles ask one question of one
view, and :meth:`Holdings.losses` checks a finished plan against all of it.
"""

from __future__ import annotations

import copy
import unicodedata
from collections.abc import Iterator, Mapping, Set
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from mozaiksai.core.session.build_context import (
    BuildContextError,
    iter_context_assets,
    resolve_context_asset_path,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    SHARED_PACK_OUTPUT_PATHS,
)
from mozaiksai.core.workflow.generator_support.persistence_artifacts import (
    normalize_data_contract_indexes,
)

from .community_component_composition import (
    KEYED_ROOTS,
    OWNERSHIP_CONTENT_HASH,
    WORKFLOW_REGISTRY_PATH,
    CompositionFormatError,
    KeyedRoot,
    content_sha256,
    keyed_entries,
    parse_document,
)
from .community_component_state import (
    component_by_id,
    load_installed_component_sources,
    load_installed_components,
)
from .resolve_managed_capability_templates import (
    ManagedCapabilityTemplateError,
    _file_owner_map,
    _read_pack_context,
    resolve_templates_for_pack,
    verify_pack_integrity,
)

DATA_CONTRACT_PATH = "data/contract.json"

Address = tuple[str, str]


@dataclass(frozen=True)
class PackOutputs:
    """What a pack's verified source renders: app files, workflow files, and the app files it only seeds."""

    app: dict[str, str]
    workflow: dict[str, str]
    seeds: frozenset[str]


def installed_pack_outputs(build_context_root: Path, pack_id: str, identity: Mapping[str, Any]) -> PackOutputs | None:
    """What a pack's bound source renders, or None unless that source still is the version ``identity`` records."""
    bound = load_installed_component_sources(build_context_root).get(pack_id)
    if bound is None:
        return None
    source = Path(bound)
    workflow: dict[str, str] = {}
    try:
        metadata = verify_pack_integrity(source, pack_id)
        if any(metadata[key] != identity.get(key) for key in ("version", "digest")):
            return None
        app = {str(file["filename"]): str(file["content"]) for file in resolve_templates_for_pack(source, pack_id)}
        seeds = frozenset(path for path, owner in _file_owner_map(source).items() if owner == "workspace")
        for asset in iter_context_assets(_read_pack_context(source, pack_id), kind="workflows"):
            folder = resolve_context_asset_path(source, asset)
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix != ".pyc":
                    workflow[path.relative_to(folder).as_posix()] = path.read_text(encoding="utf-8")
    except (ManagedCapabilityTemplateError, BuildContextError, OSError, ValueError, yaml.YAMLError):
        return None
    return PackOutputs(app, workflow, seeds)


def destination_key(path: Path) -> str:
    """One key per physical file: spellings that name one file on some host share it."""
    return "/".join(unicodedata.normalize("NFC", part).casefold() for part in path.parts)


def normalize_entry(root_path: str, entry: Any) -> Any:
    if root_path == DATA_CONTRACT_PATH and isinstance(entry, dict):
        normalized = normalize_data_contract_indexes({"version": "1", "surfaces": [copy.deepcopy(entry)], "shared_collections": []})
        return normalized["surfaces"][0]
    return entry


def recorded_modules(pack: Mapping[str, Any]) -> set[str]:
    """The modules a provenance entry records files of, so whose data surface it would contribute."""
    paths = [PurePosixPath(str(row.get("path"))) for row in pack.get("materialized_owned_files") or [] if isinstance(row, dict)]
    return {path.parts[1] for path in paths if len(path.parts) > 2 and path.parts[0] == "modules"}


def _document_entries(text: Any, root: KeyedRoot) -> dict[Address, Any] | None:
    """A document's entries, normalized, or None when it is no such document."""
    if not isinstance(text, str):
        return None
    try:
        entries = keyed_entries(parse_document(text, root.fmt), root)
    except CompositionFormatError:
        return None
    for address, value in entries.items():
        # An app's malformed entry compares as it is written.
        with suppress(ValueError, TypeError, AttributeError, KeyError):
            entries[address] = normalize_entry(root.path, value)
    return entries


def _outside_entries(text: str, root: KeyedRoot) -> dict[str, Any]:
    """Everything a document holds beside its keyed entries."""
    document = parse_document(text, root.fmt)
    outside: dict[str, Any] = {key: value for key, value in document.items() if key not in dict(root.containers)}
    for container, key_field in root.containers:
        outside[container] = [
            row for row in document.get(container) or [] if not isinstance(row, dict) or not isinstance(row.get(key_field), str)
        ]
    return outside


def _read(path: Path) -> Any:
    """A file's text, its bytes when it is not text, or None when absent."""
    if not path.is_file():
        return None
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data


def _same(left: Any, right: Any) -> bool:
    """Equal content, as content hashes see it: line endings are not an edit."""
    if isinstance(left, str) and isinstance(right, str):
        return content_sha256(left) == content_sha256(right)
    return bool(left == right)


@dataclass(frozen=True)
class Holding:
    """One thing another pack holds at one destination.

    ``kind`` is ``owned`` (the whole file), ``seed`` (a file it wrote once, the
    app's since), ``entry`` (one keyed entry of a shared document) or ``part`` (a
    share of a shared document that its records and source cannot name).
    ``recorded`` is False for a pinned pack the provenance does not record.
    """

    pack_id: str
    path: Path
    kind: str
    root: KeyedRoot | None = None
    address: Address | None = None
    hashed: bool = False
    recorded: bool = True
    # For a document owned whole: the entries its owner ships in it, None when the source cannot tell.
    entries: frozenset[Address] | None = None

    def covers(self, address: Address) -> bool:
        """Whether an entry of the document is this holding's, as far as can be told."""
        if self.kind == "entry":
            return self.address == address
        return self.kind == "part" or (self.kind == "owned" and (self.entries is None or address in self.entries))


class Holdings:
    """What every pack but one holds, keyed by physical destination."""

    def __init__(
        self, manifest: Mapping[str, Any], pack_id: str, *, app_root: Path, workflows_root: Path, build_context_root: Path,
    ) -> None:
        self._manifest = manifest
        self._pack_id = pack_id
        self._bases = {"app": app_root, "workflows": workflows_root}
        self._build_context_root = build_context_root
        self._by_key: dict[str, list[Holding]] = {}
        self._outputs: dict[str, PackOutputs | None] = {}
        # Documents several packs and the app compose together, and their keyed form when they have one.
        self._shared: dict[str, KeyedRoot | None] = {
            destination_key(app_root / path): None for path in (*SHARED_PACK_OUTPUT_PATHS, *self._composed_paths("composed_outputs"))
        }
        self._shared.update(
            (destination_key(workflows_root / path), None) for path in self._composed_paths("composed_workflow_outputs")
        )
        self._shared.update((destination_key(self._bases[root.base] / root.path), root) for root in KEYED_ROOTS)
        self._add_recorded()
        self._add_pinned_only()

    # -- building ----------------------------------------------------------------------

    def _composed_paths(self, field_name: str) -> list[str]:
        composed = self._manifest.get(field_name)
        return [str(path) for path in composed] if isinstance(composed, dict) else []

    def _packs(self) -> Iterator[tuple[str, dict[str, Any]]]:
        for entry in self._manifest.get("packs") or []:
            if isinstance(entry, dict):
                yield str(entry.get("pack_id") or "").strip(), entry

    def _add(self, holding: Holding) -> None:
        self._by_key.setdefault(destination_key(holding.path), []).append(holding)

    def _rendered(self, pack_id: str, identity: Mapping[str, Any]) -> PackOutputs | None:
        if pack_id not in self._outputs:
            self._outputs[pack_id] = installed_pack_outputs(self._build_context_root, pack_id, identity)
        return self._outputs[pack_id]

    def _whole(self, pack_id: str, base: str, path: str, *, kind: str, identity: Mapping[str, Any], recorded: bool) -> Holding:
        target = self._bases[base] / path
        root = self._shared.get(destination_key(target))
        entries: frozenset[Address] | None = None
        if root is not None and (outputs := self._rendered(pack_id, identity)) is not None:
            shipped = _document_entries((outputs.app if base == "app" else outputs.workflow).get(path), root)
            entries = frozenset(shipped) if shipped is not None else None
        hashed = identity.get("ownership") == OWNERSHIP_CONTENT_HASH
        return Holding(pack_id, target, kind, root=root, hashed=hashed, recorded=recorded, entries=entries)

    def _add_recorded(self) -> None:
        identities = dict(self._packs())
        for pack_id, entry in self._packs():
            if pack_id == self._pack_id:
                continue
            for field_name, base in (("materialized_owned_files", "app"), ("materialized_workflow_files", "workflows")):
                for row in entry.get(field_name) or []:
                    if isinstance(row, dict) and row.get("path"):
                        kind = "seed" if row.get("owner") == "workspace" else "owned"
                        self._add(self._whole(pack_id, base, str(row["path"]), kind=kind, identity=entry, recorded=True))
        for field_name, base in (("composed_outputs", "app"), ("composed_workflow_outputs", "workflows")):
            for path in self._composed_paths(field_name):
                contributors = self._manifest[field_name][path]
                for row in contributors if isinstance(contributors, list) else []:
                    pack_id = str(row.get("pack_id") or "").strip() if isinstance(row, dict) else self._pack_id
                    if pack_id != self._pack_id:
                        self._add_contributor(pack_id, base, path, row, identities.get(pack_id, row))

    def _add_contributor(
        self, pack_id: str, base: str, path: str, row: Mapping[str, Any], identity: Mapping[str, Any],
    ) -> None:
        target = self._bases[base] / path
        root = self._shared[destination_key(target)]
        addresses: Set[Address] = set()
        if root is not None and "entries" in row:
            addresses = {
                (str(item.get("container")), str(item.get("key"))) for item in row["entries"] or [] if isinstance(item, dict)
            }
        else:
            # A generator pack records that it contributes, not what: its source tells.
            outputs = self._rendered(pack_id, identity)
            text = None if outputs is None else (outputs.app if base == "app" else outputs.workflow).get(path)
            shipped = None if root is None else _document_entries(text, root)
            if shipped is not None:
                addresses = set(shipped)
            elif outputs is None or text is not None:
                self._add(Holding(pack_id, target, "part", root=root))
                if path == DATA_CONTRACT_PATH:
                    # Failing that, the modules it records do.
                    addresses = {("surfaces", name) for name in recorded_modules(identity)}
        for address in addresses:
            self._add(Holding(pack_id, target, "entry", root=root, address=address))

    def _add_pinned_only(self) -> None:
        """Provenance can be lost while a pin and its source remain, so what such a pack ships is not free.

        A source that no longer verifies says nothing.
        """
        recorded = {pack_id for pack_id, _entry in self._packs()}
        for pack_id, pin in component_by_id(load_installed_components(self._build_context_root)).items():
            if pack_id == self._pack_id or pack_id in recorded:
                continue
            outputs = self._rendered(pack_id, pin)
            for base, files in () if outputs is None else (("app", outputs.app), ("workflows", outputs.workflow)):
                for path, text in files.items():
                    target = self._bases[base] / path
                    root = self._shared.get(destination_key(target))
                    shipped = None if root is None else _document_entries(text, root)
                    if shipped is not None:
                        for address in shipped:
                            self._add(Holding(pack_id, target, "entry", root=root, address=address, recorded=False))
                        continue
                    kind = "seed" if base == "app" and outputs is not None and path in outputs.seeds else "owned"
                    self._add(Holding(pack_id, target, kind, root=root, recorded=False))

    # -- asking ------------------------------------------------------------------------

    def at(self, path: Path) -> list[Holding]:
        return self._by_key.get(destination_key(path), [])

    def is_shared(self, path: Path) -> bool:
        """Whether a destination is a document packs compose into: entries own it, no pack may take it whole."""
        return destination_key(path) in self._shared

    def label(self, path: Path, *, app_first: bool = False) -> str:
        """A destination as plans name it."""
        app_root, workflows_root = self._bases["app"], self._bases["workflows"]
        bases = [(workflows_root, "workflows/"), (app_root, "")]
        for base, prefix in reversed(bases) if app_first else bases:
            if path.is_relative_to(base):
                return f"{prefix}{path.relative_to(base).as_posix()}"
        return path.as_posix()

    def file_owner(self, path: Path, *, seeds: bool = False) -> str | None:
        """The pack recorded as owning a file, a file where the path needs a folder, or a file inside it."""
        kinds = {"owned", "seed"} if seeds else {"owned"}
        key = destination_key(path)
        for other, holdings in self._by_key.items():
            if other == key or other.startswith(f"{key}/") or key.startswith(f"{other}/"):
                for holding in holdings:
                    if holding.recorded and holding.kind in kinds:
                        return holding.pack_id
        return None

    def holder(self, path: Path) -> str | None:
        """Any pack with any claim on a file, which a removal must leave alone."""
        return next((holding.pack_id for holding in self.at(path)), None)

    def pinned_owner(self, path: Path) -> str | None:
        """A pinned pack without provenance whose source ships a file as its own."""
        return next((row.pack_id for row in self.at(path) if row.kind == "owned" and not row.recorded), None)

    def entry_owner(self, path: Path, address: Address) -> str | None:
        """The pack recorded as composing an entry."""
        return next((row.pack_id for row in self.at(path) if row.recorded and row.address == address), None)

    def entry_holder(self, path: Path, address: Address, *, recorded: bool = False, parts: bool = True) -> str | None:
        """The pack that holds an entry, by itself, with the whole document or, as far as can be told, with a ``part`` of it."""
        return next((
            row.pack_id for row in self.at(path)
            if row.covers(address) and (row.recorded or not recorded) and (parts or row.kind != "part")
        ), None)

    def whole_by_hash(self, path: Path) -> str | None:
        """A library recorded as owning a document whole, whose content no one else may add to."""
        return next((row.pack_id for row in self.at(path) if row.kind == "owned" and row.hashed), None)

    def entries(self, path: Path, *, recorded: bool) -> dict[str, str]:
        """The entry keys held by name in a document, with their holders."""
        return {row.address[1]: row.pack_id for row in self.at(path) if row.address is not None and row.recorded is recorded}

    # -- the postcondition -------------------------------------------------------------

    def losses(
        self, writes: Mapping[Path, str | bytes | None], *, own_files: Set[str], own_entries: Set[tuple[str, Address]],
    ) -> list[Holding]:
        """Everything another pack holds in the app now that planned writes would delete or change.

        ``own_files`` (destination keys) and ``own_entries`` are what the acting
        pack records as its own. They settle only what the other claim leaves
        open: a file the other pack merely seeded, and the claims of a pack the
        provenance does not record.
        """
        planned: dict[str, list[tuple[Path, str | bytes | None]]] = {}
        for path, value in writes.items():
            planned.setdefault(destination_key(path), []).append((path, value))
        lost: list[Holding] = []
        for key, holdings in self._by_key.items():
            touching = planned.get(key, [])
            # A file where this one needs a folder, or inside it as if it were one.
            displaced = any(
                value is not None for other, rows in planned.items()
                if other.startswith(f"{key}/") or key.startswith(f"{other}/") for _path, value in rows
            )
            if touching or displaced:
                lost.extend(
                    holding for holding in holdings
                    if (before := _read(holding.path)) is not None
                    and (displaced or not self._kept(holding, key, before, touching, own_files, own_entries))
                )
        return lost

    def _kept(
        self, holding: Holding, key: str, before: Any, touching: list[tuple[Path, str | bytes | None]],
        own_files: Set[str], own_entries: Set[tuple[str, Address]],
    ) -> bool:
        if any(value is not None and str(path) != str(holding.path) for path, value in touching):
            # A second spelling of the file: one file on some hosts, two on others.
            return False
        if all(_same(value, before) for _path, value in touching):
            return True
        after = touching[0][1] if len(touching) == 1 and touching[0][0] == holding.path else None
        own = {address for entry_key, address in own_entries if entry_key == key}
        if holding.kind == "entry":
            if holding.root is None or holding.address is None:
                return False
            if not holding.recorded and holding.address in own:
                return True
            current = _document_entries(before, holding.root)
            result = _document_entries(after, holding.root)
            if current is None:
                return False
            return holding.address not in current or (
                result is not None and result.get(holding.address) == current[holding.address]
            )
        if after is not None and key in own_files and (holding.kind == "seed" or not holding.recorded):
            return True
        if holding.root is None or holding.hashed and holding.kind == "owned":
            return False
        return self._rest_kept(holding, holding.root, before, after, own)

    @staticmethod
    def _rest_kept(holding: Holding, root: KeyedRoot, before: Any, after: Any, own: set[Address]) -> bool:
        """Whether a shared document keeps everything but the acting pack's own entries."""
        current, result = _document_entries(before, root), _document_entries(after, root)
        if current is None or result is None:
            return False
        if holding.kind == "owned":
            # What its owner ships is not the acting pack's to change, whoever else records it.
            own = {address for address in own if holding.entries is not None and address not in holding.entries}
        if any(result.get(address) != value for address, value in current.items() if address not in own):
            return False
        return holding.kind == "part" or _outside_entries(before, root) == _outside_entries(after, root)


def loss_conflict(holding: Holding, label: str) -> dict[str, str]:
    """The conflict that reports one loss."""
    if holding.address is not None:
        container, key = holding.address
        return {
            "kind": "composed_entry_owned_by_other_pack", "root": label, "container": container, "key": key,
            "owner": holding.pack_id,
        }
    return {"kind": "owned_by_other_pack", "path": label, "owner": holding.pack_id}


__all__ = [
    "DATA_CONTRACT_PATH",
    "WORKFLOW_REGISTRY_PATH",
    "Holding",
    "Holdings",
    "PackOutputs",
    "destination_key",
    "installed_pack_outputs",
    "loss_conflict",
    "normalize_entry",
]
