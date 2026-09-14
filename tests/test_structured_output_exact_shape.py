"""The auto-tool ``structured_output`` projection must be the EXACT result.

Live regression (greenfield Genesis, PR #521): ``save_design_docs_bundle``
reported success while persisting nothing, AppGenerator then produced an empty
bundle, and SecurityReadiness truthfully reported "no files assessed".

Root cause was NOT a lost or resume-erased payload. The payload was present and
exact in *value*, but :class:`StructuredOutputOverlay` served it through
``freeze()``, which rewrites the payload's *shape*::

    dict -> types.MappingProxyType        (isinstance(x, dict) is False)
    list -> tuple                         (isinstance(x, list) is False)

Every consumer that validates its input with ``isinstance(raw, dict)`` or
``isinstance(raw, list)`` therefore discarded a fully valid agent output and
early-returned. (BSON itself encodes both frozen forms, so persistence never
raised — the payload was dropped before it ever reached the store, which is
why the tool still reported success.)

These tests pin the generic runtime boundary: the declared auto tool receives
the validated result with its exact shape, as a fresh private deep copy.
"""

from __future__ import annotations

from types import MappingProxyType

from mozaiksai.core.workflow.context.structured_output_overlay import (
    STRUCTURED_OUTPUT_KEY,
    StructuredOutputOverlay,
)

# Shaped like a real DesignDocsBundle: nested mappings AND nested lists.
BUNDLE = {
    "frontend_markdown": "# Frontend",
    "surface_map": {"surfaces": [{"surface_id": "home"}]},
    "pages": [{"page_id": "home", "sections": [{"kind": "list"}]}],
}


class _Base:
    """Minimal live-context stand-in for the overlay's delegation target."""

    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    def get(self, key: str, default: object | None = None) -> object | None:
        return self._data.get(key, default)

    def set(self, key: str, value: object) -> None:
        self._data[key] = value

    def keys(self):
        return tuple(self._data)

    def snapshot(self) -> dict[str, object]:
        return dict(self._data)


def _assert_exact_shape(value: object) -> None:
    """Recursively assert plain, mutable, JSON/BSON-encodable containers."""
    assert not isinstance(value, MappingProxyType), f"frozen view leaked: {value!r}"
    assert not isinstance(value, tuple), f"list was rewritten to tuple: {value!r}"
    if isinstance(value, dict):
        for item in value.values():
            _assert_exact_shape(item)
    elif isinstance(value, list):
        for item in value:
            _assert_exact_shape(item)


def test_projection_preserves_the_validated_payload_shape():
    overlay = StructuredOutputOverlay(_Base(), BUNDLE)
    received = overlay.get(STRUCTURED_OUTPUT_KEY)

    assert received == BUNDLE
    assert isinstance(received, dict)
    assert isinstance(received["pages"], list)
    assert isinstance(received["surface_map"]["surfaces"], list)
    _assert_exact_shape(received)


def test_isinstance_dict_guard_accepts_the_projection():
    """The exact guard that silently dropped the live DesignDocs bundle."""
    overlay = StructuredOutputOverlay(_Base(), BUNDLE)
    raw = overlay.get(STRUCTURED_OUTPUT_KEY)

    # Before the fix this branch returned None and the tool early-returned.
    assert isinstance(raw, dict), "auto tools guard extraction with isinstance(dict)"
    surfaces = raw["surface_map"]["surfaces"]
    assert isinstance(surfaces, list) and surfaces


def test_projection_round_trips_through_bson_unchanged():
    """Persisted shape must equal the validated shape.

    BSON encodes mappingproxy and tuple too, so this never raised — but a
    frozen payload round-tripped to a *different* document than the agent
    produced only if shape mattered downstream. Pinning the round trip keeps
    the persisted artifact identical to the validated result.
    """
    bson = __import__("bson")
    delivered = StructuredOutputOverlay(_Base(), BUNDLE).get(STRUCTURED_OUTPUT_KEY)
    decoded = bson.BSON(bson.BSON.encode({"bundle": delivered})).decode()["bundle"]
    assert decoded == BUNDLE
    _assert_exact_shape(decoded)


def test_each_read_is_an_isolated_copy():
    """Mutability is safe because no read shares state with another."""
    overlay = StructuredOutputOverlay(_Base(), BUNDLE)

    first = overlay.get(STRUCTURED_OUTPUT_KEY)
    first["frontend_markdown"] = "MUTATED"
    first["pages"].append({"page_id": "injected"})
    first["surface_map"]["surfaces"].clear()

    assert overlay.get(STRUCTURED_OUTPUT_KEY) == BUNDLE
    assert BUNDLE["pages"] == [{"page_id": "home", "sections": [{"kind": "list"}]}]


def test_projection_still_fails_writes_and_stays_out_of_snapshots():
    """Exact delivery must not weaken the #491 projection guarantees."""
    from mozaiksai.core.workflow.context.structured_output_overlay import (
        StructuredOutputWriteError,
    )

    overlay = StructuredOutputOverlay(_Base(), BUNDLE)
    overlay.set("declared_key", "value")

    for mutate in (
        lambda: overlay.set(STRUCTURED_OUTPUT_KEY, {"forged": True}),
        lambda: overlay.remove(STRUCTURED_OUTPUT_KEY),
    ):
        try:
            mutate()
        except StructuredOutputWriteError:
            continue
        raise AssertionError("projection mutation must fail closed")

    assert STRUCTURED_OUTPUT_KEY not in overlay.snapshot()
    assert STRUCTURED_OUTPUT_KEY not in list(overlay.keys())
    assert overlay.snapshot()["declared_key"] == "value"


def test_real_designdocs_save_tool_accepts_the_projection():
    """The exact live failure: the checked-in DesignDocs auto tool.

    ``save_design_docs_bundle`` extracts via ``_extract_bundle``; against the
    frozen projection that returned ``None``, so the tool returned before its
    persistence work while still reporting success. Loading the real workflow
    tool keeps this honest — a stand-in would only prove the stand-in.
    """
    from tests.import_utils import import_module_directly

    tool = import_module_directly(
        "factory_app.workflows.DesignDocs.tools.save_design_doc"
    )
    overlay = StructuredOutputOverlay(_Base(), BUNDLE)

    extracted = tool._extract_bundle(overlay)

    assert extracted is not None, "DesignDocs bundle was discarded before persistence"
    assert extracted == BUNDLE
    # The tool's own canonicalizers must accept the nested lists too.
    assert tool._canonical_surface_map(extracted["surface_map"]) == BUNDLE["surface_map"]
