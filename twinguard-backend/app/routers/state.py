from collections import defaultdict

from fastapi import APIRouter, Depends

from twinguard_core import TwinManager

from ..auth import current_user
from ..db_store import DbDataStore
from ..deps import get_db
from ..schemas import StateSummary

router = APIRouter(prefix="/api/state", dependencies=[Depends(current_user)])


@router.get("/summary", response_model=StateSummary)
def summary(db=Depends(get_db)):
    store = DbDataStore(db)
    stock: dict[str, int] = defaultdict(int)
    for r in store.get_inventory():
        stock[r.warehouse_id] += r.quantity_on_hand
    cap = {w.warehouse_id: w.capacity for w in store.get_warehouses()}
    twin = TwinManager(store)
    snap = twin.create_snapshot()
    return StateSummary(
        stock_by_warehouse=dict(stock),
        capacity_use={w: stock[w] / c if c else 0.0 for w, c in cap.items()},
        freshness=snap.freshness_status,
        findings=[i.model_dump(mode="json") for i in twin.run_data_quality_checks(snap.snapshot_id)],
    )
