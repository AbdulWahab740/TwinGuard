"""SimulationEngine: determinism, every Section 5.3 violation, clean runs.

Spec Section 5.4 requires determinism to be asserted directly by running the
same simulation twice and comparing ``model_dump()``, and that the live data
version must not move.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from twinguard_core import (
    InMemoryDataStore,
    ProposedAction,
    Role,
    SimulationEngine,
    TwinManager,
    demo_baseline,
    demo_stale,
)
from twinguard_core.models import ActionType, PurchaseOrder, SimulationEvent

from .conftest import (
    cancel,
    contended_dataset,
    purchase,
    reprioritise,
    scarce_dataset,
    transfer,
)


# ---------------------------------------------------------------------------
# 5.4 determinism and isolation
# ---------------------------------------------------------------------------


def test_same_inputs_produce_a_byte_identical_report(
    engine: SimulationEngine, snapshot_id: str
):
    action = transfer(50, action_id="act-det")
    first = engine.simulate_action(snapshot_id, action)
    second = engine.simulate_action(snapshot_id, action)
    assert first.model_dump() == second.model_dump()


def test_simulation_id_is_derived_from_inputs_not_a_counter(
    engine: SimulationEngine, twin: TwinManager, snapshot_id: str
):
    action = transfer(50, action_id="act-det")
    first = engine.simulate_action(snapshot_id, action)
    second = engine.simulate_action(snapshot_id, action)
    assert first.simulation_id == second.simulation_id
    # A different seed is a different input, so a different identity.
    third = engine.simulate_action(snapshot_id, action, seed=7)
    assert third.simulation_id != first.simulation_id


def test_simulation_never_changes_the_live_data_version(
    engine: SimulationEngine, store: InMemoryDataStore, snapshot_id: str
):
    before = store.get_version()
    engine.simulate_action(snapshot_id, transfer(50))
    engine.simulate_action(snapshot_id, purchase())
    engine.simulate_action(snapshot_id, cancel())
    assert store.get_version() == before


def test_simulation_never_changes_live_data(
    engine: SimulationEngine, store: InMemoryDataStore, snapshot_id: str
):
    before = store.get_state()
    engine.simulate_action(snapshot_id, transfer(900, product_id="P-001"))
    assert store.get_state() == before


def test_simulation_never_changes_the_stored_snapshot(
    engine: SimulationEngine, twin: TwinManager, snapshot_id: str
):
    """Twice-run determinism depends on this: the engine works on a copy."""
    before = twin.get_snapshot_state(snapshot_id)
    engine.simulate_action(snapshot_id, transfer(500, product_id="P-002"))
    assert twin.get_snapshot_state(snapshot_id) == before


def test_the_seed_decides_contention_but_never_priority(contended_dataset):
    """Documents what ``seed`` is for: it breaks ties between equals, and it
    never lets a low-priority order jump a high-priority one."""
    twin = TwinManager(InMemoryDataStore(contended_dataset))
    engine = SimulationEngine(twin)
    snapshot = twin.create_snapshot().snapshot_id
    action = ProposedAction(
        action_id="act-contend",
        action_type=ActionType.CREATE_PURCHASE_ORDER,
        parameters={
            "product_id": "PC",
            "supplier_id": "SC",
            "quantity": 1,
            "destination_warehouse_id": "WC",
        },
        proposed_by=Role.AI_AGENT,
    )

    # Read the outcome from final_state_diff: the trace keeps only one focus
    # entity per day, which is not enough to see who was served.
    def served(report, order_id: str) -> bool:
        entry = report.final_state_diff["customer_orders"].get(order_id, {})
        changed = entry.get("changed", {})
        status = changed.get("status", {}).get("b", "open")
        return status == "fulfilled"

    winners = set()
    for seed in range(1, 9):
        report = engine.simulate_action(snapshot, action, seed=seed)
        # SOH is high priority, so it is served first whatever the seed.
        assert served(report, "SOH"), f"seed {seed} failed to serve the high priority order"
        winners |= {
            order_id
            for order_id in ("SON1", "SON2")
            if served(report, order_id)
        }
    assert len(winners) > 1, "the seed should decide which equal order loses"


def test_trace_has_one_event_per_day(
    engine: SimulationEngine, snapshot_id: str
):
    for horizon in (1, 7, 14):
        report = engine.simulate_action(
            snapshot_id, transfer(50), horizon_days=horizon
        )
        assert [e.day for e in report.trace] == list(range(1, horizon + 1))
        assert all(isinstance(e, SimulationEvent) for e in report.trace)


def test_zero_horizon_is_rejected(engine: SimulationEngine, snapshot_id: str):
    with pytest.raises(ValueError):
        engine.simulate_action(snapshot_id, transfer(50), horizon_days=0)


def test_report_carries_every_required_kpi(
    engine: SimulationEngine, snapshot_id: str
):
    kpis = engine.simulate_action(snapshot_id, transfer(50)).kpis
    for name in (
        "stockout_count",
        "orders_delayed",
        "total_inventory_value",
        "budget_used",
    ):
        assert name in kpis
        assert isinstance(kpis[name], float)


def test_final_state_diff_describes_the_horizon_not_the_action(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(snapshot_id, transfer(50))
    # Orders ship and purchase orders land, so the diff is not empty even
    # though the store never changed.
    assert report.final_state_diff["customer_orders"]


# ---------------------------------------------------------------------------
# 5.3 violations - one triggering and one clean case per action type
# ---------------------------------------------------------------------------


def test_transfer_over_max_quantity_violates(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(snapshot_id, transfer(501))
    assert any(
        "exceeds max_transfer_quantity 500" in v for v in report.violated_rules
    )


def test_transfer_at_max_quantity_does_not_violate(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(
        snapshot_id, transfer(50, product_id="P-002", action_id="act-clean")
    )
    assert not any("max_transfer_quantity" in v for v in report.violated_rules)


def test_transfer_that_drives_stock_negative_violates(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(
        snapshot_id, transfer(900, product_id="P-001", action_id="act-neg")
    )
    assert any(
        v.startswith("negative available inventory for P-001@WH-01")
        for v in report.violated_rules
    )


def test_transfer_into_a_full_warehouse_violates_capacity(
    engine: SimulationEngine, snapshot_id: str
):
    """The maximum permitted transfer fills WH-02 past its ceiling.

    P-001 is used because WH-01 holds 800 of it, so capacity is the *only*
    rule that fires - a product with less stock would also trip the negative
    inventory rule and the test would stop isolating capacity.
    """
    report = engine.simulate_action(
        snapshot_id, transfer(500, product_id="P-001", action_id="act-cap")
    )
    assert any(
        "WH-02 exceeded its capacity" in v for v in report.violated_rules
    )
    assert not any("negative available inventory" in v for v in report.violated_rules)
    assert report.kpis["max_warehouse_capacity_pct"] > 100.0


def test_small_transfer_into_the_same_warehouse_is_clean(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(snapshot_id, transfer(50, action_id="act-ok"))
    assert report.violated_rules == []
    assert report.kpis["stockout_count"] == 0.0
    assert report.kpis["orders_delayed"] == 0.0


def test_transfer_to_the_same_warehouse_violates(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(
        snapshot_id, transfer(10, source="WH-01", destination="WH-01", action_id="act-same")
    )
    assert any("same" in v for v in report.violated_rules)


def test_purchase_over_budget_violates(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(
        snapshot_id, purchase(product_id="P-011", quantity=400, action_id="act-over")
    )
    assert any(
        "exceeds max_purchase_budget 5000.00" in v for v in report.violated_rules
    )
    assert report.kpis["budget_used"] > 5000.0


def test_purchase_within_budget_is_clean(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(
        snapshot_id, purchase(product_id="P-011", quantity=10, action_id="act-po-ok")
    )
    assert report.violated_rules == []
    assert report.kpis["budget_used"] == pytest.approx(261.0)


def test_duplicate_pending_purchase_order_violates(
    engine: SimulationEngine, snapshot_id: str
):
    # PO-2001 is already pending for P-007 from SUP-001.
    report = engine.simulate_action(
        snapshot_id, purchase(product_id="P-007", supplier_id="SUP-001", quantity=5)
    )
    assert any(
        "pending purchase orders exist for P-007 from SUP-001" in v
        for v in report.violated_rules
    )


def test_purchase_from_a_different_supplier_is_not_a_duplicate(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(
        snapshot_id, purchase(product_id="P-007", supplier_id="SUP-002", quantity=5)
    )
    assert not any("pending purchase orders" in v for v in report.violated_rules)


def test_purchase_with_an_unknown_lead_time_is_reported_not_silently_dropped():
    """Needs the stale dataset: only there does a supplier have no lead time."""
    twin = TwinManager(InMemoryDataStore(demo_stale()))
    engine = SimulationEngine(twin)
    snapshot = twin.create_snapshot().snapshot_id
    report = engine.simulate_action(
        snapshot,
        purchase(product_id="P-014", supplier_id="SUP-003", quantity=5, action_id="act-lead"),
    )
    assert any("no known lead time" in w for w in report.warnings)
    assert any(
        "will not arrive within the horizon" in s for s in report.side_effects
    )


def _scare_transfer(quantity: int, action_id: str) -> ProposedAction:
    return ProposedAction(
        action_id=action_id,
        action_type=ActionType.TRANSFER_INVENTORY,
        parameters={
            "product_id": "PT",
            "source_warehouse_id": "WS",
            "destination_warehouse_id": "WT",
            "quantity": quantity,
        },
        proposed_by=Role.MANAGER,
    )


def test_high_priority_order_pushed_to_delayed_violates(scarce_dataset):
    """Spec 5.3. Needs a purpose-built fixture: in the baseline no single legal
    action can leave a high-priority order waiting."""
    twin = TwinManager(InMemoryDataStore(scarce_dataset))
    engine = SimulationEngine(twin)
    snapshot = twin.create_snapshot().snapshot_id
    report = engine.simulate_action(
        snapshot,
        ProposedAction(
            action_id="act-starve",
            action_type=ActionType.CANCEL_PURCHASE_ORDER,
            parameters={"po_id": "POS"},
            proposed_by=Role.MANAGER,
        ),
    )
    assert any(
        "high priority order SOH was pushed to delayed" in v
        for v in report.violated_rules
    )
    assert report.kpis["orders_delayed"] == 1.0


def test_the_same_order_is_served_when_its_purchase_order_is_kept(scarce_dataset):
    """Control for the test above: only the supply removal causes the delay."""
    twin = TwinManager(InMemoryDataStore(scarce_dataset))
    engine = SimulationEngine(twin)
    snapshot = twin.create_snapshot().snapshot_id
    report = engine.simulate_action(snapshot, _scare_transfer(50, "act-harmless"))
    assert report.violated_rules == []
    assert report.kpis["orders_fulfilled"] == 1.0
    assert report.kpis["stockout_count"] == 0.0


def test_priority_downgrade_is_clean_but_reported_as_a_side_effect(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(snapshot_id, reprioritise(action_id="act-prio"))
    assert report.violated_rules == []
    assert any("downgraded high -> low" in s for s in report.side_effects)


def test_priority_promotion_is_clean_but_reported_as_a_side_effect(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(
        snapshot_id,
        reprioritise(order_id="SO-1003", new_priority="high", action_id="act-promote"),
    )
    assert report.violated_rules == []
    assert any("promoted low -> high" in s for s in report.side_effects)


def test_cancelling_a_pending_purchase_order_is_clean_when_nothing_depends_on_it():
    """Cancelling a PO is not itself the problem - depending on it is.

    The seed's only pending PO (PO-2001) is the sole supply for SO-1003, so it
    is always material. A pending PO for a product that no open order needs can
    be cancelled with no rule violation and no stockout.
    """
    dataset = demo_baseline()
    # Two days after the existing PO's arrival, so it lands inside the horizon
    # without colliding with the other arrival.
    dataset.purchase_orders.append(
        PurchaseOrder(
            po_id="PO-3001",
            product_id="P-013",
            supplier_id="SUP-001",
            quantity=50,
            expected_arrival=dataset.purchase_orders[0].expected_arrival
            + timedelta(days=2),
            status="pending",
            destination_warehouse_id="WH-01",
        )
    )
    twin = TwinManager(InMemoryDataStore(dataset))
    engine = SimulationEngine(twin)
    report = engine.simulate_action(
        twin.create_snapshot().snapshot_id, cancel(po_id="PO-3001")
    )
    assert report.violated_rules == []
    assert report.kpis["stockout_count"] == 0.0
    assert report.kpis["orders_delayed"] == 0.0
    assert any("PO-3001 cancelled" in s for s in report.side_effects)


def test_cancelling_the_purchase_order_that_feeds_an_order_causes_a_stockout(
    engine: SimulationEngine, snapshot_id: str
):
    """Material risk with no rule violation - the human-review case."""
    report = engine.simulate_action(snapshot_id, cancel(po_id="PO-2001"))
    assert report.violated_rules == []
    assert report.kpis["stockout_count"] == 1.0
    assert report.kpis["orders_delayed"] == 1.0
    assert any("low priority order SO-1003" in s for s in report.side_effects)


def test_cancelling_an_unknown_purchase_order_violates(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(snapshot_id, cancel(po_id="PO-NOPE"))
    assert any("PO-NOPE does not exist" in v for v in report.violated_rules)


def test_cancelling_an_already_received_purchase_order_violates(
    engine: SimulationEngine, snapshot_id: str
):
    """PO-2002 is seeded as received, so it is not a cancellable target."""
    report = engine.simulate_action(snapshot_id, cancel(po_id="PO-2002"))
    assert any(
        "PO-2002 cannot be cancelled, status is 'received'" in v
        for v in report.violated_rules
    )


# ---------------------------------------------------------------------------
# Reservation and fulfilment mechanics
# ---------------------------------------------------------------------------


def test_orders_are_fulfilled_against_incoming_stock(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(snapshot_id, transfer(50))
    assert report.kpis["orders_fulfilled"] == 6.0
    assert report.kpis["orders_outstanding"] == 0.0


def test_receiving_a_purchase_order_is_traced(
    engine: SimulationEngine, snapshot_id: str
):
    report = engine.simulate_action(snapshot_id, transfer(50))
    assert any("PO-2001" in e.description for e in report.trace)
    assert any("300 units of P-007 received" in s for s in report.side_effects)


def test_a_no_op_action_still_shows_no_violation(
    engine: SimulationEngine, snapshot_id: str
):
    """The baseline is the control: nothing wrong before anything is proposed."""
    report = engine.simulate_action(
        snapshot_id, transfer(1, product_id="P-001", action_id="act-noop")
    )
    assert report.violated_rules == []
    assert report.kpis["stockout_count"] == 0.0
    assert report.kpis["orders_delayed"] == 0.0
    assert report.kpis["max_warehouse_capacity_pct"] < 100.0
