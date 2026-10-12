"""Roles, parameter schemas, and the decision classifier.

**The classifier is plain deterministic code. The LLM cannot change its result.**
The LLM's only contribution is the ``ProposedAction`` it proposes; everything in
:func:`classify` is a function of data quality, role, and the simulation report.

Classifier precedence (spec Section 6.3, implemented verbatim and in this order):

    data quality  ->  ABSTAIN
    permissions   ->  BLOCK
    rule violations -> BLOCK
    material risk ->  HUMAN_REVIEW
    otherwise     ->  APPROVE
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import (
    ActionType,
    BusinessRules,
    DataQualityIssue,
    Decision,
    DecisionReport,
    ProposedAction,
    Role,
    SimulationReport,
)

# ---------------------------------------------------------------------------
# 6.2 Action parameter schemas
# ---------------------------------------------------------------------------


class TransferInventoryParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    source_warehouse_id: str
    destination_warehouse_id: str
    quantity: int = Field(gt=0)


class CreatePurchaseOrderParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    supplier_id: str
    quantity: int = Field(gt=0)
    # Not in spec Section 6.2. Optional, because the simulator needs to know
    # where incoming stock lands (see models.PurchaseOrder docstring). When
    # omitted the simulator routes it to the first warehouse in the snapshot.
    destination_warehouse_id: Optional[str] = None


class ChangeOrderPriorityParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    new_priority: Literal["low", "normal", "high"]


class CancelPurchaseOrderParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    po_id: str


PARAM_SCHEMAS: dict[ActionType, type[BaseModel]] = {
    ActionType.TRANSFER_INVENTORY: TransferInventoryParams,
    ActionType.CREATE_PURCHASE_ORDER: CreatePurchaseOrderParams,
    ActionType.CHANGE_ORDER_PRIORITY: ChangeOrderPriorityParams,
    ActionType.CANCEL_PURCHASE_ORDER: CancelPurchaseOrderParams,
}


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    parsed: Optional[BaseModel] = None

    def __bool__(self) -> bool:  # lets `if result:` read naturally
        return self.valid


def validate_parameters(action_type: ActionType, parameters: dict) -> ValidationResult:
    """Validate a raw parameter dict against its per-action-type schema.

    Returns every error rather than the first, and separately lists the fields
    that were absent, so the agent can build a useful clarification question.
    """
    schema = PARAM_SCHEMAS[action_type]
    errors: list[str] = []
    missing: list[str] = []
    try:
        parsed = schema(**parameters)
    except ValidationError as exc:
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "<root>"
            if error["type"] == "missing":
                missing.append(location)
            errors.append(f"{location}: {error['msg']}")
        return ValidationResult(valid=False, errors=errors, missing_fields=missing)
    except TypeError as exc:  # parameters not a mapping
        return ValidationResult(valid=False, errors=[str(exc)], missing_fields=[])

    required = {
        name
        for name, field in schema.model_fields.items()
        if field.is_required()
    }
    missing = sorted(required - set(parameters))
    if missing:
        return ValidationResult(
            valid=False,
            errors=[f"missing required parameter(s): {', '.join(missing)}"],
            missing_fields=missing,
        )
    return ValidationResult(valid=True, errors=[], missing_fields=[], parsed=parsed)


def validate_action(action: ProposedAction) -> bool:
    """Boolean form used by the ``validate_action`` tool (spec Section 7.2)."""
    return validate_parameters(action.action_type, action.parameters).valid


# ---------------------------------------------------------------------------
# 6.1 Role permissions
# ---------------------------------------------------------------------------

#: Which role may propose each action type.
#:
#: Design: a viewer never acts. A planner and the AI agent handle reversible
#: supply decisions. Cancelling a purchase order is the one action that
#: irreversibly gives up supply, so it is reserved for a manager. Managers can do
#: everything. Every ``ActionType`` therefore has at least one permitting and one
#: denying role, which spec Section 6.4 requires.
PERMISSION_MATRIX: dict[ActionType, set[Role]] = {
    ActionType.TRANSFER_INVENTORY: {
        Role.PLANNER,
        Role.MANAGER,
        Role.AI_AGENT,
    },
    ActionType.CREATE_PURCHASE_ORDER: {
        Role.PLANNER,
        Role.MANAGER,
        Role.AI_AGENT,
    },
    ActionType.CHANGE_ORDER_PRIORITY: {
        Role.PLANNER,
        Role.MANAGER,
    },
    ActionType.CANCEL_PURCHASE_ORDER: {
        Role.MANAGER,
    },
}


def is_permitted(role: Role, action_type: ActionType) -> bool:
    return role in PERMISSION_MATRIX[action_type]


# ---------------------------------------------------------------------------
# 6.3 Decision classifier
# ---------------------------------------------------------------------------

#: Concrete, documented material-risk thresholds (spec Section 6.3 asks for a
#: documented threshold and for it to be written into the README).
#:
#: A simulation is material — i.e. worth a human's time but not a hard rule
#: failure — when either holds:
#:
#: 1. ``kpis["stockout_count"] > 0``: at least one customer order went unserved
#:    for want of stock. Nothing about a stockout is a *policy* violation, so it
#:    is routed to a human rather than auto-blocked.
#: 2. ``kpis["action_value"] > MATERIAL_VALUE_FRACTION * max_purchase_budget``:
#:    the action moves or commits more than half the purchasing budget in one
#:    step. The simulation engine does not have the budget, so this threshold is
#:    evaluated here against the rules passed in.
MATERIAL_STOCKOUT_THRESHOLD = 0.0
MATERIAL_VALUE_FRACTION = 0.5


def _is_material_risk(
    report: SimulationReport, rules: BusinessRules | None = None
) -> bool:
    """True when the run shows risk that no rule violation covers."""
    kpis = report.kpis
    if kpis.get("stockout_count", 0.0) > MATERIAL_STOCKOUT_THRESHOLD:
        return True
    if rules is not None and rules.max_purchase_budget > 0:
        if kpis.get("action_value", 0.0) > MATERIAL_VALUE_FRACTION * rules.max_purchase_budget:
            return True
    return False


def classify(
    data_quality_issues: list[DataQualityIssue],
    permission_ok: bool,
    simulation_report: SimulationReport | None,
    business_rules: BusinessRules | None = None,
) -> tuple[Decision, list[str]]:
    """The decision function. Plain code; the LLM has no influence on it.

    ``business_rules`` is an optional addition to the Section 6.3 signature: it
    supplies the budget that ``_is_material_risk`` needs for its value
    threshold. Omitting it degrades the value check to the stockout check only;
    it cannot change the ABSTAIN / BLOCK branches.
    """
    if data_quality_issues:
        return Decision.ABSTAIN, [
            f"missing/stale evidence: {i.detail}" for i in data_quality_issues
        ]
    if not permission_ok:
        return Decision.BLOCK, ["role is not permitted to propose or approve this action"]
    if simulation_report and simulation_report.violated_rules:
        return Decision.BLOCK, list(simulation_report.violated_rules)
    if simulation_report and _is_material_risk(simulation_report, business_rules):
        return Decision.HUMAN_REVIEW, ["risk is material but not a rule violation"]
    return Decision.APPROVE, ["passes all checks"]


# ---------------------------------------------------------------------------
# Orchestration helper used by the agent and the HTTP layer
# ---------------------------------------------------------------------------


class PolicyEngine:
    """Parameter validation, role checks, and report construction.

    A thin, testable facade over the module-level functions above. It holds no
    mutable state, so the backend can keep one per process.
    """

    def __init__(self, business_rules: BusinessRules | None = None) -> None:
        self._business_rules = business_rules

    def set_business_rules(self, rules: BusinessRules) -> None:
        self._business_rules = rules

    @property
    def business_rules(self) -> BusinessRules | None:
        return self._business_rules

    def validate(self, action: ProposedAction) -> ValidationResult:
        return validate_parameters(action.action_type, action.parameters)

    def check_permissions(self, action: ProposedAction) -> bool:
        return is_permitted(action.proposed_by, action.action_type)

    def classify(
        self,
        data_quality_issues: list[DataQualityIssue],
        permission_ok: bool,
        simulation_report: SimulationReport | None,
    ) -> tuple[Decision, list[str]]:
        return classify(
            data_quality_issues,
            permission_ok,
            simulation_report,
            business_rules=self._business_rules,
        )

    def build_report(
        self,
        action: ProposedAction,
        data_quality_issues: list[DataQualityIssue],
        permission_ok: bool,
        simulation_report: SimulationReport | None,
    ) -> DecisionReport:
        decision, reasons = self.classify(
            data_quality_issues, permission_ok, simulation_report
        )
        missing_evidence = (
            [issue.detail for issue in data_quality_issues]
            if decision is Decision.ABSTAIN
            else []
        )
        return DecisionReport(
            decision=decision,
            reasons=reasons,
            missing_evidence=missing_evidence,
            simulation_report=simulation_report,
            data_quality_issues=list(data_quality_issues),
        )
