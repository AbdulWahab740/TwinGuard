"""TwinManager: snapshot immutability, diffing, data-quality detection.

Covers spec Section 4.3 acceptance criteria and the DataQualityIssue codes in
Section 4.2.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from twinguard_core import (
    InMemoryDataStore,
    SeedDataset,
    TwinManager,
    demo_baseline,
    demo_stale,
)
from twinguard_core.models import (
    SNAPSHOT_COLLECTIONS,
    CustomerOrder,
    InventoryRecord,
    PurchaseOrder,
)


# ---------------------------------------------------------------------------
# 4.2 create_snapshot
# ---------------------------------------------------------------------------


def test_create_snapshot_returns_meta_pointing_at_current_version(
    twin: TwinManager, store: InMemoryDataStore
):
    meta = twin.create_snapshot()
    assert meta.snapshot_id == "snap-0001"
    assert meta.source_version == store.get_version()
    assert meta.freshness_status == "fresh"


def test_create_snapshot_does_not_mutate_the_source_store(
    twin: TwinManager, store: InMemoryDataStore
):
    before = store.get_state()
    version_before = store.get_version()
    twin.create_snapshot()
    twin.create_snapshot()
    assert store.get_state() == before
    assert store.get_version() == version_before


def test_two_snapshots_of_an_unchanged_store_have_identical_entities(
    twin: TwinManager,
):
    """Spec 4.3: only snapshot_id / created_at may differ."""
    a = twin.create_snapshot().snapshot_id
    b = twin.create_snapshot().snapshot_id
    assert a != b
    state_a = twin.get_snapshot_state(a)
    state_b = twin.get_snapshot_state(b)
    assert state_a == state_b

    meta_a, meta_b = twin.get_snapshot_meta(a), twin.get_snapshot_meta(b)
    assert meta_a.created_at <= meta_b.created_at
    assert meta_a.source_version == meta_b.source_version


def test_create_snapshot_goes_through_the_data_store_interface(
    twin: TwinManager, store: InMemoryDataStore
):
    """The backend fills snapshot_* tables off this hook (spec 3.1)."""
    meta = twin.create_snapshot()
    assert meta.snapshot_id in store.persisted_snapshots


def test_freshness_status_is_stale_when_the_sync_is_old():
    twin = TwinManager(InMemoryDataStore(demo_stale()))
    assert twin.create_snapshot().freshness_status == "stale"


def test_freshness_status_is_unknown_without_inventory(baseline_dataset: SeedDataset):
    baseline_dataset.inventory = []
    twin = TwinManager(InMemoryDataStore(baseline_dataset))
    assert twin.create_snapshot().freshness_status == "unknown"


def test_snapshot_state_contains_exactly_the_six_collections(
    twin: TwinManager, snapshot_id: str
):
    assert set(twin.get_snapshot_state(snapshot_id)) == set(SNAPSHOT_COLLECTIONS)


# ---------------------------------------------------------------------------
# 4.2 get_snapshot_state
# ---------------------------------------------------------------------------


def test_get_snapshot_state_returns_a_deep_copy(
    twin: TwinManager, snapshot_id: str
):
    """Spec 4.3: mutating the result must not affect the stored snapshot."""
    first = twin.get_snapshot_state(snapshot_id)
    first["products"].append(first["products"][0].model_copy())
    first["products"][0].name = "MUTATED"
    first["inventory"][0].quantity_on_hand = -999
    first["warehouses"].clear()

    second = twin.get_snapshot_state(snapshot_id)
    assert second["products"][0].name != "MUTATED"
    assert second["inventory"][0].quantity_on_hand != -999
    assert second["warehouses"] != []
    assert second == twin.get_snapshot_state(snapshot_id)


def test_unknown_snapshot_id_raises(twin: TwinManager):
    with pytest.raises(KeyError):
        twin.get_snapshot_state("snap-9999")
    with pytest.raises(KeyError):
        twin.get_snapshot_meta("snap-9999")
    with pytest.raises(KeyError):
        twin.reset_scenario("snap-9999")


# ---------------------------------------------------------------------------
# 4.2 reset_scenario
# ---------------------------------------------------------------------------


def test_reset_scenario_restores_the_create_time_state(
    twin: TwinManager, snapshot_id: str
):
    pristine = twin.get_snapshot_state(snapshot_id)
    # A scenario-layer mutation, the way an experiment would poke at the twin.
    twin.get_snapshot_state(snapshot_id)
    mutated = twin._state[snapshot_id]  # the scenario layer, not a copy
    mutated["inventory"][0].quantity_on_hand = 1
    mutated["customer_orders"][0].status = "fulfilled"
    assert twin.get_snapshot_state(snapshot_id) != pristine

    twin.reset_scenario(snapshot_id)
    assert twin.get_snapshot_state(snapshot_id) == pristine


# ---------------------------------------------------------------------------
# 4.2 diff_snapshots
# ---------------------------------------------------------------------------


def test_diff_of_identical_snapshots_is_empty(twin: TwinManager):
    a = twin.create_snapshot().snapshot_id
    b = twin.create_snapshot().snapshot_id
    diff = twin.diff_snapshots(a, b)
    assert set(diff) == set(SNAPSHOT_COLLECTIONS)
    assert all(entries == {} for entries in diff.values())


def test_diff_reports_changed_fields_keyed_by_entity_id(twin: TwinManager):
    a = twin.create_snapshot().snapshot_id
    twin._state[a]["products"][0].unit_price = 99.99
    b = twin.create_snapshot().snapshot_id
    # The second snapshot reads the store, so it is unchanged; the diff is
    # between the mutated scenario layer and the fresh copy.
    diff = twin.diff_snapshots(a, b)
    changed = diff["products"]["P-001"]["changed"]
    assert changed["unit_price"] == {"a": 99.99, "b": 0.40}


def test_diff_reports_added_and_removed_entities(twin: TwinManager):
    a = twin.create_snapshot().snapshot_id
    twin._state[a]["purchase_orders"].pop()
    b = twin.create_snapshot().snapshot_id
    diff = twin.diff_snapshots(a, b)
    assert diff["purchase_orders"]["PO-2002"]["added"]["po_id"] == "PO-2002"

    twin._state[b]["purchase_orders"].clear()
    diff2 = twin.diff_snapshots(a, b)
    assert "PO-2001" in diff2["purchase_orders"]
    assert "removed" in diff2["purchase_orders"]["PO-2001"]


def test_diff_keys_inventory_by_product_and_warehouse(
    twin: TwinManager, snapshot_id: str
):
    twin._state[snapshot_id]["inventory"][0].quantity_reserved = 7
    b = twin.create_snapshot().snapshot_id
    diff = twin.diff_snapshots(snapshot_id, b)
    key = "P-001|WH-01"
    assert diff["inventory"][key]["changed"]["quantity_reserved"] == {
        "a": 7,
        "b": 0,
    }


# ---------------------------------------------------------------------------
# 4.2 run_data_quality_checks
# ---------------------------------------------------------------------------


def test_clean_baseline_has_no_data_quality_issues():
    """The clean dataset is genuinely clean: no anchor override, because
    freshness is measured against the wall clock."""
    clean_twin = TwinManager(InMemoryDataStore(demo_baseline()))
    snapshot = clean_twin.create_snapshot().snapshot_id
    assert clean_twin.run_data_quality_checks(snapshot) == []


def test_missing_supplier_lead_time_is_always_reported():
    """Spec 4.3: a supplier with lead_time_days=None always appears."""
    twin = TwinManager(InMemoryDataStore(demo_stale()))
    issues = twin.run_data_quality_checks(twin.create_snapshot().snapshot_id)
    missing = [i for i in issues if i.code == "missing_field"]
    assert len(missing) == 1
    assert missing[0].entity_id == "SUP-003"
    assert "lead time" in missing[0].detail


def test_stale_timestamps_are_reported_against_the_freshness_limit():
    twin = TwinManager(InMemoryDataStore(demo_stale()))
    issues = twin.run_data_quality_checks(twin.create_snapshot().snapshot_id)
    stale = [i for i in issues if i.code == "stale_timestamp"]
    assert stale
    assert all(i.entity_id.count("|") == 1 for i in stale)


def test_fresh_inventory_is_not_reported_as_stale():
    twin = TwinManager(InMemoryDataStore(demo_baseline()))
    issues = twin.run_data_quality_checks(twin.create_snapshot().snapshot_id)
    assert [i for i in issues if i.code == "stale_timestamp"] == []


def test_duplicate_product_id_is_reported(twin: TwinManager, snapshot_id: str):
    twin._state[snapshot_id]["products"].append(
        twin._state[snapshot_id]["products"][0].model_copy()
    )
    issues = twin.run_data_quality_checks(snapshot_id)
    duplicates = [i for i in issues if i.code == "duplicate_record"]
    assert any("product_id P-001" in i.detail for i in duplicates)


def test_identical_duplicate_inventory_rows_are_reported(
    twin: TwinManager, snapshot_id: str
):
    row = twin._state[snapshot_id]["inventory"][0]
    twin._state[snapshot_id]["inventory"].append(row.model_copy())
    issues = twin.run_data_quality_checks(snapshot_id)
    assert any(
        i.code == "duplicate_record" and "identical inventory rows" in i.detail
        for i in issues
    )


def test_disagreeing_inventory_rows_are_reported_as_conflicting(
    twin: TwinManager, snapshot_id: str
):
    row = twin._state[snapshot_id]["inventory"][0]
    twin._state[snapshot_id]["inventory"].append(
        row.model_copy(update={"quantity_on_hand": row.quantity_on_hand + 5})
    )
    issues = twin.run_data_quality_checks(snapshot_id)
    assert any(i.code == "conflicting_quantity" for i in issues)


@pytest.mark.parametrize(
    "bad_field,bad_value,expected",
    [
        ("product_id", "NOPE", "unknown product"),
        ("supplier_id", "NOPE", "unknown supplier"),
        ("destination_warehouse_id", "WH-NOPE", "unknown warehouse"),
    ],
)
def test_purchase_order_with_a_dangling_reference_is_reported(
    twin: TwinManager, snapshot_id: str, bad_field: str, bad_value: str, expected: str
):
    fields: dict = {
        "po_id": "PO-BAD",
        "product_id": "P-001",
        "supplier_id": "SUP-001",
        "quantity": 1,
        "expected_arrival": date.today(),
        "status": "pending",
    }
    fields[bad_field] = bad_value
    twin._state[snapshot_id]["purchase_orders"].append(PurchaseOrder(**fields))
    issues = twin.run_data_quality_checks(snapshot_id)
    assert any(
        i.code == "invalid_reference" and expected in i.detail for i in issues
    ), f"expected {expected!r}, got {[i.model_dump() for i in issues]}"


def test_customer_order_with_an_unknown_product_is_reported(
    twin: TwinManager, snapshot_id: str
):
    twin._state[snapshot_id]["customer_orders"].append(
        CustomerOrder(
            order_id="SO-BAD",
            product_id="NOPE",
            quantity=1,
            priority="normal",
            due_date=date.today(),
            status="open",
        )
    )
    issues = twin.run_data_quality_checks(snapshot_id)
    assert any(
        i.code == "invalid_reference"
        and "unknown product" in i.detail
        and i.entity_id == "SO-BAD"
        for i in issues
    )


def test_inventory_pointing_at_an_unknown_warehouse_is_reported(
    twin: TwinManager, snapshot_id: str
):
    twin._state[snapshot_id]["inventory"].append(
        InventoryRecord(
            product_id="P-001",
            warehouse_id="WH-NOPE",
            quantity_on_hand=1,
            quantity_reserved=0,
            last_synced_at=datetime.now(timezone.utc),
        )
    )
    issues = twin.run_data_quality_checks(snapshot_id)
    assert any(
        i.code == "invalid_reference" and "unknown warehouse" in i.detail
        for i in issues
    )


def test_issues_are_sorted_and_stable():
    stale_twin = TwinManager(InMemoryDataStore(demo_stale()))
    snapshot = stale_twin.create_snapshot().snapshot_id
    first = stale_twin.run_data_quality_checks(snapshot)
    second = stale_twin.run_data_quality_checks(snapshot)
    assert first == second
    assert first == sorted(first, key=lambda i: (i.code, i.entity_id, i.detail))


def test_data_quality_check_never_mutates_the_snapshot(
    twin: TwinManager, snapshot_id: str
):
    before = twin.get_snapshot_state(snapshot_id)
    twin.run_data_quality_checks(snapshot_id)
    assert twin.get_snapshot_state(snapshot_id) == before
