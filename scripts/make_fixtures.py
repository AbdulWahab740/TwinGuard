"""Regenerate contracts/examples/*.json and the `expected` block of each scenario.

Runs the real backend in-process (fake LLM, in-memory DB, no network). Examples are real
responses with only the request id and timestamp pinned. Scenario inputs are never rewritten.
"""
import json
import time

import _setup  # noqa: F401  (must come before the app imports)
from fastapi.testclient import TestClient

from _setup import CONTRACTS, ROOT
from app import scenarios
from app.config import Settings
from app.db import MemoryDb
from app.main import create_app
from app.seed import seed_dataset_into, seed_users
from twinguard_core.agent.provider import LLMProvider

PW = "fixture-password"


class Unreachable(LLMProvider):
    def run_with_tools(self, *a, **k):
        raise RuntimeError("LLM provider unreachable")


def client_for(dataset: str) -> TestClient:
    db = MemoryDb()
    seed_dataset_into(db, dataset)
    seed_users(db, PW)
    s = Settings(_env_file=None, env="dev", jwt_secret="x" * 32, agent_timeout_s=30)
    return TestClient(create_app(s, db))


def headers(c: TestClient, role: str) -> dict:
    t = c.post("/api/auth/login", json={"username": role, "password": PW}).json()["access_token"]
    return {"Authorization": f"Bearer {t}"}


def ask(c: TestClient, role: str, text: str) -> dict:
    h = headers(c, role)
    rid = c.post("/api/requests", json={"text": text}, headers=h).json()["request_id"]
    for _ in range(100):
        d = c.get(f"/api/requests/{rid}", headers=h).json()
        if d["status"] != "running":
            break
        time.sleep(0.1)
    d.update(request_id="req-example", created_at="2026-01-01T00:00:00Z")
    return d


def build_examples() -> dict[str, dict]:
    base, stale, failed = client_for("demo_baseline"), client_for("demo_stale"), client_for("demo_baseline")
    failed.app.state.provider_factory = lambda s, t: Unreachable()
    return {
        "approve": ask(base, "planner", "transfer stock"),
        "block": ask(base, "planner", "cancel PO-2001"),
        "human_review": ask(base, "manager", "cancel PO-2001"),
        "stale_data": ask(stale, "manager", "order pumps"),
        "hidden_po": base.post("/api/scenarios/hidden-po-order/run", headers=headers(base, "viewer")).json(),
        "failed": ask(failed, "planner", "transfer stock"),
    }


def evaluation_example() -> dict:
    src = sorted(ROOT.glob("twinguard-core/eval_results_seed*_1100.json"))[0]
    full = json.loads(src.read_text(encoding="utf-8"))
    keep = ("scenario_count", "condition_descriptions", "overall")  # trimmed: the real file is 2 MB
    return {"files": {src.name: {k: full[k] for k in keep}}}


def write(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    for name, body in build_examples().items():
        write(CONTRACTS / "examples" / f"{name}.json", body)
    write(CONTRACTS / "evaluation_summary.example.json", evaluation_example())
    for p in sorted((CONTRACTS / "scenarios").glob("*.json")):
        scn = json.loads(p.read_text(encoding="utf-8"))
        scn["expected"] = {"conditions": scenarios.run(scn)["conditions"]}
        write(p, scn)
    print("fixtures written")
