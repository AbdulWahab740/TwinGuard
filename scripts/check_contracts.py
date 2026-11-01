"""Check every contract file against the code and each other. Exit 1 on any problem.

1. generated files (openapi.yaml, decision.schema.json) match what the code produces now
2. example reports and LIVE backend responses validate against decision.schema.json
3. every reason/finding code seen is listed in reason_codes.json
4. each scenario's `expected` decisions still match a real run
5. evaluation_summary.example.json has the endpoint's shape
"""
import json
import sys

import _setup  # noqa: F401  (must come before the app imports)
from jsonschema import Draft202012Validator

import export_contracts
import make_fixtures
from _setup import CONTRACTS
from app import scenarios
from app.schemas import EvaluationSummary

problems: list[str] = []


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


for name, text in export_contracts.render().items():
    if (CONTRACTS / name).read_text(encoding="utf-8") != text:
        problems.append(f"{name} is out of date: run scripts/export_contracts.py")

schema = load(CONTRACTS / "decision.schema.json")
codes = load(CONTRACTS / "reason_codes.json")


def check_findings(where: str, findings: list[dict]) -> None:
    for f in findings:
        if f["code"] not in codes["finding_codes"]:
            problems.append(f"{where}: unknown finding code {f['code']}")


def check_report(where: str, report: dict, kind: str) -> None:
    v = Draft202012Validator({"$ref": f"#/$defs/{kind}", "$defs": schema["$defs"]})
    problems.extend(f"{where}: {e.message}" for e in v.iter_errors(report))
    if kind == "ScenarioReport":
        d = report["detail"]
        if d["reason_code"] not in codes["reason_codes"]:
            problems.append(f"{where}: unknown reason_code {d['reason_code']}")
        check_findings(where, d["findings"])
    else:
        check_findings(where, report["data_quality_issues"])


def check_response(where: str, body: dict) -> None:
    rep = body.get("report")
    if rep is not None:
        check_report(where, rep, "ScenarioReport" if "detail" in rep else "DecisionReport")


for p in sorted((CONTRACTS / "examples").glob("*.json")):
    check_response(f"examples/{p.name}", load(p))
for name, body in make_fixtures.build_examples().items():
    check_response(f"live:{name}", body)

for p in sorted((CONTRACTS / "scenarios").glob("*.json")):
    scn = load(p)
    got = scenarios.run(scn)["conditions"]
    if scn.get("expected", {}).get("conditions") != got:
        problems.append(f"scenarios/{p.name}: expected {scn.get('expected')} but got {got}")

try:
    EvaluationSummary.model_validate(load(CONTRACTS / "evaluation_summary.example.json"))
except Exception as e:  # noqa: BLE001 - report any shape problem
    problems.append(f"evaluation_summary.example.json: {e}")

print("\n".join(problems) or "contracts OK")
sys.exit(1 if problems else 0)
