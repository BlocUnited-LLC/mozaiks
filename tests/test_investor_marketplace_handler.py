from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_handler_module():
    platform_path = os.environ.get("PLATFORM_PATH", "").strip()
    workspace_path = os.environ.get("MOZAIKS_APP_WORKSPACE_PATH", "").strip()
    if platform_path:
        app_root = Path(platform_path)
    elif workspace_path:
        app_root = Path(workspace_path) / "app"
    else:
        pytest.skip("No active app workspace configured. Set MOZAIKS_APP_WORKSPACE_PATH or PLATFORM_PATH.", allow_module_level=True)
    file_path = app_root / "modules" / "investor_marketplace" / "backend" / "handler.py"
    module_name = "tests.investor_marketplace_handler_direct"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


handler_module = _load_handler_module()


def _matches_query(document: dict, query: dict) -> bool:
    for key, expected in query.items():
        value = document.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if value not in expected["$in"]:
                return False
            continue
        if value != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._limit = None

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        self._limit = count
        return self

    async def to_list(self, length=None):
        count = length if length is not None else self._limit
        if count is None:
            return list(self._docs)
        return list(self._docs)[:count]


class _Collection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, query, _projection=None):
        return _Cursor([doc for doc in self._docs if _matches_query(doc, query)])

    async def count_documents(self, query):
        return len([doc for doc in self._docs if _matches_query(doc, query)])


@pytest.mark.asyncio
async def test_list_listings_returns_renderer_friendly_rows_when_marketplace_is_empty(monkeypatch):
    collections = {
        "hosted_marketplace_listings": _Collection([]),
        "hosted_investor_profiles": _Collection([]),
        "hosted_marketplace_interest": _Collection([]),
    }

    async def fake_collection(name: str):
        return collections[name]

    monkeypatch.setattr(handler_module, "_collection", fake_collection)

    handler = handler_module.InvestorMarketplaceHandler()
    result = await handler.list_listings(SimpleNamespace(user_id="user_investor"), limit=12)

    assert result["count"] == 3
    assert len(result["rows"]) == 3
    assert result["rows"][0]["listing_id"] == "listing_atlas_finance"
    assert result["rows"][0]["funding_goal_display"] == "$2.4M"
    assert result["rows"][0]["updated_at_display"] == "Apr 22, 2026"


@pytest.mark.asyncio
async def test_get_marketplace_summary_returns_metrics_contract_when_marketplace_is_empty(monkeypatch):
    collections = {
        "hosted_marketplace_listings": _Collection([]),
        "hosted_investor_profiles": _Collection([]),
        "hosted_marketplace_interest": _Collection([]),
    }

    async def fake_collection(name: str):
        return collections[name]

    monkeypatch.setattr(handler_module, "_collection", fake_collection)

    handler = handler_module.InvestorMarketplaceHandler()
    result = await handler.get_marketplace_summary(SimpleNamespace(user_id="user_investor"))

    assert result["metrics"]["live_listings"]["value"] == 3
    assert result["metrics"]["submitted_listings"]["value"] == 1
    assert result["metrics"]["investor_profiles"]["value"] == 18
    assert result["metrics"]["investment_interests"]["value"] == 27
