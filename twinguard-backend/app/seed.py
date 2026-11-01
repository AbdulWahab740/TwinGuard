from twinguard_core import get_dataset

from .db import Db


def seed_dataset_into(db: Db, name: str, ds=None) -> None:
    """Idempotent: does nothing if store_meta already has a row."""
    if db.select("store_meta"):
        return
    ds = ds or get_dataset(name)

    def dump(rows):
        return [r.model_dump(mode="json") for r in rows]

    db.insert("products", dump(ds.products))
    db.insert("suppliers", dump(ds.suppliers))
    db.insert("warehouses", dump(ds.warehouses))
    db.insert("inventory_records", dump(ds.inventory))
    db.insert("customer_orders", dump(ds.customer_orders))
    db.insert("purchase_orders", dump(ds.purchase_orders))
    db.insert("business_rules", [{"id": 1, **ds.business_rules.model_dump(mode="json")}])
    db.insert("store_meta", [{"id": 1, "version": 1}])
