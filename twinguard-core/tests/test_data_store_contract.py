"""The DataStore contract, as a reusable suite.

Spec Section 4 lists the reads a ``DataStore`` must support and names
``apply_approved_action`` as the only write path. The backend replaces
``InMemoryDataStore`` with a SQLAlchemy implementation, so those requirements
are expressed here as a suite that *any* store can be run against::

    @pytest.mark.parametrize("store", [my_store()], indirect=True)
    def test_reads_the_contract(store):
        assert_data_store_contract(store)

The SQLAlchemy store is not built yet, so only ``InMemoryDataStore`` is wired
up below. When it lands, add one factory next to it - do not copy these tests.
"""

from __future__ import annotations

import pytest

from twinguard_core import (
    ActionType,
    DataStore,
    InMemoryDataStore,
    Role,
    demo_baseline,
)
from twinguard_core.models import BusinessRules, ProposedAction, Warehouse

from .conftest import transfer

STORE_FACTORIES = {
    "in_memory": InMemoryDataStore,
}


def build_store(name: str = "in_memory") -> DataStore:
    return STORE_FACTORIES[name](demo_baseline())


@pytest.fixture(params=sorted(STORE_FACTORIES))
def store(request) -> DataStore:
    """Parametrised over every store implementation.

    Adding a backend store here is what makes the shared contract real rather
    than aspirational.
    """
    return build_store(request.param)


# ---------------------------------------------------------------------------
# Section 4: required reads
# ---------------------------------------------------------------------------


def test_the_contract_methods_all_exist(store: DataStore):
    for name in (
        "get_products",
        "get_suppliers",
        "get_warehouses",
        "get_inventory",
        "get_customer_orders",
        "get_purchase_orders",
        "get_business_rules",
        "get_version",
        "apply_approved_action",
        "persist_snapshot",
    ):
        assert callable(getattr(store, name)), name


def test_every_abstract_method_of_the_interface_is_implemented(store: DataStore):
    """The contract is the ABC: a store that fails to implement all of it
    cannot be instantiated in the first place, so this is the guard against
    the interface silently losing a required read."""
    import inspect

    required = {
        name
        for name, member in inspect.getmembers(DataStore, inspect.isfunction)
        if getattr(member, "__isabstractmethod__", False)
    }
    assert required == {
        "get_products",
        "get_suppliers",
        "get_warehouses",
        "get_inventory",
        "get_customer_orders",
        "get_purchase_orders",
        "get_business_rules",
        "get_version",
        "apply_approved_action",
    }
    assert not getattr(type(store), "__abstractmethods__", frozenset())


def test_the_seed_produces_a_reproducible_starting_state():
    """Section 9: two calls to the same dataset are equal, so a demo, a test and
    a fresh database all start from the same state.

    The anchor is pinned because every date in the seed is *relative* to it -
    "due in 3 days", "synced 1 hour ago". Pinning it is what makes the
    comparison meaningful; with the default ``now`` the shape is identical and
    only the absolute timestamps move, which is the point of anchoring at all.
    """
    from datetime import datetime, timezone

    from twinguard_core import demo_baseline, demo_stale, seed_dataset

    anchor = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    for factory in (demo_baseline, demo_stale, seed_dataset):
        first = factory(now=anchor).model_dump(mode="json")
        second = factory(now=anchor).model_dump(mode="json")
        assert first == second, factory.__name__


def test_the_datasets_differ_only_where_the_spec_requires():
    """``demo_stale`` is the baseline with a missing lead time and an old sync
    timestamp - nothing else, so the abstain scenario has a single cause."""
    from datetime import datetime, timezone

    from twinguard_core import demo_baseline, demo_stale

    anchor = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    baseline, stale = demo_baseline(now=anchor), demo_stale(now=anchor)

    # Identical catalogue, warehouses, orders and rules.
    for collection in ("products", "warehouses", "customer_orders", "business_rules"):
        assert getattr(baseline, collection) == getattr(stale, collection), collection

    # The only supplier difference is the null lead time the spec requires.
    assert {s.supplier_id: s.lead_time_days for s in stale.suppliers} == {
        s.supplier_id: s.lead_time_days for s in baseline.suppliers
    } | {next(s.supplier_id for s in stale.suppliers if s.lead_time_days is None): None}

    # The only inventory difference is the sync timestamp.
    for fresh, old in zip(baseline.inventory, stale.inventory):
        assert (fresh.product_id, fresh.warehouse_id, fresh.quantity_on_hand) == (
            old.product_id,
            old.warehouse_id,
            old.quantity_on_hand,
        )
        assert old.last_synced_at < fresh.last_synced_at


def test_every_read_returns_a_non_empty_list(store: DataStore):
    for name in (
        "get_products",
        "get_suppliers",
        "get_warehouses",
        "get_inventory",
        "get_customer_orders",
        "get_purchase_orders",
    ):
        rows = getattr(store, name)()
        assert isinstance(rows, list) and rows, name


def test_the_store_implements_the_abstract_base(store: DataStore):
    assert isinstance(store, DataStore)


def test_the_version_is_present_and_not_a_float(store: DataStore):
    version = store.get_version()
    assert version is not None
    assert not isinstance(version, float)


def test_business_rules_come_back_as_a_model(store: DataStore):
    rules = store.get_business_rules()
    assert isinstance(rules, BusinessRules)
    assert rules.max_transfer_quantity > 0
    assert rules.max_purchase_budget > 0


def test_entities_come_back_as_models(store: DataStore):
    assert store.get_products()[0].product_id
    assert store.get_suppliers()[0].supplier_id
    assert store.get_inventory()[0].product_id
    assert store.get_customer_orders()[0].order_id
    assert store.get_purchase_orders()[0].po_id
    assert isinstance(store.get_warehouses()[0], Warehouse)


def test_the_seed_inventory_is_where_the_scenarios_expect_it(store: DataStore):
    """P-003 is the product the demo scenarios move."""
    p003 = [row for row in store.get_inventory() if row.product_id == "P-003"]
    assert {row.warehouse_id for row in p003} == {"WH-01", "WH-02"}
    assert sum(row.quantity_on_hand for row in p003) == 600


# ---------------------------------------------------------------------------
# Section 4: reads are isolated from writes
# ---------------------------------------------------------------------------


def test_mutating_a_returned_object_does_not_change_the_store(store: DataStore):
    store.get_products()[0].name = "MUTATED"
    store.get_customer_orders().clear()
    assert store.get_products()[0].name != "MUTATED"
    assert store.get_customer_orders()


def test_the_inventory_read_is_a_copy_not_a_view(store: DataStore):
    rows = store.get_inventory()
    rows[0].quantity_on_hand = -1
    assert store.get_inventory()[0].quantity_on_hand != -1


# ---------------------------------------------------------------------------
# apply_approved_action: the only write path
# ---------------------------------------------------------------------------


def test_an_approved_transfer_moves_stock(store: DataStore):
    action = transfer(100, product_id="P-003")
    store.apply_approved_action(action)

    rows = {
        (row.product_id, row.warehouse_id): row.quantity_on_hand
        for row in store.get_inventory()
        if row.product_id == "P-003"
    }
    assert rows[("P-003", "WH-01")] == 400
    assert rows[("P-003", "WH-02")] == 200


def test_a_write_moves_the_version(store: DataStore):
    before = store.get_version()
    store.apply_approved_action(transfer(10, product_id="P-003"))
    assert store.get_version() != before


def test_a_rejected_write_leaves_the_version_untouched(store: DataStore):
    """The write path does not re-check policy, but it must still be atomic:
    a target that does not exist raises and nothing is claimed to have changed.
    """
    before = store.get_version()
    with pytest.raises(KeyError):
        store.apply_approved_action(
            ProposedAction(
                action_id="act-missing",
                action_type=ActionType.CANCEL_PURCHASE_ORDER,
                parameters={"po_id": "PO-DOES-NOT-EXIST"},
                proposed_by=Role.MANAGER,
            )
        )
    assert store.get_version() == before


def test_a_create_purchase_order_write_adds_a_record(store: DataStore):
    before = {p.po_id for p in store.get_purchase_orders()}
    store.apply_approved_action(
        ProposedAction(
            action_id="act-po",
            action_type=ActionType.CREATE_PURCHASE_ORDER,
            parameters={
                "product_id": "P-002",
                "supplier_id": "SUP-001",
                "quantity": 40,
                "destination_warehouse_id": "WH-01",
            },
            proposed_by=Role.MANAGER,
        )
    )
    after = {p.po_id for p in store.get_purchase_orders()}
    assert len(after) == len(before) + 1
    new = [p for p in store.get_purchase_orders() if p.po_id not in before][0]
    assert new.product_id == "P-002"
    assert new.quantity == 40
    assert new.status == "pending"


def test_a_priority_change_write_is_applied(store: DataStore):
    store.apply_approved_action(
        ProposedAction(
            action_id="act-prio",
            action_type=ActionType.CHANGE_ORDER_PRIORITY,
            parameters={"order_id": "SO-1003", "new_priority": "high"},
            proposed_by=Role.MANAGER,
        )
    )
    order = [o for o in store.get_customer_orders() if o.order_id == "SO-1003"][0]
    assert order.priority == "high"


# ---------------------------------------------------------------------------
# persist_snapshot
# ---------------------------------------------------------------------------


def test_persisting_a_snapshot_does_not_change_operational_data(store: DataStore):
    """Snapshot persistence is a write to *snapshot* storage, not to the
    operational tables the agent is forbidden from touching."""
    from twinguard_core import TwinManager

    twin = TwinManager(store)
    before_version = store.get_version()
    before_inventory = {
        (row.product_id, row.warehouse_id): row.quantity_on_hand
        for row in store.get_inventory()
    }

    twin.create_snapshot()

    assert store.get_version() == before_version
    assert {
        (row.product_id, row.warehouse_id): row.quantity_on_hand
        for row in store.get_inventory()
    } == before_inventory


def test_create_snapshot_goes_through_the_store_interface(store: DataStore):
    """``TwinManager.create_snapshot()`` must call ``persist_snapshot`` rather
    than keeping snapshots to itself - that is the backend's extension point."""
    from twinguard_core import TwinManager

    calls: list[tuple] = []
    store.persist_snapshot = lambda meta, state: calls.append((meta, state))

    twin = TwinManager(store)
    snapshot = twin.create_snapshot()

    assert len(calls) == 1
    meta, state = calls[0]
    assert meta.snapshot_id == snapshot.snapshot_id
    assert set(state) >= {"products", "inventory", "customer_orders"}


def test_a_persisted_snapshot_carries_the_state_the_twin_snapshotted(
    store: DataStore,
):
    from twinguard_core import TwinManager

    twin = TwinManager(store)
    captured: list[dict] = []
    original = store.persist_snapshot
    store.persist_snapshot = lambda meta, state: (
        captured.append(state), original(meta, state)
    )[0]

    snapshot = twin.create_snapshot()

    assert snapshot.snapshot_id in store.persisted_snapshots
    assert store.persisted_snapshots[snapshot.snapshot_id].source_version == (
        store.get_version()
    )
    assert set(captured[0]) >= {"products", "inventory", "customer_orders"}
    # The state handed to persist is a copy: mutating it cannot reach the store.
    captured[0]["products"].clear()
    assert store.get_products()
