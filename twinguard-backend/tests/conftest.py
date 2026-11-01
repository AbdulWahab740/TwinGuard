import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import Settings
from app.db import MemoryDb
from app.main import create_app
from app.seed import seed_dataset_into

ROLES = ("planner", "manager", "viewer")


@pytest.fixture
def settings():
    return Settings(env="dev", jwt_secret="test-secret", agent_timeout_s=5)


@pytest.fixture
def db():
    d = MemoryDb()
    seed_dataset_into(d, "demo_baseline")
    d.insert("users", [{"username": r, "password_hash": hash_password(f"pw-{r}"), "role": r}
                       for r in ROLES])
    return d


@pytest.fixture
def client(settings, db):
    with TestClient(create_app(settings, db)) as c:
        yield c


@pytest.fixture
def auth(client):
    def _auth(role):
        r = client.post("/api/auth/login", json={"username": role, "password": f"pw-{role}"})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _auth
