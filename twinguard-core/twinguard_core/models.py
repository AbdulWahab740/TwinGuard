"""Pydantic models for every entity, action, report and decision in TwinGuard.

Field lists follow the AI + Digital Twin module spec, Section 2. That spec states
the lists are minimums and that fields may be added when a later section needs
them. The three additions made here are each required by a later section and are
documented at their definitions:

``PurchaseOrder.destination_warehouse_id``
    Section 5.2 step 2 receives purchase orders into a warehouse, but the Section 2
    ``PurchaseOrder`` has no warehouse field. Added as optional so a PO without it
    still validates; the simulator then routes it to the first warehouse.

``DecisionReport.explanation``
    Section 7.3 steps 6-7 attach an LLM-authored explanation to the report.

``DecisionReport.report_id`` / ``AuditEvent.decision_report_id``
    Required by the Backend spec's ``decision_reports`` and ``audit_events``
    tables. Both default to ``None`` and are set by the backend on persist.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    name: str
    unit_price: float


class Supplier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: str
    name: str
    # None = missing/unknown, must trigger data-quality failure.
    lead_time_days: Optional[int] = None


class Warehouse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse_id: str
    name: str
    capacity: int


class InventoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    warehouse_id: str
    quantity_on_hand: int
    quantity_reserved: int
    last_synced_at: datetime


class CustomerOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    product_id: str
    quantity: int
    priority: Literal["low", "normal", "high"]
    due_date: date
    status: Literal["open", "reserved", "fulfilled", "delayed"]


class PurchaseOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    po_id: str
    product_id: str
    supplier_id: str
    quantity: int
    expected_arrival: date
    status: Literal["pending", "received", "cancelled"]
    # Not in spec Section 2; see module docstring. Required for the simulator to
    # know where received stock lands. None => first warehouse in the snapshot.
    destination_warehouse_id: Optional[str] = None


class BusinessRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_transfer_quantity: int
    max_purchase_budget: float
    freshness_limit_hours: int
    allow_negative_inventory: bool = False


# ---------------------------------------------------------------------------
# Roles, actions, decisions
# ---------------------------------------------------------------------------


class Role(str, Enum):
    VIEWER = "viewer"
    PLANNER = "planner"
    MANAGER = "manager"
    AI_AGENT = "ai_agent"


class ActionType(str, Enum):
    TRANSFER_INVENTORY = "transfer_inventory"
    CREATE_PURCHASE_ORDER = "create_purchase_order"
    CHANGE_ORDER_PRIORITY = "change_order_priority"
    CANCEL_PURCHASE_ORDER = "cancel_purchase_order"


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    action_type: ActionType
    # Validated against a per-action-type schema in policy_engine.py
    parameters: dict
    proposed_by: Role


class SnapshotMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    source_version: int
    created_at: datetime
    freshness_status: Literal["fresh", "stale", "unknown"]


DataQualityCode = Literal[
    "missing_field",
    "stale_timestamp",
    "duplicate_record",
    "invalid_reference",
    "conflicting_quantity",
    "unsupported_status",
]


class DataQualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: DataQualityCode
    detail: str
    entity_id: str


class SimulationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int
    description: str
    entity_id: str


class SimulationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_id: str
    snapshot_id: str
    action: ProposedAction
    final_state_diff: dict
    side_effects: list[str]
    violated_rules: list[str]
    kpis: dict[str, float]
    warnings: list[str]
    trace: list[SimulationEvent]


class Decision(str, Enum):
    APPROVE = "approve"
    BLOCK = "block"
    HUMAN_REVIEW = "human_review"
    ABSTAIN = "abstain"


class DecisionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reasons: list[str]
    missing_evidence: list[str]
    simulation_report: Optional[SimulationReport] = None
    data_quality_issues: list[DataQualityIssue] = Field(default_factory=list)
    # Set by the backend on persist; see module docstring.
    report_id: Optional[str] = None
    # Section 7.3 step 6: the LLM explains a decision it did not make.
    explanation: Optional[str] = None


class ClarificationRequest(BaseModel):
    """Returned instead of a guessed ProposedAction (spec Section 7.3 step 3)."""

    model_config = ConfigDict(extra="forbid")

    question: str
    missing_fields: list[str] = Field(default_factory=list)
    original_request: str


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str
    timestamp: datetime
    request_text: Optional[str] = None
    proposed_action: Optional[ProposedAction] = None
    snapshot_id: Optional[str] = None
    decision_report: Optional[DecisionReport] = None
    approved_by: Optional[str] = None
    executed: bool = False
    # Set by the backend on persist; see module docstring.
    decision_report_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Snapshot payload
# ---------------------------------------------------------------------------

#: Keys of the dict returned by ``TwinManager.get_snapshot_state`` and stored
#: in the ``snapshot_*`` tables by the backend.
SNAPSHOT_COLLECTIONS: tuple[str, ...] = (
    "products",
    "suppliers",
    "warehouses",
    "inventory",
    "customer_orders",
    "purchase_orders",
)


class SeedDataset(BaseModel):
    """A complete starting state produced by ``seed_data.py``."""

    model_config = ConfigDict(extra="forbid")

    products: list[Product]
    suppliers: list[Supplier]
    warehouses: list[Warehouse]
    inventory: list[InventoryRecord]
    customer_orders: list[CustomerOrder]
    purchase_orders: list[PurchaseOrder]
    business_rules: BusinessRules

    def to_state(self) -> dict:
        """The entity part of a snapshot state (no business rules)."""
        return {name: list(getattr(self, name)) for name in SNAPSHOT_COLLECTIONS}
