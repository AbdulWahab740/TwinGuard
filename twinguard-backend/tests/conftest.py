import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings():
    return Settings(env="dev", jwt_secret="test-secret")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c
