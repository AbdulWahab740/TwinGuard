"""Shared fixtures.

Two things worth knowing about the seed data:

* Timestamps are relative to an anchor, not absolute. ``demo_baseline()`` builds
  ``last_synced_at`` as *now minus 1 hour* and order due dates as *today plus N
  days*, because the data-quality check and the simulator both compare against
  the wall clock. The *content* is fixed and seeded; pass ``now=`` to pin the
  anchor when a test needs byte-identical datasets.
* ``demo_baseline`` is the clean dataset and ``demo_stale`` is the one carrying
  the unknown supplier lead time. See the ``seed_data`` module docstring for why
  both are needed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from twinguard_core import (
    InMemoryDataStore,
    PolicyEngine,
    ProposedAction,
    Role,
    SeedDataset,
    SimulationEngine,
    TwinManager,
    demo_baseline,
    demo_stale,
)
from twinguard_core.models import (
    ActionType,
    BusinessRules,
    CustomerOrder,
    InventoryRecord,
    Product,
    PurchaseOrder,
    Supplier,
    Warehouse,
)

#: A fixed anchor for tests that need reproducible timestamps.
ANCHOR = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


@pytest.fixture
def baseline_dataset() -> SeedDataset:
    """Clean data: every lead time known, fresh sync, no rule violations."""
    return demo_baseline()


@pytest.fixture
def stale_dataset() -> SeedDataset:
    """Unreliable data: SUP-003 has no lead time and the sync is 96h old."""
    return demo_stale()


@pytest.fixture
def scarce_dataset() -> SeedDataset:
    """A purpose-built dataset for the high-priority-delay rule.

    ``SOH`` is a high-priority order for 60 units of ``PS``, and ``PS`` has zero
    stock in both warehouses. The only supply is ``POS``, a pending purchase
    order landing before the order is due. Cancelling that purchase order is a
    legal, rule-clean action that leaves a high-priority customer waiting -
    spec Section 5.3. ``PT`` carries ordinary stock so an unrelated, harmless
    transfer is also available as a control.

    Note a transfer alone can never starve an order in this model: available
    stock is summed across every warehouse, so moving stock between them does
    not change the total. Only removing supply from the system does.
    """
    today = date.today()
    return SeedDataset(
        products=[
            Product(product_id="PS", name="Starved Widget", unit_price=10.0),
            Product(product_id="PT", name="Spare Widget", unit_price=2.0),
        ],
        suppliers=[Supplier(supplier_id="SS", name="Scarce Supply", lead_time_days=5)],
        warehouses=[
            Warehouse(warehouse_id="WS", name="Main", capacity=1000),
            Warehouse(warehouse_id="WT", name="Spare", capacity=1000),
        ],
        inventory=[
            InventoryRecord(
                product_id="PS",
                warehouse_id="WS",
                quantity_on_hand=0,
                quantity_reserved=0,
                last_synced_at=datetime.now(timezone.utc),
            ),
            InventoryRecord(
                product_id="PS",
                warehouse_id="WT",
                quantity_on_hand=0,
                quantity_reserved=0,
                last_synced_at=datetime.now(timezone.utc),
            ),
            InventoryRecord(
                product_id="PT",
                warehouse_id="WS",
                quantity_on_hand=100,
                quantity_reserved=0,
                last_synced_at=datetime.now(timezone.utc),
            ),
            InventoryRecord(
                product_id="PT",
                warehouse_id="WT",
                quantity_on_hand=0,
                quantity_reserved=0,
                last_synced_at=datetime.now(timezone.utc),
            ),
        ],
        customer_orders=[
            CustomerOrder(
                order_id="SOH",
                product_id="PS",
                quantity=60,
                priority="high",
                due_date=today + timedelta(days=8),
                status="open",
            )
        ],
        purchase_orders=[
            PurchaseOrder(
                po_id="POS",
                product_id="PS",
                supplier_id="SS",
                quantity=100,
                expected_arrival=today + timedelta(days=5),
                status="pending",
                destination_warehouse_id="WS",
            )
        ],
        business_rules=BusinessRules(
            max_transfer_quantity=500,
            max_purchase_budget=1000.0,
            freshness_limit_hours=24,
            allow_negative_inventory=False,
        ),
    )


@pytest.fixture
def contended_dataset() -> SeedDataset:
    """One product, 100 units, three due orders competing for them.

    ``SOH`` (high, 60) must always win, because priority is the primary sort
    key. The two normal-priority orders then fight over the remaining 40, and
    which of them wins is decided by the ``seed``-driven tie-break. This is what
    the ``seed`` parameter of ``simulate_action`` actually does.
    """
    today = date.today()
    return SeedDataset(
        products=[Product(product_id="PC", name="Contended Widget", unit_price=5.0)],
        suppliers=[Supplier(supplier_id="SC", name="Contended Supply", lead_time_days=5)],
        warehouses=[Warehouse(warehouse_id="WC", name="Only Warehouse", capacity=1000)],
        inventory=[
            InventoryRecord(
                product_id="PC",
                warehouse_id="WC",
                quantity_on_hand=100,
                quantity_reserved=0,
                last_synced_at=datetime.now(timezone.utc),
            )
        ],
        customer_orders=[
            CustomerOrder(
                order_id="SOH",
                product_id="PC",
                quantity=60,
                priority="high",
                due_date=today + timedelta(days=3),
                status="open",
            ),
            CustomerOrder(
                order_id="SON1",
                product_id="PC",
                quantity=40,
                priority="normal",
                due_date=today + timedelta(days=3),
                status="open",
            ),
            CustomerOrder(
                order_id="SON2",
                product_id="PC",
                quantity=40,
                priority="normal",
                due_date=today + timedelta(days=3),
                status="open",
            ),
        ],
        purchase_orders=[],
        business_rules=BusinessRules(
            max_transfer_quantity=500,
            max_purchase_budget=1000.0,
            freshness_limit_hours=24,
            allow_negative_inventory=False,
        ),
    )


# ---------------------------------------------------------------------------
# Wired-up objects
# ---------------------------------------------------------------------------


@pytest.fixture
def store(baseline_dataset: SeedDataset) -> InMemoryDataStore:
    return InMemoryDataStore(baseline_dataset)


@pytest.fixture
def twin(store: InMemoryDataStore) -> TwinManager:
    return TwinManager(store)


@pytest.fixture
def engine(twin: TwinManager) -> SimulationEngine:
    return SimulationEngine(twin)


@pytest.fixture
def policy(store: InMemoryDataStore) -> PolicyEngine:
    return PolicyEngine(store.get_business_rules())


@pytest.fixture
def snapshot_id(twin: TwinManager) -> str:
    return twin.create_snapshot().snapshot_id


# ---------------------------------------------------------------------------
# Action builders
# ---------------------------------------------------------------------------


def transfer(
    quantity: int = 50,
    product_id: str = "P-003",
    source: str = "WH-01",
    destination: str = "WH-02",
    role: Role = Role.AI_AGENT,
    action_id: str = "act-transfer",
) -> ProposedAction:
    return ProposedAction(
        action_id=action_id,
        action_type=ActionType.TRANSFER_INVENTORY,
        parameters={
            "product_id": product_id,
            "source_warehouse_id": source,
            "destination_warehouse_id": destination,
            "quantity": quantity,
        },
        proposed_by=role,
    )


def purchase(
    product_id: str = "P-011",
    supplier_id: str = "SUP-001",
    quantity: int = 10,
    destination: str | None = "WH-01",
    role: Role = Role.AI_AGENT,
    action_id: str = "act-purchase",
) -> ProposedAction:
    parameters: dict = {
        "product_id": product_id,
        "supplier_id": supplier_id,
        "quantity": quantity,
    }
    if destination is not None:
        parameters["destination_warehouse_id"] = destination
    return ProposedAction(
        action_id=action_id,
        action_type=ActionType.CREATE_PURCHASE_ORDER,
        parameters=parameters,
        proposed_by=role,
    )


def reprioritise(
    order_id: str = "SO-1001",
    new_priority: str = "low",
    role: Role = Role.PLANNER,
    action_id: str = "act-priority",
) -> ProposedAction:
    return ProposedAction(
        action_id=action_id,
        action_type=ActionType.CHANGE_ORDER_PRIORITY,
        parameters={"order_id": order_id, "new_priority": new_priority},
        proposed_by=role,
    )


def cancel(
    po_id: str = "PO-2001",
    role: Role = Role.MANAGER,
    action_id: str = "act-cancel",
) -> ProposedAction:
    return ProposedAction(
        action_id=action_id,
        action_type=ActionType.CANCEL_PURCHASE_ORDER,
        parameters={"po_id": po_id},
        proposed_by=role,
    )
