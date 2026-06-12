from __future__ import annotations

import pytest
import yaml

from tests.import_utils import active_app_root


def test_marketplace_page_is_data_driven() -> None:
    app_root = active_app_root()
    page_path = app_root / "ui" / "pages" / "marketplace.yaml"
    if not page_path.exists():
        pytest.skip("Product marketplace page is not present in the active app workspace")
    page = yaml.safe_load(page_path.read_text(encoding="utf-8"))
    sections = {section["id"]: section for section in page["sections"]}

    hero = sections.get("marketplace-hero") or sections["marketplace-header"]
    kpis = sections["marketplace-kpis"]
    table = sections["marketplace-listings"]

    hero_actions = {action["id"]: action for action in hero["config"]["actions"]}
    assert kpis["config"]["api_endpoint"] == "/api/modules/investor_marketplace/get_marketplace_summary"
    assert kpis["primitive"] == "SummaryStrip"
    assert table["config"]["api_endpoint"] == "/api/modules/investor_marketplace/list_listings"
    assert table["config"]["page_size"] == 12
    assert "data" not in table["config"]
    first_metric = kpis["config"]["items"][0]
    assert first_metric["value_key"] == "metrics.live_listings.value"
    assert first_metric["format"] == "number"
    assert table["config"]["actions"][1]["href"] == "/api/modules/investor_marketplace/record_investment_interest"
    assert hero_actions["open-build-journey"]["href"] == "/create"
    assert table["config"]["empty"]["action"]["href"] == "/create"

