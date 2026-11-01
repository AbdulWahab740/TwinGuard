import json
import typing
from pathlib import Path

from app import schemas
from twinguard_core import DecisionReport

EX = Path(__file__).parent.parent / "docs" / "api" / "examples"
MODELS = {
    "login": schemas.LoginOut,
    "request_created": schemas.RequestCreated,
    "request_needs_review": schemas.RequestOut,
    "request_blocked": schemas.RequestOut,
    "review_queue": None,
    "scenario_run": schemas.ScenarioRunOut,
}


def test_examples_validate_against_schemas():
    assert {p.stem for p in EX.glob("*.json")} == set(MODELS)
    for name, model in MODELS.items():
        data = json.loads((EX / f"{name}.json").read_text())
        if name == "review_queue":
            [schemas.ReviewTaskOut.model_validate(x) for x in data]
        else:
            model.model_validate(data)
        rep = data.get("report") if isinstance(data, dict) else None
        if rep:
            DecisionReport.model_validate(rep)


def test_request_status_enum_is_spec_set():
    args = typing.get_args(schemas.RequestOut.model_fields["status"].annotation)
    assert set(args) == {
        "running", "decided", "needs_review", "approved", "rejected", "applied",
        "failed", "needs_clarification", "stale_decision",
    }
