import pytest

from app.config import Settings, check_secret
from tests.test_db_store import FakeClient
from app.db import SupabaseDb
from tests.test_requests import post, settle


def test_snapshot_ids_unique_across_requests(client, auth, db):
    for _ in range(2):
        settle(client, auth, post(client, auth, "manager", "cancel PO-2001"))
    ids = [s["snapshot_id"] for s in db.select("snapshots")]
    assert len(ids) >= 2 and len(ids) == len(set(ids))


def test_state_summary_twice_keeps_unique_snapshots(client, auth, db):
    for _ in range(2):
        assert client.get("/api/state/summary", headers=auth("viewer")).status_code == 200
    ids = [s["snapshot_id"] for s in db.select("snapshots")]
    assert len(ids) == len(set(ids)) == 2


def test_provider_build_failure_marks_failed(client, auth, monkeypatch):
    for k in ("LLM_API_KEY", "GROK_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    client.app.state.settings.llm_provider = "openai_compatible"
    d = settle(client, auth, post(client, auth, "planner", "transfer stock"))
    assert d["status"] == "failed" and d["error"]


def test_request_list_returns_newest_500(client, auth, db):
    db.insert("requests", [{"id": f"r{i:04d}", "text": "t", "created_by": "p",
                            "created_by_role": "planner", "status": "decided",
                            "created_at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00"}
                           for i in range(600)])
    rows = client.get("/api/requests", headers=auth("viewer")).json()
    assert len(rows) == 500 and rows[0]["request_id"] == "r0599" and rows[-1]["request_id"] == "r0100"


def test_audit_global_list_is_newest_500_ascending(client, auth, db):
    db.insert("audit_events", [{"id": f"e{i:04d}", "request_id": None, "kind": "k", "actor": "a",
                                "at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00", "payload": {}}
                               for i in range(600)])
    rows = client.get("/api/audit", headers=auth("viewer")).json()
    assert len(rows) == 500 and rows[0]["id"] == "e0100" and rows[-1]["id"] == "e0599"


def test_supabase_select_pushes_order_and_limit():
    c = FakeClient()
    SupabaseDb(c).select("requests", order_by="created_at", desc=True, limit=5)
    assert ("order", ("created_at",), {"desc": True}) in c.log
    assert ("limit", (5,), {}) in c.log


def test_secret_rules():
    with pytest.raises(RuntimeError):
        check_secret(Settings(_env_file=None, env="prod", jwt_secret="short"))
    with pytest.raises(RuntimeError):
        check_secret(Settings(_env_file=None, env="prod"))
    check_secret(Settings(_env_file=None, env="prod", jwt_secret="x" * 32))
    check_secret(Settings(_env_file=None, env="dev"))


def test_review_task_created_after_action_saved(client, auth, db, monkeypatch):
    seen, orig = [], db.insert

    def spy(table, rows):
        if table == "review_tasks":
            seen.append(db.select("requests")[0].get("action"))
        return orig(table, rows)

    monkeypatch.setattr(db, "insert", spy)
    settle(client, auth, post(client, auth, "manager", "cancel PO-2001"))
    assert seen and seen[0] is not None
