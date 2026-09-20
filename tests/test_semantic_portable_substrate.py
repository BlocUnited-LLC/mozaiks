"""ADR 0007 Slice 4A adversarial contract tests.

Covers the portable path profile (``mozaiks.portable_path.v1``), the
deterministic archive transport envelope, and the layout registry v2 evolution
(dependency closure, acyclic stable ordering, digest closure). Slice 4A is
substrate only: these tests also pin that it introduces no semantic authority,
no AG2 imports, and no filesystem behavior.
"""

from __future__ import annotations

import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from mozaiksai.core.runtime.app.layout_registry import (
    SCHEMA_VERSION,
    AppLayoutRegistry,
    ArtifactFamily,
    ArtifactKind,
    ConditionIdentifier,
    LayoutOwner,
    MaterializerIdentifier,
    Multiplicity,
    PathScope,
    Requirement,
    RuntimeConsumerIdentifier,
    SecurityClass,
    StubKind,
    ValidatorIdentifier,
    build_app_layout_registry,
    default_app_layout_registry,
)
from mozaiksai.core.semantics.archive import (
    ArchiveEntry,
    ArchiveError,
    archive_digest,
    build_deterministic_archive,
    read_archive_manifest,
)
from mozaiksai.core.semantics.portable_path import (
    PortablePathError,
    collision_key,
    detect_collisions,
    validate_portable_path,
)

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Portable path profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "reason_fragment"),
    [
        ("/etc/passwd", "absolute"),
        ("C:/windows/system32", "drive"),
        ("C:relative", "drive"),
        ("//server/share/file", "absolute"),
        ("a\\b", "backslash"),
        ("a/../b", "traversal"),
        ("..", "traversal"),
        ("a/./b", "traversal"),
        ("a//b", "empty path segment"),
        ("a/*.py", "not portable"),
        ("a/b?.txt", "not portable"),
        ("a/[x].txt", "glob"),
        ("CON", "reserved"),
        ("a/nul.txt", "reserved"),
        ("a/COM1", "reserved"),
        ("a/lpt3.log", "reserved"),
        ("a/b\x00c", "null byte"),
        ("a/b\x01c", "control"),
        ("a/b\x7fc", "control"),
        ("a/stream:ads", "not portable"),
        ("a/trailing.", "trailing dot"),
        ("a/trailing ", "trailing dot or space"),
        ("a/ leading", "leading space"),
        ("~/home", "home-relative"),
        ("", "empty path"),
        ("a/<b>.txt", "not portable"),
        ('a/"b".txt', "not portable"),
        ("a/b|c.txt", "not portable"),
    ],
)
def test_portable_path_rejections_fail_closed(raw: str, reason_fragment: str) -> None:
    with pytest.raises(PortablePathError) as exc_info:
        validate_portable_path(raw)
    assert reason_fragment.lower() in exc_info.value.reason.lower(), exc_info.value.reason


def test_portable_path_accepts_and_normalizes_nfc() -> None:
    # NFD input (e + combining acute) normalizes to the NFC code point.
    nfd = "docs/re" + "\u0301" + "sume.md"
    portable = validate_portable_path(nfd)
    assert portable.text == unicodedata.normalize("NFC", nfd)
    assert "\u0301" not in portable.text


def test_portable_path_rules_apply_identically_regardless_of_host() -> None:
    """Windows-compatible restrictions are enforced on every host.

    The profile is pure computation: the same rejection set and the same
    normalized output must be produced on Windows and on POSIX. This asserts
    host-independence structurally — no os/platform conditional exists in the
    module — and behaviorally via a subprocess with an empty platform config.
    """
    source = (ROOT / "mozaiksai/core/semantics/portable_path.py").read_text(encoding="utf-8")
    for token in ("os.name", "sys.platform", "platform.", "ntpath", "posixpath", "os.sep"):
        assert token not in source, f"host-dependent token {token!r} in portable_path"

    probe = (
        "from mozaiksai.core.semantics.portable_path import validate_portable_path\n"
        "print(validate_portable_path('a/b.txt').text)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=str(ROOT), capture_output=True, text=True, timeout=300
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "a/b.txt"


def test_collision_key_case_folds_conservatively() -> None:
    assert collision_key("Docs/Readme.MD") == collision_key("docs/readme.md")
    # German sharp s case-folds to "ss".
    assert collision_key("a/stra\u00dfe.txt") == collision_key("a/STRASSE.txt")


@pytest.mark.parametrize(
    "paths",
    [
        ["a/b.txt", "A/B.TXT"],  # case-fold duplicate
        ["a/b.txt", "a/b.txt"],  # exact duplicate
        ["a/b", "a/b/c.txt"],  # file/directory prefix collision
        ["A/B", "a/b/c.txt"],  # folded prefix collision
    ],
)
def test_collision_detection_fails_closed(paths: list[str]) -> None:
    with pytest.raises(PortablePathError):
        detect_collisions(paths)


def test_collision_detection_accepts_disjoint_sets() -> None:
    detect_collisions(["a/b.txt", "a/c.txt", "d/e/f.json"])


# ---------------------------------------------------------------------------
# Deterministic archive envelope
# ---------------------------------------------------------------------------

_CORPUS = (
    ArchiveEntry(path="app/config/shell.json", content=b'{"nav": []}\n'),
    ArchiveEntry(path="app/app.json", content=b'{"appName": "Golden"}\n'),
    ArchiveEntry(path="modules/reports/module.yaml", content=b"module:\n  id: reports\n"),
    ArchiveEntry(path="ui/pages/reports.yaml", content="name: Répörts\n".encode()),
)
# Golden vector: STORED envelope bytes are compressor-independent, so this
# digest must be identical on every host, Python build, and process.
_GOLDEN_DIGEST = "sha256:aecaa65174d8db2ff8e9f020760445b737d5f915a651458bc0f960b395f39841"


def test_archive_bytes_are_order_independent_and_digest_stable() -> None:
    forward = build_deterministic_archive(_CORPUS)
    reversed_bytes = build_deterministic_archive(tuple(reversed(_CORPUS)))
    assert forward == reversed_bytes
    assert archive_digest(forward) == archive_digest(reversed_bytes)


def test_archive_golden_vector_across_process_restart() -> None:
    local = archive_digest(build_deterministic_archive(_CORPUS))

    probe = (
        "from mozaiksai.core.semantics.archive import ArchiveEntry, "
        "build_deterministic_archive, archive_digest\n"
        "corpus = (\n"
        "    ArchiveEntry(path='app/config/shell.json', content=b'{\"nav\": []}\\n'),\n"
        "    ArchiveEntry(path='app/app.json', content=b'{\"appName\": \"Golden\"}\\n'),\n"
        "    ArchiveEntry(path='modules/reports/module.yaml', content=b'module:\\n  id: reports\\n'),\n"
        "    ArchiveEntry(path='ui/pages/reports.yaml', content='name: R\\u00e9p\\u00f6rts\\n'.encode()),\n"
        ")\n"
        "print(archive_digest(build_deterministic_archive(corpus)))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=str(ROOT), capture_output=True, text=True, timeout=300
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == local, "archive digest differs across processes"


def test_archive_golden_vector_pinned() -> None:
    assert archive_digest(build_deterministic_archive(_CORPUS)) == _GOLDEN_DIGEST


def test_archive_round_trip_manifest() -> None:
    data = build_deterministic_archive(_CORPUS)
    manifest = read_archive_manifest(data)
    assert [entry.path for entry in manifest.entries] == sorted(
        entry.path for entry in _CORPUS
    )
    assert manifest.archive_sha256 == archive_digest(data)


def test_archive_rejects_empty_collisions_and_bad_names() -> None:
    with pytest.raises(ArchiveError):
        build_deterministic_archive([])
    with pytest.raises(PortablePathError):
        build_deterministic_archive([ArchiveEntry(path="a/../b", content=b"x")])
    with pytest.raises(PortablePathError):
        build_deterministic_archive(
            [ArchiveEntry(path="a/b.txt", content=b"1"), ArchiveEntry(path="A/B.TXT", content=b"2")]
        )


def test_archive_verification_rejects_noncanonical_envelopes() -> None:
    import io
    import stat as stat_module
    import zipfile

    def _zip(builder) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as archive:
            builder(archive)
        return buffer.getvalue()

    def _link(archive: zipfile.ZipFile) -> None:
        info = zipfile.ZipInfo("a/link", date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = (stat_module.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target")

    def _dir(archive: zipfile.ZipFile) -> None:
        archive.writestr(zipfile.ZipInfo("a/dir/", date_time=(1980, 1, 1, 0, 0, 0)), "")

    def _timestamp(archive: zipfile.ZipFile) -> None:
        archive.writestr(zipfile.ZipInfo("a/file.txt", date_time=(2024, 6, 1, 12, 0, 0)), "x")

    def _out_of_order(archive: zipfile.ZipFile) -> None:
        archive.writestr(zipfile.ZipInfo("b/late.txt", date_time=(1980, 1, 1, 0, 0, 0)), "1")
        archive.writestr(zipfile.ZipInfo("a/early.txt", date_time=(1980, 1, 1, 0, 0, 0)), "2")

    def _absolute(archive: zipfile.ZipFile) -> None:
        archive.writestr(zipfile.ZipInfo("/abs.txt", date_time=(1980, 1, 1, 0, 0, 0)), "x")

    for builder in (_link, _dir, _timestamp, _out_of_order, _absolute):
        with pytest.raises(ArchiveError):
            read_archive_manifest(_zip(builder))
    with pytest.raises(ArchiveError):
        read_archive_manifest(b"not a zip")


def _canonical_except(mutate=None, archive_comment: bytes = b"") -> bytes:
    """Build an envelope canonical in every field except the one mutated.

    Mirrors the Slice 4A writer field-for-field so each rejection test isolates
    exactly one non-canonical metadata field on the second entry.
    """
    import io
    import stat as stat_module
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for path, content in (("app/a.txt", b"alpha"), ("app/b.txt", b"beta")):
            info = zipfile.ZipInfo(filename=path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat_module.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_STORED
            if mutate is not None and path == "app/b.txt":
                mutate(info)
            archive.writestr(info, content)
        if archive_comment:
            archive.comment = archive_comment
    return buffer.getvalue()


def _archive_metadata_mutants() -> list[tuple[str, bytes, str]]:
    import stat as stat_module
    import zipfile

    return [
        (
            "compression_method",
            _canonical_except(lambda i: setattr(i, "compress_type", zipfile.ZIP_DEFLATED)),
            "non-canonical compression method",
        ),
        (
            "create_system",
            _canonical_except(lambda i: setattr(i, "create_system", 0)),
            "non-canonical create_system",
        ),
        (
            "executable_permissions",
            _canonical_except(
                lambda i: setattr(i, "external_attr", (stat_module.S_IFREG | 0o755) << 16)
            ),
            "executable permission bits",
        ),
        (
            "non_regular_type_bits",
            _canonical_except(
                lambda i: setattr(i, "external_attr", (stat_module.S_IFIFO | 0o644) << 16)
            ),
            "non-regular-file type bits",
        ),
        (
            "missing_type_bits",
            _canonical_except(lambda i: setattr(i, "external_attr", 0o644 << 16)),
            "non-regular-file type bits",
        ),
        (
            "noncanonical_permissions",
            _canonical_except(
                lambda i: setattr(i, "external_attr", (stat_module.S_IFREG | 0o600) << 16)
            ),
            "non-canonical permissions",
        ),
        (
            "entry_extra_field",
            _canonical_except(lambda i: setattr(i, "extra", b"\xfe\xca\x04\x00abcd")),
            "extra field not permitted",
        ),
        (
            "entry_comment",
            _canonical_except(lambda i: setattr(i, "comment", b"x")),
            "comment not permitted",
        ),
        (
            "archive_comment",
            _canonical_except(archive_comment=b"x"),
            "archive comment not permitted",
        ),
        (
            "internal_attributes",
            _canonical_except(lambda i: setattr(i, "internal_attr", 1)),
            "non-canonical internal attributes",
        ),
        (
            "prepended_bytes",
            b"garbage!" + _canonical_except(),
            "not the canonical serialization",
        ),
    ]


@pytest.mark.parametrize(
    ("name", "data", "reason_fragment"),
    [pytest.param(name, data, fragment, id=name) for name, data, fragment in _archive_metadata_mutants()],
)
def test_archive_verification_rejects_each_noncanonical_metadata_field(
    name: str, data: bytes, reason_fragment: str
) -> None:
    """Fail-closed per metadata field the canonical writer never produces.

    Each mutant differs from the writer's output in exactly one field, so a
    verifier that merely accepts whatever :mod:`zipfile` can read fails here.
    """
    with pytest.raises(ArchiveError, match=reason_fragment):
        read_archive_manifest(data)


def test_archive_verification_baseline_for_mutants_is_canonical() -> None:
    """The unmutated twin of every metadata mutant verifies cleanly."""
    manifest = read_archive_manifest(_canonical_except())
    assert [entry.path for entry in manifest.entries] == ["app/a.txt", "app/b.txt"]


def test_archive_verification_rejects_local_header_desync() -> None:
    """Central-directory checks alone are insufficient: mutating only the
    local header must still fail the canonical-serialization closure."""
    data = bytearray(_canonical_except())
    assert data[0:4] == b"PK\x03\x04"
    # Local-header mod-time field (offset 10-11) of the first entry: the DOS
    # epoch encodes as zero, so any nonzero value desyncs it from the
    # still-canonical central directory.
    assert data[10:12] == b"\x00\x00"
    data[10:12] = b"\x00\x08"
    with pytest.raises(ArchiveError, match="not the canonical serialization"):
        read_archive_manifest(bytes(data))


def test_archive_inputs_are_immutable_frozen_models() -> None:
    from pydantic import ValidationError

    entry = ArchiveEntry(path="a/b.txt", content=b"x")
    with pytest.raises(ValidationError):
        entry.path = "c/d.txt"  # type: ignore[misc]
    data = build_deterministic_archive([entry])
    assert entry.path == "a/b.txt" and entry.content == b"x"
    assert data


# ---------------------------------------------------------------------------
# Layout registry v2 evolution
# ---------------------------------------------------------------------------


def _family(
    kind: ArtifactKind,
    template: str,
    deps: tuple[ArtifactKind, ...] = (),
) -> ArtifactFamily:
    from mozaiksai.core.runtime.app.layout_registry import ArtifactDisposition

    return ArtifactFamily(
        kind=kind,
        owner=LayoutOwner.APP_WORKSPACE,
        requirement=Requirement.OPTIONAL,
        multiplicity=Multiplicity.SINGLE,
        condition=ConditionIdentifier.WHEN_APP_DECLARED,
        path_scope=PathScope.APP_BUNDLE_ROOT,
        path_template=template,
        materializer=MaterializerIdentifier.APP_GENERATOR,
        disposition=ArtifactDisposition.RENDER,
        validator=ValidatorIdentifier.NONE,
        runtime_consumer=RuntimeConsumerIdentifier.NONE,
        security_class=SecurityClass.INTERNAL_CONTRACT,
        dependency_families=deps,
    )


def test_registry_v2_schema_and_digest_closure() -> None:
    registry = default_app_layout_registry()
    assert registry.schema_version == SCHEMA_VERSION == "mozaiks.app_layout.v2"

    # Tampering with any digest-covered field (including the new v2 fields)
    # must fail closed at model construction.
    families = list(registry.families)
    target = next(f for f in families if f.kind is ArtifactKind.MODULE_BACKEND_HANDLER)
    index = families.index(target)
    families[index] = target.model_copy(update={"dependency_families": ()})
    with pytest.raises(Exception, match="registry_digest"):
        AppLayoutRegistry(
            families=tuple(families),
            registry_digest=registry.registry_digest,
        )


def test_registry_orders_dependencies_before_dependents_and_is_total() -> None:
    registry = default_app_layout_registry()
    ordered = registry.ordered_families()
    assert len(ordered) == len(registry.families)
    position: dict[ArtifactKind, int] = {}
    for index, family in enumerate(ordered):
        position.setdefault(family.kind, index)
    for family in ordered:
        for dependency in family.dependency_families:
            assert position[dependency] < position[family.kind], (
                f"{dependency.value} must order before {family.kind.value}"
            )
    # Deterministic across construction.
    assert [f.identity_payload for f in ordered] == [
        f.identity_payload for f in build_app_layout_registry().ordered_families()
    ]


def test_registry_rejects_dependency_cycles_and_unknown_dependencies() -> None:
    from mozaiksai.core.runtime.app.layout_registry import _stable_digest

    cyclic = (
        _family(ArtifactKind.APP_CONFIG, "one.json", deps=(ArtifactKind.APP_DASHBOARD,)),
        _family(ArtifactKind.APP_DASHBOARD, "two.json", deps=(ArtifactKind.APP_CONFIG,)),
    )
    digest = _stable_digest(
        {
            "schema_version": SCHEMA_VERSION,
            "families": [family.identity_payload for family in cyclic],
        }
    )
    with pytest.raises(Exception, match="cycle"):
        AppLayoutRegistry(families=cyclic, registry_digest=digest)

    dangling = (_family(ArtifactKind.APP_CONFIG, "one.json", deps=(ArtifactKind.APP_DASHBOARD,)),)
    digest = _stable_digest(
        {
            "schema_version": SCHEMA_VERSION,
            "families": [family.identity_payload for family in dangling],
        }
    )
    with pytest.raises(Exception, match="unregistered"):
        AppLayoutRegistry(families=dangling, registry_digest=digest)

    with pytest.raises(Exception, match="depend on itself"):
        _family(ArtifactKind.APP_CONFIG, "one.json", deps=(ArtifactKind.APP_CONFIG,))


def test_registry_templates_conform_to_portable_profile() -> None:
    for family in default_app_layout_registry().families:
        probe = family.path_template
        # Placeholders substituted with a benign identifier — same probe the
        # registry itself applies at construction.
        import re

        substituted = re.sub(r"\{[a-z][a-z0-9_]*\}", "x0", probe)
        validate_portable_path(substituted)

    with pytest.raises(Exception, match="portable_path"):
        _family(ArtifactKind.APP_CONFIG, "config/aux.json")


def test_registry_stub_kinds_are_bounded_declarations() -> None:
    registry = default_app_layout_registry()
    stubbed = {f.kind: f.allowed_stub_kinds for f in registry.families if f.allowed_stub_kinds}
    assert StubKind.PYTHON_BACKEND in stubbed[ArtifactKind.MODULE_MANIFEST]
    assert StubKind.JS_FRONTEND in stubbed[ArtifactKind.APP_UI_PAGE_SCHEMA]
    # Declarations only: no execution surface exists for stubs in this slice.
    assert not hasattr(registry, "render")
    assert not hasattr(registry, "execute")


# ---------------------------------------------------------------------------
# Authority hygiene: substrate only, no semantics, no AG2
# ---------------------------------------------------------------------------


def test_substrate_modules_have_no_ag2_or_authority_imports() -> None:
    forbidden = (
        "import ag2",
        "from ag2",
        "offline_projection",
        "semantics.graph",
        "semantics.binding",
        "semantics.manifest",
    )
    for name in ("portable_path.py", "archive.py"):
        source = (ROOT / "mozaiksai/core/semantics" / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{name} must not reference {token!r}"
        # No filesystem or network surface in the substrate.
        for effect in ("open(", "os.environ", "requests", "httpx", "socket"):
            assert effect not in source, f"{name} must not use {effect!r}"


def test_layout_registry_has_no_ag2_imports() -> None:
    source = (ROOT / "mozaiksai/core/runtime/app/layout_registry.py").read_text(encoding="utf-8")
    assert "import ag2" not in source and "from ag2" not in source
