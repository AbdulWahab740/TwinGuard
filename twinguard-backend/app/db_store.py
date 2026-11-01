import uuid
from typing import Optional

from twinguard_core import (BusinessRules, CustomerOrder, DataStore, InventoryRecord,
                            Product, ProposedAction, PurchaseOrder, SnapshotMeta,
                            Supplier, Warehouse)

from .db import Db, rules_of


class DbDataStore(DataStore):
    def __init__(self, db: Db) -> None:
        self.db = db

    def _rows(self, table, model):
        return [model.model_validate(r) for r in self.db.select(table)]

    def get_products(self): return self._rows("products", Product)
    def get_suppliers(self): return self._rows("suppliers", Supplier)
    def get_warehouses(self): return self._rows("warehouses", Warehouse)
    def get_inventory(self): return self._rows("inventory_records", InventoryRecord)
    def get_customer_orders(self): return self._rows("customer_orders", CustomerOrder)
    def get_purchase_orders(self): return self._rows("purchase_orders", PurchaseOrder)

    def get_business_rules(self):
        return BusinessRules.model_validate(rules_of(self.db.select("business_rules")[0]))

    def get_version(self) -> int:
        return self.db.select("store_meta", id=1)[0]["version"]

    def persist_snapshot(self, meta: SnapshotMeta, state: dict) -> None:
        # core ids restart at snap-0001 for every TwinManager, so the stored key must be our own
        row = meta.model_dump(mode="json")
        row["core_snapshot_id"] = row["snapshot_id"]
        row["snapshot_id"] = uuid.uuid4().hex
        self.db.insert("snapshots", [row])

    def apply_approved_action(
        self, action: ProposedAction, expected_version: Optional[int] = None
    ) -> None:
        """Raises StaleVersionError if the version moved. Only app/review.py calls this."""
        if expected_version is None:
            expected_version = self.get_version()
        self.db.apply_action(action.model_dump(mode="json"), expected_version)
