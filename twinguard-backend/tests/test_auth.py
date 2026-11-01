import logging

from app.auth import require_role


def test_login_returns_token_and_role(client):
    r = client.post("/api/auth/login", json={"username": "manager", "password": "pw-manager"})
    assert r.status_code == 200
    assert r.json()["role"] == "manager" and r.json()["access_token"]


def test_bad_password_401(client):
    r = client.post("/api/auth/login", json={"username": "manager", "password": "nope"})
    assert r.status_code == 401
    assert client.post("/api/auth/login", json={"username": "ghost", "password": "x"}).status_code == 401


def test_no_token_401(client):
    client.app.add_api_route("/t/any", lambda: 1, dependencies=[require_role("planner")])
    assert client.get("/t/any").status_code == 401


def test_require_role_403(client, auth):
    client.app.add_api_route("/t/mgr", lambda: 1, dependencies=[require_role("manager")])
    assert client.get("/t/mgr", headers=auth("viewer")).status_code == 403
    assert client.get("/t/mgr", headers=auth("manager")).status_code == 200


def test_token_not_logged(client, auth, caplog):
    caplog.set_level(logging.DEBUG)
    h = auth("planner")
    client.app.add_api_route("/t/p", lambda: 1, dependencies=[require_role("planner")])
    client.get("/t/p", headers=h)
    assert h["Authorization"].split()[1] not in caplog.text


def test_password_stored_hashed(db):
    for u in db.select("users"):
        assert u["password_hash"] != "pw-" + u["username"]
        assert u["password_hash"].startswith("$2")
