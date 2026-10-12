"""Agent: clarification, abstention, block and approve paths, offline.

Everything here runs through ``FakeLLMProvider`` (spec Section 7.4). No network,
no API key.
"""

from __future__ import annotations

import inspect

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
    TOOL_DEFINITIONS,
    TOOL_NAMES,
    FakeLLMProvider,
    TwinGuardAgent,
    text_response,
    tool_response,
)

from .conftest import cancel, transfer

SYSTEM_SAFETY_PHRASE = "simulated on a digital twin"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def baseline_agent():
    store = InMemoryDataStore(demo_baseline())
    twin = TwinManager(store)
    engine = SimulationEngine(twin)
    policy = PolicyEngine(store.get_business_rules())
    return TwinGuardAgent(FakeLLMProvider(), twin, engine, policy), store


@pytest.fixture
def stale_agent():
    store = InMemoryDataStore(demo_stale())
    twin = TwinManager(store)
    engine = SimulationEngine(twin)
    policy = PolicyEngine(store.get_business_rules())
    return TwinGuardAgent(FakeLLMProvider(), twin, engine, policy), store


def provider_for(*turns) -> FakeLLMProvider:
    return FakeLLMProvider(script=list(turns))


def propose_transfer(quantity: int = 50, product: str = "P-003") -> dict:
    return {
        "action_type": "transfer_inventory",
        "parameters": {
            "product_id": product,
            "source_warehouse_id": "WH-01",
            "destination_warehouse_id": "WH-02",
            "quantity": quantity,
        },
    }


# ---------------------------------------------------------------------------
# 7.2 the tool surface
# ---------------------------------------------------------------------------


def test_exactly_the_seven_spec_tools_are_exposed():
    assert set(TOOL_NAMES) == {
        "get_current_state",
        "get_data_quality_report",
        "propose_action",
        "validate_action",
        "run_simulation",
        "get_decision",
        "request_human_review",
    }


def test_no_tool_definition_exposes_a_write_to_live_data():
    """No tool is an apply/execute/commit operation."""
    for tool in TOOL_DEFINITIONS:
        name = tool["function"]["name"]
        for forbidden in ("apply", "execute", "commit", "write", "delete", "sql"):
            assert forbidden not in name.lower(), name


def test_every_tool_definition_has_a_strict_object_schema():
    for tool in TOOL_DEFINITIONS:
        params = tool["function"]["parameters"]
        assert params["type"] == "object"
        assert params["additionalProperties"] is False
        assert "name" in tool["function"]
        assert tool["function"]["description"].strip()


# ---------------------------------------------------------------------------
# 7.3 step 3: the clarification path
# ---------------------------------------------------------------------------


def test_an_ambiguous_request_returns_a_clarification(baseline_agent):
    """Spec 7.4: 'move some stock to the other warehouse' must not be guessed."""
    agent, _ = baseline_agent
    agent._provider = provider_for(
        text_response("Which product and how many units should I move?")
    )
    result = agent.handle_request("move some stock to the other warehouse", Role.AI_AGENT)

    assert isinstance(result, ClarificationRequest)
    assert result.original_request == "move some stock to the other warehouse"
    assert "product" in result.question.lower()
    assert result.missing_fields == []


def test_a_missing_parameter_becomes_a_clarification_not_a_guess(baseline_agent):
    agent, store = baseline_agent
    agent._provider = provider_for(
        tool_response(
            "propose_action",
            action_type="transfer_inventory",
            parameters={"product_id": "P-003", "quantity": 20},
        )
    )
    version_before = store.get_version()
    result = agent.handle_request("move some of P-003 across", Role.AI_AGENT)

    assert isinstance(result, ClarificationRequest)
    assert set(result.missing_fields) == {
        "source_warehouse_id",
        "destination_warehouse_id",
    }
    assert store.get_version() == version_before


def test_an_out_of_range_parameter_becomes_a_clarification(baseline_agent):
    agent, _ = baseline_agent
    agent._provider = provider_for(
        tool_response("propose_action", action_type="transfer_inventory",
                      parameters={"product_id": "P-003",
                                  "source_warehouse_id": "WH-01",
                                  "destination_warehouse_id": "WH-02",
                                  "quantity": -5})
    )
    result = agent.handle_request("move minus five units", Role.AI_AGENT)
    assert isinstance(result, ClarificationRequest)
    assert "quantity" in result.question


def test_an_unknown_action_type_becomes_a_clarification(baseline_agent):
    agent, _ = baseline_agent
    agent._provider = provider_for(
        tool_response("propose_action", action_type="teleport_stock", parameters={})
    )
    result = agent.handle_request("teleport the stock", Role.AI_AGENT)
    assert isinstance(result, ClarificationRequest)


# ---------------------------------------------------------------------------
# 7.3 step 5: the four decisions
# ---------------------------------------------------------------------------


def test_the_approve_path(baseline_agent):
    agent, _ = baseline_agent
    agent._provider = provider_for(
        tool_response("get_current_state", snapshot_id="snap-0001"),
        tool_response("propose_action", **propose_transfer(50)),
        tool_response("get_decision", action={}, simulation_report={}),
        text_response("Moving 50 units is within policy and the horizon stays clean."),
    )
    result = agent.handle_request("move 50 of P-003 to WH-02", Role.AI_AGENT)

    assert isinstance(result, DecisionReport)
    assert result.decision is Decision.APPROVE
    assert result.reasons == ["passes all checks"]
    assert result.data_quality_issues == []
    assert result.simulation_report is not None
    assert result.simulation_report.violated_rules == []
    assert "within policy" in result.explanation


def test_the_block_path(baseline_agent):
    agent, _ = baseline_agent
    agent._provider = provider_for(
        tool_response("propose_action", **propose_transfer(900, "P-001")),
        tool_response("get_decision", action={}, simulation_report={}),
        text_response("Blocked: the transfer is over the configured maximum."),
    )
    result = agent.handle_request("move 900 of P-001 to WH-02", Role.AI_AGENT)

    assert isinstance(result, DecisionReport)
    assert result.decision is Decision.BLOCK
    assert any("max_transfer_quantity" in reason for reason in result.reasons)
    assert "Blocked" in result.explanation


def test_the_block_path_on_a_permission_failure(baseline_agent):
    agent, _ = baseline_agent
    agent._provider = provider_for(
        tool_response("propose_action", **propose_transfer(50)),
        tool_response("get_decision", action={}, simulation_report={}),
        text_response("A viewer may not move stock."),
    )
    result = agent.handle_request("move 50 of P-003", Role.VIEWER)

    assert isinstance(result, DecisionReport)
    assert result.decision is Decision.BLOCK
    assert result.reasons == ["role is not permitted to propose or approve this action"]


def test_the_abstain_path_names_the_missing_evidence(stale_agent):
    """Spec 7.4: the explanation must name the specific missing evidence."""
    agent, _ = stale_agent
    agent._provider = provider_for(
        tool_response("propose_action", **propose_transfer(50)),
        tool_response("get_decision", action={}, simulation_report={}),
        text_response(
            "I cannot decide: the supplier lead time for SUP-003 is unknown, so "
            "the replenishment date is a guess."
        ),
    )
    result = agent.handle_request("move 50 of P-003 to WH-02", Role.AI_AGENT)

    assert isinstance(result, DecisionReport)
    assert result.decision is Decision.ABSTAIN
    assert any(i.code == "missing_field" for i in result.data_quality_issues)
    assert any("SUP-003" in m for m in result.missing_evidence)
    assert "SUP-003" in result.explanation


def test_the_human_review_path_queues_the_action(baseline_agent):
    agent, _ = baseline_agent
    agent._provider = provider_for(
        tool_response("propose_action", action_type="cancel_purchase_order",
                      parameters={"po_id": "PO-2001"}),
        tool_response("get_decision", action={}, simulation_report={}),
        tool_response("request_human_review", action={}, report={}),
        text_response("A manager should look at this before it ships."),
    )
    result = agent.handle_request("cancel PO-2001, it is late", Role.MANAGER)

    assert isinstance(result, DecisionReport)
    assert result.decision is Decision.HUMAN_REVIEW
    assert result.simulation_report.kpis["stockout_count"] == 1.0
    assert agent.last_tools is not None
    assert len(agent.last_tools.human_review_queue) == 1
    assert agent.last_tools.human_review_queue[0]["decision"] == "human_review"


def test_a_runaway_model_abstains_instead_of_guessing(baseline_agent):
    agent, _ = baseline_agent
    agent._provider = provider_for(
        *[
            tool_response("get_current_state", snapshot_id="snap-0001")
            for _ in range(20)
        ]
    )
    result = agent.handle_request("do something sensible", Role.AI_AGENT)
    assert isinstance(result, DecisionReport)
    assert result.decision is Decision.ABSTAIN
    assert "tool steps" in result.reasons[0]


# ---------------------------------------------------------------------------
# 7.3 step 1: a fresh snapshot per request
# ---------------------------------------------------------------------------


def test_each_request_gets_its_own_snapshot(baseline_agent):
    agent, _ = baseline_agent
    turns = (
        tool_response("propose_action", **propose_transfer(50)),
        tool_response("get_decision", action={}, simulation_report={}),
        text_response("ok"),
    )
    agent._provider = provider_for(*turns, *turns)
    agent.handle_request("first", Role.AI_AGENT)
    first = agent.last_snapshot_id
    agent.handle_request("second", Role.AI_AGENT)
    second = agent.last_snapshot_id
    assert first != second


# ---------------------------------------------------------------------------
# 7.3 hard rule: the LLM never writes
# ---------------------------------------------------------------------------


def test_agent_module_cannot_reach_the_write_path():
    """By construction: no executable statement in either module can reach the
    write path.

    The check walks the AST rather than the raw text, so a docstring that
    *names* ``apply_approved_action`` in order to explain why it is unreachable
    does not count as a call.
    """
    import ast

    from twinguard_core.agent import agent as agent_module
    from twinguard_core.agent import tools as tools_module

    forbidden = {
        "apply_approved_action",
        "execute_approved_action",
        "InMemoryDataStore",
        "DataStore",
        "data_store",
    }

    for module in (agent_module, tools_module):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                raise AssertionError(
                    f"{module.__name__}:{node.lineno} references {node.attr}"
                )
            if isinstance(node, ast.Name) and node.id in forbidden:
                raise AssertionError(
                    f"{module.__name__}:{node.lineno} references {node.id}"
                )
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = {
                    alias.name for alias in node.names
                } | ({node.module} if getattr(node, "module", None) else set())
                for name in names:
                    assert not any(name.endswith(f) for f in forbidden), (
                        f"{module.__name__}:{node.lineno} imports {name}"
                    )


def test_the_agent_holds_no_data_store_reference(baseline_agent):
    agent, _ = baseline_agent
    for value in vars(agent).values():
        assert not isinstance(value, InMemoryDataStore)
    # Its collaborators are all read-only with respect to live data.
    assert not hasattr(agent._twin, "apply_approved_action")
    assert not hasattr(agent._sim, "apply_approved_action")


def test_no_tool_is_reachable_from_the_agent_that_writes():
    from twinguard_core.agent.tools import TwinGuardTools

    for name in dir(TwinGuardTools):
        if name.startswith("_"):
            continue
        attribute = getattr(TwinGuardTools, name)
        if callable(attribute):
            assert "apply" not in name
            assert "execute" not in name


def test_an_approved_request_still_does_not_change_live_data(baseline_agent):
    """Even on the approve path, nothing is written until the backend executes."""
    agent, store = baseline_agent
    agent._provider = provider_for(
        tool_response("propose_action", **propose_transfer(50)),
        tool_response("get_decision", action={}, simulation_report={}),
        text_response("Approved."),
    )
    before = store.get_state()
    version = store.get_version()
    result = agent.handle_request("move 50 of P-003", Role.AI_AGENT)
    assert result.decision is Decision.APPROVE
    assert store.get_state() == before
    assert store.get_version() == version


# ---------------------------------------------------------------------------
# 7.1 provider plumbing
# ---------------------------------------------------------------------------


def test_the_provider_receives_the_tool_definitions(baseline_agent):
    agent, _ = baseline_agent
    provider = provider_for(text_response("need more detail"))
    agent._provider = provider
    agent.handle_request("do a thing", Role.AI_AGENT)
    assert len(provider.calls) == 1
    assert provider.calls[0]["tools"] == TOOL_DEFINITIONS
    assert SYSTEM_SAFETY_PHRASE in provider.calls[0]["system_prompt"]


def test_the_conversation_history_accumulates_across_turns(baseline_agent):
    agent, _ = baseline_agent
    provider = provider_for(
        tool_response("get_current_state", snapshot_id="snap-0001"),
        tool_response("propose_action", **propose_transfer(50)),
        tool_response("get_decision", action={}, simulation_report={}),
        text_response("done"),
    )
    agent._provider = provider
    agent.handle_request("move 50 of P-003", Role.AI_AGENT)
    histories = [call["conversation_history"] for call in provider.calls]
    assert histories[0] == []
    assert len(histories[-1]) == 6  # 3 tool calls, assistant + user each


def test_the_explanation_turn_gets_the_finished_report(baseline_agent):
    agent, _ = baseline_agent
    provider = provider_for(
        tool_response("propose_action", **propose_transfer(50)),
        tool_response("get_decision", action={}, simulation_report={}),
        text_response("All good."),
    )
    agent._provider = provider
    agent.handle_request("move 50 of P-003", Role.AI_AGENT)
    # The explanation is produced by the final text turn, which the agent
    # accepts only after a decision already exists.
    assert "All good." in (agent.last_tools.decision_report.explanation or "")


def test_an_exhausted_script_fails_loudly(baseline_agent):
    agent, _ = baseline_agent
    agent._provider = provider_for()
    with pytest.raises(AssertionError, match="ran out of scripted turns"):
        agent.handle_request("anything", Role.AI_AGENT)


def test_responses_by_text_lets_one_fake_serve_several_scenarios(baseline_agent):
    agent, _ = baseline_agent
    turn = (
        tool_response("propose_action", **propose_transfer(50)),
        tool_response("get_decision", action={}, simulation_report={}),
        text_response("Explained the approval."),
    )
    agent._provider = FakeLLMProvider(
        # Matched first, so an "unclear" request short-circuits to a question
        # while every other request falls through to the script.
        responses_by_text={"unclear": text_response("Which product do you mean?")},
        script=[*turn, *turn],
    )
    assert isinstance(
        agent.handle_request("something unclear", Role.AI_AGENT),
        ClarificationRequest,
    )
    report = agent.handle_request("move 50 of P-003", Role.AI_AGENT)
    assert report.decision is Decision.APPROVE
