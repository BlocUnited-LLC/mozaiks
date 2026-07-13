"""Tenant isolation assertion helpers and smoke tests.

Provides ``assert_cross_tenant_read_is_blocked``, a reusable helper that any
generated-app test suite can import to verify that a repo query method never
returns records belonging to a different tenant's ``app_id``.

The helper works by:
1. Injecting two documents with distinct ``app_id`` values into a real (or
   in-memory stub) MongoDB collection.
2. Calling the repo method under test with *tenant A's* ``app_id``.
3. Asserting that none of the returned records carry *tenant B's* ``app_id``.

This module also contains pure unit tests for the ``build_app_scope_filter``
primitive, which is the canonical multi-tenant scope anchor used by all
generated repo layers.

Covered:

  build_app_scope_filter:
    - normal app_id → {"app_id": value}
    - whitespace-only app_id → __invalid__ sentinel
    - empty string → __invalid__ sentinel
    - None → __invalid__ sentinel

  assert_cross_tenant_read_is_blocked (pure helper):
    - returns no records for empty result (vacuous pass)
    - raises AssertionError when leaked record detected

  Cross-tenant scope filter composition:
    - filter for tenant A does not match tenant B's document key
    - __invalid__ sentinel never matches a real app_id
"""
from __future__ import annotations

from typing import Any

import pytest

from mozaiksai.core.multitenant.app_ids import build_app_scope_filter

# ---------------------------------------------------------------------------
# build_app_scope_filter unit tests
# ---------------------------------------------------------------------------


def test_build_app_scope_filter_normal() -> None:
    f = build_app_scope_filter("app_abc")
    assert f == {"app_id": "app_abc"}


def test_build_app_scope_filter_trims_whitespace() -> None:
    f = build_app_scope_filter("  app_abc  ")
    assert f == {"app_id": "app_abc"}


def test_build_app_scope_filter_empty_string_returns_invalid() -> None:
    f = build_app_scope_filter("")
    assert f == {"app_id": "__invalid__"}


def test_build_app_scope_filter_whitespace_only_returns_invalid() -> None:
    f = build_app_scope_filter("   ")
    assert f == {"app_id": "__invalid__"}


def test_build_app_scope_filter_none_returns_invalid() -> None:
    f = build_app_scope_filter(None)  # type: ignore[arg-type]
    assert f == {"app_id": "__invalid__"}


def test_invalid_sentinel_never_matches_real_app_id() -> None:
    """The __invalid__ sentinel must never match a real tenant document."""
    tenant_a_doc = {"app_id": "tenant_a", "value": 1}
    filter_for_invalid = build_app_scope_filter("")
    # Simulates a Mongo equality match: doc["app_id"] == filter["app_id"]
    assert tenant_a_doc.get("app_id") != filter_for_invalid.get("app_id")


def test_tenant_a_filter_does_not_match_tenant_b_doc() -> None:
    filter_a = build_app_scope_filter("tenant_a")
    doc_b = {"app_id": "tenant_b", "value": 2}
    assert doc_b.get("app_id") != filter_a.get("app_id")


# ---------------------------------------------------------------------------
# assert_cross_tenant_read_is_blocked — reusable helper
# ---------------------------------------------------------------------------


def assert_cross_tenant_read_is_blocked(
    records: list[dict[str, Any]],
    *,
    queried_app_id: str,
    leaked_app_id: str,
) -> None:
    """Assert that none of *records* belong to a different tenant.

    Call this after invoking a repo list/query method with *queried_app_id*.
    It fails the test immediately if any returned record carries
    *leaked_app_id*, which indicates a missing or incorrect ``app_id`` scope
    filter in the repo layer.

    Usage in a generated-app test::

        records = await repo.list_items(ctx_tenant_a, ...)
        assert_cross_tenant_read_is_blocked(
            records,
            queried_app_id="tenant_a",
            leaked_app_id="tenant_b",
        )

    Args:
        records: The list of documents returned by the repo method.
        queried_app_id: The ``app_id`` passed to the repo method.
        leaked_app_id: The ``app_id`` that must *not* appear in any record.

    Raises:
        AssertionError: When any record's ``app_id`` matches *leaked_app_id*.
    """
    leaked = [r for r in records if r.get("app_id") == leaked_app_id]
    assert not leaked, (
        f"Cross-tenant data leak detected: repo method queried for "
        f"app_id={queried_app_id!r} but returned {len(leaked)} record(s) "
        f"with app_id={leaked_app_id!r}. "
        f"Ensure the repo layer applies build_app_scope_filter(app_id) to "
        f"all MongoDB queries."
    )


# ---------------------------------------------------------------------------
# Unit tests for assert_cross_tenant_read_is_blocked itself
# ---------------------------------------------------------------------------


def test_helper_passes_on_empty_result() -> None:
    assert_cross_tenant_read_is_blocked(
        [],
        queried_app_id="tenant_a",
        leaked_app_id="tenant_b",
    )


def test_helper_passes_when_all_records_match_queried_tenant() -> None:
    records = [{"app_id": "tenant_a", "x": 1}, {"app_id": "tenant_a", "x": 2}]
    assert_cross_tenant_read_is_blocked(
        records,
        queried_app_id="tenant_a",
        leaked_app_id="tenant_b",
    )


def test_helper_fails_when_leaked_record_present() -> None:
    records = [
        {"app_id": "tenant_a", "x": 1},
        {"app_id": "tenant_b", "x": 2},  # leaked!
    ]
    with pytest.raises(AssertionError, match="Cross-tenant data leak detected"):
        assert_cross_tenant_read_is_blocked(
            records,
            queried_app_id="tenant_a",
            leaked_app_id="tenant_b",
        )


def test_helper_fails_on_multiple_leaked_records() -> None:
    records = [{"app_id": "tenant_b"}, {"app_id": "tenant_b"}]
    with pytest.raises(AssertionError, match="2 record"):
        assert_cross_tenant_read_is_blocked(
            records,
            queried_app_id="tenant_a",
            leaked_app_id="tenant_b",
        )


def test_helper_ignores_records_without_app_id_field() -> None:
    """Records missing the app_id field are not counted as leaks."""
    records = [{"x": 1}, {"app_id": "tenant_a", "x": 2}]
    assert_cross_tenant_read_is_blocked(
        records,
        queried_app_id="tenant_a",
        leaked_app_id="tenant_b",
    )
