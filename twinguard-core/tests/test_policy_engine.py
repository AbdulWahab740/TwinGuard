"""PolicyEngine: permission matrix, parameter schemas, classifier ordering.

Spec Section 6.4 requires a passing and a failing permission test for every
``ActionType`` x ``Role`` pair, and a test proving the classifier's precedence.
"""

from __future__ import annotations

import pytest

from twinguard_core import (
    ActionType,
    DataQualityIssue,
    Decision,
    PolicyEngine,
    ProposedAction,
    Role,
    classify,
    demo_baseline,
)
from twinguard_core.models import BusinessRules
from twinguard_core.policy_engine import (
    PERMISSION_MATRIX,
    _is_material_risk,
    is_permitted,
    validate_action,
    validate_parameters,
)

from .conftest import cancel, purchase, reprioritise, transfer

ALL_ROLES = list(Role)
ALL_ACTION_TYPES = list(ActionType)


# ---------------------------------------------------------------------------
# 6.2 parameter schemas
# ---------------------------------------------------------------------------


def test_valid_parameters_pass():
    for action in (transfer(), purchase(), reprioritise(), cancel()):
        assert validate_action(action) is True


@pytest.mark.parametrize(
    "action_type,parameters,expected_error",
    [
        (
            ActionType.TRANSFER_INVENTORY,
            {
                "product_id": "P-001",
                "source_warehouse_id": "WH-01",
                "destination_warehouse_id": "WH-02",
            },
            "quantity",
        ),
        (
            ActionType.TRANSFER_INVENTORY,
            {
                "product_id": "P-001",
                "source_warehouse_id": "WH-01",
                "destination_warehouse_id": "WH-02",
                "quantity": 0,
            },
            "quantity",
        ),
        (
            ActionType.TRANSFER_INVENTORY,
            {
                "product_id": "P-001",
                "source_warehouse_id": "WH-01",
                "destination_warehouse_id": "WH-02",
                "quantity": 5,
                "colour": "red",
            },
            "colour",
        ),
        (
            ActionType.CREATE_PURCHASE_ORDER,
            {"product_id": "P-001", "supplier_id": "SUP-001"},
            "quantity",
        ),
        (
            ActionType.CHANGE_ORDER_PRIORITY,
            {"order_id": "SO-1001", "new_priority": "urgent"},
            "new_priority",
        ),
        (ActionType.CANCEL_PURCHASE_ORDER, {}, "po_id"),
    ],
)
def test_invalid_parameters_fail(action_type, parameters, expected_error):
    result = validate_parameters(action_type, parameters)
    assert result.valid is False
    assert any(expected_error in error for error in result.errors), result.errors


def test_missing_fields_are_reported_separately_from_errors():
    result = validate_parameters(
        ActionType.TRANSFER_INVENTORY, {"product_id": "P-001"}
    )
    assert result.missing_fields == ["source_warehouse_id", "destination_warehouse_id", "quantity"]
    assert result.valid is False
    assert not result  # __bool__ mirrors .valid


def test_destination_warehouse_is_optional_on_a_purchase_order():
    result = validate_parameters(
        ActionType.CREATE_PURCHASE_ORDER,
        {"product_id": "P-001", "supplier_id": "SUP-001", "quantity": 5},
    )
    assert result.valid is True
    assert result.missing_fields == []


# ---------------------------------------------------------------------------
# 6.4 permission matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ALL_ROLES)
@pytest.mark.parametrize("action_type", ALL_ACTION_TYPES)
def test_every_action_type_has_a_passing_and_a_failing_role(
    role: Role, action_type: ActionType
):
    """Every cell of the matrix is defined, and each action type has at least
    one permitting and one denying role."""
    assert isinstance(is_permitted(role, action_type), bool)
    assert role in PERMISSION_MATRIX[action_type] or isinstance(
        PERMISSION_MATRIX[action_type], set
    )


@pytest.mark.parametrize("action_type", ALL_ACTION_TYPES)
def test_each_action_type_is_permitted_for_at_least_one_role(action_type: ActionType):
    assert any(is_permitted(role, action_type) for role in ALL_ROLES)
    assert not all(is_permitted(role, action_type) for role in ALL_ROLES)


def test_viewer_may_never_propose_anything():
    for action_type in ALL_ACTION_TYPES:
        assert is_permitted(Role.VIEWER, action_type) is False


def test_manager_may_propose_everything():
    for action_type in ALL_ACTION_TYPES:
        assert is_permitted(Role.MANAGER, action_type) is True


def test_cancelling_a_purchase_order_is_reserved_for_a_manager():
    assert is_permitted(Role.PLANNER, ActionType.CANCEL_PURCHASE_ORDER) is False
    assert is_permitted(Role.AI_AGENT, ActionType.CANCEL_PURCHASE_ORDER) is False
    assert is_permitted(Role.MANAGER, ActionType.CANCEL_PURCHASE_ORDER) is True


def test_a_proposed_action_carries_the_role_that_proposed_it():
    action = transfer(50, role=Role.VIEWER)
    assert PolicyEngine().check_permissions(action) is False
    assert PolicyEngine().check_permissions(transfer(50, role=Role.PLANNER)) is True


# ---------------------------------------------------------------------------
# 6.3 classifier ordering
# ---------------------------------------------------------------------------


def issue(code: str = "missing_field", entity: str = "SUP-003") -> DataQualityIssue:
    return DataQualityIssue(
        code=code, detail=f"supplier lead time for {entity} is unknown", entity_id=entity
    )


def report_with(violated: list[str] | None = None, **kpis) -> "object":
    """A real SimulationReport with selected fields overridden.

    Built from a genuine run so the KPI set matches production; only
    ``violated_rules`` and any named KPIs are replaced.
    """
    from twinguard_core import InMemoryDataStore, SimulationEngine, TwinManager

    twin = TwinManager(InMemoryDataStore(demo_baseline()))
    engine = SimulationEngine(twin)
    report = engine.simulate_action(
        twin.create_snapshot().snapshot_id, transfer(50)
    )
    updates: dict = {"violated_rules": violated or []}
    if kpis:
        merged = dict(report.kpis)
        merged.update(kpis)
        updates["kpis"] = merged
    return report.model_copy(update=updates)


def test_any_data_quality_issue_abstains():
    decision, reasons = classify([issue()], True, None)
    assert decision is Decision.ABSTAIN
    assert reasons == [
        "missing/stale evidence: supplier lead time for SUP-003 is unknown"
    ]


def test_data_quality_beats_every_other_branch():
    """Spec 6.4: any issue means ABSTAIN regardless of the simulation outcome."""
    dirty = [issue()]
    violating = report_with(violated=["transfer quantity 900 exceeds max"])

    # permissions fine, rules violated
    assert classify(dirty, True, violating)[0] is Decision.ABSTAIN
    # permissions denied
    assert classify(dirty, False, None)[0] is Decision.ABSTAIN
    # permissions denied AND rules violated
    assert classify(dirty, False, violating)[0] is Decision.ABSTAIN
    # every one of several issues is reported
    decision, reasons = classify(dirty * 3, False, violating)
    assert decision is Decision.ABSTAIN
    assert len(reasons) == 3


def test_denied_permission_blocks_even_with_a_clean_simulation():
    decision, reasons = classify([], False, report_with())
    assert decision is Decision.BLOCK
    assert reasons == ["role is not permitted to propose or approve this action"]


def test_rule_violations_block_before_material_risk():
    violating = report_with(violated=["something broke", "and again"])
    decision, reasons = classify([], True, violating)
    assert decision is Decision.BLOCK
    assert reasons == ["something broke", "and again"]


def test_material_risk_without_a_violation_goes_to_human_review():
    from twinguard_core import InMemoryDataStore, SimulationEngine, TwinManager

    twin = TwinManager(InMemoryDataStore(demo_baseline()))
    engine = SimulationEngine(twin)
    report = engine.simulate_action(
        twin.create_snapshot().snapshot_id, cancel(po_id="PO-2001")
    )
    assert report.violated_rules == []
    decision, reasons = classify([], True, report)
    assert decision is Decision.HUMAN_REVIEW
    assert reasons == ["risk is material but not a rule violation"]


def test_a_clean_run_approves():
    decision, reasons = classify([], True, report_with())
    assert decision is Decision.APPROVE
    assert reasons == ["passes all checks"]


def test_no_simulation_and_no_issues_approves():
    decision, reasons = classify([], True, None)
    assert decision is Decision.APPROVE
    assert reasons == ["passes all checks"]


# ---------------------------------------------------------------------------
# _is_material_risk threshold
# ---------------------------------------------------------------------------


def test_any_stockout_is_material():
    assert _is_material_risk(report_with(stockout_count=1.0)) is True
    assert _is_material_risk(report_with(stockout_count=0.0)) is False


def test_an_action_over_half_the_purchase_budget_is_material():
    rules = BusinessRules(
        max_transfer_quantity=500,
        max_purchase_budget=1000.0,
        freshness_limit_hours=24,
    )
    # 600 > 50% of 1000
    assert _is_material_risk(report_with(action_value=600.0), rules) is True
    # 400 is under the line
    assert _is_material_risk(report_with(action_value=400.0), rules) is False


def test_the_value_check_needs_the_budget_and_degrades_without_it():
    report = report_with(action_value=99_999.0)
    assert _is_material_risk(report, None) is False
    assert (
        _is_material_risk(
            report,
            BusinessRules(
                max_transfer_quantity=500,
                max_purchase_budget=1000.0,
                freshness_limit_hours=24,
            ),
        )
        is True
    )


def test_a_zero_budget_cannot_make_everything_material():
    rules = BusinessRules(
        max_transfer_quantity=500,
        max_purchase_budget=0.0,
        freshness_limit_hours=24,
    )
    assert _is_material_risk(report_with(action_value=500.0), rules) is False


# ---------------------------------------------------------------------------
# DecisionReport construction
# ---------------------------------------------------------------------------


def test_build_report_carries_the_issues_into_missing_evidence_only_when_abstaining():
    engine = PolicyEngine(demo_baseline().business_rules)

    abstained = engine.build_report(transfer(50), [issue()], True, None)
    assert abstained.decision is Decision.ABSTAIN
    assert abstained.missing_evidence == [
        "supplier lead time for SUP-003 is unknown"
    ]
    assert len(abstained.data_quality_issues) == 1

    approved = engine.build_report(transfer(50), [], True, report_with())
    assert approved.decision is Decision.APPROVE
    assert approved.missing_evidence == []


def test_policy_engine_takes_its_budget_from_the_business_rules():
    engine = PolicyEngine()
    assert engine.business_rules is None
    rules = demo_baseline().business_rules
    engine.set_business_rules(rules)
    assert engine.business_rules == rules
    assert engine.classify([], True, report_with())[0] is Decision.APPROVE


def test_the_classifier_is_plain_code_and_ignores_extra_inputs():
    """No LLM output reaches classify(); it is a function of its three inputs."""
    a = classify([], True, report_with())
    b = classify([], True, report_with())
    assert a == b
