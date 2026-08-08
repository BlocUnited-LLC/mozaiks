from __future__ import annotations

from mozaiksai.core.app_context import (
    build_app_intelligence_catalog,
    index_file_map,
    search_app_intelligence_snapshot,
)


def test_app_intelligence_snapshot_summarizes_code_without_source_dump() -> None:
    indexed = index_file_map(
        app_id="market_app",
        build_record_id="av_market",
        file_map={
            "app/modules/listings/module.yaml": "id: listings\nactions:\n  - id: publish_listing\n",
            "app/modules/listings/backend/handler.py": "def publish_listing(payload):\n    return payload\n",
            "app/modules/listings/backend/schemas.py": "class Listing:\n    pass\n",
            "app/services/adapters/payments/stripe.py": "def create_checkout():\n    return None\n",
            "app/ui/pages/custom/MarketplacePage.jsx": (
                "export default function MarketplacePage() { return <main>Listings</main>; }\n"
            ),
            "tests/test_listings.py": "def test_publish_listing():\n    assert True\n",
        },
    )

    snapshot = indexed.app_intelligence_snapshot
    catalog = build_app_intelligence_catalog(snapshot)

    assert snapshot.schema_version == "mozaiks.app_intelligence.snapshot.v1"
    assert snapshot.coverage["file_count"] == 6
    assert snapshot.architecture["module_roots"][0]["module_id"] == "listings"
    assert any(item["capability_id"] == "module:listings" for item in snapshot.capabilities)
    assert any(item["surface_type"] == "integration" for item in snapshot.integration_surfaces)
    assert "file_contents" not in catalog
    assert catalog["agent_context_policy"]["policy"] == "retrieve_not_dump"

    results = search_app_intelligence_snapshot(snapshot, "listing checkout", max_results=5)
    assert results
    assert any("listings" in str(item).lower() for item in results)

