from __future__ import annotations

from collections.abc import Mapping
from typing import Any

Document = dict[str, Any]
_WITHOUT_ID = {"_id": 0}


def _collection(ctx, entity_name: str):
    persistence = getattr(ctx, "persistence", None)
    if persistence is None:
        raise RuntimeError("Persistence is not available for this app context.")
    return persistence.collection("commerce", entity_name)


def _normalize_doc(value: Any) -> Document | None:
    return dict(value) if isinstance(value, Mapping) else None


def _normalize_docs(value: Any) -> list[Document]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


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
        rows = await _collection(ctx, "products").find_many(
            query,
            limit=limit,
            sort=[("updated_at", -1)],
            projection=_WITHOUT_ID,
        )
        return _normalize_docs(rows)

    async def get(self, ctx, *, product_id: str | None = None, slug: str | None = None) -> dict[str, Any] | None:
        query: dict[str, Any] = {"product_id": product_id} if product_id else {"slug": slug}
        return _normalize_doc(await _collection(ctx, "products").find_one(query, _WITHOUT_ID))

    async def insert(self, ctx, *, doc: Mapping[str, Any]) -> None:
        await _collection(ctx, "products").insert_one(dict(doc))

    async def update(self, ctx, *, product_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        await _collection(ctx, "products").update_one({"product_id": product_id}, {"$set": dict(updates)})
        return await self.get(ctx, product_id=product_id)

    async def adjust_inventory(self, ctx, *, product_id: str, delta: int) -> dict[str, Any] | None:
        await _collection(ctx, "products").update_one(
            {"product_id": product_id, "track_inventory": True},
            {"$inc": {"inventory_quantity": int(delta)}},
        )
        return await self.get(ctx, product_id=product_id)

    async def reserve_inventory(self, ctx, *, product_id: str, quantity: int) -> bool:
        result = await _collection(ctx, "products").update_one(
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
        return _normalize_doc(
            await _collection(ctx, "carts").find_one({"actor_id": actor_id, "status": "active"}, _WITHOUT_ID)
        )

    async def upsert_active(self, ctx, *, actor_id: str, cart: dict[str, Any]) -> dict[str, Any]:
        # Mongo rejects one path in both $set and $setOnInsert, so cart_id is set only on insert.
        changes = {key: value for key, value in cart.items() if key != "cart_id"}
        await _collection(ctx, "carts").update_one(
            {"actor_id": actor_id, "status": "active"},
            {"$set": changes, "$setOnInsert": {"cart_id": cart["cart_id"]}},
            upsert=True,
        )
        saved = await self.get_active(ctx, actor_id=actor_id)
        return saved or cart

    async def mark_converted(self, ctx, *, cart_id: str, now: str) -> None:
        await _collection(ctx, "carts").update_one(
            {"cart_id": cart_id},
            {"$set": {"status": "converted", "updated_at": now}},
        )


class OrderRepo:
    async def insert(self, ctx, *, doc: Mapping[str, Any]) -> None:
        await _collection(ctx, "orders").insert_one(dict(doc))

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
        rows = await _collection(ctx, "orders").find_many(
            query,
            limit=limit,
            sort=[("created_at", -1)],
            projection=_WITHOUT_ID,
        )
        return _normalize_docs(rows)

    async def get(self, ctx, *, order_id: str) -> dict[str, Any] | None:
        return _normalize_doc(await _collection(ctx, "orders").find_one({"order_id": order_id}, _WITHOUT_ID))

    async def find_by_provider_reference(self, ctx, *, provider_reference: str) -> dict[str, Any] | None:
        if not provider_reference:
            return None
        return _normalize_doc(
            await _collection(ctx, "orders").find_one({"payment.provider_reference": provider_reference}, _WITHOUT_ID)
        )

    async def update(self, ctx, *, order_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        await _collection(ctx, "orders").update_one({"order_id": order_id}, {"$set": dict(updates)})
        return await self.get(ctx, order_id=order_id)


class CheckoutRequestRepo:
    async def insert(self, ctx, *, doc: Mapping[str, Any]) -> None:
        await _collection(ctx, "checkout_requests").insert_one(dict(doc))

    async def update_status(self, ctx, *, order_id: str, updates: dict[str, Any]) -> None:
        await _collection(ctx, "checkout_requests").update_one({"order_id": order_id}, {"$set": dict(updates)})
