"""Fixed, seeded demo data.

Every generator here is deterministic: values come from ``random.Random(SEED)``
with an explicit seed, or from literal tables. Two calls produce equal datasets.

Datasets
--------
``demo_baseline(now=None)``
    The clean starting state: 14 products, 3 suppliers that all have a known
    ``lead_time_days``, 2 warehouses, a mix of order priorities, 1 pending
    purchase order. Simulating a no-op against this over a 14-day horizon
    produces zero stockouts and zero rule violations, which is what makes the
    "safe action" demo able to reach Decision.APPROVE.

``demo_stale(now=None)``
    Identical shape, but ``SUP-003`` has ``lead_time_days=None`` and the
    inventory was last synced 96 hours ago. This is the spec Section 3 dataset
    that exercises the missing-data path.

Why two datasets
----------------
Spec Section 3 requires a supplier with ``lead_time_days=None`` in the seed, and
Section 4.3 requires ``run_data_quality_checks`` to *always* report it. Section
6.3 makes ``classify()`` return ABSTAIN whenever any data-quality issue exists.
Together those three rules mean a single dataset containing that supplier can
never reach APPROVE, so the "safe action" demo would be unreachable. Rather than
narrow the data-quality check (which would break Section 4.3) or drop the
missing supplier (which would break Section 3), each demo scenario selects the
dataset it needs. This matches the Backend spec Section 7, where every scenario
fixture carries its own "starting seed data overrides".
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import (
    BusinessRules,
    CustomerOrder,
    InventoryRecord,
    Product,
    PurchaseOrder,
    SeedDataset,
    Supplier,
    Warehouse,
)

#: Explicit seed. Spec Section 3: "no random without an explicit seed".
SEED = 42

SUPPLIER_TABLE = [
    ("SUP-001", "Northwind Components", 5),
    ("SUP-002", "Pacific Fasteners", 12),
    ("SUP-003", "Meridian Plastics", None),  # unknown lead time
]

WAREHOUSE_TABLE = [
    ("WH-01", "North Hub", 6000),
    # WH-02 holds ~690 units at rest against this 1000-unit ceiling: 69% at
    # rest, so a no-op run is clean, and a single full 500-unit transfer
    # (the maximum the policy allows) pushes it to ~119%. The capacity rule
    # therefore has a reliable trigger that does not depend on a purchase order
    # arriving on a particular day.
    ("WH-02", "South Hub", 1000),
]

# (product_id, name, unit_price)
PRODUCT_TABLE = [
    ("P-001", "Hex Bolt M8", 0.40),
    ("P-002", "Hex Nut M8", 0.15),
    ("P-003", "Steel Washer M8", 0.08),
    ("P-004", "Bearing 6204", 14.50),
    ("P-005", "Bearing 6206", 19.75),
    ("P-006", "Timing Belt 400", 11.20),
    ("P-007", "Drive Chain 08B", 23.40),
    ("P-008", "Sprocket 32T", 9.90),
    ("P-009", "Hydraulic Hose 1m", 17.60),
    ("P-010", "Quick Coupler 1/4", 7.35),
    ("P-011", "Pressure Gauge 10bar", 26.10),
    ("P-012", "Nitrile Gasket A", 1.25),
    ("P-013", "Threadlocker 50ml", 6.80),
    ("P-014", "Zinc Washer M10", 0.11),
]

DEFAULT_RULES = BusinessRules(
    max_transfer_quantity=500,
    max_purchase_budget=5000.0,
    freshness_limit_hours=24,
    allow_negative_inventory=False,
)

#: Stock levels for the products the four demo scenarios act on are written out
#: rather than drawn from the RNG, so the scenarios are legible and cannot drift.
#:
#: ``P-001``  ample, so an oversized transfer drives it negative.
#: ``P-003``  600 in total against a 400-unit order, so moving 50 out is safe
#:            and moving 500 out starves the order.
#: ``P-007``  only 5 in total, so the pending PO-2001 is the sole thing keeping
#:            SO-1003 alive. Cancelling that PO is the human-review scenario.
#:
#: A product listed here gets *exactly* these rows, which is how P-007 ends up
#: with no WH-02 row at all.
EXPLICIT_INVENTORY: dict[str, dict[str, int]] = {
    "P-001": {"WH-01": 800, "WH-02": 120},
    "P-003": {"WH-01": 500, "WH-02": 100},
    "P-007": {"WH-01": 5},
}

#: How many products (in id order) are also stocked in WH-02.
WAREHOUSE_02_PRODUCT_COUNT = 8


def _anchor(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _build(
    *,
    now: Optional[datetime],
    lead_time_override: Optional[int],
    hours_since_sync: int,
) -> SeedDataset:
    """Build a dataset. Only ``lead_time_override`` and ``hours_since_sync``
    differ between the baseline and the stale variant."""
    anchor = _anchor(now)
    today = anchor.date()
    synced_at = anchor - timedelta(hours=hours_since_sync)
    rng = random.Random(SEED)

    products = [
        Product(product_id=pid, name=name, unit_price=price)
        for pid, name, price in PRODUCT_TABLE
    ]
    suppliers = [
        Supplier(
            supplier_id=sid,
            name=name,
            lead_time_days=(
                lead_time_override
                if table_lead is None
                else table_lead
            ),
        )
        for sid, name, table_lead in SUPPLIER_TABLE
    ]
    warehouses = [
        Warehouse(warehouse_id=wid, name=name, capacity=capacity)
        for wid, name, capacity in WAREHOUSE_TABLE
    ]

    # Every product is stocked in WH-01. The first WAREHOUSE_02_PRODUCT_COUNT
    # are also stocked in WH-02, which is the small warehouse: a large transfer
    # into it overflows its capacity, which is the capacity-violation case.
    # Stock levels are sized so a no-op run has no stockout, no delay and no
    # violation at all - that is what lets the "safe" demo reach APPROVE.
    inventory: list[InventoryRecord] = []
    for index, product in enumerate(products):
        explicit = EXPLICIT_INVENTORY.get(product.product_id)
        if explicit is not None:
            for warehouse_id in sorted(explicit):
                inventory.append(
                    InventoryRecord(
                        product_id=product.product_id,
                        warehouse_id=warehouse_id,
                        quantity_on_hand=explicit[warehouse_id],
                        quantity_reserved=0,
                        last_synced_at=synced_at,
                    )
                )
            continue

        inventory.append(
            InventoryRecord(
                product_id=product.product_id,
                warehouse_id="WH-01",
                quantity_on_hand=200 + rng.randrange(0, 200),
                quantity_reserved=0,
                last_synced_at=synced_at,
            )
        )
        if index < WAREHOUSE_02_PRODUCT_COUNT:
            inventory.append(
                InventoryRecord(
                    product_id=product.product_id,
                    warehouse_id="WH-02",
                    quantity_on_hand=60 + rng.randrange(0, 100),
                    quantity_reserved=0,
                    last_synced_at=synced_at,
                )
            )

    # (order_id, product_id, quantity, priority, due_in_days)
    order_table = [
        ("SO-1001", "P-001", 120, "high", 3),
        ("SO-1002", "P-004", 40, "normal", 5),
        ("SO-1003", "P-007", 25, "low", 7),
        ("SO-1004", "P-011", 15, "normal", 9),
        ("SO-1005", "P-003", 400, "low", 11),
        ("SO-1006", "P-009", 30, "normal", 13),
    ]
    customer_orders = [
        CustomerOrder(
            order_id=oid,
            product_id=pid,
            quantity=qty,
            priority=priority,
            due_date=today + timedelta(days=days_out),
            status="open",
        )
        for oid, pid, qty, priority, days_out in order_table
    ]

    # Exactly one *pending* PO, so the cancel scenario has a single target.
    # PO-2002 is already received and exists to give the duplicate-PO rule and
    # the received-PO path something to work against.
    purchase_orders = [
        PurchaseOrder(
            po_id="PO-2001",
            product_id="P-007",
            supplier_id="SUP-001",
            quantity=300,
            expected_arrival=today + timedelta(days=4),
            status="pending",
            destination_warehouse_id="WH-01",
        ),
        PurchaseOrder(
            po_id="PO-2002",
            product_id="P-011",
            supplier_id="SUP-002",
            quantity=60,
            expected_arrival=today - timedelta(days=1),
            status="received",
            destination_warehouse_id="WH-02",
        ),
    ]

    return SeedDataset(
        products=products,
        suppliers=suppliers,
        warehouses=warehouses,
        inventory=inventory,
        customer_orders=customer_orders,
        purchase_orders=purchase_orders,
        business_rules=DEFAULT_RULES,
    )


def demo_baseline(now: Optional[datetime] = None) -> SeedDataset:
    """Clean starting state: every supplier lead time known, fresh sync."""
    return _build(now=now, lead_time_override=5, hours_since_sync=1)


def demo_stale(now: Optional[datetime] = None) -> SeedDataset:
    """Unreliable starting state: unknown lead time and a 96h-old sync."""
    return _build(now=now, lead_time_override=None, hours_since_sync=96)


def seed_dataset(now: Optional[datetime] = None) -> SeedDataset:
    """The canonical dataset named in spec Section 3.

    This is ``demo_stale`` — it is the one that carries the missing
    ``lead_time_days`` the spec requires the seed to contain.
    """
    return demo_stale(now=now)


def dataset_names() -> list[str]:
    return ["demo_baseline", "demo_stale", "seed_dataset"]


def get_dataset(name: str, now: Optional[datetime] = None) -> SeedDataset:
    """Look a dataset up by name; used by the backend seeder and eval harness."""
    factories = {
        "demo_baseline": demo_baseline,
        "demo_stale": demo_stale,
        "seed_dataset": seed_dataset,
    }
    try:
        factory = factories[name]
    except KeyError:
        raise KeyError(
            f"unknown dataset {name!r}; available: {sorted(factories)}"
        ) from None
    return factory(now=now)
