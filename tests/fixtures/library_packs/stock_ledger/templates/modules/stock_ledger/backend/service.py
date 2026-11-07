from pathlib import Path

import yaml
from services.adapters.stock.memory import MemoryStockAdapter

# The app owns config/stock_ledger.yaml: the library seeds it once and never overwrites it.
_CONFIG = Path(__file__).resolve().parents[3] / "config" / "stock_ledger.yaml"


def _threshold() -> int:
    data = yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) if _CONFIG.is_file() else {}
    return int((data or {}).get("low_stock_threshold", 5))


class StockLedgerService:
    def __init__(self) -> None:
        self.adapter = MemoryStockAdapter()

    def list_items(self) -> dict:
        return {"items": self.adapter.items(), "low_stock_threshold": _threshold()}

    def record_item(self, sku: str) -> dict:
        return self.adapter.add(sku)
