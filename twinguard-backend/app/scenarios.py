"""Scenario Lab. The only module that reads truth data; it never feeds decision code."""
import json
from pathlib import Path

from twinguard_core import Decision, ProposedAction, SeedDataset
from twinguard_core.eval import conditions as cond


def _val(d: Decision) -> str:
    return "human_review" if d is Decision.ABSTAIN else d.value


def load_all(directory: str) -> dict[str, dict]:
    out = {}
    for p in sorted(Path(directory).glob("*.json")):
        s = json.loads(p.read_text())
        out[s["id"]] = s
    return out


def run(scn: dict) -> dict:
    obs = SeedDataset.model_validate(scn["observed"])
    action = ProposedAction.model_validate(scn["action"])
    seed = scn["seed"]
    c_decision, _, detail = cond.condition_c_detail(obs, action, seed)
    conditions = {
        "A": _val(cond.condition_a(obs, action)[0]),
        "B-strong": _val(cond.condition_b_strong(obs, action)[0]),
        "B2": _val(cond.condition_b2(obs, action, seed)[0]),
        "C": _val(c_decision),
    }
    return {
        "report": {"decision": conditions["C"], "detail": json.loads(json.dumps(detail, default=str))},
        "conditions": conditions,
        "truth": scn["truth"] if scn.get("synthetic") else None,
    }
