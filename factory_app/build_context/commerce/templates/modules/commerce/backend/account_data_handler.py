from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.composition.bson_safe import json_safe_bson

_PAGE_SIZE = 100


class AccountDataHandler:
    """GDPR data handler for commerce module records.

    Commerce stores user-linked data in three collections:
      - carts (actor_id = the shopper)
      - orders (actor_id = the buyer)
      - checkout_requests (actor_id = the buyer)

    Products are catalog data — not user-owned — and are excluded.

    Note on order retention: tax and accounting regulations in many jurisdictions
    require order records to be retained for 5–7 years even after a user data
    deletion request. App operators should review applicable regulations and may
    need to anonymise rather than hard-delete completed order records.
    """

    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence

    def _collection(self, app_id: str, user_id: str, name: str) -> Any:
        if self._persistence.app_id != app_id:
            raise PermissionError("account persistence is bound to another app")
        principal = self._persistence.principal
        if principal is None or not user_id or principal.user_id != user_id:
            raise PermissionError("account persistence is bound to another user")
        return self._persistence.collection("commerce", name)

    async def _records(self, app_id: str, user_id: str, name: str) -> list[dict[str, Any]]:
        collection = self._collection(app_id, user_id, name)
        query: dict[str, Any] = {"actor_id": user_id}
        records = []
        while True:
            page = await collection.find_many(query, limit=_PAGE_SIZE, sort=[("_id", 1)])
            records.extend({key: value for key, value in row.items() if key != "_id"} for row in page)
            if len(page) < _PAGE_SIZE:
                return records
            query = {"actor_id": user_id, "_id": {"$gt": page[-1]["_id"]}}

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Delete cart, order, and checkout request records owned by this user."""
        query = {"actor_id": user_id}

        carts_coll = self._collection(app_id, user_id, "carts")
        carts_result = await carts_coll.delete_many(query)
        carts_deleted = int(getattr(carts_result, "deleted_count", 0) or 0)

        orders_coll = self._collection(app_id, user_id, "orders")
        orders_result = await orders_coll.delete_many(query)
        orders_deleted = int(getattr(orders_result, "deleted_count", 0) or 0)

        checkout_coll = self._collection(app_id, user_id, "checkout_requests")
        checkout_result = await checkout_coll.delete_many(query)
        checkout_deleted = int(getattr(checkout_result, "deleted_count", 0) or 0)

        return {
            "module": "commerce",
            "carts_deleted": carts_deleted,
            "orders_deleted": orders_deleted,
            "checkout_requests_deleted": checkout_deleted,
        }

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Export cart, order, and checkout request records owned by this user."""
        carts_coll = self._collection(app_id, user_id, "carts")
        cart = await carts_coll.find_one({"actor_id": user_id}, {"_id": 0})
        return json_safe_bson({
            "module": "commerce",
            "cart": dict(cart) if cart else None,
            "orders": await self._records(app_id, user_id, "orders"),
            "checkout_requests": await self._records(app_id, user_id, "checkout_requests"),
        })
