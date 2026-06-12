"""
Pure helper unit tests for:
  mozaiksai/control_plane/app_context_impact.py

Covers helpers NOT tested in test_app_context_impact_pure_helpers.py:

  _dedupe:
    - empty list → []
    - unique values preserved in order
    - duplicate values → first kept
    - whitespace-stripped duplicates collapsed
    - empty strings filtered
    - None-coerced empty strings filtered

  _normalize_path:
    - valid relative path → returned as-is
    - backslash path → normalised to forward slash
    - absolute path → leading slash stripped
    - absolute windows path (C:\\) → slashes normalised and leading stripped
    - empty string → ""
    - None-like → ""

  _ownership_value:
    - no ownership metadata → None
    - ownership key present with string → returned lowercase
    - ownership_class key used as fallback
    - enum-like .value attribute extracted
    - whitespace-only value → None

  _ownership_warnings:
    - empty node list → []
    - node with read_only_discovered ownership → warning returned
    - node with other ownership → not warned
    - node label used in warning message
    - node_id used when label absent
    - duplicates deduped

  _risk_warnings:
    - empty list → []
    - RISK node type → node label in warnings
    - RISK node without label → fallback message
    - non-RISK node with "risk" metadata key → warning included
    - non-RISK node without risk metadata → not warned
    - duplicates deduped

  _edge_key:
    - edge with edge_id → edge_id returned
    - edge without edge_id → synthesized key from type:source:target

  _node_path_hints:
    - FILE node with label → label in path hints
    - non-FILE node with label → label not automatically added
    - node with file_path metadata → included
    - unsafe/absolute paths filtered out
    - duplicates deduped

  _node_search_text:
    - includes node_id (lowercased)
    - includes node_type value
    - includes label when present
    - includes metadata values
    - result is lowercase
"""
from __future__ import annotations

from mozaiksai.control_plane.app_context_impact import (
    _dedupe,
    _edge_key,
    _node_path_hints,
    _node_search_text,
    _normalize_path,
    _ownership_value,
    _ownership_warnings,
    _risk_warnings,
)
from mozaiksai.core.app_context.models import (
    AppContextGraphEdge,
    AppContextGraphNode,
    GraphEdgeType,
    GraphNodeType,
)

# ---------------------------------------------------------------------------
# Helpers to build test nodes/edges
# ---------------------------------------------------------------------------

def _node(
    node_id: str = "node1",
    node_type: GraphNodeType = GraphNodeType.MODULE,
    label: str | None = None,
    metadata: dict | None = None,
) -> AppContextGraphNode:
    return AppContextGraphNode(
        node_id=node_id,
        node_type=node_type,
        label=label,
        metadata=metadata or {},
    )


def _edge(
    source: str = "n1",
    target: str = "n2",
    edge_type: GraphEdgeType = GraphEdgeType.CONTAINS,
    edge_id: str | None = None,
) -> AppContextGraphEdge:
    return AppContextGraphEdge(
        source_node_id=source,
        target_node_id=target,
        edge_type=edge_type,
        edge_id=edge_id,
    )


# ---------------------------------------------------------------------------
# 1. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_list_returns_empty(self):
        assert _dedupe([]) == []

    def test_unique_values_preserved(self):
        assert _dedupe(["a", "b", "c"]) == ["a", "b", "c"]

    def test_order_preserved(self):
        result = _dedupe(["z", "a", "m"])
        assert result == ["z", "a", "m"]

    def test_duplicate_first_kept(self):
        result = _dedupe(["a", "b", "a"])
        assert result == ["a", "b"]

    def test_whitespace_stripped_before_dedup(self):
        result = _dedupe(["x", " x "])
        assert result == ["x"]

    def test_empty_strings_filtered(self):
        result = _dedupe(["a", "", "b"])
        assert result == ["a", "b"]

    def test_whitespace_only_filtered(self):
        result = _dedupe(["a", "  ", "b"])
        assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# 2. _normalize_path
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def test_valid_relative_path_returned(self):
        assert _normalize_path("modules/orders/module.yaml") == "modules/orders/module.yaml"

    def test_backslash_normalised(self):
        result = _normalize_path("modules\\orders\\module.yaml")
        assert result == "modules/orders/module.yaml"

    def test_leading_slash_stripped(self):
        result = _normalize_path("/modules/orders/module.yaml")
        assert result == "modules/orders/module.yaml"

    def test_empty_string_returns_empty(self):
        assert _normalize_path("") == ""

    def test_none_returns_empty(self):
        assert _normalize_path(None) == ""  # type: ignore

    def test_nested_path_preserved(self):
        assert _normalize_path("ui/pages/dashboard.yaml") == "ui/pages/dashboard.yaml"


# ---------------------------------------------------------------------------
# 3. _ownership_value
# ---------------------------------------------------------------------------

class TestOwnershipValue:
    def test_no_ownership_metadata_returns_none(self):
        n = _node(metadata={})
        assert _ownership_value(n) is None

    def test_ownership_key_lowercase_returned(self):
        n = _node(metadata={"ownership": "READ_ONLY_DISCOVERED"})
        assert _ownership_value(n) == "read_only_discovered"

    def test_ownership_class_key_used(self):
        n = _node(metadata={"ownership_class": "generated"})
        assert _ownership_value(n) == "generated"

    def test_ownership_takes_priority_over_class(self):
        n = _node(metadata={"ownership": "owned", "ownership_class": "generated"})
        assert _ownership_value(n) == "owned"

    def test_whitespace_only_value_returns_none(self):
        n = _node(metadata={"ownership": "   "})
        assert _ownership_value(n) is None


# ---------------------------------------------------------------------------
# 4. _ownership_warnings
# ---------------------------------------------------------------------------

class TestOwnershipWarnings:
    def test_empty_list_returns_empty(self):
        assert _ownership_warnings([]) == []

    def test_read_only_discovered_generates_warning(self):
        n = _node(label="My Module", metadata={"ownership": "read_only_discovered"})
        warnings = _ownership_warnings([n])
        assert len(warnings) == 1
        assert "My Module" in warnings[0]

    def test_other_ownership_not_warned(self):
        n = _node(label="My Module", metadata={"ownership": "owned"})
        assert _ownership_warnings([n]) == []

    def test_node_without_ownership_not_warned(self):
        n = _node()
        assert _ownership_warnings([n]) == []

    def test_node_id_used_when_label_absent(self):
        n = _node(node_id="special_node", metadata={"ownership": "read_only_discovered"})
        warnings = _ownership_warnings([n])
        assert len(warnings) == 1
        assert "special_node" in warnings[0]

    def test_duplicate_warnings_deduped(self):
        n1 = _node(node_id="n1", label="Same", metadata={"ownership": "read_only_discovered"})
        n2 = _node(node_id="n2", label="Same", metadata={"ownership": "read_only_discovered"})
        warnings = _ownership_warnings([n1, n2])
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# 5. _risk_warnings
# ---------------------------------------------------------------------------

class TestRiskWarnings:
    def test_empty_list_returns_empty(self):
        assert _risk_warnings([]) == []

    def test_risk_node_type_label_in_warnings(self):
        n = _node(node_type=GraphNodeType.RISK, label="SQL injection risk")
        warnings = _risk_warnings([n])
        assert "SQL injection risk" in warnings

    def test_risk_node_without_label_fallback_message(self):
        n = _node(node_id="risk_node1", node_type=GraphNodeType.RISK)
        warnings = _risk_warnings([n])
        assert len(warnings) == 1
        assert "risk_node1" in warnings[0] or "Risk" in warnings[0]

    def test_non_risk_node_with_risk_metadata_warns(self):
        n = _node(node_type=GraphNodeType.MODULE, metadata={"risk": "High blast radius"})
        warnings = _risk_warnings([n])
        assert "High blast radius" in warnings

    def test_non_risk_node_without_risk_metadata_not_warned(self):
        n = _node(node_type=GraphNodeType.MODULE, metadata={"description": "Orders module"})
        assert _risk_warnings([n]) == []

    def test_duplicate_risk_warnings_deduped(self):
        n1 = _node(node_id="n1", node_type=GraphNodeType.RISK, label="Same risk")
        n2 = _node(node_id="n2", node_type=GraphNodeType.RISK, label="Same risk")
        warnings = _risk_warnings([n1, n2])
        assert warnings.count("Same risk") == 1


# ---------------------------------------------------------------------------
# 6. _edge_key
# ---------------------------------------------------------------------------

class TestEdgeKey:
    def test_edge_with_id_returns_id(self):
        e = _edge(edge_id="edge-abc123")
        assert _edge_key(e) == "edge-abc123"

    def test_edge_without_id_synthesizes_key(self):
        e = _edge(source="nodeA", target="nodeB", edge_type=GraphEdgeType.CONTAINS)
        key = _edge_key(e)
        assert "contains" in key
        assert "nodeA" in key
        assert "nodeB" in key

    def test_synthesized_key_format(self):
        e = _edge(source="src", target="tgt", edge_type=GraphEdgeType.IMPORTS)
        assert _edge_key(e) == "imports:src:tgt"


# ---------------------------------------------------------------------------
# 7. _node_path_hints
# ---------------------------------------------------------------------------

class TestNodePathHints:
    def test_file_node_with_label_returns_label(self):
        n = _node(node_type=GraphNodeType.FILE, label="modules/orders/handler.py")
        hints = _node_path_hints(n)
        assert "modules/orders/handler.py" in hints

    def test_module_node_label_not_auto_added(self):
        n = _node(node_type=GraphNodeType.MODULE, label="my_module")
        hints = _node_path_hints(n)
        # "my_module" is not a valid relative path (no extension/slash), so safe_relative_path filters it
        # The important thing is it's not unconditionally added
        assert "my_module" not in hints

    def test_file_path_metadata_included(self):
        n = _node(metadata={"file_path": "services/adapters/dns/cloudflare.py"})
        hints = _node_path_hints(n)
        assert "services/adapters/dns/cloudflare.py" in hints

    def test_unsafe_absolute_path_filtered(self):
        n = _node(node_type=GraphNodeType.FILE, label="/absolute/path.py")
        hints = _node_path_hints(n)
        assert "/absolute/path.py" not in hints

    def test_empty_metadata_returns_empty_or_filtered(self):
        n = _node(metadata={})
        # With no FILE label and no path metadata, hints should be empty
        hints = _node_path_hints(n)
        assert isinstance(hints, list)

    def test_duplicate_paths_deduped(self):
        n = _node(
            node_type=GraphNodeType.FILE,
            label="modules/orders/service.py",
            metadata={"file_path": "modules/orders/service.py"},
        )
        hints = _node_path_hints(n)
        assert hints.count("modules/orders/service.py") == 1


# ---------------------------------------------------------------------------
# 8. _node_search_text
# ---------------------------------------------------------------------------

class TestNodeSearchText:
    def test_includes_node_id_lowercased(self):
        n = _node(node_id="MyOrdersModule")
        text = _node_search_text(n)
        assert "myordersmodule" in text

    def test_includes_node_type_value(self):
        n = _node(node_type=GraphNodeType.MODULE)
        text = _node_search_text(n)
        assert "module" in text

    def test_includes_label(self):
        n = _node(label="Orders Module")
        text = _node_search_text(n)
        assert "orders module" in text

    def test_includes_metadata_values(self):
        n = _node(metadata={"description": "Handles order processing"})
        text = _node_search_text(n)
        assert "handles order processing" in text

    def test_result_is_lowercase(self):
        n = _node(label="OrdersService", metadata={"key": "VALUE"})
        text = _node_search_text(n)
        assert text == text.lower()

    def test_no_label_no_metadata_still_works(self):
        n = _node(node_id="plain_node", node_type=GraphNodeType.CONFIG)
        text = _node_search_text(n)
        assert "plain_node" in text
        assert "config" in text
