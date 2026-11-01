import re
import threading
from pathlib import Path

import pytest

from app.auth import User
from app.db_store import DbDataStore
from app.review import Conflict, decide_review
from tests.test_requests import get, post, settle
from twinguard_core import ActionType, ProposedAction, Role

APP = Path(__file__).parent.parent / "app"


def kinds(db, rid):
    ev = sorted(db.select("audit_events", request_id=rid), key=lambda e: e["at"])
    return [e["kind"] for e in ev]


def review(client, auth, rid, verb, role="manager", comment="ok"):
    return client.post(f"/api/reviews/{rid}/{verb}", json={"comment": comment}, headers=auth(role))


def pending(client, auth):
    return settle(client, auth, post(client, auth, "manager", "cancel PO-2001"))["request_id"]


def po_status(db):
    return next(p["status"] for p in db.select("purchase_orders") if p["po_id"] == "PO-2001")


def test_full_flow_approve_applies(client, auth, db):
    rid = pending(client, auth)
    assert client.get("/api/reviews", headers=auth("viewer")).json()[0]["request_id"] == rid
    r = review(client, auth, rid, "approve")
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert po_status(db) == "cancelled" and db.select("store_meta")[0]["version"] == 2
    assert kinds(db, rid) == ["request_received", "action_proposed", "decision",
                              "review_created", "review_approved", "applied"]
    audit = client.get(f"/api/audit?request_id={rid}", headers=auth("viewer")).json()
    assert [e["kind"] for e in audit] == kinds(db, rid)


def test_reject_leaves_data_unchanged(client, auth, db):
    rid = pending(client, auth)
    assert review(client, auth, rid, "reject").json()["status"] == "rejected"
    assert po_status(db) == "pending" and db.select("store_meta")[0]["version"] == 1


def test_stale_version_blocks_apply(client, auth, db):
    rid = pending(client, auth)
    other = ProposedAction(action_id="x", action_type=ActionType.CHANGE_ORDER_PRIORITY,
                           parameters={"order_id": db.select("customer_orders")[0]["order_id"],
                                       "new_priority": "high"}, proposed_by=Role.MANAGER)
    DbDataStore(db).apply_approved_action(other)
    r = review(client, auth, rid, "approve")
    assert r.json()["status"] == "stale_decision"
    assert po_status(db) == "pending" and "stale_decision" in kinds(db, rid)


def test_double_approve_applies_once(client, auth, db):
    rid = pending(client, auth)
    assert review(client, auth, rid, "approve").status_code == 200
    assert review(client, auth, rid, "approve").status_code == 409
    assert db.select("store_meta")[0]["version"] == 2


def test_concurrent_approves_apply_once(client, auth, db):
    rid = pending(client, auth)
    out = []
    def go():
        try:
            decide_review(db, rid, True, "ok", User(username="manager", role="manager"))
            out.append("ok")
        except Conflict:
            out.append("conflict")
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(out) == ["conflict"] * 5 + ["ok"]
    assert db.select("store_meta")[0]["version"] == 2


@pytest.mark.parametrize("first,second", [("approve", "reject"), ("reject", "approve")])
def test_second_decision_409(client, auth, db, first, second):
    rid = pending(client, auth)
    review(client, auth, rid, first)
    assert review(client, auth, rid, second).status_code == 409


def test_approve_on_block_409(client, auth, db):
    rid = settle(client, auth, post(client, auth, "planner", "cancel PO-2001"))["request_id"]
    assert review(client, auth, rid, "approve").status_code == 409
    assert po_status(db) == "pending"


def test_unknown_request_404(client, auth):
    assert review(client, auth, "req-nope", "approve").status_code == 404


def test_comment_required_and_manager_only(client, auth):
    rid = pending(client, auth)
    assert review(client, auth, rid, "approve", comment="").status_code == 422
    assert review(client, auth, rid, "approve", role="planner").status_code == 403
    assert review(client, auth, rid, "reject", role="viewer").status_code == 403


def test_approve_decision_needs_human_by_default(client, auth, db):
    rid = settle(client, auth, post(client, auth, "planner", "transfer stock"))["request_id"]
    assert "approved_by_gate" in kinds(db, rid) and "applied" not in kinds(db, rid)
    assert review(client, auth, rid, "approve").json()["status"] == "applied"


def test_auto_apply_when_configured(client, auth, db):
    client.app.state.settings.allow_auto_apply = True
    d = settle(client, auth, post(client, auth, "planner", "transfer stock"))
    assert d["status"] == "applied" and db.select("store_meta")[0]["version"] == 2
    assert "approved_by_gate" in kinds(db, d["request_id"])


def test_only_review_module_applies():
    hits = {p.name for p in APP.rglob("*.py") if re.search(r"\.apply_approved_action\(", p.read_text())}
    # db.py: MemoryDb's internals reuse the core in-memory store; reachable only via DbDataStore.
    assert hits == {"review.py", "db.py"}
    raw = {p.name for p in APP.rglob("*.py") if re.search(r"\.apply_action\(", p.read_text())}
    assert raw == {"db_store.py"}
    assert "apply_approved_action" not in (APP / "agent_runner.py").read_text()
