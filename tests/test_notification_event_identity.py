from copy import deepcopy
from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

from mozaiksai.core import core_config
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter


@pytest.mark.asyncio
async def test_notification_replay_after_router_restart_keeps_one_durable_alert(monkeypatch):
    rows = {}

    class Collection:
        async def insert_one(self, record):
            if record["_id"] in rows:
                raise DuplicateKeyError("replayed notification")
            rows[record["_id"]] = deepcopy(record)
            return SimpleNamespace(acknowledged=True)

        async def find_one(self, query):
            return deepcopy(rows.get(query["_id"]))

    monkeypatch.setattr(core_config, "get_mongo_client", lambda: {"mozaiks": {"platform_notifications": Collection()}})
    first = {"notification_id": "initial", "app_id": "app", "module_id": "friends", "rule_id": "accepted",
             "event_type": "domain.social.friendship.created", "source_event_id": "persisted-event", "title": "Accepted"}
    assert await ModuleEventRouter([])._store_notification(first) is True
    next(iter(rows.values()))["status"] = "read"
    retried = {**first, "notification_id": "different", "title": "retry must preserve the stored alert", "status": "unread"}
    assert await ModuleEventRouter([])._store_notification(retried) is True
    assert first["notification_id"] == retried["notification_id"]
    assert len(rows) == 1
    assert next(iter(rows.values()))["title"] == "Accepted"
    assert retried["title"] == "Accepted"
    assert retried["status"] == "read"
    for change in [{"app_id": "other"}, {"workspace_id": "other"}, {"rule_id": "other"}]:
        assert await ModuleEventRouter([])._store_notification({**first, **change}) is True
    assert len(rows) == 4
