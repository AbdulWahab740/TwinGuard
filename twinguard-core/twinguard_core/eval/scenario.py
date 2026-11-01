"""Scenario construction. Ground truth and observed data are separate copies."""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta, timezone
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..models import (
    ActionType,
    CustomerOrder,
    InventoryRecord,
    Product,
    ProposedAction,
    PurchaseOrder,
    SeedDataset,
    Warehouse,
)
from ..seed_data import demo_baseline
from .actions import make_action
from .corruption import CORRUPTION_TAGS, corrupt_dataset


class Scenario(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    scenario_id: str
    seed: int
    true_dataset: SeedDataset
    observed_dataset: SeedDataset
    action: ProposedAction
    corruptions: list[str]
    label: Literal["safe", "unsafe"] | None = None


def make_scenario(
    seed: int,
    corruptions: list[str] | None = None,
    *,
    unsafe_target: bool | None = None,
    action_type: ActionType | None = None,
    vary_business: bool = True,
) -> Scenario:
    rng = random.Random(seed)
    # Pin the anchor to a day so repeated calls, including across midnight, are
    # byte-identical while remaining fresh for today's data-quality checks.
    anchor = datetime.combine(datetime.now(timezone.utc).date(), time(0), timezone.utc)
    true = demo_baseline(now=anchor)
    effective_action_type = action_type or (
        ActionType.TRANSFER_INVENTORY if seed % 10 <= 6
        else ActionType.CREATE_PURCHASE_ORDER if seed % 10 == 7
        else ActionType.CHANGE_ORDER_PRIORITY if seed % 10 == 8
        else ActionType.CANCEL_PURCHASE_ORDER
    )
    safe_cancel = (
        unsafe_target is False if action_type is not None
        else ((seed - 9) // 10) % 22 >= 19
    )
    safe_po = (
        unsafe_target is False if action_type is not None
        else (seed // 10) % 2 == 0
    )
    vary_business = vary_business and not (
        (effective_action_type is ActionType.CANCEL_PURCHASE_ORDER and safe_cancel)
        or (effective_action_type is ActionType.CREATE_PURCHASE_ORDER and safe_po)
    )
    # Vary the business using a private RNG; seed 42 remains the recognizable
    # demo_baseline case, while scenario ids reproducibly select other values.
    if seed != 42 and vary_business:
        for row in true.inventory:
            row.quantity_on_hand = max(0, row.quantity_on_hand + rng.randint(-35, 35))
        for order in true.customer_orders:
            order.quantity = max(1, order.quantity + rng.randint(-30, 30))
        for supplier in true.suppliers:
            supplier.lead_time_days = rng.randint(2, 14)
        # Keep the baseline's deliberately unknown third supplier known.
        # Vary facility size/count and assortment across the seeded business pool.
        true.warehouses[1].capacity = rng.randint(950, 1450)
        if seed % 3 == 0:
            true.warehouses.append(Warehouse(
                warehouse_id="WH-03", name=f"Regional Hub {seed % 7 + 1}",
                capacity=rng.randint(900, 2600),
            ))
            for product in true.products[:rng.randint(3, 8)]:
                true.inventory.append(InventoryRecord(
                    product_id=product.product_id, warehouse_id="WH-03",
                    quantity_on_hand=rng.randint(15, 130), quantity_reserved=0,
                    last_synced_at=anchor,
                ))
        for extra in range(seed % 5):
            product_id = f"P-X{extra + 1:02d}"
            true.products.append(Product(product_id=product_id, name=f"Variant {seed % 13}-{extra + 1}",
                                         unit_price=round(rng.uniform(1, 85), 2)))
            for warehouse in true.warehouses[: 2 if seed % 2 else len(true.warehouses)]:
                true.inventory.append(InventoryRecord(
                    product_id=product_id, warehouse_id=warehouse.warehouse_id,
                    quantity_on_hand=rng.randint(20, 240), quantity_reserved=0,
                    last_synced_at=anchor,
                ))

    if effective_action_type is ActionType.CANCEL_PURCHASE_ORDER and safe_cancel:
        true.purchase_orders.append(PurchaseOrder(
            po_id="PO-2003", product_id="P-003", supplier_id="SUP-001",
            quantity=20, expected_arrival=anchor.date() + timedelta(days=4),
            status="pending", destination_warehouse_id="WH-01",
        ))
        for row in true.inventory:
            if row.product_id == "P-003" and row.warehouse_id == "WH-01":
                row.quantity_on_hand = 1000
    if effective_action_type is ActionType.CANCEL_PURCHASE_ORDER and not safe_cancel:
        for row in true.inventory:
            if row.product_id == "P-007":
                row.quantity_on_hand = 0
    unsafe_priority_target = (
        effective_action_type is ActionType.CHANGE_ORDER_PRIORITY
        and (
            unsafe_target is True
            or (action_type is None and seed % 100 == 8)
        )
    )
    if unsafe_priority_target:
        target = next(order for order in true.customer_orders if order.order_id == "SO-1002")
        target.priority = "high"
        target.quantity = 40
        for row in true.inventory:
            if row.product_id == "P-004":
                row.quantity_on_hand = 0
                if row.warehouse_id == "WH-01":
                    row.quantity_on_hand = 60
        true.customer_orders.append(CustomerOrder(
            order_id="SO-1000", product_id="P-004", quantity=40,
            priority="normal", due_date=target.due_date,
            status="open",
        ))

    tags = list(corruptions or [])
    observed = corrupt_dataset(true, tags, seed=seed)
    action = make_action(
        true, seed, unsafe_target=unsafe_target, action_type=action_type,
    )
    if unsafe_priority_target:
        action.parameters["new_priority"] = "low"
    return Scenario(
        scenario_id=f"scenario-{seed:06d}", seed=seed,
        true_dataset=true, observed_dataset=observed,
        action=action, corruptions=tags,
    )


def build_scenarios(count: int = 500, seed: int = 42, *, action_profile: str = "mixed") -> list[Scenario]:
    if count < 1:
        raise ValueError("count must be at least 1")
    pairs = list(combinations(CORRUPTION_TAGS, 2))
    groups = [[]]
    groups.extend([[tag] for tag in CORRUPTION_TAGS])
    groups.extend([list(pair) for pair in pairs])
    scenarios = []
    for index in range(count):
        # Cycling the 22 groups makes them exactly equal whenever count is a
        # multiple of 22 (for example, 3,080 gives 140 scenarios per group).
        tags = groups[index % len(groups)]
        unsafe_target = None
        action_type = None
        if action_profile == "ten_percent_unsafe":
            action_type = list(ActionType)[index % len(ActionType)]
            action_type_index = index % len(ActionType)
            action_ordinal = index // len(ActionType)
            action_count = len(range(action_type_index, count, len(ActionType)))
            unsafe_total = count // 10
            unsafe_per_type, remainder = divmod(unsafe_total, len(ActionType))
            unsafe_per_type += int(action_type_index < remainder)
            unsafe_target = (
                action_ordinal * unsafe_per_type // action_count
                < (action_ordinal + 1) * unsafe_per_type // action_count
            )
        elif action_profile != "mixed":
            raise ValueError(f"unknown action profile: {action_profile}")
        scenarios.append(make_scenario(
            seed + index, tags, unsafe_target=unsafe_target, action_type=action_type,
            vary_business=action_profile != "ten_percent_unsafe",
        ))
    return scenarios
