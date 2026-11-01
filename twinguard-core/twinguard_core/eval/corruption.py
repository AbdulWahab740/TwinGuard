"""Pure transformations from true to observed business data."""

from __future__ import annotations

import random
from datetime import timedelta

from ..models import SeedDataset

CORRUPTION_TAGS = (
    "stale_sync", "missing_po", "missing_customer_order", "duplicate_record",
    "conflicting_stock", "missing_lead_time",
)


def corrupt_dataset(true_dataset: SeedDataset, corruptions: list[str], seed: int = 0) -> SeedDataset:
    unknown = set(corruptions) - set(CORRUPTION_TAGS)
    if unknown:
        raise ValueError(f"unknown corruption tag(s): {sorted(unknown)}")
    observed = true_dataset.model_copy(deep=True)
    rng = random.Random(seed)
    for tag in corruptions:
        if tag == "stale_sync":
            for row in observed.inventory:
                row.last_synced_at -= timedelta(days=4)
                row.quantity_on_hand = max(0, row.quantity_on_hand - 20)
        elif tag == "missing_po" and observed.purchase_orders:
            observed.purchase_orders.pop(0)
        elif tag == "missing_customer_order" and observed.customer_orders:
            observed.customer_orders.pop(0)
        elif tag == "duplicate_record":
            observed.purchase_orders.append(observed.purchase_orders[0].model_copy(deep=True))
        elif tag == "conflicting_stock" and observed.inventory:
            row = observed.inventory[rng.randrange(len(observed.inventory))]
            row.quantity_on_hand += 37
            # A second row for the same slot exposes conflict to the current checks.
            observed.inventory.append(row.model_copy(deep=True, update={"quantity_on_hand": row.quantity_on_hand - 37}))
        elif tag == "missing_lead_time" and observed.suppliers:
            observed.suppliers[0].lead_time_days = None
    return observed
