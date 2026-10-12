"""The tool surface exposed to the LLM (spec Section 7.2).

Exactly seven tools, each with a strict JSON schema derived from the Pydantic
models in spec Section 6.2. The LLM is never given raw data access and is never
asked to write anything: there is no tool whose implementation calls
``DataStore.apply_approved_action``, and no tool returns a database handle.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from ..models import (
    ActionType,
    ClarificationRequest,
    DataQualityIssue,
    DecisionReport,
    ProposedAction,
    Role,
    SimulationReport,
)
from ..policy_engine import (
    PARAM_SCHEMAS,
    PolicyEngine,
    validate_parameters,
)
from ..simulation_engine import SimulationEngine
from ..twin_manager import TwinManager

#: JSON schemas handed to the provider. Kept as literal dicts so the wire format
#: is inspectable in tests without depending on a provider SDK's schema renderer.
TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_state",
            "description": (
                "Read the frozen twin state for a snapshot. Read-only. Use this "
                "to find real product, warehouse, order and purchase order ids."
            ),
            "parameters": {
                "type": "object",
                "properties": {"snapshot_id": {"type": "string"}},
                "required": ["snapshot_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_data_quality_report",
            "description": (
                "List data-quality problems in a snapshot, e.g. a supplier with an "
                "unknown lead time or inventory that is out of date. Read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {"snapshot_id": {"type": "string"}},
                "required": ["snapshot_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_action",
            "description": (
                "Propose exactly one action. Use only ids returned by "
                "get_current_state. This does not execute anything; TwinGuard "
                "simulates the action on the twin first and a human or the policy "
                "engine decides whether it is applied to live data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action_type": {
                        "type": "string",
                        "enum": [t.value for t in ActionType],
                    },
                    "parameters": {
                        "type": "object",
                        "description": (
                            "Shape depends on action_type. transfer_inventory: "
                            "product_id, source_warehouse_id, "
                            "destination_warehouse_id, quantity (>0). "
                            "create_purchase_order: product_id, supplier_id, "
                            "quantity (>0), optional destination_warehouse_id. "
                            "change_order_priority: order_id, new_priority. "
                            "cancel_purchase_order: po_id."
                        ),
                    },
                },
                "required": ["action_type", "parameters"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_action",
            "description": (
                "Check a proposed action's parameters against the schema for its "
                "action type. Read-only. Always call this before run_simulation."
            ),
            "parameters": {
                "type": "object",
                "properties": {"action": {"$ref": "#/definitions/ProposedAction"}},
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_simulation",
            "description": (
                "Run the action against a copy of the twin and return the "
                "forecast. Never touches live data. Read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "snapshot_id": {"type": "string"},
                    "action": {"$ref": "#/definitions/ProposedAction"},
                    "horizon_days": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Defaults to 14.",
                    },
                },
                "required": ["snapshot_id", "action"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_decision",
            "description": (
                "Hand the simulated action to the deterministic policy engine and "
                "get back approve / block / human_review / abstain. This is plain "
                "code, not a judgement call: the decision cannot be argued with."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"$ref": "#/definitions/ProposedAction"},
                    "simulation_report": {
                        "type": "object",
                        "description": "The report returned by run_simulation.",
                    },
                },
                "required": ["action", "simulation_report"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_human_review",
            "description": (
                "Queue an action for a human approver. Call this when the decision "
                "is human_review. It does not apply the action."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"$ref": "#/definitions/ProposedAction"},
                    "report": {
                        "type": "object",
                        "description": "The report returned by get_decision.",
                    },
                },
                "required": ["action", "report"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_NAMES: tuple[str, ...] = tuple(
    tool["function"]["name"] for tool in TOOL_DEFINITIONS
)

#: Tool names that are safe to call at any point, including before an action
#: exists. Everything else presupposes a validated action.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {"get_current_state", "get_data_quality_report"}
)


class TwinGuardTools:
    """Executable implementations of the seven tools.

    Holds no reference to a ``DataStore``. The only store reachable from here is
    the one behind :class:`~twinguard_core.twin_manager.TwinManager`, and that
    one has no write method for operational data.
    """

    def __init__(
        self,
        twin_manager: TwinManager,
        simulation_engine: SimulationEngine,
        policy_engine: PolicyEngine,
        requester_role: Role,
    ) -> None:
        self._twin = twin_manager
        self._sim = simulation_engine
        self._policy = policy_engine
        self._role = requester_role
        self.snapshot_id: str | None = None
        self.action: ProposedAction | None = None
        self.simulation_report: SimulationReport | None = None
        self.decision_report: DecisionReport | None = None
        #: Actions queued for a human, for the HTTP layer to persist.
        self.human_review_queue: list[dict] = []

    # -- tool implementations ----------------------------------------------

    def get_current_state(self, snapshot_id: str) -> dict:
        state = self._twin.get_snapshot_state(snapshot_id)
        return {
            name: [record.model_dump(mode="json") for record in records]
            for name, records in state.items()
        }

    def get_data_quality_report(self, snapshot_id: str) -> list[DataQualityIssue]:
        return self._twin.run_data_quality_checks(snapshot_id)

    def propose_action(
        self, action_type: str, parameters: dict
    ) -> ProposedAction:
        try:
            resolved_type = ActionType(action_type)
        except ValueError:
            raise ValueError(
                f"unknown action_type {action_type!r}; expected one of "
                f"{[t.value for t in ActionType]}"
            ) from None
        schema = PARAM_SCHEMAS[resolved_type]
        action = ProposedAction(
            action_id=deterministic_action_id(resolved_type, parameters),
            action_type=resolved_type,
            parameters=dict(parameters),
            proposed_by=self._role,
        )
        self.action = action
        return action

    def validate_action(self, action: ProposedAction) -> bool:
        result = validate_parameters(action.action_type, action.parameters)
        return result.valid

    def run_simulation(
        self,
        snapshot_id: str,
        action: ProposedAction,
        horizon_days: int = 14,
    ) -> SimulationReport:
        report = self._sim.simulate_action(
            snapshot_id, action, horizon_days=horizon_days
        )
        self.simulation_report = report
        return report

    def get_decision(
        self, action: ProposedAction, simulation_report: SimulationReport
    ) -> DecisionReport:
        snapshot_id = self.snapshot_id
        if snapshot_id is None:
            raise RuntimeError("tools have not been bound to a snapshot")
        issues = self._twin.run_data_quality_checks(snapshot_id)
        report = self._policy.build_report(
            action=action,
            data_quality_issues=issues,
            permission_ok=self._policy.check_permissions(action),
            simulation_report=simulation_report,
        )
        self.decision_report = report
        return report

    def request_human_review(
        self, action: ProposedAction, report: DecisionReport
    ) -> None:
        self.human_review_queue.append(
            {
                "action": action.model_dump(mode="json"),
                "decision": report.decision.value,
                "reasons": list(report.reasons),
                "proposed_by": action.proposed_by.value,
            }
        )

    # -- dispatch ------------------------------------------------------------

    def dispatch(self, name: str, arguments: dict) -> Any:
        """Route a tool call by name. Unknown names raise, never silently pass."""
        if name not in TOOL_NAMES:
            raise ValueError(
                f"unknown tool {name!r}; available tools: {list(TOOL_NAMES)}"
            )
        method: Callable[..., Any] = getattr(self, name)
        return method(**arguments)


def deterministic_action_id(
    action_type: ActionType, parameters: dict
) -> str:
    """Stable id derived from the action's content.

    Replaying the same request yields the same ``action_id``, which is what lets
    the simulator produce byte-identical reports for the same proposal.
    """
    payload = json.dumps(
        {"action_type": action_type.value, "parameters": parameters},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"act-{digest}"


def clarification_for_missing_fields(
    original_request: str,
    action_type: str,
    result_errors: list[str],
    missing_fields: list[str],
) -> ClarificationRequest:
    """Turn a failed parameter validation into a question for the user."""
    if missing_fields:
        detail = f"valid values for {', '.join(missing_fields)}"
    else:
        detail = "; ".join(result_errors) or "valid action parameters"
    return ClarificationRequest(
        question=(
            f"I can turn this into a {action_type} action, but I need {detail} "
            "before I can simulate it."
        ),
        missing_fields=list(missing_fields),
        original_request=original_request,
    )
