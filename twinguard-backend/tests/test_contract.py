import json
import typing
from pathlib import Path

from app import schemas

CONTRACTS = Path(__file__).parent.parent.parent / "contracts"
EX = CONTRACTS / "examples"
NAMES = {"approve", "block", "human_review", "stale_data", "hidden_po", "failed"}


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_examples_validate_against_api_models():
    assert {p.stem for p in EX.glob("*.json")} == NAMES
    for n in NAMES:
        (schemas.ScenarioRunOut if n == "hidden_po" else schemas.RequestOut).model_validate(load(EX / f"{n}.json"))


def test_example_outcomes_are_what_their_names_say():
    assert load(EX / "approve.json")["decision"] == "approve"
    assert load(EX / "block.json")["decision"] == "block"
    assert load(EX / "human_review.json")["decision"] == "human_review"
    stale = load(EX / "stale_data.json")
    assert any(i["code"] == "stale_timestamp" for i in stale["report"]["data_quality_issues"])
    failed = load(EX / "failed.json")
    assert failed["status"] == "failed" and failed["error"] and failed["report"] is None
    assert load(EX / "hidden_po.json")["truth"]["corruptions"] == ["missing_po"]


def test_evaluation_example_has_endpoint_shape():
    schemas.EvaluationSummary.model_validate(load(CONTRACTS / "evaluation_summary.example.json"))


def test_request_status_enum_is_spec_set():
    args = typing.get_args(schemas.RequestOut.model_fields["status"].annotation)
    assert set(args) == {
        "running", "decided", "needs_review", "approved", "rejected", "applied",
        "failed", "needs_clarification", "stale_decision",
    }
