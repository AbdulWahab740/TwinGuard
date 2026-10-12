"""Snapshot create / replay / reset / diff, plus data-quality checks.

Design notes
------------
* The twin is a *copy*. Nothing in this module writes to the ``DataStore``.
* ``_base_state[sid]`` is the pristine copy taken at ``create_snapshot()`` time and
  is never mutated. ``_state[sid]`` is the scenario layer that scenario
  experiments may write to. ``reset_scenario`` restores the former from the
  latter, which is why a pristine copy has to be kept separately.
* ``get_snapshot_state`` returns ``copy.deepcopy`` of the scenario layer, so a
  caller can mutate the result freely. The simulation engine relies on that: it
  works on its own copy and therefore cannot perturb the stored snapshot, which
  is what makes repeated runs byte-identical.
* ``get_snapshot_meta`` is an addition to the spec Section 4.2 interface. The
  simulator needs a start date for its day stepping, and the only stable,
  deterministic one available is the snapshot's own ``created_at``; using the wall
  clock instead would make reports differ between two runs of the same snapshot.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from .data_store import DataStore
from .models import (
    SNAPSHOT_COLLECTIONS,
    BusinessRules,
    DataQualityIssue,
    InventoryRecord,
    SnapshotMeta,
)

_VALID_ORDER_STATUS = {"open", "reserved", "fulfilled", "delayed"}
_VALID_PO_STATUS = {"pending", "received", "cancelled"}
_VALID_PRIORITY = {"low", "normal", "high"}


#: Which field is the natural key of each collection.
_ID_FIELD = {
    "products": "product_id",
    "suppliers": "supplier_id",
    "warehouses": "warehouse_id",
    "customer_orders": "order_id",
    "purchase_orders": "po_id",
}


def _entity_key(collection: str, record) -> str:
    """Natural key for a record in a collection (used by the diff)."""
    if collection == "inventory":
        return f"{record.product_id}|{record.warehouse_id}"
    return str(getattr(record, _ID_FIELD[collection]))


def _as_dict(record) -> dict:
    if hasattr(record, "model_dump"):
        return record.model_dump(mode="json")
    return dict(record)  # pragma: no cover - states are always Pydantic models


def diff_entity_states(state_a: dict, state_b: dict) -> dict:
    """Field-by-field difference between two entity states, keyed by entity id.

    Returns, per collection, a mapping of entity key to either
    ``{"changed": {field: {"a": ..., "b": ...}}}``, ``{"added": {...}}`` or
    ``{"removed": {...}}``. Collections with no differences map to an empty dict,
    so the shape is stable and diffs are comparable across runs.
    """
    diff: dict[str, dict] = {}
    for collection in SNAPSHOT_COLLECTIONS:
        left = {_entity_key(collection, r): r for r in state_a[collection]}
        right = {_entity_key(collection, r): r for r in state_b[collection]}
        entries: dict[str, dict] = {}
        for key in sorted(set(left) & set(right)):
            a_fields = _as_dict(left[key])
            b_fields = _as_dict(right[key])
            changed = {
                field: {"a": a_fields[field], "b": b_fields[field]}
                for field in sorted(a_fields)
                if a_fields[field] != b_fields[field]
            }
            if changed:
                entries[key] = {"changed": changed}
        for key in sorted(set(right) - set(left)):
            entries[key] = {"added": _as_dict(right[key])}
        for key in sorted(set(left) - set(right)):
            entries[key] = {"removed": _as_dict(left[key])}
        diff[collection] = entries
    return diff


class TwinManager:
    """Owns the twin snapshot table."""

    def __init__(self, data_store: DataStore) -> None:
        self._data_store = data_store
        self._meta: dict[str, SnapshotMeta] = {}
        # scenario layer: mutated by experiments
        self._state: dict[str, dict] = {}
        # pristine layer: written once, never mutated
        self._base_state: dict[str, dict] = {}
        self._counter = 0

    # -- lifecycle -----------------------------------------------------------

    def create_snapshot(self) -> SnapshotMeta:
        """Copy current state into the twin table and return its metadata.

        Never mutates the source store.
        """
        self._counter += 1
        snapshot_id = f"snap-{self._counter:04d}"
        state = self._read_state()
        rules = self._data_store.get_business_rules()

        meta = SnapshotMeta(
            snapshot_id=snapshot_id,
            source_version=self._data_store.get_version(),
            created_at=datetime.now(timezone.utc),
            freshness_status=self._freshness_status(state, rules.freshness_limit_hours),
        )

        # Persist before publishing so a snapshot is never visible unless its
        # rows were written.
        self._data_store.persist_snapshot(meta, copy.deepcopy(state))

        self._meta[snapshot_id] = meta
        self._base_state[snapshot_id] = copy.deepcopy(state)
        self._state[snapshot_id] = state
        return meta

    def get_snapshot_meta(self, snapshot_id: str) -> SnapshotMeta:
        try:
            return self._meta[snapshot_id]
        except KeyError:
            raise KeyError(f"unknown snapshot_id {snapshot_id!r}") from None

    def get_snapshot_state(self, snapshot_id: str) -> dict:
        """Return a deep copy of the snapshot's entities.

        Mutating the result never affects the stored snapshot.
        """
        if snapshot_id not in self._state:
            raise KeyError(f"unknown snapshot_id {snapshot_id!r}")
        return copy.deepcopy(self._state[snapshot_id])

    def reset_scenario(self, snapshot_id: str) -> None:
        """Discard scenario-layer mutations, restoring the create-time state."""
        if snapshot_id not in self._state:
            raise KeyError(f"unknown snapshot_id {snapshot_id!r}")
        self._state[snapshot_id] = copy.deepcopy(self._base_state[snapshot_id])

    def diff_snapshots(self, snapshot_id_a: str, snapshot_id_b: str) -> dict:
        return diff_entity_states(
            self.get_snapshot_state(snapshot_id_a),
            self.get_snapshot_state(snapshot_id_b),
        )

    def list_snapshots(self) -> list[SnapshotMeta]:
        return [self._meta[k] for k in sorted(self._meta)]

    def get_business_rules(self) -> BusinessRules:
        """The live business rules. Read-only; the twin never edits them."""
        return self._data_store.get_business_rules()

    def get_sync_anchor(self, snapshot_id: str) -> datetime | None:
        """Most recent ``last_synced_at`` in a snapshot, or None if it has no
        inventory. The simulator stamps rows it creates with this so a
        synthesised inventory record still looks like it came from the snapshot."""
        state = self.get_snapshot_state(snapshot_id)
        synced = [r.last_synced_at for r in state["inventory"]]
        return max(synced) if synced else None

    # -- data quality --------------------------------------------------------

    def run_data_quality_checks(self, snapshot_id: str) -> list[DataQualityIssue]:
        """Full-snapshot check. See module docstring for why it is not scoped.

        Emits, in this order: missing_field, stale_timestamp, duplicate_record,
        invalid_reference, conflicting_quantity, unsupported_status — then sorts
        by code and entity id so the output is stable.
        """
        state = self.get_snapshot_state(snapshot_id)
        rules = self._data_store.get_business_rules()
        now = datetime.now(timezone.utc)

        issues: list[DataQualityIssue] = []

        products = state["products"]
        suppliers = state["suppliers"]
        warehouses = state["warehouses"]
        inventory = state["inventory"]
        orders = state["customer_orders"]
        purchase_orders = state["purchase_orders"]

        # -- missing_field
        for supplier in suppliers:
            if supplier.lead_time_days is None:
                issues.append(
                    DataQualityIssue(
                        code="missing_field",
                        detail=(
                            f"supplier lead time for {supplier.supplier_id} "
                            f"({supplier.name}) is unknown"
                        ),
                        entity_id=supplier.supplier_id,
                    )
                )

        # -- stale_timestamp
        for record in inventory:
            synced = record.last_synced_at
            if synced.tzinfo is None:
                synced = synced.replace(tzinfo=timezone.utc)
            age_hours = (now - synced).total_seconds() / 3600.0
            if age_hours > rules.freshness_limit_hours:
                issues.append(
                    DataQualityIssue(
                        code="stale_timestamp",
                        detail=(
                            f"inventory {record.product_id}@{record.warehouse_id} "
                            f"last synced {age_hours:.0f}h ago, limit is "
                            f"{rules.freshness_limit_hours}h"
                        ),
                        entity_id=f"{record.product_id}|{record.warehouse_id}",
                    )
                )

        # -- duplicate_record (repeated primary ids)
        for collection, records, id_field in (
            ("products", products, "product_id"),
            ("suppliers", suppliers, "supplier_id"),
            ("warehouses", warehouses, "warehouse_id"),
            ("customer_orders", orders, "order_id"),
            ("purchase_orders", purchase_orders, "po_id"),
        ):
            seen: set[str] = set()
            for record in records:
                value = getattr(record, id_field)
                if value in seen:
                    issues.append(
                        DataQualityIssue(
                            code="duplicate_record",
                            detail=(
                                f"duplicate {id_field} {value} in {collection}"
                            ),
                            entity_id=value,
                        )
                    )
                seen.add(value)

        # -- duplicate_record / conflicting_quantity (one row per product+warehouse)
        by_slot: dict[tuple[str, str], list[InventoryRecord]] = {}
        for record in inventory:
            by_slot.setdefault(
                (record.product_id, record.warehouse_id), []
            ).append(record)
        for (product_id, warehouse_id), rows in by_slot.items():
            if len(rows) < 2:
                continue
            entity_id = f"{product_id}|{warehouse_id}"
            quantities = {
                (r.quantity_on_hand, r.quantity_reserved) for r in rows
            }
            if len(quantities) == 1:
                issues.append(
                    DataQualityIssue(
                        code="duplicate_record",
                        detail=(
                            f"{len(rows)} identical inventory rows for "
                            f"{product_id}@{warehouse_id}"
                        ),
                        entity_id=entity_id,
                    )
                )
            else:
                issues.append(
                    DataQualityIssue(
                        code="conflicting_quantity",
                        detail=(
                            f"{len(rows)} inventory rows for "
                            f"{product_id}@{warehouse_id} disagree on quantity"
                        ),
                        entity_id=entity_id,
                    )
                )

        # -- invalid_reference
        product_ids = {p.product_id for p in products}
        supplier_ids = {s.supplier_id for s in suppliers}
        warehouse_ids = {w.warehouse_id for w in warehouses}

        for order in orders:
            if order.product_id not in product_ids:
                issues.append(
                    DataQualityIssue(
                        code="invalid_reference",
                        detail=(
                            f"customer order {order.order_id} references unknown "
                            f"product {order.product_id}"
                        ),
                        entity_id=order.order_id,
                    )
                )
        for po in purchase_orders:
            if po.product_id not in product_ids:
                issues.append(
                    DataQualityIssue(
                        code="invalid_reference",
                        detail=(
                            f"purchase order {po.po_id} references unknown product "
                            f"{po.product_id}"
                        ),
                        entity_id=po.po_id,
                    )
                )
            if po.supplier_id not in supplier_ids:
                issues.append(
                    DataQualityIssue(
                        code="invalid_reference",
                        detail=(
                            f"purchase order {po.po_id} references unknown supplier "
                            f"{po.supplier_id}"
                        ),
                        entity_id=po.po_id,
                    )
                )
            if (
                po.destination_warehouse_id is not None
                and po.destination_warehouse_id not in warehouse_ids
            ):
                issues.append(
                    DataQualityIssue(
                        code="invalid_reference",
                        detail=(
                            f"purchase order {po.po_id} references unknown warehouse "
                            f"{po.destination_warehouse_id}"
                        ),
                        entity_id=po.po_id,
                    )
                )
        for record in inventory:
            entity_id = f"{record.product_id}|{record.warehouse_id}"
            if record.product_id not in product_ids:
                issues.append(
                    DataQualityIssue(
                        code="invalid_reference",
                        detail=(
                            f"inventory {entity_id} references unknown product "
                            f"{record.product_id}"
                        ),
                        entity_id=entity_id,
                    )
                )
            if record.warehouse_id not in warehouse_ids:
                issues.append(
                    DataQualityIssue(
                        code="invalid_reference",
                        detail=(
                            f"inventory {entity_id} references unknown warehouse "
                            f"{record.warehouse_id}"
                        ),
                        entity_id=entity_id,
                    )
                )

        # -- unsupported_status (defensive: Pydantic blocks these at construction,
        #    so this only fires for states assembled by a backend that skips
        #    model validation).
        for order in orders:
            if order.status not in _VALID_ORDER_STATUS:
                issues.append(
                    DataQualityIssue(
                        code="unsupported_status",
                        detail=f"customer order {order.order_id} has status "
                        f"{order.status!r}",
                        entity_id=order.order_id,
                    )
                )
            if order.priority not in _VALID_PRIORITY:
                issues.append(
                    DataQualityIssue(
                        code="unsupported_status",
                        detail=f"customer order {order.order_id} has priority "
                        f"{order.priority!r}",
                        entity_id=order.order_id,
                    )
                )
        for po in purchase_orders:
            if po.status not in _VALID_PO_STATUS:
                issues.append(
                    DataQualityIssue(
                        code="unsupported_status",
                        detail=f"purchase order {po.po_id} has status {po.status!r}",
                        entity_id=po.po_id,
                    )
                )

        issues.sort(key=lambda i: (i.code, i.entity_id, i.detail))
        return issues

    # -- internals -----------------------------------------------------------

    def _read_state(self) -> dict:
        return {
            "products": self._data_store.get_products(),
            "suppliers": self._data_store.get_suppliers(),
            "warehouses": self._data_store.get_warehouses(),
            "inventory": self._data_store.get_inventory(),
            "customer_orders": self._data_store.get_customer_orders(),
            "purchase_orders": self._data_store.get_purchase_orders(),
        }

    @staticmethod
    def _freshness_status(state: dict, freshness_limit_hours: int) -> str:
        synced = [r.last_synced_at for r in state["inventory"]]
        if not synced:
            return "unknown"
        newest = max(synced)
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - newest).total_seconds() / 3600.0
        if age_hours > freshness_limit_hours:
            return "stale"
        return "fresh"
