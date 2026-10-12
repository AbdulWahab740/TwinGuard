"""TwinGuard AI + Digital Twin module.

Pure Python: no database, no web framework. Talks only to the :class:`DataStore`
interface, so the backend can swap in :class:`SqlDataStore` without changing a
line of the twin, simulation, policy or agent logic.

Public surface:

``models``            all entities, actions, reports, decisions
``data_store``        ``DataStore`` interface + ``InMemoryDataStore``
``seed_data``         fixed, seeded demo datasets
``twin_manager``      snapshot create / get / reset / diff, data-quality checks
``simulation_engine`` deterministic day-stepped simulator
``policy_engine``     roles, parameter schemas, the decision classifier
``agent``             LLM provider adapter, tools, orchestration loop
"""

from .data_store import DataStore, InMemoryDataStore
from .models import (
    SNAPSHOT_COLLECTIONS,
    ActionType,
    AuditEvent,
    BusinessRules,
    ClarificationRequest,
    CustomerOrder,
    DataQualityIssue,
    Decision,
    DecisionReport,
    InventoryRecord,
    Product,
    ProposedAction,
    PurchaseOrder,
    Role,
    SeedDataset,
    SimulationEvent,
    SimulationReport,
    SnapshotMeta,
    Supplier,
    Warehouse,
)
from .policy_engine import PolicyEngine, classify, is_permitted, validate_action
from .seed_data import demo_baseline, demo_stale, get_dataset, seed_dataset
from .simulation_engine import SimulationEngine
from .twin_manager import TwinManager

__all__ = [
    "SNAPSHOT_COLLECTIONS",
    "ActionType",
    "AuditEvent",
    "BusinessRules",
    "ClarificationRequest",
    "CustomerOrder",
    "DataQualityIssue",
    "DataStore",
    "Decision",
    "DecisionReport",
    "InMemoryDataStore",
    "InventoryRecord",
    "PolicyEngine",
    "Product",
    "ProposedAction",
    "PurchaseOrder",
    "Role",
    "SeedDataset",
    "SimulationEngine",
    "SimulationEvent",
    "SimulationReport",
    "SnapshotMeta",
    "Supplier",
    "TwinManager",
    "Warehouse",
    "classify",
    "demo_baseline",
    "demo_stale",
    "get_dataset",
    "is_permitted",
    "seed_dataset",
    "validate_action",
]

__version__ = "0.1.0"
