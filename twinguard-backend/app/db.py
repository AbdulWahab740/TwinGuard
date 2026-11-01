"""Thin DB layer. `SupabaseDb` for production, `MemoryDb` for tests/offline demo."""
import copy
import threading
from typing import Any, Protocol

from twinguard_core import InMemoryDataStore, ProposedAction, SeedDataset

BUSINESS = ("products", "suppliers", "warehouses", "inventory_records",
            "customer_orders", "purchase_orders")
TABLES = (*BUSINESS, "business_rules", "store_meta", "users", "requests",
          "agent_steps", "review_tasks", "audit_events", "snapshots")


class StaleVersionError(Exception):
    pass


class Db(Protocol):
    def select(self, table: str, **eq: Any) -> list[dict]: ...

    def insert(self, table: str, rows: list[dict]) -> list[dict]: ...

    def update(self, table: str, values: dict, **eq: Any) -> list[dict]:
        """Conditional update; returns the rows it changed ([] = condition failed)."""

    def apply_action(self, action: dict, expected_version: int) -> int:
        """Atomically apply an approved action; returns the new version."""


def rules_of(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "id"}


class MemoryDb:
    def __init__(self) -> None:
        self.t: dict[str, list[dict]] = {n: [] for n in TABLES}
        self._lock = threading.RLock()

    def select(self, table, **eq):
        with self._lock:
            return [copy.deepcopy(r) for r in self.t[table]
                    if all(r.get(k) == v for k, v in eq.items())]

    def insert(self, table, rows):
        with self._lock:
            self.t[table].extend(copy.deepcopy(rows))
            return copy.deepcopy(rows)

    def update(self, table, values, **eq):
        with self._lock:
            hit = [r for r in self.t[table] if all(r.get(k) == v for k, v in eq.items())]
            for r in hit:
                r.update(copy.deepcopy(values))
            return copy.deepcopy(hit)

    def apply_action(self, action, expected_version):
        with self._lock:
            meta = self.t["store_meta"][0]
            if meta["version"] != expected_version:
                raise StaleVersionError()
            ds = SeedDataset(
                products=self.t["products"], suppliers=self.t["suppliers"],
                warehouses=self.t["warehouses"], inventory=self.t["inventory_records"],
                customer_orders=self.t["customer_orders"],
                purchase_orders=self.t["purchase_orders"],
                business_rules=rules_of(self.t["business_rules"][0]),
            )
            store = InMemoryDataStore(ds)
            store.apply_approved_action(ProposedAction.model_validate(action))
            for table, rows in (("inventory_records", store.get_inventory()),
                                ("customer_orders", store.get_customer_orders()),
                                ("purchase_orders", store.get_purchase_orders())):
                self.t[table] = [r.model_dump(mode="json") for r in rows]
            meta["version"] += 1
            return meta["version"]


class SupabaseDb:
    def __init__(self, client) -> None:
        self.c = client

    @classmethod
    def from_settings(cls, s):
        from supabase import create_client
        if not (s.supabase_url and s.supabase_key):
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
        return cls(create_client(s.supabase_url, s.supabase_key))

    @staticmethod
    def _eq(q, eq):
        for k, v in eq.items():
            q = q.eq(k, v)
        return q

    def select(self, table, **eq):
        return self._eq(self.c.table(table).select("*"), eq).execute().data

    def insert(self, table, rows):
        return self.c.table(table).insert(rows).execute().data

    def update(self, table, values, **eq):
        return self._eq(self.c.table(table).update(values), eq).execute().data

    def apply_action(self, action, expected_version):
        try:
            res = self.c.rpc("apply_approved_action",
                             {"p_action": action, "p_expected_version": expected_version}).execute()
        except Exception as e:
            if "stale_version" in str(e):
                raise StaleVersionError() from e
            raise
        return res.data
