from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from .policy import (
    actor_id,
    can_manage_catalog,
    can_manage_orders,
    order_scope_query,
    product_visibility_query,
)
from .repo import CartRepo, CheckoutRequestRepo, OrderRepo, ProductRepo
from .schemas import (
    CART_STATUS_ACTIVE,
    CHECKOUT_RESULT_STATUSES,
    DEFAULT_CURRENCY,
    ORDER_STATUSES,
    PRODUCT_STATUSES,
    coerce_limit,
    coerce_quantity,
    minor_units_to_display,
    money_to_minor_units,
    normalize_currency,
    normalize_status,
    product_snapshot,
    recalculate_cart,
    serialize_order,
    serialize_product,
    timestamp_now,
)


def _clean_text(value: Any, *, max_length: int, default: str = "") -> str:
    text = str(value or default).strip()
    return text[:max_length]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:90] or str(uuid4())


def _require_actor(ctx) -> str | None:
    actor = actor_id(ctx)
    return actor or None


class CommerceService:
    def __init__(
        self,
        products: ProductRepo | None = None,
        carts: CartRepo | None = None,
        orders: OrderRepo | None = None,
        checkout_requests: CheckoutRequestRepo | None = None,
    ) -> None:
        self.products = products or ProductRepo()
        self.carts = carts or CartRepo()
        self.orders = orders or OrderRepo()
        self.checkout_requests = checkout_requests or CheckoutRequestRepo()

    # ---------------------------------------------------------------------
    # Catalog
    # ---------------------------------------------------------------------

    async def list_products(
        self,
        ctx,
        *,
        search: str | None = None,
        category: str | None = None,
        status: str | None = None,
        before: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        try:
            requested_status = normalize_status(
                status, allowed=PRODUCT_STATUSES, default="active"
            ) if status else None
        except ValueError as exc:
            return {"products": [], "count": 0, "next_cursor": None, "error": str(exc)}

        query = product_visibility_query(ctx, status=requested_status)
        clean_category = _clean_text(category, max_length=80)
        if clean_category:
            query["category"] = clean_category
        clean_search = _clean_text(search, max_length=100)
        if clean_search:
            query["$or"] = [
                {"title": {"$regex": clean_search, "$options": "i"}},
                {"description": {"$regex": clean_search, "$options": "i"}},
                {"sku": {"$regex": clean_search, "$options": "i"}},
            ]

        page_size = coerce_limit(limit, default=20)
        rows = await self.products.list(ctx, query=query, limit=page_size + 1, before=before)
        has_more = len(rows) > page_size
        if has_more:
            rows = rows[:page_size]
        next_cursor = rows[-1]["updated_at"] if has_more and rows else None
        return {
            "products": [serialize_product(row) for row in rows],
            "count": len(rows),
            "next_cursor": next_cursor,
        }

    async def get_product(
        self,
        ctx,
        *,
        product_id: str | None = None,
        slug: str | None = None,
    ) -> dict[str, Any]:
        if not product_id and not slug:
            return {"product": None, "error": "product_id or slug is required"}
        product = await self.products.get(ctx, product_id=product_id, slug=slug)
        if not product:
            return {"product": None, "error": "product not found"}
        if not can_manage_catalog(ctx) and product.get("status") != "active":
            return {"product": None, "error": "product not found"}
        return {"product": serialize_product(product)}

    async def create_product(
        self,
        ctx,
        *,
        title: str,
        price_amount: Any,
        slug: str | None = None,
        description: str = "",
        category: str = "",
        sku: str | None = None,
        status: str = "draft",
        currency: str = DEFAULT_CURRENCY,
        inventory_quantity: int | None = None,
        track_inventory: bool = False,
        image_url: str | None = None,
    ) -> dict[str, Any]:
        actor = _require_actor(ctx)
        if not actor:
            return {"success": False, "error": "user_id or session_id is required"}

        clean_title = _clean_text(title, max_length=140)
        if not clean_title:
            return {"success": False, "error": "title is required"}
        try:
            clean_status = normalize_status(status, allowed=PRODUCT_STATUSES, default="draft")
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        now = timestamp_now()
        product = {
            "product_id": str(uuid4()),
            "title": clean_title,
            "slug": _slugify(slug or clean_title),
            "description": _clean_text(description, max_length=2000),
            "category": _clean_text(category, max_length=80),
            "sku": _clean_text(sku, max_length=80) or None,
            "status": clean_status,
            "price_amount": money_to_minor_units(price_amount),
            "currency": normalize_currency(currency),
            "inventory_quantity": max(0, int(inventory_quantity or 0)) if track_inventory else None,
            "track_inventory": bool(track_inventory),
            "image_url": _clean_text(image_url, max_length=1000) or None,
            "created_by": actor,
            "created_at": now,
            "updated_at": now,
        }
        await self.products.insert(ctx, doc=product)
        await ctx.emit(
            "domain.commerce.product.created",
            {"product_id": product["product_id"], "status": product["status"], "created_by": actor},
        )
        return {"success": True, "product": serialize_product(product)}

    async def update_product(self, ctx, *, product_id: str, **fields: Any) -> dict[str, Any]:
        product = await self.products.get(ctx, product_id=product_id)
        if not product:
            return {"success": False, "error": "product not found"}

        updates: dict[str, Any] = {}
        text_fields = {
            "title": 140,
            "description": 2000,
            "category": 80,
            "sku": 80,
            "image_url": 1000,
        }
        for key, max_length in text_fields.items():
            if key in fields and fields[key] is not None:
                value = _clean_text(fields[key], max_length=max_length)
                updates[key] = value or None if key in {"sku", "image_url"} else value
        if "slug" in fields and fields["slug"] is not None:
            updates["slug"] = _slugify(str(fields["slug"]))
        if "status" in fields and fields["status"] is not None:
            try:
                updates["status"] = normalize_status(
                    fields["status"], allowed=PRODUCT_STATUSES, default=product.get("status", "draft")
                )
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
        if "price_amount" in fields and fields["price_amount"] is not None:
            updates["price_amount"] = money_to_minor_units(fields["price_amount"])
        if "currency" in fields and fields["currency"] is not None:
            updates["currency"] = normalize_currency(fields["currency"])
        if "track_inventory" in fields and fields["track_inventory"] is not None:
            updates["track_inventory"] = bool(fields["track_inventory"])
            if not updates["track_inventory"]:
                updates["inventory_quantity"] = None
        if "inventory_quantity" in fields and fields["inventory_quantity"] is not None:
            updates["inventory_quantity"] = max(0, int(fields["inventory_quantity"]))

        if not updates:
            return {"success": True, "product": serialize_product(product)}

        updates["updated_at"] = timestamp_now()
        updated = await self.products.update(ctx, product_id=product_id, updates=updates)
        await ctx.emit(
            "domain.commerce.product.updated",
            {"product_id": product_id, "changes": sorted(updates.keys()), "updated_by": actor_id(ctx)},
        )
        return {"success": True, "product": serialize_product(updated or product)}

    async def archive_product(self, ctx, *, product_id: str) -> dict[str, Any]:
        updated = await self.products.update(
            ctx,
            product_id=product_id,
            updates={"status": "archived", "updated_at": timestamp_now()},
        )
        if not updated:
            return {"success": False, "error": "product not found"}
        await ctx.emit(
            "domain.commerce.product.archived",
            {"product_id": product_id, "archived_by": actor_id(ctx)},
        )
        return {"success": True, "product": serialize_product(updated)}

    async def adjust_inventory(
        self,
        ctx,
        *,
        product_id: str,
        delta: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        product = await self.products.adjust_inventory(ctx, product_id=product_id, delta=int(delta))
        if not product:
            return {"success": False, "error": "tracked product not found"}
        if int(product.get("inventory_quantity") or 0) < 0:
            product = await self.products.update(
                ctx,
                product_id=product_id,
                updates={"inventory_quantity": 0, "updated_at": timestamp_now()},
            )
            if not product:
                return {"success": False, "error": "product not found"}
        await ctx.emit(
            "domain.commerce.inventory.adjusted",
            {
                "product_id": product_id,
                "delta": int(delta),
                "reason": _clean_text(reason, max_length=240),
                "adjusted_by": actor_id(ctx),
            },
        )
        return {"success": True, "product": serialize_product(product)}

    # ---------------------------------------------------------------------
    # Cart
    # ---------------------------------------------------------------------

    async def get_cart(self, ctx) -> dict[str, Any]:
        actor = _require_actor(ctx)
        if not actor:
            return {"cart": None, "error": "user_id or session_id is required"}
        cart = await self._get_or_create_cart(ctx, actor=actor)
        return {"cart": recalculate_cart(cart)}

    async def add_cart_item(
        self,
        ctx,
        *,
        product_id: str,
        quantity: int = 1,
    ) -> dict[str, Any]:
        actor = _require_actor(ctx)
        if not actor:
            return {"success": False, "error": "user_id or session_id is required"}
        qty = coerce_quantity(quantity, default=1)
        if qty <= 0:
            return {"success": False, "error": "quantity must be greater than zero"}

        product = await self._get_buyable_product(ctx, product_id=product_id)
        if not product:
            return {"success": False, "error": "product is not available"}

        cart = await self._get_or_create_cart(ctx, actor=actor)
        existing_qty = self._cart_quantity(cart, product_id=product_id)
        desired_qty = existing_qty + qty
        stock_error = self._stock_error(product, desired_qty)
        if stock_error:
            return {"success": False, "error": stock_error}

        cart = self._replace_cart_item(cart, product=product, quantity=desired_qty)
        saved = await self._save_cart(ctx, actor=actor, cart=cart)
        await self._emit_cart_updated(ctx, saved)
        return {"success": True, "cart": recalculate_cart(saved)}

    async def update_cart_item(
        self,
        ctx,
        *,
        product_id: str,
        quantity: int,
    ) -> dict[str, Any]:
        actor = _require_actor(ctx)
        if not actor:
            return {"success": False, "error": "user_id or session_id is required"}
        qty = coerce_quantity(quantity, default=0)
        cart = await self._get_or_create_cart(ctx, actor=actor)

        if qty == 0:
            cart["items"] = [item for item in cart.get("items", []) if item.get("product_id") != product_id]
        else:
            product = await self._get_buyable_product(ctx, product_id=product_id)
            if not product:
                return {"success": False, "error": "product is not available"}
            stock_error = self._stock_error(product, qty)
            if stock_error:
                return {"success": False, "error": stock_error}
            cart = self._replace_cart_item(cart, product=product, quantity=qty)

        saved = await self._save_cart(ctx, actor=actor, cart=cart)
        await self._emit_cart_updated(ctx, saved)
        return {"success": True, "cart": recalculate_cart(saved)}

    async def remove_cart_item(self, ctx, *, product_id: str) -> dict[str, Any]:
        return await self.update_cart_item(ctx, product_id=product_id, quantity=0)

    async def clear_cart(self, ctx) -> dict[str, Any]:
        actor = _require_actor(ctx)
        if not actor:
            return {"success": False, "error": "user_id or session_id is required"}
        cart = await self._get_or_create_cart(ctx, actor=actor)
        cart["items"] = []
        saved = await self._save_cart(ctx, actor=actor, cart=cart)
        await self._emit_cart_updated(ctx, saved)
        return {"success": True, "cart": recalculate_cart(saved)}

    # ---------------------------------------------------------------------
    # Checkout and orders
    # ---------------------------------------------------------------------

    async def start_checkout(
        self,
        ctx,
        *,
        payment_provider: str = "mozaikspay",
        success_url: str = "",
        cancel_url: str = "",
    ) -> dict[str, Any]:
        actor = _require_actor(ctx)
        if not actor:
            return {"success": False, "error": "user_id or session_id is required"}
        cart = recalculate_cart(await self._get_or_create_cart(ctx, actor=actor))
        items = list(cart.get("items") or [])
        if not items:
            return {"success": False, "error": "cart is empty"}

        stock_error = await self._validate_cart_stock(ctx, items)
        if stock_error:
            return {"success": False, "error": stock_error}

        now = timestamp_now()
        order_id = str(uuid4())
        checkout_id = str(uuid4())
        provider = _clean_text(payment_provider, max_length=80, default="mozaikspay") or "mozaikspay"
        currency = normalize_currency(cart.get("currency"))
        total_amount = int(cart.get("subtotal_amount") or 0)
        order = {
            "order_id": order_id,
            "actor_id": actor,
            "cart_id": cart["cart_id"],
            "status": "checkout_pending",
            "items": items,
            "subtotal_amount": total_amount,
            "total_amount": total_amount,
            "total_display": minor_units_to_display(total_amount, currency),
            "currency": currency,
            "payment": {
                "provider": provider,
                "status": "pending",
                "provider_reference": None,
                "checkout_id": checkout_id,
            },
            "fulfillment": {"status": "not_started", "updated_at": None},
            "created_at": now,
            "updated_at": now,
        }
        checkout_request = {
            "checkout_id": checkout_id,
            "order_id": order_id,
            "actor_id": actor,
            "cart_id": cart["cart_id"],
            "status": "requested",
            "payment_provider": provider,
            "amount": total_amount,
            "currency": currency,
            "success_url": _clean_text(success_url, max_length=1000),
            "cancel_url": _clean_text(cancel_url, max_length=1000),
            "created_at": now,
            "updated_at": now,
        }
        await self.orders.insert(ctx, doc=order)
        await self.checkout_requests.insert(ctx, doc=checkout_request)
        await self.carts.mark_converted(ctx, cart_id=cart["cart_id"], now=now)
        await ctx.emit(
            "domain.commerce.checkout.requested",
            {
                "checkout_id": checkout_id,
                "order_id": order_id,
                "actor_id": actor,
                "payment_provider": provider,
                "amount": total_amount,
                "currency": currency,
            },
        )
        return {
            "success": True,
            "order": serialize_order(order),
            "checkout_request": checkout_request,
        }

    async def record_checkout_result(
        self,
        ctx,
        *,
        order_id: str,
        status: str,
        provider: str = "",
        provider_reference: str = "",
        raw_event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            result_status = normalize_status(
                status, allowed=CHECKOUT_RESULT_STATUSES, default="failed"
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc), "order": None}

        existing_by_reference = await self.orders.find_by_provider_reference(
            ctx, provider_reference=provider_reference
        )
        if existing_by_reference and existing_by_reference.get("order_id") != order_id:
            return {
                "success": True,
                "idempotent": True,
                "order": serialize_order(existing_by_reference),
            }

        order = await self.orders.get(ctx, order_id=order_id)
        if not order:
            return {"success": False, "error": "order not found", "order": None}

        already_paid = order.get("status") == "paid" or (order.get("payment") or {}).get("status") == "paid"
        if result_status == "paid" and not already_paid:
            reserve_error = await self._reserve_order_inventory(ctx, order)
            if reserve_error:
                return {"success": False, "error": reserve_error, "order": serialize_order(order)}

        now = timestamp_now()
        mapped_order_status = {
            "paid": "paid",
            "failed": "payment_failed",
            "cancelled": "cancelled",
            "expired": "expired",
        }[result_status]
        payment = dict(order.get("payment") or {})
        payment.update(
            {
                "provider": _clean_text(provider, max_length=80) or payment.get("provider"),
                "status": result_status,
                "provider_reference": _clean_text(provider_reference, max_length=160) or payment.get("provider_reference"),
                "updated_at": now,
            }
        )
        updates = {
            "status": mapped_order_status,
            "payment": payment,
            "updated_at": now,
        }
        if raw_event:
            updates["payment_event"] = {
                "received_at": now,
                "event_type": _clean_text(raw_event.get("type") if isinstance(raw_event, dict) else None, max_length=120),
            }

        updated = await self.orders.update(ctx, order_id=order_id, updates=updates)
        await self.checkout_requests.update_status(
            ctx,
            order_id=order_id,
            updates={"status": result_status, "updated_at": now},
        )
        event_type = "domain.commerce.order.paid" if result_status == "paid" else "domain.commerce.checkout.failed"
        await ctx.emit(
            event_type,
            {
                "order_id": order_id,
                "status": mapped_order_status,
                "payment_provider": payment.get("provider"),
                "provider_reference": payment.get("provider_reference"),
            },
        )
        return {"success": True, "order": serialize_order(updated or {**order, **updates})}

    async def list_orders(
        self,
        ctx,
        *,
        status: str | None = None,
        before: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if not can_manage_orders(ctx) and not _require_actor(ctx):
            return {"orders": [], "count": 0, "next_cursor": None, "error": "user_id or session_id is required"}
        try:
            clean_status = normalize_status(status, allowed=ORDER_STATUSES, default="paid") if status else None
        except ValueError as exc:
            return {"orders": [], "count": 0, "next_cursor": None, "error": str(exc)}
        query = order_scope_query(ctx, status=clean_status)
        page_size = coerce_limit(limit, default=20)
        rows = await self.orders.list(ctx, query=query, limit=page_size + 1, before=before)
        has_more = len(rows) > page_size
        if has_more:
            rows = rows[:page_size]
        next_cursor = rows[-1]["created_at"] if has_more and rows else None
        return {
            "orders": [serialize_order(row) for row in rows],
            "count": len(rows),
            "next_cursor": next_cursor,
        }

    async def get_order(self, ctx, *, order_id: str) -> dict[str, Any]:
        order = await self.orders.get(ctx, order_id=order_id)
        if not order:
            return {"order": None, "error": "order not found"}
        if not can_manage_orders(ctx) and order.get("actor_id") != actor_id(ctx):
            return {"order": None, "error": "order not found"}
        return {"order": serialize_order(order)}

    async def update_order_status(
        self,
        ctx,
        *,
        order_id: str,
        status: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        order = await self.orders.get(ctx, order_id=order_id)
        if not order:
            return {"success": False, "error": "order not found", "order": None}
        allowed = {"processing", "fulfilled", "cancelled", "refunded"}
        try:
            clean_status = normalize_status(status, allowed=allowed, default=order.get("status", "processing"))
        except ValueError as exc:
            return {"success": False, "error": str(exc), "order": serialize_order(order)}
        now = timestamp_now()
        fulfillment = dict(order.get("fulfillment") or {})
        fulfillment.update(
            {
                "status": clean_status,
                "note": _clean_text(note, max_length=500),
                "updated_at": now,
                "updated_by": actor_id(ctx),
            }
        )
        updated = await self.orders.update(
            ctx,
            order_id=order_id,
            updates={"status": clean_status, "fulfillment": fulfillment, "updated_at": now},
        )
        await ctx.emit(
            "domain.commerce.order.updated",
            {"order_id": order_id, "status": clean_status, "updated_by": actor_id(ctx)},
        )
        return {"success": True, "order": serialize_order(updated or order)}

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

    async def _get_buyable_product(self, ctx, *, product_id: str) -> dict[str, Any] | None:
        product = await self.products.get(ctx, product_id=product_id)
        if not product or product.get("status") != "active":
            return None
        return product

    async def _get_or_create_cart(self, ctx, *, actor: str) -> dict[str, Any]:
        existing = await self.carts.get_active(ctx, actor_id=actor)
        if existing:
            return recalculate_cart(existing)
        now = timestamp_now()
        return {
            "cart_id": str(uuid4()),
            "actor_id": actor,
            "status": CART_STATUS_ACTIVE,
            "items": [],
            "subtotal_amount": 0,
            "currency": DEFAULT_CURRENCY,
            "created_at": now,
            "updated_at": now,
        }

    async def _save_cart(self, ctx, *, actor: str, cart: dict[str, Any]) -> dict[str, Any]:
        now = timestamp_now()
        cart = recalculate_cart({**cart, "status": CART_STATUS_ACTIVE, "updated_at": now})
        return await self.carts.upsert_active(ctx, actor_id=actor, cart=cart)

    @staticmethod
    def _cart_quantity(cart: dict[str, Any], *, product_id: str) -> int:
        for item in cart.get("items") or []:
            if item.get("product_id") == product_id:
                return int(item.get("quantity") or 0)
        return 0

    @staticmethod
    def _replace_cart_item(
        cart: dict[str, Any],
        *,
        product: dict[str, Any],
        quantity: int,
    ) -> dict[str, Any]:
        product_id = product["product_id"]
        items = [item for item in cart.get("items", []) if item.get("product_id") != product_id]
        items.append(product_snapshot(product, quantity))
        return recalculate_cart({**cart, "items": items})

    @staticmethod
    def _stock_error(product: dict[str, Any], quantity: int) -> str | None:
        if not product.get("track_inventory"):
            return None
        available = int(product.get("inventory_quantity") or 0)
        if available < quantity:
            return f"only {available} available"
        return None

    async def _validate_cart_stock(self, ctx, items: list[dict[str, Any]]) -> str | None:
        for item in items:
            if not item.get("track_inventory"):
                continue
            product = await self.products.get(ctx, product_id=item.get("product_id"))
            if not product or product.get("status") != "active":
                return f"{item.get('title') or 'product'} is no longer available"
            error = self._stock_error(product, int(item.get("quantity") or 0))
            if error:
                return f"{item.get('title') or 'product'}: {error}"
        return None

    async def _reserve_order_inventory(self, ctx, order: dict[str, Any]) -> str | None:
        reserved: list[tuple[str, int]] = []
        for item in order.get("items") or []:
            if not item.get("track_inventory"):
                continue
            product_id = str(item.get("product_id") or "")
            quantity = int(item.get("quantity") or 0)
            if quantity <= 0:
                continue
            ok = await self.products.reserve_inventory(ctx, product_id=product_id, quantity=quantity)
            if ok:
                reserved.append((product_id, quantity))
                continue
            for reserved_product_id, reserved_quantity in reserved:
                await self.products.adjust_inventory(
                    ctx,
                    product_id=reserved_product_id,
                    delta=reserved_quantity,
                )
            return f"{item.get('title') or 'product'} no longer has enough inventory"
        return None

    async def _emit_cart_updated(self, ctx, cart: dict[str, Any]) -> None:
        await ctx.emit(
            "domain.commerce.cart.updated",
            {
                "cart_id": cart["cart_id"],
                "actor_id": cart["actor_id"],
                "item_count": len(cart.get("items") or []),
                "subtotal_amount": int(cart.get("subtotal_amount") or 0),
            },
        )
