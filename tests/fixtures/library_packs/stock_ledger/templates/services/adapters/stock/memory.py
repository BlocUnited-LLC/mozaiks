class MemoryStockAdapter:
    """In-process stock store; a real library would ship a persistent adapter."""

    def __init__(self) -> None:
        self._items: list[dict] = []

    def items(self) -> list[dict]:
        return list(self._items)

    def add(self, sku: str) -> dict:
        item = {"sku": sku, "quantity": 1}
        self._items.append(item)
        return item
