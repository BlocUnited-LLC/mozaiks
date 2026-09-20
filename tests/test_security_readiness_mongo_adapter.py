"""The security module must consume the real app-scoped persistence adapter."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory_app.app.modules.security_readiness.backend.service import SecurityReadinessService
from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceContext


@pytest.mark.asyncio
async def test_record_list_and_update_use_real_mongo_adapter_scope():
    documents = []
    queries = []

    def matching(query):
        queries.append(dict(query))
        return [row for row in documents if all(row.get(key) == value for key, value in query.items())]

    async def update_one(query, update, *, upsert=False):
        rows = matching(query)
        if rows:
            rows[0].update(update["$set"])
        elif upsert:
            documents.append({**query, **update["$set"]})

    def find(query, projection=None):
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.limit.return_value = cursor
        cursor.to_list = AsyncMock(return_value=matching(query))
        return cursor

    async def find_one(query, projection=None):
        return next(iter(matching(query)), None)

    raw = MagicMock()
    raw.update_one = update_one
    raw.find = find
    raw.find_one = find_one
    client = MagicMock()
    client.__getitem__.return_value.__getitem__.return_value = raw
    persistence = MongoPersistenceContext(app_id="host-app", user_id="owner", client=client)
    ctx = SimpleNamespace(app_id="host-app", user_id="owner", persistence=persistence, emit=AsyncMock())
    service = SecurityReadinessService()
    for project in ("project-one", "project-two"):
        result = await service.record_assessment(ctx, app_id="host-app", build_registry_id=project,
            findings=[{"finding_id": "same-scanner-key", "title": "Auth review", "severity": "high", "control_area": "auth"}])
        assert result["saved"] == 1
        assert result["summary"]["total"] == 1
    listed = await service.list_findings(ctx, app_id="host-app", build_registry_id="project-one")
    assert listed["count"] == 1
    finding_id = listed["findings"][0]["finding_id"]
    updated = await service.update_finding_status(ctx, finding_id=finding_id, status="resolved")
    assert updated["finding"]["status"] == "resolved"
    summary = await service.get_summary(ctx, app_id="host-app", build_registry_id="project-one")
    assert summary["summary"]["resolved"] == 1
    assert len(documents) == 2
    assert len({row["finding_id"] for row in documents}) == 2
    assert all(query["app_id"] == "host-app" and query["owner_user_id"] == "owner" for query in queries)
    count_before = len(queries)
    with pytest.raises(ValueError, match="persistence context app_id"):
        await service.list_findings(ctx, app_id="other-app")
    with pytest.raises(ValueError, match="persistence context app_id"):
        await service.record_assessment(ctx, app_id="other-app", findings=[{"title": "Cannot store here"}])
    assert len(queries) == count_before
    assert len(documents) == 2
