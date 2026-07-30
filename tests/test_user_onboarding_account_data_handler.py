from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factory_app.app.modules.user_onboarding.backend.account_data_handler import (
    AccountDataHandler,
    _collection_name,
)


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self, *, length: int | None = None) -> list[dict[str, Any]]:
        return list(self._docs if length is None else self._docs[:length])


@dataclass
class DeleteResult:
    deleted_count: int


class FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def find(self, query: dict[str, Any], projection: dict[str, Any]) -> FakeCursor:
        matched = [
            {
                key: value
                for key, value in doc.items()
                if not (projection.get("_id") == 0 and key == "_id")
            }
            for doc in self.docs
            if all(doc.get(key) == value for key, value in query.items())
        ]
        return FakeCursor(matched)

    async def delete_many(self, query: dict[str, Any]) -> DeleteResult:
        before = len(self.docs)
        self.docs = [
            doc
            for doc in self.docs
            if not all(doc.get(key) == value for key, value in query.items())
        ]
        return DeleteResult(deleted_count=before - len(self.docs))


class FakeDb(dict[str, FakeCollection]):
    pass


async def test_user_onboarding_account_data_handler_exports_and_deletes_scoped_state() -> None:
    app_id = "demo-app"
    collection = FakeCollection(
        [
            {
                "_id": "private-object-id",
                "app_id": app_id,
                "user_id": "alice",
                "seen_welcome": True,
                "dismissed": False,
            },
            {
                "_id": "other-user",
                "app_id": app_id,
                "user_id": "bob",
                "seen_welcome": True,
                "dismissed": True,
            },
        ]
    )
    handler = AccountDataHandler(db=FakeDb({_collection_name(app_id): collection}))

    exported = await handler.export_user_data(app_id=app_id, user_id="alice")
    assert exported == {
        "user_onboarding_status": [
            {
                "app_id": app_id,
                "user_id": "alice",
                "seen_welcome": True,
                "dismissed": False,
            }
        ]
    }

    result = await handler.delete_user_data(app_id=app_id, user_id="alice")
    assert result == {"deleted_count": 1}
    assert [doc["user_id"] for doc in collection.docs] == ["bob"]
