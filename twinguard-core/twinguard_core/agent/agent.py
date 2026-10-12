"""The orchestration loop: request -> tool calls -> decision report -> explanation.

Two invariants this module is built around:

1. **The LLM never writes.** ``agent.py`` holds no reference to a ``DataStore`` at
   all, let alone ``apply_approved_action``. The only thing it can do is ask
   ``policy_engine`` for a decision, and the only thing that reaches operational
   data is the separate ``execute_approved_action`` path in the backend. A test
   asserts this by inspecting this module's source and its call graph.
2. **The LLM explains but does not decide.** Step 6 asks the model to describe a
   ``DecisionReport`` that ``policy_engine.classify`` already produced. Its text
   is stored on the report as ``explanation`` and can never change ``decision``.
"""

from __future__ import annotations

from typing import Any

from ..models import (
    ClarificationRequest,
    Decision,
    DecisionReport,
    ProposedAction,
    Role,
)
from ..policy_engine import PolicyEngine, validate_parameters
from ..simulation_engine import SimulationEngine
from ..twin_manager import TwinManager
from .provider import LLMProvider
from .tools import (
    READ_ONLY_TOOLS,
    TOOL_DEFINITIONS,
    TwinGuardTools,
    clarification_for_missing_fields,
)

SYSTEM_PROMPT = """You are TwinGuard's planning agent.

You propose inventory actions. You do not execute anything: every proposal is
first simulated on a digital twin and then decided by a deterministic policy
engine, so the decision is not yours to influence.

Rules:
- Call get_current_state before proposing, and use only the ids it returns.
- Never invent a product, warehouse, order or purchase order id.
- If the request is ambiguous, or you are missing information the user has not
  given you, say so in plain text instead of guessing. A question is always
  better than an invented number.
- Propose one action at a time with propose_action.
- After proposing, call validate_action, then run_simulation, then get_decision.
- If the decision is human_review, call request_human_review.
- Your final text message is a short explanation of the decision you were given.
  Explain the decision, the evidence behind it, the assumptions, the side effects
  and anything still unknown. Do not restate a different decision.
"""

MAX_STEPS = 10


class TwinGuardAgent:
    """Turns a natural-language request into a ``DecisionReport``."""

    def __init__(
        self,
        provider: LLMProvider,
        twin_manager: TwinManager,
        simulation_engine: SimulationEngine,
        policy_engine: PolicyEngine,
    ) -> None:
        self._provider = provider
        self._twin = twin_manager
        self._sim = simulation_engine
        self._policy = policy_engine
        #: The last tools object built, so the HTTP layer can read the human
        #: review queue and the snapshot id.
        self.last_tools: TwinGuardTools | None = None
        self.last_snapshot_id: str | None = None

    def handle_request(
        self, user_text: str, requester_role: Role
    ) -> DecisionReport | ClarificationRequest:
        # 1. Fresh snapshot. Never reused between requests, so two requests can
        #    never share scenario state.
        meta = self._twin.create_snapshot()
        snapshot_id = meta.snapshot_id
        self.last_snapshot_id = snapshot_id

        tools = TwinGuardTools(
            twin_manager=self._twin,
            simulation_engine=self._sim,
            policy_engine=self._policy,
            requester_role=requester_role,
        )
        tools.snapshot_id = snapshot_id
        self.last_tools = tools

        history: list[dict] = []
        pending_action: ProposedAction | None = None

        for _step in range(MAX_STEPS):
            response = self._provider.run_with_tools(
                system_prompt=SYSTEM_PROMPT,
                user_message=user_text,
                tools=TOOL_DEFINITIONS,
                conversation_history=history,
            )

            # 3. A plain-text answer with no tool call is either the final
            #    explanation (if a decision already exists) or a request for
            #    clarification. In the latter case we do not attempt to guess a
            #    ProposedAction from the text.
            if not response.is_tool_call:
                text = (response.text or "").strip()
                if tools.decision_report is not None:
                    # 6. The model explains a decision it did not make.
                    tools.decision_report.explanation = text
                    return tools.decision_report
                return ClarificationRequest(
                    question=text
                    or "Could you clarify which product and quantity you mean?",
                    missing_fields=[],
                    original_request=user_text,
                )

            call = response.tool_call
            assert call is not None
            arguments = self._coerce_arguments(call.name, call.arguments)
            observation, action, stop = self._run_tool(
                tools, call.name, arguments, pending_action, user_text
            )
            if stop is not None:
                return stop
            if action is not None:
                pending_action = action

            history.append(
                {
                    "role": "assistant",
                    "content": f"tool_call {call.name}",
                }
            )
            history.append(
                {
                    "role": "user",
                    "content": f"tool_result {call.name}: {observation}",
                }
            )
            # The follow-up turn just carries the tool result.
            user_text = f"Continue from the result of {call.name}."

        # Ran out of steps. If a decision was reached, finish it properly
        # (including its explanation); otherwise abstain rather than guess.
        if tools.decision_report is not None:
            return self.explain(tools.decision_report)
        return DecisionReport(
            decision=Decision.ABSTAIN,
            reasons=[
                f"the model did not reach a decision within {MAX_STEPS} tool steps"
            ],
            missing_evidence=["a complete propose -> simulate -> decide sequence"],
            simulation_report=None,
            data_quality_issues=[],
        )

    # -- internals -----------------------------------------------------------

    def _coerce_arguments(self, tool_name: str, arguments: Any) -> dict:
        """Accept either a full object or the scalar shorthand.

        Real models often emit ``{"action_type": "transfer_inventory",
        "parameters": {...}}`` but may also flatten. Anything that is not a
        mapping is a hard error rather than a guess.
        """
        if arguments is None:
            return {}
        if not isinstance(arguments, dict):
            raise ValueError(
                f"tool {tool_name!r} arguments must be a JSON object, got "
                f"{type(arguments).__name__}"
            )
        return arguments

    def _run_tool(
        self,
        tools: TwinGuardTools,
        tool_name: str,
        arguments: dict,
        pending_action: ProposedAction | None,
        user_text: str,
    ) -> tuple[str, ProposedAction | None, DecisionReport | ClarificationRequest | None]:
        """Run one tool call.

        Returns ``(observation, pending_action, early_return)``. A non-None
        ``early_return`` ends ``handle_request`` immediately.
        """
        if tool_name in READ_ONLY_TOOLS:
            # get_current_state / get_data_quality_report: pure reads.
            arguments.setdefault("snapshot_id", tools.snapshot_id)
            result = tools.dispatch(tool_name, arguments)
            return self._summarise(result), pending_action, None

        if tool_name == "propose_action":
            try:
                action = tools.propose_action(
                    action_type=arguments["action_type"],
                    parameters=arguments.get("parameters") or {},
                )
            except (KeyError, ValueError) as exc:
                return (
                    f"propose_action rejected the arguments: {exc}",
                    pending_action,
                    clarification_for_missing_fields(
                        original_request=user_text,
                        action_type=str(arguments.get("action_type", "unknown")),
                        result_errors=[str(exc)],
                        missing_fields=["action_type"]
                        if "action_type" not in arguments
                        else ["parameters"],
                    ),
                )
            # 4. Validate BEFORE any simulation runs.
            result = validate_parameters(action.action_type, action.parameters)
            if not result.valid:
                return (
                    f"validate_action: invalid. {'; '.join(result.errors)}",
                    None,
                    clarification_for_missing_fields(
                        original_request=user_text,
                        action_type=action.action_type.value,
                        result_errors=result.errors,
                        missing_fields=result.missing_fields,
                    ),
                )
            return (
                f"propose_action: {action.action_id} accepted and valid",
                action,
                None,
            )

        if tool_name == "validate_action":
            action = self._require_action(pending_action, tools, tool_name)
            valid = tools.dispatch(tool_name, {"action": action})
            return (f"validate_action: {valid}", pending_action, None)

        if tool_name == "run_simulation":
            action = self._require_action(pending_action, tools, tool_name)
            snapshot_id = arguments.get("snapshot_id") or tools.snapshot_id
            report = tools.run_simulation(
                snapshot_id=snapshot_id,
                action=action,
                horizon_days=int(arguments.get("horizon_days") or 14),
            )
            observation = (
                f"run_simulation: {report.simulation_id}; kpis="
                f"{ {k: v for k, v in sorted(report.kpis.items())} }; "
                f"violated_rules={report.violated_rules}; "
                f"side_effects={report.side_effects}"
            )
            return observation, pending_action, None

        if tool_name == "get_decision":
            action = self._require_action(pending_action, tools, tool_name)
            if tools.simulation_report is None:
                report = tools.run_simulation(
                    snapshot_id=tools.snapshot_id, action=action
                )
            else:
                report = tools.simulation_report
            decision_report = tools.get_decision(action, report)
            return (
                f"get_decision: {decision_report.decision.value} "
                f"{decision_report.reasons}",
                pending_action,
                None,
            )

        if tool_name == "request_human_review":
            action = self._require_action(pending_action, tools, tool_name)
            report = tools.decision_report
            if report is None:
                raise ValueError(
                    "request_human_review called before get_decision"
                )
            tools.request_human_review(action, report)
            return ("request_human_review: queued", pending_action, None)

        # Unknown tool: tell the model rather than guessing.
        return (
            f"unknown tool {tool_name!r}; available: "
            f"{[t['function']['name'] for t in TOOL_DEFINITIONS]}",
            pending_action,
            None,
        )

    @staticmethod
    def _require_action(
        pending_action: ProposedAction | None,
        tools: TwinGuardTools,
        tool_name: str,
    ) -> ProposedAction:
        action = pending_action or tools.action
        if action is None:
            raise ValueError(
                f"{tool_name} was called before a valid action was proposed"
            )
        return action

    @staticmethod
    def _summarise(result: Any) -> str:
        """Compact, deterministic observation text for the conversation."""
        if isinstance(result, list):
            if result and hasattr(result[0], "model_dump"):
                return f"{len(result)} issue(s): " + "; ".join(
                    f"{i.code} {i.entity_id}" for i in result
                )
            return f"{len(result)} item(s)"
        if isinstance(result, dict):
            return ", ".join(f"{name}={len(rows)}" for name, rows in result.items())
        return str(result)  # pragma: no cover

    def explain(self, report: DecisionReport) -> DecisionReport:
        """Step 6: ask the model to describe a decision it did not make.

        The returned text is stored on ``report.explanation``. It cannot change
        ``report.decision``; the model is handed the finished report and only
        writes prose.
        """
        if isinstance(report, ClarificationRequest):  # pragma: no cover
            return report
        prompt = (
            "Explain this decision in plain language for the person who asked. "
            "Cover the decision, the evidence behind it, the assumptions you had "
            "to make, the side effects, and anything still unknown.\n\n"
            f"{report.model_dump_json(indent=2)}"
        )
        response = self._provider.run_with_tools(
            system_prompt=SYSTEM_PROMPT,
            user_message=prompt,
            tools=[],
            conversation_history=[],
        )
        report.explanation = (response.text or "").strip()
        return report
