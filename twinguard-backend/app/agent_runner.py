"""Runs the core agent for one request and persists the outcome.

Never touches the write path: this module has no reference to the store's apply method.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from datetime import datetime, timezone

from twinguard_core import (ClarificationRequest, Decision, DecisionReport, PolicyEngine,
                            Role, SimulationEngine, TwinManager)
from twinguard_core.agent import TwinGuardAgent
from twinguard_core.agent.provider import (FakeLLMProvider, LLMProvider, OpenAICompatibleProvider,
                                           text_response, tool_response)

from .audit import record
from .db_store import DbDataStore
from .review import auto_apply

ROLE_MAP = {"planner": Role.PLANNER, "manager": Role.MANAGER}
ACTIONS = {  # keyword in the request text -> scripted proposal (fake LLM only)
    "cancel": ("cancel_purchase_order", {"po_id": "PO-2001"}),
    "order": ("create_purchase_order",
              {"product_id": "P-007", "supplier_id": "SUP-001", "quantity": 500}),
    "transfer": ("transfer_inventory",
                 {"product_id": "P-003", "source_warehouse_id": "WH-01",
                  "destination_warehouse_id": "WH-02", "quantity": 50}),
}


def demo_provider(text: str) -> LLMProvider:
    """Scripted, offline. Unknown requests get a clarifying question."""
    for word, (kind, params) in ACTIONS.items():
        if word in text.lower():
            return FakeLLMProvider(script=[
                tool_response("get_current_state"),
                tool_response("propose_action", action_type=kind, parameters=params),
                tool_response("validate_action"),
                tool_response("run_simulation"),
                tool_response("get_decision"),
                text_response("Decision explained from the simulation and data checks above."),
            ])
    return FakeLLMProvider(script=[text_response("Which product and how many units do you mean?")])


def make_provider(settings, text: str) -> LLMProvider:
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider.from_env()
    return demo_provider(text)


class RecordingProvider(LLMProvider):
    """Wraps a provider to capture the tool calls as visible agent steps."""

    def __init__(self, inner: LLMProvider) -> None:
        self.inner, self.steps = inner, []

    def run_with_tools(self, system_prompt, user_message, tools, conversation_history):
        r = self.inner.run_with_tools(system_prompt, user_message, tools, conversation_history)
        if r.is_tool_call:
            self.steps.append({"tool": r.tool_call.name, "summary": str(r.tool_call.arguments)[:200]})
        return r


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(msg: str, settings) -> str:
    for secret in (settings.supabase_key, settings.jwt_secret, os.getenv("LLM_API_KEY", "")):
        if secret:
            msg = msg.replace(secret, "***")
    return msg


def _handle(rec: RecordingProvider, db, text: str, role: Role):
    store = DbDataStore(db)
    twin = TwinManager(store)
    # Fresh agent per request: it keeps per-request state (last_tools, last_snapshot_id).
    agent = TwinGuardAgent(rec, twin, SimulationEngine(twin), PolicyEngine(store.get_business_rules()))
    result = agent.handle_request(text, role)
    version = twin.get_snapshot_meta(agent.last_snapshot_id).source_version
    action = agent.last_tools.action if agent.last_tools else None
    return result, version, action


def run_request(request_id: str, db, settings, provider: LLMProvider | None = None) -> None:
    row = db.select("requests", id=request_id)[0]
    rec = RecordingProvider(provider or make_provider(settings, row["text"]))
    done = lambda **v: db.update("requests", v, id=request_id)
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(_handle, rec, db, row["text"], ROLE_MAP[row["created_by_role"]])
        # shortcut: a timed-out agent thread keeps running in the background; fine for demo scale
        result, version, action = fut.result(timeout=settings.agent_timeout_s)
        db.insert("agent_steps", [{"request_id": request_id, "idx": i, **s}
                                  for i, s in enumerate(rec.steps)])
        if isinstance(result, ClarificationRequest):
            done(status="needs_clarification", clarification=result.question)
            return
        decision = "human_review" if result.decision in (Decision.ABSTAIN, Decision.HUMAN_REVIEW) \
            else result.decision.value
        if action is None:
            done(status="failed", error="; ".join(result.reasons) or "agent reached no decision")
            record(db, request_id, "failed", "system", {"reasons": result.reasons})
            return
        action_json = action.model_dump(mode="json")
        record(db, request_id, "action_proposed", "agent", {"action": action_json})
        record(db, request_id, "decision", "system", {"decision": decision, "reasons": result.reasons})
        status = "decided"
        gate_ok = decision == "approve"
        if gate_ok:
            record(db, request_id, "approved_by_gate", "system", {})
        if decision == "human_review" or (gate_ok and not settings.allow_auto_apply):
            db.insert("review_tasks", [{"request_id": request_id, "status": "pending",
                                        "action": action_json, "reasons": result.reasons,
                                        "created_at": _now()}])
            record(db, request_id, "review_created", "system", {})
            status = "needs_review"
        done(status=status, decision=decision, action=action_json, snapshot_version=version,
             report=result.model_dump(mode="json"), explanation=result.explanation)
        if gate_ok and settings.allow_auto_apply:
            auto_apply(db, request_id)
    except FutTimeout:
        done(status="failed", error=f"agent timed out after {settings.agent_timeout_s}s")
        record(db, request_id, "failed", "system", {"error": "timeout"})
    except Exception as e:  # noqa: BLE001 - any agent failure must end as a readable `failed`
        msg = _redact(f"{type(e).__name__}: {e}", settings)[:300]
        done(status="failed", error=msg)
        record(db, request_id, "failed", "system", {"error": msg})
    finally:
        ex.shutdown(wait=False)
