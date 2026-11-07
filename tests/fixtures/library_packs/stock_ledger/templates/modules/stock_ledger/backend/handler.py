from .service import StockLedgerService


class StockLedgerHandler:
    def __init__(self) -> None:
        self.service = StockLedgerService()

    async def list_items(self, ctx, **params):
        return self.service.list_items()

    async def record_item(self, ctx, sku: str, **params):
        return {"item": self.service.record_item(sku)}
