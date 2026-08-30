from __future__ import annotations

from typing import Any


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

    def __init__(self, db: Any) -> None:
        self._db = db

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Delete cart, order, and checkout request records owned by this user."""
        query = {"app_id": app_id, "actor_id": user_id}

        carts_coll = self._db.get_collection("commerce", "carts")
        carts_result = await carts_coll.delete_many(query)
        carts_deleted = int(getattr(carts_result, "deleted_count", 0) or 0)

        orders_coll = self._db.get_collection("commerce", "orders")
        orders_result = await orders_coll.delete_many(query)
        orders_deleted = int(getattr(orders_result, "deleted_count", 0) or 0)

        checkout_coll = self._db.get_collection("commerce", "checkout_requests")
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
        query = {"app_id": app_id, "actor_id": user_id}

        carts_coll = self._db.get_collection("commerce", "carts")
        cart = await carts_coll.find_one(query)

        orders_coll = self._db.get_collection("commerce", "orders")
        orders = await orders_coll.find_many(query)

        checkout_coll = self._db.get_collection("commerce", "checkout_requests")
        checkout_requests = await checkout_coll.find_many(query)

        return {
            "module": "commerce",
            "cart": dict(cart) if cart else None,
            "orders": [dict(o) for o in (orders or [])],
            "checkout_requests": [dict(c) for c in (checkout_requests or [])],
        }
