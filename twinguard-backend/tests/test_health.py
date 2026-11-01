def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_docs_served(client):
    assert client.get("/docs").status_code == 200
