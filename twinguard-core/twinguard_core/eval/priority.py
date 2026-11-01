"""Counterfactual helpers for evaluating priority changes."""

from __future__ import annotations

from ..models import ProposedAction, SeedDataset, SimulationReport


def noop_priority_action(dataset: SeedDataset, action: ProposedAction) -> ProposedAction:
    order_id = action.parameters["order_id"]
    order = next(
        (order for order in dataset.customer_orders if order.order_id == order_id),
        None,
    )
    if order is None:
        raise ValueError(f"Cannot build priority no-op baseline: order {order_id} is missing")
    baseline = action.model_copy(deep=True)
    baseline.parameters["new_priority"] = order.priority
    return baseline


def compare_priority_outcomes(
    dataset: SeedDataset,
    action_report: SimulationReport,
    baseline_report: SimulationReport,
) -> tuple[bool, list[str], list[str], bool]:
    """Return regression, new rules, newly delayed orders, and capacity increase."""
    new_violations = sorted(
        set(action_report.violated_rules) - set(baseline_report.violated_rules)
    )
    action_statuses = _final_order_statuses(dataset, action_report)
    baseline_statuses = _final_order_statuses(dataset, baseline_report)
    newly_delayed_orders = sorted(
        order_id
        for order_id, status in action_statuses.items()
        if status == "delayed" and baseline_statuses.get(order_id) != "delayed"
    )
    action_kpis, baseline_kpis = action_report.kpis, baseline_report.kpis
    capacity_worsened = (
        action_kpis.get("max_warehouse_capacity_pct", 0)
        > baseline_kpis.get("max_warehouse_capacity_pct", 0)
    )
    worsened = (
        bool(new_violations)
        or bool(newly_delayed_orders)
        or action_kpis.get("stockout_count", 0) > baseline_kpis.get("stockout_count", 0)
        or action_kpis.get("orders_delayed", 0) > baseline_kpis.get("orders_delayed", 0)
        or action_kpis.get("orders_fulfilled", 0) < baseline_kpis.get("orders_fulfilled", 0)
    )
    return worsened, new_violations, newly_delayed_orders, capacity_worsened


def _final_order_statuses(
    dataset: SeedDataset, report: SimulationReport
) -> dict[str, str]:
    statuses = {order.order_id: order.status for order in dataset.customer_orders}
    for order_id, diff in report.final_state_diff.get("customer_orders", {}).items():
        status_change = diff.get("changed", {}).get("status")
        if status_change:
            statuses[order_id] = status_change["b"]
    return statuses
