from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mozaiksai.core.runtime.persistence import app_data_from_context

_PRODUCTS_ALIAS = "commerce.products"
_CARTS_ALIAS = "commerce.carts"
_ORDERS_ALIAS = "commerce.orders"
_CHECKOUT_REQUESTS_ALIAS = "commerce.checkout_requests"

Document = dict[str, Any]


def _persistence(ctx):
    return app_data_from_context(ctx)


def _normalize_doc(value: Any) -> Document | None:
    return dict(value) if isinstance(value, dict) else None


def _normalize_docs(value: Any) -> list[Document]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


class ProductRepo:
    async def list(
        self,
        ctx,
        *,
        query: dict[str, Any],
        limit: int,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        if before:
            query = {**query, "updated_at": {"$lt": before}}
        col = _persistence(ctx).collection(_PRODUCTS_ALIAS)
        cursor = col.find(query, {"_id": 0}).sort("updated_at", -1).limit(limit)
        return _normalize_docs(await cursor.to_list(length=limit))

    async def get(self, ctx, *, product_id: str | None = None, slug: str | None = None) -> dict[str, Any] | None:
        query: dict[str, Any] = {"product_id": product_id} if product_id else {"slug": slug}
        col = _persistence(ctx).collection(_PRODUCTS_ALIAS)
        return _normalize_doc(await col.find_one(query, {"_id": 0}))

    async def insert(self, ctx, *, doc: Mapping[str, Any]) -> None:
        await _persistence(ctx).collection(_PRODUCTS_ALIAS).insert_one(dict(doc))

    async def update(self, ctx, *, product_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        col = _persistence(ctx).collection(_PRODUCTS_ALIAS)
        await col.update_one({"product_id": product_id}, {"$set": dict(updates)})
        return await self.get(ctx, product_id=product_id)

    async def adjust_inventory(self, ctx, *, product_id: str, delta: int) -> dict[str, Any] | None:
        col = _persistence(ctx).collection(_PRODUCTS_ALIAS)
        await col.update_one(
            {"product_id": product_id, "track_inventory": True},
            {"$inc": {"inventory_quantity": int(delta)}},
        )
        return await self.get(ctx, product_id=product_id)

    async def reserve_inventory(self, ctx, *, product_id: str, quantity: int) -> bool:
        col = _persistence(ctx).collection(_PRODUCTS_ALIAS)
        result = await col.update_one(
            {
                "product_id": product_id,
                "track_inventory": True,
                "inventory_quantity": {"$gte": int(quantity)},
            },
            {"$inc": {"inventory_quantity": -int(quantity)}},
        )
        return bool(result.modified_count)


class CartRepo:
    async def get_active(self, ctx, *, actor_id: str) -> dict[str, Any] | None:
        col = _persistence(ctx).collection(_CARTS_ALIAS)
        return _normalize_doc(await col.find_one({"actor_id": actor_id, "status": "active"}, {"_id": 0}))

    async def upsert_active(self, ctx, *, actor_id: str, cart: dict[str, Any]) -> dict[str, Any]:
        col = _persistence(ctx).collection(_CARTS_ALIAS)
        await col.update_one(
            {"actor_id": actor_id, "status": "active"},
            {"$set": dict(cart), "$setOnInsert": {"cart_id": cart["cart_id"]}},
            upsert=True,
        )
        saved = await self.get_active(ctx, actor_id=actor_id)
        return saved or cart

    async def mark_converted(self, ctx, *, cart_id: str, now: str) -> None:
        col = _persistence(ctx).collection(_CARTS_ALIAS)
        await col.update_one(
            {"cart_id": cart_id},
            {"$set": {"status": "converted", "updated_at": now}},
        )


class OrderRepo:
    async def insert(self, ctx, *, doc: Mapping[str, Any]) -> None:
        await _persistence(ctx).collection(_ORDERS_ALIAS).insert_one(dict(doc))

    async def list(
        self,
        ctx,
        *,
        query: dict[str, Any],
        limit: int,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        if before:
            query = {**query, "created_at": {"$lt": before}}
        col = _persistence(ctx).collection(_ORDERS_ALIAS)
        cursor = col.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
        return _normalize_docs(await cursor.to_list(length=limit))

    async def get(self, ctx, *, order_id: str) -> dict[str, Any] | None:
        col = _persistence(ctx).collection(_ORDERS_ALIAS)
        return _normalize_doc(await col.find_one({"order_id": order_id}, {"_id": 0}))

    async def find_by_provider_reference(self, ctx, *, provider_reference: str) -> dict[str, Any] | None:
        if not provider_reference:
            return None
        col = _persistence(ctx).collection(_ORDERS_ALIAS)
        return _normalize_doc(await col.find_one({"payment.provider_reference": provider_reference}, {"_id": 0}))

    async def update(self, ctx, *, order_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        col = _persistence(ctx).collection(_ORDERS_ALIAS)
        await col.update_one({"order_id": order_id}, {"$set": dict(updates)})
        return await self.get(ctx, order_id=order_id)


class CheckoutRequestRepo:
    async def insert(self, ctx, *, doc: Mapping[str, Any]) -> None:
        await _persistence(ctx).collection(_CHECKOUT_REQUESTS_ALIAS).insert_one(dict(doc))

    async def update_status(self, ctx, *, order_id: str, updates: dict[str, Any]) -> None:
        col = _persistence(ctx).collection(_CHECKOUT_REQUESTS_ALIAS)
        await col.update_one({"order_id": order_id}, {"$set": dict(updates)})
