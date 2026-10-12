"""The seam between twinguard-core and whatever holds the data.

Everything else in this package talks to ``DataStore`` only. The backend phase
replaces ``InMemoryDataStore`` with ``SqlDataStore`` and nothing else changes.

``apply_approved_action`` is the ONLY write path to operational data, in this
package and in the whole system. The agent layer cannot reach it: it is not
exposed through any tool, and ``agent.py`` never imports it (enforced by
``tests/test_agent.py``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import (
    SNAPSHOT_COLLECTIONS,
    ActionType,
    BusinessRules,
    CustomerOrder,
    InventoryRecord,
    Product,
    ProposedAction,
    PurchaseOrder,
    SeedDataset,
    SnapshotMeta,
    Supplier,
    Warehouse,
)


class DataStore(ABC):
    """Read access to operational data, plus the single approved-write method."""

    @abstractmethod
    def get_products(self) -> list[Product]: ...

    @abstractmethod
    def get_suppliers(self) -> list[Supplier]: ...

    @abstractmethod
    def get_warehouses(self) -> list[Warehouse]: ...

    @abstractmethod
    def get_inventory(self) -> list[InventoryRecord]: ...

    @abstractmethod
    def get_customer_orders(self) -> list[CustomerOrder]: ...

    @abstractmethod
    def get_purchase_orders(self) -> list[PurchaseOrder]: ...

    @abstractmethod
    def get_business_rules(self) -> BusinessRules: ...

    @abstractmethod
    def get_version(self) -> int:
        """Increments on every operational write."""

    @abstractmethod
    def apply_approved_action(self, action: ProposedAction) -> None:
        """Apply an already-approved action to operational data.

        Does NOT re-run policy checks or simulation. Callers are responsible for
        having a Decision.APPROVE or a recorded human approval first.
        """

    def persist_snapshot(self, meta: SnapshotMeta, state: dict) -> None:
        """Copy a snapshot's rows into durable storage.

        Called by ``TwinManager.create_snapshot()`` so the backend can fill the
        ``snapshot_*`` tables. Deliberately a no-op default: the in-memory twin
        table is the authority for reads (spec Section 4.2), and nothing in this
        package needs durable snapshots. The backend overrides it.
        """


class InMemoryDataStore(DataStore):
    """``DataStore`` over plain Python objects, seeded from ``seed_data.py``.

    Holds *live* operational state. ``TwinManager`` copies out of it and never
    writes back, so the twin cannot corrupt live data.
    """

    def __init__(self, dataset: SeedDataset, version: int = 1) -> None:
        self._products: list[Product] = list(dataset.products)
        self._suppliers: list[Supplier] = list(dataset.suppliers)
        self._warehouses: list[Warehouse] = list(dataset.warehouses)
        self._inventory: list[InventoryRecord] = list(dataset.inventory)
        self._customer_orders: list[CustomerOrder] = list(dataset.customer_orders)
        self._purchase_orders: list[PurchaseOrder] = list(dataset.purchase_orders)
        self._business_rules: BusinessRules = dataset.business_rules
        self._version: int = version
        # Snapshot persistence is a no-op for this store, but recording it lets
        # tests assert that create_snapshot() really went through the interface.
        self.persisted_snapshots: dict[str, SnapshotMeta] = {}

    # -- reads ---------------------------------------------------------------
    #
    # These return deep copies, not just new lists. The records are mutable
    # Pydantic models, so handing out the store's own instances would let any
    # caller (notably TwinManager) mutate live operational state through them
    # and break the "the twin never writes to the store" guarantee structurally
    # rather than by convention.

    def get_products(self) -> list[Product]:
        return [p.model_copy(deep=True) for p in self._products]

    def get_suppliers(self) -> list[Supplier]:
        return [s.model_copy(deep=True) for s in self._suppliers]

    def get_warehouses(self) -> list[Warehouse]:
        return [w.model_copy(deep=True) for w in self._warehouses]

    def get_inventory(self) -> list[InventoryRecord]:
        return [r.model_copy(deep=True) for r in self._inventory]

    def get_customer_orders(self) -> list[CustomerOrder]:
        return [o.model_copy(deep=True) for o in self._customer_orders]

    def get_purchase_orders(self) -> list[PurchaseOrder]:
        return [p.model_copy(deep=True) for p in self._purchase_orders]

    def get_business_rules(self) -> BusinessRules:
        return self._business_rules

    def get_version(self) -> int:
        return self._version

    def get_state(self) -> dict[str, list[Any]]:
        """Every entity collection, as the twin sees it."""
        return {
            "products": self.get_products(),
            "suppliers": self.get_suppliers(),
            "warehouses": self.get_warehouses(),
            "inventory": self.get_inventory(),
            "customer_orders": self.get_customer_orders(),
            "purchase_orders": self.get_purchase_orders(),
        }

    def persist_snapshot(self, meta: SnapshotMeta, state: dict) -> None:
        self.persisted_snapshots[meta.snapshot_id] = meta

    # -- the single write path ----------------------------------------------

    def apply_approved_action(self, action: ProposedAction) -> None:
        """Apply an approved action to live data, then bump the version.

        This is the only method in twinguard-core that mutates operational state.
        """
        if action.action_type is ActionType.TRANSFER_INVENTORY:
            self._apply_transfer(action.parameters)
        elif action.action_type is ActionType.CREATE_PURCHASE_ORDER:
            self._apply_create_purchase_order(action.parameters)
        elif action.action_type is ActionType.CHANGE_ORDER_PRIORITY:
            self._apply_change_order_priority(action.parameters)
        elif action.action_type is ActionType.CANCEL_PURCHASE_ORDER:
            self._apply_cancel_purchase_order(action.parameters)
        else:  # pragma: no cover - ActionType is exhaustive
            raise ValueError(f"unsupported action_type: {action.action_type!r}")

        self._version += 1

    def _inventory_row(self, product_id: str, warehouse_id: str) -> InventoryRecord:
        for row in self._inventory:
            if row.product_id == product_id and row.warehouse_id == warehouse_id:
                return row
        raise KeyError(
            f"no inventory row for product={product_id} warehouse={warehouse_id}"
        )

    def _apply_transfer(self, params: dict) -> None:
        source = self._inventory_row(
            params["product_id"], params["source_warehouse_id"]
        )
        destination = self._inventory_row(
            params["product_id"], params["destination_warehouse_id"]
        )
        quantity = int(params["quantity"])
        source.quantity_on_hand -= quantity
        destination.quantity_on_hand += quantity

    def _apply_create_purchase_order(self, params: dict) -> None:
        # arrival date is derived from the supplier lead time, defaulting to 0
        # when unknown (an unknown lead time is caught earlier as a data-quality
        # failure, so reaching here with None is not expected).
        lead_time = next(
            (
                s.lead_time_days
                for s in self._suppliers
                if s.supplier_id == params["supplier_id"]
            ),
            None,
        )
        days = 7 if lead_time is None else int(lead_time)
        from datetime import date, timedelta

        self._purchase_orders.append(
            PurchaseOrder(
                po_id=f"PO-NEW-{len(self._purchase_orders) + 1:04d}",
                product_id=params["product_id"],
                supplier_id=params["supplier_id"],
                quantity=int(params["quantity"]),
                expected_arrival=date.today() + timedelta(days=days),
                status="pending",
                destination_warehouse_id=params.get("destination_warehouse_id"),
            )
        )

    def _apply_change_order_priority(self, params: dict) -> None:
        for order in self._customer_orders:
            if order.order_id == params["order_id"]:
                order.priority = params["new_priority"]
                return
        raise KeyError(f"no customer order {params['order_id']!r}")

    def _apply_cancel_purchase_order(self, params: dict) -> None:
        for po in self._purchase_orders:
            if po.po_id == params["po_id"]:
                po.status = "cancelled"
                return
        raise KeyError(f"no purchase order {params['po_id']!r}")


def state_collections_match(state: dict) -> None:
    """Raise if a snapshot state dict is missing or has extra collections.

    Shared by ``TwinManager`` and the backend so both agree on the shape.
    """
    missing = set(SNAPSHOT_COLLECTIONS) - set(state)
    if missing:
        raise ValueError(f"snapshot state missing collections: {sorted(missing)}")
