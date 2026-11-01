from pathlib import Path

import pytest

from app.db import MemoryDb, StaleVersionError, SupabaseDb, TABLES
from app.db_store import DbDataStore
from app.seed import seed_dataset_into
from twinguard_core import ActionType, InMemoryDataStore, ProposedAction, Role, get_dataset

NAME = "demo_baseline"


def act(kind, **params):
    return ProposedAction(
        action_id="a1", action_type=kind, parameters=params, proposed_by=Role.MANAGER
    )


@pytest.fixture
def pair():
    db, ds = MemoryDb(), get_dataset(NAME)
    seed_dataset_into(db, NAME, ds)
    return DbDataStore(db), InMemoryDataStore(ds)


def actions(ds):
    inv = ds.inventory
    a = inv[0]
    other = next(w for w in ds.warehouses if w.warehouse_id != a.warehouse_id)
    # make sure a destination row exists
    dest = next(
        (r for r in inv if r.product_id == a.product_id and r.warehouse_id != a.warehouse_id),
        None,
    )
    out = []
    if dest:
        out.append(act(ActionType.TRANSFER_INVENTORY, product_id=a.product_id,
                       source_warehouse_id=a.warehouse_id,
                       destination_warehouse_id=dest.warehouse_id, quantity=1))
    out.append(act(ActionType.CREATE_PURCHASE_ORDER, product_id=a.product_id,
                   supplier_id=ds.suppliers[0].supplier_id, quantity=5))
    out.append(act(ActionType.CHANGE_ORDER_PRIORITY,
                   order_id=ds.customer_orders[0].order_id, new_priority="high"))
    out.append(act(ActionType.CANCEL_PURCHASE_ORDER, po_id=ds.purchase_orders[0].po_id))
    return out


def same(a, b):
    assert a.get_inventory() == b.get_inventory()
    assert a.get_purchase_orders() == b.get_purchase_orders()
    assert a.get_customer_orders() == b.get_customer_orders()


def test_reads_match_in_memory(pair):
    s, m = pair
    assert s.get_products() == m.get_products()
    assert s.get_suppliers() == m.get_suppliers()
    assert s.get_warehouses() == m.get_warehouses()
    same(s, m)
    assert s.get_business_rules() == m.get_business_rules()


def test_seed_is_idempotent():
    db = MemoryDb()
    seed_dataset_into(db, NAME)
    seed_dataset_into(db, NAME)
    assert len(DbDataStore(db).get_products()) == len(get_dataset(NAME).products)


def test_each_action_matches_in_memory_and_bumps_version(pair):
    s, m = pair
    v = s.get_version()
    for i, a in enumerate(actions(get_dataset(NAME))):
        s.apply_approved_action(a)
        m.apply_approved_action(a)
        same(s, m)
        assert s.get_version() == v + i + 1


def test_version_guard(pair):
    s, _ = pair
    a = actions(get_dataset(NAME))[0]
    s.apply_approved_action(a)
    before = s.get_inventory()
    with pytest.raises(StaleVersionError):
        s.apply_approved_action(a, expected_version=1)
    assert s.get_inventory() == before


def test_update_is_conditional():
    db = MemoryDb()
    db.insert("review_tasks", [{"request_id": "r", "status": "pending"}])
    assert len(db.update("review_tasks", {"status": "approved"}, request_id="r", status="pending")) == 1
    assert db.update("review_tasks", {"status": "rejected"}, request_id="r", status="pending") == []


def test_schema_sql_has_every_table_and_rpc():
    sql = (Path(__file__).parent.parent / "sql" / "schema.sql").read_text().lower()
    for t in TABLES:
        assert f"create table if not exists {t}" in sql
    assert "function apply_approved_action" in sql


class FakeQ:
    def __init__(self, log, err=None):
        self.log, self.err = log, err

    def __getattr__(self, name):
        def call(*a, **k):
            self.log.append((name, a, k))
            return self
        return call

    def execute(self):
        if self.err:
            raise self.err
        return type("R", (), {"data": [{"ok": 1}]})()


class FakeClient:
    def __init__(self, err=None):
        self.log, self.err = [], err

    def table(self, t):
        self.log.append(("table", t)); return FakeQ(self.log)

    def rpc(self, fn, params):
        self.log.append(("rpc", fn, params)); return FakeQ(self.log, self.err)


def test_supabase_db_select_and_stale_mapping():
    c = FakeClient()
    assert SupabaseDb(c).select("products", product_id="p") == [{"ok": 1}]
    assert ("eq", ("product_id", "p"), {}) in c.log
    bad = SupabaseDb(FakeClient(err=Exception("stale_version")))
    with pytest.raises(StaleVersionError):
        bad.apply_action({}, 1)
