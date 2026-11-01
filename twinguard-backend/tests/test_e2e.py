import socket
from pathlib import Path

import yaml
from app.auth import verify_password
from app.config import Settings
from app.db import MemoryDb
from app.main import create_app
from app.seed import seed_users
from tests.test_requests import post, settle

ROOT = Path(__file__).parent.parent


def test_openapi_file_is_current():
    live = create_app(Settings(_env_file=None, env="dev"), MemoryDb()).openapi()
    assert yaml.safe_load((ROOT.parent / "contracts" / "openapi.yaml").read_text(encoding="utf-8")) == live


def test_seed_users_creates_three_hashed_idempotent():
    db = MemoryDb()
    seed_users(db, "pw")
    seed_users(db, "pw")
    users = db.select("users")
    assert sorted(u["username"] for u in users) == ["manager", "planner", "viewer"]
    assert all(verify_password("pw", u["password_hash"]) for u in users)


def test_e2e_demo_offline(client, auth, db, monkeypatch):
    def no_net(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    rid = post(client, auth, "manager", "cancel PO-2001")
    d = settle(client, auth, rid)
    assert d["status"] == "needs_review"
    r = client.post(f"/api/reviews/{rid}/approve", json={"comment": "ok"}, headers=auth("manager"))
    assert r.json()["status"] == "applied"
    trail = client.get(f"/api/audit?request_id={rid}", headers=auth("viewer")).json()
    assert len(trail) == 6
