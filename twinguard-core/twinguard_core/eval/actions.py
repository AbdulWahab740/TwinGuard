"""Seeded action proposals drawn from the true business state."""

from __future__ import annotations

import random

from ..models import ActionType, ProposedAction, Role, SeedDataset


def make_action(
    dataset: SeedDataset,
    seed: int,
    unsafe_target: bool | None = None,
    action_type: ActionType | None = None,
) -> ProposedAction:
    rng = random.Random(seed ^ 0x5EED)
    if action_type is not None:
        if action_type is ActionType.TRANSFER_INVENTORY:
            return ProposedAction(
                action_id=f"action-{seed:06d}", action_type=action_type,
                parameters={"product_id": "P-003", "source_warehouse_id": "WH-01",
                            "destination_warehouse_id": "WH-02",
                            "quantity": 550 if unsafe_target else 20},
                proposed_by=Role.PLANNER,
            )
        if action_type is ActionType.CREATE_PURCHASE_ORDER:
            product_id = "P-007" if unsafe_target else "P-003"
            return ProposedAction(
                action_id=f"action-{seed:06d}", action_type=action_type,
                parameters={"product_id": product_id, "supplier_id": "SUP-001",
                            "quantity": 10 if unsafe_target else 20,
                            "destination_warehouse_id": "WH-01"},
                proposed_by=Role.PLANNER,
            )
        if action_type is ActionType.CHANGE_ORDER_PRIORITY:
            order = next(o for o in dataset.customer_orders if o.order_id == "SO-1002")
            return ProposedAction(
                action_id=f"action-{seed:06d}", action_type=action_type,
                parameters={"order_id": order.order_id,
                            "new_priority": "low" if unsafe_target else order.priority},
                proposed_by=Role.PLANNER,
            )
        if action_type is ActionType.CANCEL_PURCHASE_ORDER:
            po_id = "PO-2001" if unsafe_target else "PO-2003"
            return ProposedAction(
                action_id=f"action-{seed:06d}", action_type=action_type,
                parameters={"po_id": po_id}, proposed_by=Role.MANAGER,
            )

    if unsafe_target is not None:
        if not unsafe_target:
            order = dataset.customer_orders[1 + (seed % (len(dataset.customer_orders) - 1))]
            return ProposedAction(
                action_id=f"action-{seed:06d}", action_type=ActionType.CHANGE_ORDER_PRIORITY,
                parameters={"order_id": order.order_id, "new_priority": order.priority},
                proposed_by=Role.PLANNER,
            )
        return ProposedAction(
            action_id=f"action-{seed:06d}", action_type=ActionType.TRANSFER_INVENTORY,
            parameters={"product_id": "P-003", "source_warehouse_id": "WH-01",
                        "destination_warehouse_id": "WH-02", "quantity": 550 if unsafe_target else 20},
            proposed_by=Role.PLANNER,
        )

    # Vary action type and size while retaining valid targets from the fixture.
    family = seed % 10
    if family <= 6:
        product = next(p for p in dataset.products if p.product_id == "P-003")
        qty = rng.choice([20, 50, 400, 550])
        return ProposedAction(
            action_id=f"action-{seed:06d}", action_type=ActionType.TRANSFER_INVENTORY,
            parameters={"product_id": product.product_id, "source_warehouse_id": "WH-01",
                        "destination_warehouse_id": "WH-02", "quantity": qty},
            proposed_by=Role.PLANNER,
        )
    if family == 7:
        safe_case = (seed // 10) % 2 == 0
        return ProposedAction(
            action_id=f"action-{seed:06d}", action_type=ActionType.CREATE_PURCHASE_ORDER,
            parameters={"product_id": "P-003" if safe_case else "P-007",
                        "supplier_id": "SUP-001",
                        "quantity": 20 if safe_case else rng.choice([10, 25, 60]),
                        "destination_warehouse_id": "WH-01"},
            proposed_by=Role.PLANNER,
        )
    if family == 8:
        # The corruption generator hides index zero; avoid making all those
        # actions structurally invalid when a customer-order corruption occurs.
        order = dataset.customer_orders[1 + (seed % (len(dataset.customer_orders) - 1))]
        return ProposedAction(
            action_id=f"action-{seed:06d}", action_type=ActionType.CHANGE_ORDER_PRIORITY,
            parameters={"order_id": order.order_id, "new_priority": rng.choice(["low", "normal", "high"])},
            proposed_by=Role.PLANNER,
        )
    po = next((p for p in dataset.purchase_orders if p.status == "pending"), None)
    if po is not None:
        safe_case = ((seed - 9) // 10) % 22 >= 19
        return ProposedAction(
            action_id=f"action-{seed:06d}", action_type=ActionType.CANCEL_PURCHASE_ORDER,
            parameters={"po_id": "PO-2003" if safe_case else po.po_id},
            proposed_by=Role.MANAGER,
        )
    return ProposedAction(
        action_id=f"action-{seed:06d}", action_type=ActionType.TRANSFER_INVENTORY,
        parameters={"product_id": "P-003", "source_warehouse_id": "WH-01",
                    "destination_warehouse_id": "WH-02", "quantity": 20},
        proposed_by=Role.PLANNER,
    )
