import json
import re
import shutil
from pathlib import Path

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from tests.test_requests import post, settle

ROOT = Path(__file__).parent.parent
SCN = ROOT.parent / "contracts" / "scenarios"


def scn_client(settings, db, **over):
    s = settings.model_copy(update={"scenarios_dir": str(SCN), **over})
    return TestClient(create_app(s, db))


def login(c, role="viewer"):
    r = c.post("/api/auth/login", json={"username": role, "password": f"pw-{role}"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_state_summary_fields(client, auth):
    d = client.get("/api/state/summary", headers=auth("viewer")).json()
    assert set(d["stock_by_warehouse"]) == {"WH-01", "WH-02"}
    assert all(0 <= v for v in d["capacity_use"].values())
    assert d["freshness"] in ("fresh", "stale", "unknown")
    assert isinstance(d["findings"], list)


def test_state_summary_stale_has_findings(settings):
    from app.db import MemoryDb
    from app.seed import seed_dataset_into
    from app.auth import hash_password
    db = MemoryDb(); seed_dataset_into(db, "demo_stale")
    db.insert("users", [{"username": "viewer", "password_hash": hash_password("pw-viewer"), "role": "viewer"}])
    with TestClient(create_app(settings, db)) as c:
        d = c.get("/api/state/summary", headers=login(c)).json()
    assert d["findings"] and d["freshness"] == "stale"


def test_scenario_list_and_run(settings, db):
    with scn_client(settings, db) as c:
        h = login(c)
        ids = {s["id"] for s in c.get("/api/scenarios", headers=h).json()}
        assert {"safe-transfer", "hidden-po-order", "stale-transfer"} <= ids
        d = c.post("/api/scenarios/hidden-po-order/run", headers=h).json()
    assert set(d["conditions"]) == {"A", "B-strong", "B2", "C"}
    assert d["conditions"]["A"] == "approve"
    assert d["truth"]["label"] == "unsafe"
    assert d["report"]["decision"] == d["conditions"]["C"]


def test_scenario_c_equals_core_condition_c(settings, db):
    from twinguard_core import ProposedAction, SeedDataset
    from twinguard_core.eval.conditions import condition_c
    f = json.loads((SCN / "hidden-po-order.json").read_text())
    want, _ = condition_c(SeedDataset.model_validate(f["observed"]),
                          ProposedAction.model_validate(f["action"]), f["seed"])
    with scn_client(settings, db) as c:
        d = c.post("/api/scenarios/hidden-po-order/run", headers=login(c)).json()
    assert d["conditions"]["C"] == want.value


def test_non_synthetic_scenario_hides_truth(settings, db, tmp_path):
    f = json.loads((SCN / "safe-transfer.json").read_text())
    f["synthetic"] = False
    (tmp_path / "x.json").write_text(json.dumps(f))
    with scn_client(settings, db, scenarios_dir=str(tmp_path)) as c:
        d = c.post("/api/scenarios/safe-transfer/run", headers=login(c)).json()
    assert d["truth"] is None


def test_unknown_scenario_404(settings, db):
    with scn_client(settings, db) as c:
        assert c.post("/api/scenarios/nope/run", headers=login(c)).status_code == 404


def test_scenario_requires_login(settings, db):
    with scn_client(settings, db) as c:
        assert c.get("/api/scenarios").status_code == 401


def test_truth_never_on_normal_endpoints(client, auth):
    rid = settle(client, auth, post(client, auth, "manager", "cancel PO-2001"))["request_id"]
    h = auth("manager")
    for path in (f"/api/requests/{rid}", "/api/requests", "/api/reviews", "/api/audit",
                 "/api/state/summary"):
        text = client.get(path, headers=h).text
        assert "truth" not in text.lower() and "S_true" not in text, path


def test_decision_code_never_references_truth():
    app = ROOT / "app"
    for name in ("agent_runner.py", "review.py", "db_store.py"):
        src = (app / name).read_text()
        assert not re.search(r"truth|scenarios", src, re.I), name


def test_evaluation_summary_returns_files(settings, db, tmp_path):
    (tmp_path / "a.json").write_text('{"x": 1}')
    (tmp_path / "b.json").write_text('{"y": [2]}')
    (tmp_path / "notes.txt").write_text("ignore")
    s = settings.model_copy(update={"eval_results_dir": str(tmp_path)})
    with TestClient(create_app(s, db)) as c:
        d = c.get("/api/evaluation/summary", headers=login(c)).json()
    assert d == {"files": {"a.json": {"x": 1}, "b.json": {"y": [2]}}}


def test_scenario_runs_match_expected_in_pack(settings, db):
    with scn_client(settings, db) as c:
        h = login(c)
        for f in sorted(SCN.glob("*.json")):
            exp = json.loads(f.read_text(encoding="utf-8"))["expected"]["conditions"]
            got = c.post(f"/api/scenarios/{f.stem}/run", headers=h).json()["conditions"]
            assert got == exp, f.stem
