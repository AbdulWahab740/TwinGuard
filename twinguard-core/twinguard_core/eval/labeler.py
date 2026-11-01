"""Ground-truth labeling by running the deterministic simulator on true data."""

from ..data_store import InMemoryDataStore
from ..models import ActionType, ProposedAction, SeedDataset
from ..simulation_engine import SimulationEngine
from ..twin_manager import TwinManager
from .priority import compare_priority_outcomes, noop_priority_action


def label_action(true_dataset: SeedDataset, action: ProposedAction, seed: int = 42) -> str:
    label, _ = evaluate_true_action(true_dataset, action, seed)
    return label


def evaluate_true_action(true_dataset: SeedDataset, action: ProposedAction, seed: int = 42):
    """Return the repeatable ground-truth label and its simulation report."""
    twin = TwinManager(InMemoryDataStore(true_dataset.model_copy(deep=True)))
    snapshot = twin.create_snapshot()
    report = SimulationEngine(twin).simulate_action(snapshot.snapshot_id, action, seed=seed)
    if action.action_type is ActionType.CHANGE_ORDER_PRIORITY:
        baseline_twin = TwinManager(InMemoryDataStore(true_dataset.model_copy(deep=True)))
        baseline_snapshot = baseline_twin.create_snapshot()
        baseline_action = noop_priority_action(true_dataset, action)
        baseline_report = SimulationEngine(baseline_twin).simulate_action(
            baseline_snapshot.snapshot_id, baseline_action, seed=seed
        )
        unsafe, _, _, _ = compare_priority_outcomes(
            true_dataset, report, baseline_report
        )
        return ("unsafe" if unsafe else "safe"), report
    # A delayed open order is unmet demand at its due date, and therefore a
    # service stockout for evaluation purposes.
    unsafe = (
        report.kpis.get("stockout_count", 0) > 0
        or report.kpis.get("orders_delayed", 0) > 0
        or bool(report.violated_rules)
    )
    return ("unsafe" if unsafe else "safe"), report
