from __future__ import annotations

from typing import Any

from .service import CommerceService


class CommerceHandler:
    def __init__(self) -> None:
        self.service: CommerceService = CommerceService()

    async def list_products(self, ctx, **kwargs: Any) -> dict[str, Any]:
        return await self.service.list_products(ctx, **kwargs)

    async def get_product(self, ctx, **kwargs: Any) -> dict[str, Any]:
        return await self.service.get_product(ctx, **kwargs)

    async def create_product(self, ctx, **kwargs: Any) -> dict[str, Any]:
        return await self.service.create_product(ctx, **kwargs)

    async def update_product(self, ctx, **kwargs: Any) -> dict[str, Any]:
        return await self.service.update_product(ctx, **kwargs)

    async def archive_product(self, ctx, *, product_id: str) -> dict[str, Any]:
        return await self.service.archive_product(ctx, product_id=product_id)

    async def adjust_inventory(self, ctx, **kwargs: Any) -> dict[str, Any]:
        return await self.service.adjust_inventory(ctx, **kwargs)

    async def get_cart(self, ctx) -> dict[str, Any]:
        return await self.service.get_cart(ctx)

    async def add_cart_item(self, ctx, **kwargs: Any) -> dict[str, Any]:
        return await self.service.add_cart_item(ctx, **kwargs)

    async def update_cart_item(self, ctx, **kwargs: Any) -> dict[str, Any]:
        return await self.service.update_cart_item(ctx, **kwargs)

    async def remove_cart_item(self, ctx, *, product_id: str) -> dict[str, Any]:
        return await self.service.remove_cart_item(ctx, product_id=product_id)

    async def clear_cart(self, ctx) -> dict[str, Any]:
        return await self.service.clear_cart(ctx)

    async def start_checkout(self, ctx, **kwargs: Any) -> dict[str, Any]:
        return await self.service.start_checkout(ctx, **kwargs)

    async def record_checkout_result(self, ctx, **kwargs: Any) -> dict[str, Any]:
        return await self.service.record_checkout_result(ctx, **kwargs)

    async def list_orders(self, ctx, **kwargs: Any) -> dict[str, Any]:
        return await self.service.list_orders(ctx, **kwargs)

    async def get_order(self, ctx, *, order_id: str) -> dict[str, Any]:
        return await self.service.get_order(ctx, order_id=order_id)

    async def update_order_status(self, ctx, **kwargs: Any) -> dict[str, Any]:
        return await self.service.update_order_status(ctx, **kwargs)
