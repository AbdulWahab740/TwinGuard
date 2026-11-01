import time

import pytest

from twinguard_core.agent.provider import LLMProvider


def post(client, auth, role, text):
    r = client.post("/api/requests", json={"text": text}, headers=auth(role))
    assert r.status_code == 200, r.text
    return r.json()["request_id"]


def get(client, auth, rid, role="manager"):
    return client.get(f"/api/requests/{rid}", headers=auth(role)).json()


def settle(client, auth, rid):
    for _ in range(50):
        d = get(client, auth, rid)
        if d["status"] != "running":
            return d
        time.sleep(0.1)
    raise AssertionError("still running")


def test_manager_cancel_goes_to_needs_review(client, auth, db):
    d = settle(client, auth, post(client, auth, "manager", "cancel PO-2001"))
    assert d["status"] == "needs_review" and d["decision"] == "human_review"
    assert d["report"]["decision"] in ("human_review", "abstain")
    assert [s["tool"] for s in d["steps"]][:2] == ["get_current_state", "propose_action"]
    assert d["proposed_action"]["action_type"] == "cancel_purchase_order"
    assert db.select("review_tasks", request_id=d["request_id"])[0]["status"] == "pending"
    assert d["report"] is not None and db.select("store_meta")[0]["version"] == 1


def test_snapshot_version_recorded(client, auth, db):
    d = settle(client, auth, post(client, auth, "manager", "cancel PO-2001"))
    assert db.select("requests", id=d["request_id"])[0]["snapshot_version"] == 1


def test_block_has_no_review_task(client, auth, db):
    d = settle(client, auth, post(client, auth, "planner", "cancel PO-2001"))
    assert d["status"] == "decided" and d["decision"] == "block"
    assert db.select("review_tasks") == []


def test_approve_decision_is_decided_and_not_applied(client, auth, db):
    d = settle(client, auth, post(client, auth, "planner", "transfer stock"))
    assert d["status"] == "decided" and d["decision"] == "approve"
    assert db.select("store_meta")[0]["version"] == 1


def test_clarification(client, auth, db):
    d = settle(client, auth, post(client, auth, "planner", "hello"))
    assert d["status"] == "needs_clarification" and d["clarification"]
    assert db.select("review_tasks") == []


def test_viewer_forbidden(client, auth, db):
    r = client.post("/api/requests", json={"text": "transfer stock"}, headers=auth("viewer"))
    assert r.status_code == 403 and db.select("requests") == []


@pytest.mark.parametrize("text", ["", "x" * 2001])
def test_bad_text_422(client, auth, text):
    assert client.post("/api/requests", json={"text": text}, headers=auth("planner")).status_code == 422


class Boom(LLMProvider):
    def run_with_tools(self, *a, **k):
        raise RuntimeError("secret-test-secret boom")


class Slow(LLMProvider):
    def run_with_tools(self, *a, **k):
        time.sleep(1)


def test_agent_exception_marks_failed_without_leaking(client, auth):
    client.app.state.provider_factory = lambda s, t: Boom()
    d = settle(client, auth, post(client, auth, "planner", "transfer stock"))
    assert d["status"] == "failed" and "boom" in d["error"]
    assert "Traceback" not in d["error"] and "test-secret" not in d["error"]


def test_timeout_marks_failed(client, auth):
    client.app.state.settings.agent_timeout_s = 0.1
    client.app.state.provider_factory = lambda s, t: Slow()
    d = settle(client, auth, post(client, auth, "planner", "transfer stock"))
    assert d["status"] == "failed" and "timed out" in d["error"]


def test_audit_events_in_order(client, auth, db):
    d = settle(client, auth, post(client, auth, "manager", "cancel PO-2001"))
    kinds = [e["kind"] for e in sorted(db.select("audit_events"), key=lambda e: e["at"])]
    assert kinds == ["request_received", "action_proposed", "decision", "review_created"]


def test_list_filters(client, auth):
    a = post(client, auth, "planner", "hello")
    b = post(client, auth, "planner", "cancel PO-2001")
    h = auth("viewer")
    assert {r["request_id"] for r in client.get("/api/requests", headers=h).json()} == {a, b}
    r = client.get("/api/requests?status=decided&outcome=block", headers=h).json()
    assert [x["request_id"] for x in r] == [b]
