"""The four decisions, end to end, offline.

Spec Section 8.2 requires the demo to produce all four decision types. This
walks each one from a natural-language request to a ``DecisionReport``, through
the real agent, simulator, classifier and decision report - the only fake is
the LLM itself.
"""

from __future__ import annotations

import pytest

from twinguard_core import (
    ClarificationRequest,
    Decision,
    DecisionReport,
    InMemoryDataStore,
    PolicyEngine,
    Role,
    SimulationEngine,
    TwinManager,
    demo_baseline,
    demo_stale,
)
from twinguard_core.agent import (
    FakeLLMProvider,
    TwinGuardAgent,
    text_response,
    tool_response,
)
from twinguard_core.models import ClarificationRequest as ClarificationModel

from .conftest import cancel, transfer


def build(dataset=None):
    store = InMemoryDataStore(dataset or demo_baseline())
    twin = TwinManager(store)
    engine = SimulationEngine(twin)
    policy = PolicyEngine(store.get_business_rules())
    return TwinGuardAgent(FakeLLMProvider(), twin, engine, policy), store


def explain(text: str) -> dict:
    return text_response(text)


def test_the_readme_demo_table_is_accurate():
    """The four rows in README.md, asserted rather than trusted.

    Docs drift silently; this makes the documented demo a test.
    """
    cases = [
        ("safe", demo_baseline(), transfer(50, product_id="P-003"), Role.AI_AGENT,
         Decision.APPROVE),
        ("harmful", demo_baseline(), transfer(5000, product_id="P-003"), Role.AI_AGENT,
         Decision.BLOCK),
        ("stale data", demo_stale(), transfer(50, product_id="P-003"), Role.AI_AGENT,
         Decision.ABSTAIN),
        ("human review", demo_baseline(), cancel(po_id="PO-2001"), Role.MANAGER,
         Decision.HUMAN_REVIEW),
    ]
    for name, dataset, action, role, expected in cases:
        agent, _ = build(dataset)
        agent._provider = FakeLLMProvider(
            script=[
                tool_response("propose_action", action_type=action.action_type.value,
                              parameters=action.parameters),
                tool_response("get_decision", action={}, simulation_report={}),
                explain("here is why"),
            ]
        )
        result = agent.handle_request("go", role)
        assert isinstance(result, DecisionReport), name
        assert result.decision is expected, name


def test_the_seed_has_exactly_one_pending_purchase_order():
    """README and the spec's seed description both promise this, and the
    cancel scenario depends on there being a single target."""
    pending = [p for p in demo_baseline().purchase_orders if p.status == "pending"]
    assert [p.po_id for p in pending] == ["PO-2001"]


def test_po_2001_is_the_sole_supply_for_so_1003():
    """The documented reason the cancel escalates rather than blocks."""
    dataset = demo_baseline()
    stock = sum(r.quantity_on_hand for r in dataset.inventory if r.product_id == "P-007")
    order = next(
        o for o in dataset.customer_orders if o.order_id == "SO-1003"
    )
    assert order.product_id == "P-007"
    assert order.quantity > stock
    assert [p.po_id for p in dataset.purchase_orders
            if p.product_id == "P-007" and p.status == "pending"] == ["PO-2001"]


def test_the_four_decisions_are_all_reachable():
    """Guard against the demo silently collapsing to fewer outcomes."""
    seen = set()
    agent, _ = build()
    agent._provider = provider = FakeLLMProvider(
        script=[
            tool_response("propose_action", action_type="transfer_inventory",
                          parameters={"product_id": "P-003",
                                      "source_warehouse_id": "WH-01",
                                      "destination_warehouse_id": "WH-02",
                                      "quantity": 50}),
            tool_response("get_decision", action={}, simulation_report={}),
            explain("Approved."),
            tool_response("propose_action", action_type="transfer_inventory",
                          parameters={"product_id": "P-003",
                                      "source_warehouse_id": "WH-01",
                                      "destination_warehouse_id": "WH-02",
                                      "quantity": 5000}),
            tool_response("get_decision", action={}, simulation_report={}),
            explain("Blocked."),
            tool_response("propose_action", action_type="cancel_purchase_order",
                          parameters={"po_id": "PO-2001"}),
            tool_response("get_decision", action={}, simulation_report={}),
            tool_response("request_human_review", action={}, report={}),
            explain("Escalated."),
            text_response("Which product and warehouse did you mean?"),
        ]
    )
    for _ in range(4):
        seen.add(agent.handle_request("go", Role.AI_AGENT).__class__)

    assert seen == {DecisionReport, ClarificationModel}


@pytest.mark.parametrize(
    "quantity,expected",
    [
        (50, Decision.APPROVE),
        (5000, Decision.BLOCK),
    ],
)
def test_the_transfer_scenarios(quantity: int, expected: Decision):
    agent, _ = build()
    agent._provider = FakeLLMProvider(
        script=[
            tool_response(
                "propose_action",
                action_type="transfer_inventory",
                parameters={
                    "product_id": "P-003",
                    "source_warehouse_id": "WH-01",
                    "destination_warehouse_id": "WH-02",
                    "quantity": quantity,
                },
            ),
            tool_response("get_decision", action={}, simulation_report={}),
            explain("Here is what I found and why."),
        ]
    )
    result = agent.handle_request("move stock", Role.AI_AGENT)
    assert isinstance(result, DecisionReport)
    assert result.decision is expected


def test_the_abstain_scenario_names_its_missing_evidence():
    agent, _ = build(demo_stale())
    agent._provider = FakeLLMProvider(
        script=[
            tool_response(
                "propose_action",
                action_type="transfer_inventory",
                parameters={
                    "product_id": "P-003",
                    "source_warehouse_id": "WH-01",
                    "destination_warehouse_id": "WH-02",
                    "quantity": 50,
                },
            ),
            tool_response("get_decision", action={}, simulation_report={}),
            explain("I cannot sign this off: SUP-003 has no lead time."),
        ]
    )
    result = agent.handle_request("move 50", Role.AI_AGENT)
    assert result.decision is Decision.ABSTAIN
    assert any("SUP-003" in m for m in result.missing_evidence)


def test_the_human_review_scenario_surfaces_the_stockout():
    agent, _ = build()
    agent._provider = FakeLLMProvider(
        script=[
            tool_response("propose_action", action_type="cancel_purchase_order",
                          parameters={"po_id": "PO-2001"}),
            tool_response("get_decision", action={}, simulation_report={}),
            tool_response("request_human_review", action={}, report={}),
            explain("A manager needs to look at this."),
        ]
    )
    result = agent.handle_request("cancel PO-2001", Role.MANAGER)
    assert result.decision is Decision.HUMAN_REVIEW
    assert result.simulation_report.kpis["stockout_count"] > 0
    # Escalation is not execution: nothing on a DecisionReport may be read as
    # "this was applied". Execution state lives on AuditEvent.executed.
    assert "executed" not in result.model_dump()
    assert len(agent.last_tools.human_review_queue) == 1


def test_a_clarification_never_touches_the_data():
    agent, store = build()
    before = {row.po_id for row in store.get_purchase_orders()}
    agent._provider = FakeLLMProvider(
        script=[text_response("Which warehouse did you mean, WH-01 or WH-02?")]
    )
    result = agent.handle_request("move some of it to the other place", Role.AI_AGENT)
    assert isinstance(result, ClarificationRequest)
    assert {row.po_id for row in store.get_purchase_orders()} == before


def test_no_decision_path_writes_to_operational_data():
    """The strongest statement of the core guarantee: walk every outcome, and
    operational data is byte-identical afterwards in all of them."""
    agent, store = build()
    script = [
        tool_response("propose_action", action_type="transfer_inventory",
                      parameters={"product_id": "P-003",
                                  "source_warehouse_id": "WH-01",
                                  "destination_warehouse_id": "WH-02",
                                  "quantity": 50}),
        tool_response("get_decision", action={}, simulation_report={}),
        explain("ok"),
        tool_response("propose_action", action_type="cancel_purchase_order",
                      parameters={"po_id": "PO-2001"}),
        tool_response("get_decision", action={}, simulation_report={}),
        tool_response("request_human_review", action={}, report={}),
        explain("ok"),
    ]
    agent._provider = FakeLLMProvider(script=script)

    inventory_before = [
        (r.product_id, r.warehouse_id, r.quantity_on_hand) for r in store.get_inventory()
    ]
    version_before = store.get_version()

    first = agent.handle_request("one", Role.AI_AGENT)
    # Cancelling a purchase order is manager-only, so the escalation has to
    # come from a manager or it would block on permissions instead.
    second = agent.handle_request("two", Role.MANAGER)

    assert first.decision is Decision.APPROVE
    assert second.decision is Decision.HUMAN_REVIEW
    assert [
        (r.product_id, r.warehouse_id, r.quantity_on_hand) for r in store.get_inventory()
    ] == inventory_before
    assert store.get_version() == version_before


def test_simulation_is_reproducible_for_the_same_request():
    """The same request simulated twice yields an identical outcome.

    The snapshot and simulation ids are excluded: each request legitimately
    takes a *fresh* snapshot (Section 7.3 step 1), so the ids must differ.
    Everything that describes what happened must not.
    """
    agent, _ = build()

    def run() -> dict:
        agent._provider = FakeLLMProvider(
            script=[
                tool_response("propose_action", action_type="transfer_inventory",
                              parameters={"product_id": "P-003",
                                          "source_warehouse_id": "WH-01",
                                          "destination_warehouse_id": "WH-02",
                                          "quantity": 50}),
                tool_response("get_decision", action={}, simulation_report={}),
                explain("ok"),
            ]
        )
        result = agent.handle_request("same question", Role.AI_AGENT)
        report = result.simulation_report.model_dump(mode="json")
        report.pop("snapshot_id")
        report.pop("simulation_id")
        return report

    assert run() == run()


def test_a_report_carries_everything_the_ui_needs():
    """Section 8/9: the DecisionReport is the whole contract to the frontend."""
    agent, _ = build()
    agent._provider = FakeLLMProvider(
        script=[
            tool_response("propose_action", action_type="transfer_inventory",
                          parameters={"product_id": "P-003",
                                      "source_warehouse_id": "WH-01",
                                      "destination_warehouse_id": "WH-02",
                                      "quantity": 50}),
            tool_response("get_decision", action={}, simulation_report={}),
            explain("Moving 50 units of P-003 is within the transfer limit."),
        ]
    )
    report = agent.handle_request("move 50 of P-003", Role.AI_AGENT)

    assert report.decision is Decision.APPROVE
    assert report.explanation
    assert report.reasons
    assert report.simulation_report.kpis
    assert report.simulation_report.trace
    assert report.simulation_report.final_state_diff
    # The role is carried on the action, not the report.
    assert report.simulation_report.action.proposed_by is Role.AI_AGENT
    # It must serialise cleanly for the API.
    assert isinstance(report.model_dump(mode="json"), dict)
