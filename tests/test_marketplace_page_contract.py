from __future__ import annotations

import yaml

from conftest import active_app_root


def test_marketplace_page_is_data_driven() -> None:
    app_root = active_app_root()
    page_path = app_root / "ui" / "pages" / "marketplace.yaml"
    page = yaml.safe_load(page_path.read_text(encoding="utf-8"))
    sections = {section["id"]: section for section in page["sections"]}

    hero = sections["marketplace-hero"]
    kpis = sections["marketplace-kpis"]
    table = sections["marketplace-listings"]

    hero_actions = {action["id"]: action for action in hero["config"]["actions"]}
    assert kpis["config"]["api_endpoint"] == "/api/modules/investor_marketplace/get_marketplace_summary"
    assert table["config"]["api_endpoint"] == "/api/modules/investor_marketplace/list_listings?limit=12"
    assert "data" not in table["config"]
    assert kpis["config"]["children"][0]["config"]["value_key"] == "metrics.live_listings.value"
    assert table["config"]["actions"][1]["href"] == "/api/modules/investor_marketplace/record_investment_interest"
    assert hero_actions["open-build-journey"]["href"] == "/create"
    assert table["config"]["empty"]["action"]["href"] == "/create"
