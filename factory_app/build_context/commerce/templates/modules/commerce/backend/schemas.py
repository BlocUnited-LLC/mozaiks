from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, TypedDict

PRODUCT_STATUSES = {"draft", "active", "archived"}
CART_STATUS_ACTIVE = "active"
CART_STATUS_CONVERTED = "converted"
ORDER_STATUSES = {
    "checkout_pending",
    "paid",
    "processing",
    "fulfilled",
    "cancelled",
    "refunded",
    "payment_failed",
    "expired",
}
CHECKOUT_RESULT_STATUSES = {"paid", "failed", "cancelled", "expired"}
DEFAULT_CURRENCY = "USD"


class Product(TypedDict):
    product_id: str
    title: str
    slug: str | None
    description: str
    category: str
    sku: str | None
    status: str
    price_amount: int
    currency: str
    inventory_quantity: int | None
    track_inventory: bool
    image_url: str | None
    created_by: str
    created_at: str
    updated_at: str


class CartItem(TypedDict):
    product_id: str
    title: str
    sku: str | None
    quantity: int
    unit_amount: int
    line_total: int
    currency: str
    track_inventory: bool


class Cart(TypedDict):
    cart_id: str
    actor_id: str
    status: str
    items: list[CartItem]
    subtotal_amount: int
    currency: str
    created_at: str
    updated_at: str


class Order(TypedDict):
    order_id: str
    actor_id: str
    cart_id: str
    status: str
    items: list[CartItem]
    subtotal_amount: int
    total_amount: int
    currency: str
    payment: dict[str, Any]
    fulfillment: dict[str, Any]
    created_at: str
    updated_at: str


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def coerce_limit(value: Any, default: int = 20, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, min(parsed, maximum))


def normalize_status(value: str | None, *, allowed: set[str], default: str) -> str:
    status = str(value or default).strip().lower()
    if status not in allowed:
        raise ValueError(f"invalid status: {value!r}")
    return status


def normalize_currency(value: str | None) -> str:
    currency = str(value or DEFAULT_CURRENCY).strip().upper()
    return currency[:3] if len(currency) >= 3 else DEFAULT_CURRENCY


def money_to_minor_units(value: Any) -> int:
    try:
        amount = Decimal(str(value or "0"))
    except Exception:
        amount = Decimal("0")
    cents = (amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return max(0, int(cents))


def minor_units_to_display(amount: Any, currency: str = DEFAULT_CURRENCY) -> str:
    try:
        cents = int(amount or 0)
    except Exception:
        cents = 0
    return f"{currency.upper()} {Decimal(cents) / Decimal('100'):.2f}"


def coerce_quantity(value: Any, *, default: int = 1, maximum: int = 999) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(0, min(parsed, maximum))


def product_snapshot(product: dict[str, Any], quantity: int) -> CartItem:
    unit_amount = int(product.get("price_amount") or 0)
    return {
        "product_id": str(product.get("product_id") or ""),
        "title": str(product.get("title") or "Untitled product"),
        "sku": product.get("sku") or None,
        "quantity": quantity,
        "unit_amount": unit_amount,
        "line_total": unit_amount * quantity,
        "currency": normalize_currency(product.get("currency")),
        "track_inventory": bool(product.get("track_inventory")),
    }


def recalculate_cart(cart: dict[str, Any]) -> dict[str, Any]:
    items = list(cart.get("items") or [])
    subtotal = sum(int(item.get("line_total") or 0) for item in items)
    currency = items[0].get("currency") if items else cart.get("currency")
    return {
        **cart,
        "items": items,
        "subtotal_amount": subtotal,
        "subtotal_display": minor_units_to_display(subtotal, normalize_currency(currency)),
        "currency": normalize_currency(currency),
    }


def serialize_product(product: dict[str, Any]) -> dict[str, Any]:
    clean = dict(product)
    clean["price_display"] = minor_units_to_display(clean.get("price_amount"), clean.get("currency", DEFAULT_CURRENCY))
    return clean


def serialize_order(order: dict[str, Any]) -> dict[str, Any]:
    clean = dict(order)
    clean["total_display"] = minor_units_to_display(clean.get("total_amount"), clean.get("currency", DEFAULT_CURRENCY))
    return clean
