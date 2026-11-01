"""The shared contracts (repo-root contracts/) must match the core models."""
import json
import typing
from pathlib import Path

from twinguard_core import DecisionReport
from twinguard_core.models import DataQualityCode

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def test_example_reports_parse_as_core_decision_reports():
    for name in ("approve", "block", "human_review", "stale_data"):
        body = json.loads((CONTRACTS / "examples" / f"{name}.json").read_text(encoding="utf-8"))
        DecisionReport.model_validate(body["report"])


def test_finding_codes_match_core_exactly():
    codes = json.loads((CONTRACTS / "reason_codes.json").read_text(encoding="utf-8"))
    assert set(codes["finding_codes"]) == set(typing.get_args(DataQualityCode))
