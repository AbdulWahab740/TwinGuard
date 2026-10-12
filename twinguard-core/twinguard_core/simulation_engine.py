"""Deterministic, day-stepped simulation of a proposed action.

Hard guarantees
---------------
* **Never writes to the live data store.** Every run works on
  ``TwinManager.get_snapshot_state()``, which is a deep copy.
* **Deterministic.** Two runs with the same ``snapshot_id``, ``action``,
  ``horizon_days`` and ``seed`` produce byte-identical reports, including the
  ``simulation_id``, which is therefore derived from a hash of the inputs rather
  than from a call counter. The only date the run reads is its own start date,
  taken from the snapshot's ``created_at``, never from the wall clock.
* ``seed`` is not decorative: it seeds ``random.Random(seed)``, which draws one
  tie-break score per open order. When several due orders compete for the same
  stock that score decides who is served first. Priority is always the primary
  key, so a different seed changes *who* loses a contended unit, never whether
  high-priority demand gets served first.

Day loop (spec Section 5.2, fixed order, one event per day):
    1. demand falls due for open customer orders
    2. purchase orders whose ``expected_arrival`` is today are received
    3. reserve available stock for due orders where possible
    4. the proposed action's direct effect (applied on day 1 only)
    5. update stock and order status
    6. check warehouse capacity
    7. detect side effects and rule violations
    8. append one ``SimulationEvent`` to ``trace``

The action is applied on day 1 because it is being proposed *now*; applying it on
every day of the horizon would be meaningless.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .models import (
    ActionType,
    BusinessRules,
    CustomerOrder,
    InventoryRecord,
    Product,
    ProposedAction,
    PurchaseOrder,
    SimulationEvent,
    SimulationReport,
)
from .twin_manager import TwinManager, diff_entity_states

_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}


@dataclass
class _Reservations:
    """Where a given order's reserved units sit, per warehouse."""

    slots: list[tuple[str, int]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(qty for _, qty in self.slots)

    def release(self) -> list[tuple[str, int]]:
        taken = list(self.slots)
        self.slots = []
        return taken


@dataclass
class _Context:
    """Mutable working state for one simulation run."""

    state: dict
    rules: BusinessRules
    start_date: date
    horizon_days: int
    rng: random.Random
    sync_anchor: datetime | None = None
    tiebreak: dict[str, float] = field(default_factory=dict)
    remaining: dict[str, int] = field(default_factory=dict)
    reservations: dict[str, _Reservations] = field(default_factory=dict)
    initially_delayed: set[str] = field(default_factory=set)
    stockouts: set[str] = field(default_factory=set)
    violated_rules: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    budget_used: float = 0.0
    po_counter: int = 0
    peak_capacity_pct: dict[str, float] = field(default_factory=dict)

    # -- lookups ------------------------------------------------------------

    def product(self, product_id: str) -> Product:
        for p in self.state["products"]:
            if p.product_id == product_id:
                return p
        raise ValueError(f"unknown product {product_id!r} in snapshot")

    def price(self, product_id: str) -> float:
        return self.product(product_id).unit_price

    def order(self, order_id: str) -> CustomerOrder:
        for o in self.state["customer_orders"]:
            if o.order_id == order_id:
                return o
        raise ValueError(f"unknown customer order {order_id!r} in snapshot")

    def inventory_row(
        self, product_id: str, warehouse_id: str
    ) -> InventoryRecord | None:
        for row in self.state["inventory"]:
            if row.product_id == product_id and row.warehouse_id == warehouse_id:
                return row
        return None

    def ensure_inventory_row(self, product_id: str, warehouse_id: str) -> InventoryRecord:
        row = self.inventory_row(product_id, warehouse_id)
        if row is None:
            row = InventoryRecord(
                product_id=product_id,
                warehouse_id=warehouse_id,
                quantity_on_hand=0,
                quantity_reserved=0,
                last_synced_at=self.sync_anchor,
            )
            self.state["inventory"].append(row)
        return row

    def available(self, product_id: str) -> int:
        return sum(
            max(0, row.quantity_on_hand - row.quantity_reserved)
            for row in self.state["inventory"]
            if row.product_id == product_id
        )

    def warehouse_ids(self) -> list[str]:
        return [w.warehouse_id for w in self.state["warehouses"]]

    def default_warehouse(self) -> str:
        warehouses = self.state["warehouses"]
        if not warehouses:
            raise ValueError("snapshot has no warehouses")
        return warehouses[0].warehouse_id

    # -- deduped collectors -------------------------------------------------

    def violate(self, message: str) -> None:
        if message not in self.violated_rules:
            self.violated_rules.append(message)

    def side_effect(self, message: str) -> None:
        if message not in self.side_effects:
            self.side_effects.append(message)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def outstanding(self) -> list[CustomerOrder]:
        """Orders still owing stock, most important first."""
        pending = [
            o
            for o in self.state["customer_orders"]
            if o.order_id in self.remaining and self.remaining[o.order_id] > 0
        ]
        pending.sort(
            key=lambda o: (
                _PRIORITY_RANK[o.priority],
                self.tiebreak.get(o.order_id, 0.0),
                o.order_id,
            )
        )
        return pending


class SimulationEngine:
    """Runs :meth:`simulate_action` against a twin snapshot."""

    def __init__(self, twin_manager: TwinManager) -> None:
        self._twin = twin_manager

    # -- public API ---------------------------------------------------------

    def simulate_action(
        self,
        snapshot_id: str,
        action: ProposedAction,
        horizon_days: int = 14,
        seed: int = 42,
    ) -> SimulationReport:
        if horizon_days < 1:
            raise ValueError("horizon_days must be >= 1")

        meta = self._twin.get_snapshot_meta(snapshot_id)
        initial_state = self._twin.get_snapshot_state(snapshot_id)
        rules = self._twin.get_business_rules()

        rng = random.Random(seed)
        ctx = _Context(
            state=initial_state,
            rules=rules,
            start_date=meta.created_at.date(),
            horizon_days=horizon_days,
            rng=rng,
            sync_anchor=self._twin.get_sync_anchor(snapshot_id),
        )
        for order in initial_state["customer_orders"]:
            ctx.tiebreak[order.order_id] = rng.random()
            ctx.remaining[order.order_id] = order.quantity
            ctx.reservations[order.order_id] = _Reservations()
            if order.status == "delayed":
                ctx.initially_delayed.add(order.order_id)

        ctx.warn(
            f"simulation horizon {horizon_days} days from "
            f"{ctx.start_date.isoformat()}, seed {seed}"
        )
        self._precheck_action(ctx, action)

        trace: list[SimulationEvent] = []
        for day in range(1, horizon_days + 1):
            current_date = ctx.start_date + timedelta(days=day - 1)
            notes: list[str] = []
            focus = ""

            # 1. demand falls due
            due_today = self._step_demand(ctx, current_date)
            if due_today:
                notes.append(f"demand due for {len(due_today)} order(s)")

            # 2. receive purchase orders
            received = self._step_receive(ctx, current_date)
            if received:
                notes.append(
                    "received " + ", ".join(f"{po.po_id}({po.quantity})" for po in received)
                )
                focus = focus or received[0].po_id

            # 3. reserve stock
            reserved_notes = self._step_reserve(ctx, current_date)
            notes.extend(reserved_notes)

            # 4. the action's direct effect
            if day == 1:
                action_note, focus = self._apply_action(ctx, action, current_date)
                if action_note:
                    notes.append(action_note)
                    focus = focus or action.action_id

            # 5. update stock and order status
            status_notes, focus = self._step_update(ctx, current_date, focus)
            notes.extend(status_notes)

            # 6. warehouse capacity
            notes.extend(self._step_capacity(ctx))

            # 7. side effects and rule violations
            self._step_detect(ctx)

            description = "; ".join(notes) if notes else "no change"
            trace.append(
                SimulationEvent(day=day, description=description, entity_id=focus)
            )

        kpis = self._compute_kpis(ctx, action)
        final_diff = diff_entity_states(
            self._twin.get_snapshot_state(snapshot_id), ctx.state
        )
        return SimulationReport(
            simulation_id=self._simulation_id(snapshot_id, action, horizon_days, seed),
            snapshot_id=snapshot_id,
            action=action.model_copy(deep=True),
            final_state_diff=final_diff,
            side_effects=list(ctx.side_effects),
            violated_rules=list(ctx.violated_rules),
            kpis=kpis,
            warnings=list(ctx.warnings),
            trace=trace,
        )

    # -- step 0: cheap pre-checks ------------------------------------------

    def _precheck_action(self, ctx: _Context, action: ProposedAction) -> None:
        """Violations detectable before day 1 (spec Section 5.3, no timing)."""
        params = action.parameters
        if action.action_type is ActionType.TRANSFER_INVENTORY:
            quantity = int(params["quantity"])
            if quantity > ctx.rules.max_transfer_quantity:
                ctx.violate(
                    f"transfer quantity {quantity} exceeds max_transfer_quantity "
                    f"{ctx.rules.max_transfer_quantity}"
                )
            if params["source_warehouse_id"] == params["destination_warehouse_id"]:
                ctx.violate(
                    "source and destination warehouse are the same "
                    f"({params['source_warehouse_id']})"
                )
        elif action.action_type is ActionType.CREATE_PURCHASE_ORDER:
            value = int(params["quantity"]) * ctx.price(params["product_id"])
            if value > ctx.rules.max_purchase_budget:
                ctx.violate(
                    f"purchase value {value:.2f} exceeds max_purchase_budget "
                    f"{ctx.rules.max_purchase_budget:.2f}"
                )
            lead = next(
                (
                    s.lead_time_days
                    for s in ctx.state["suppliers"]
                    if s.supplier_id == params["supplier_id"]
                ),
                None,
            )
            if lead is None:
                ctx.warn(
                    f"supplier {params['supplier_id']} has no known lead time; the "
                    "new purchase order is assumed to arrive after the horizon"
                )

    # -- step 1 --------------------------------------------------------------

    def _step_demand(self, ctx: _Context, current_date: date) -> list[CustomerOrder]:
        due = [
            o
            for o in ctx.state["customer_orders"]
            if o.order_id in ctx.remaining
            and ctx.remaining[o.order_id] > 0
            and o.due_date <= current_date
        ]
        return sorted(due, key=lambda o: o.order_id)

    # -- step 2 --------------------------------------------------------------

    def _step_receive(
        self, ctx: _Context, current_date: date
    ) -> list[PurchaseOrder]:
        received: list[PurchaseOrder] = []
        for po in ctx.state["purchase_orders"]:
            if po.status != "pending" or po.expected_arrival != current_date:
                continue
            destination = po.destination_warehouse_id or ctx.default_warehouse()
            row = ctx.ensure_inventory_row(po.product_id, destination)
            row.quantity_on_hand += po.quantity
            po.status = "received"
            received.append(po)
            ctx.side_effect(
                f"{po.quantity} units of {po.product_id} received into {destination} "
                f"from {po.po_id}"
            )
        return received

    # -- step 3 --------------------------------------------------------------

    def _step_reserve(self, ctx: _Context, current_date: date) -> list[str]:
        notes: list[str] = []
        for order in ctx.outstanding():
            if order.due_date > current_date:
                continue
            need = ctx.remaining[order.order_id]
            available = ctx.available(order.product_id)
            if available <= 0:
                continue
            take = min(available, need)
            reservation = ctx.reservations[order.order_id]
            for warehouse_id in sorted(ctx.warehouse_ids()):
                if take <= 0:
                    break
                row = ctx.inventory_row(order.product_id, warehouse_id)
                if row is None:
                    continue
                spare = max(0, row.quantity_on_hand - row.quantity_reserved)
                grab = min(spare, take)
                if grab <= 0:
                    continue
                row.quantity_reserved += grab
                reservation.slots.append((warehouse_id, grab))
                take -= grab
            got = reservation.total
            if got:
                notes.append(f"{order.order_id} reserved {got}/{need}")
            if got < need:
                # The order could not be covered in full on the day it was due.
                # Partial cover counts: a 20-of-25 fill is a stockout just as
                # much as a 0-of-25 one.
                ctx.stockouts.add(order.order_id)
                ctx.side_effect(
                    f"{order.order_id} only partially reserved ({got} of {need} units)"
                )
        return notes

    # -- step 4: the action --------------------------------------------------

    def _apply_action(
        self, ctx: _Context, action: ProposedAction, current_date: date
    ) -> tuple[str, str]:
        params = action.parameters
        if action.action_type is ActionType.TRANSFER_INVENTORY:
            source = ctx.inventory_row(
                params["product_id"], params["source_warehouse_id"]
            )
            destination = ctx.inventory_row(
                params["product_id"], params["destination_warehouse_id"]
            )
            if source is None or destination is None:
                ctx.violate(
                    "no inventory row for product "
                    f"{params['product_id']} in the source or destination warehouse"
                )
                return (
                    f"transfer of {params['quantity']} x {params['product_id']} "
                    "could not be applied (missing inventory row)",
                    action.action_id,
                )
            quantity = int(params["quantity"])
            source.quantity_on_hand -= quantity
            destination.quantity_on_hand += quantity
            if source.quantity_reserved > 0:
                ctx.side_effect(
                    f"transfer moved {quantity} units of {params['product_id']} out of "
                    f"{source.warehouse_id} while {source.quantity_reserved} units "
                    "there were already reserved for customer orders"
                )
            return (
                f"applied {action.action_type.value}: {quantity} x "
                f"{params['product_id']} "
                f"{params['source_warehouse_id']} -> {params['destination_warehouse_id']}",
                params["product_id"],
            )

        if action.action_type is ActionType.CREATE_PURCHASE_ORDER:
            ctx.po_counter += 1
            supplier_id = params["supplier_id"]
            lead = next(
                (
                    s.lead_time_days
                    for s in ctx.state["suppliers"]
                    if s.supplier_id == supplier_id
                ),
                None,
            )
            quantity = int(params["quantity"])
            value = quantity * ctx.price(params["product_id"])
            ctx.budget_used += value
            if lead is None:
                arrival = current_date + timedelta(days=ctx.horizon_days)
            else:
                arrival = current_date + timedelta(days=int(lead))
            ctx.state["purchase_orders"].append(
                PurchaseOrder(
                    po_id=f"PO-SIM-{ctx.po_counter:04d}",
                    product_id=params["product_id"],
                    supplier_id=supplier_id,
                    quantity=quantity,
                    expected_arrival=arrival,
                    status="pending",
                    destination_warehouse_id=params.get("destination_warehouse_id"),
                )
            )
            if arrival > current_date + timedelta(days=ctx.horizon_days - 1):
                ctx.side_effect(
                    f"new purchase order for {quantity} units of "
                    f"{params['product_id']} will not arrive within the horizon"
                )
            duplicates = [
                po
                for po in ctx.state["purchase_orders"]
                if po.status == "pending"
                and po.product_id == params["product_id"]
                and po.supplier_id == supplier_id
            ]
            if len(duplicates) > 1:
                ctx.violate(
                    f"{len(duplicates)} pending purchase orders exist for "
                    f"{params['product_id']} from {supplier_id}"
                )
            return (
                f"applied {action.action_type.value}: {quantity} x "
                f"{params['product_id']} from {supplier_id} arriving {arrival.isoformat()}",
                params["product_id"],
            )

        if action.action_type is ActionType.CHANGE_ORDER_PRIORITY:
            order = ctx.order(params["order_id"])
            previous = order.priority
            order.priority = params["new_priority"]
            if _PRIORITY_RANK[params["new_priority"]] > _PRIORITY_RANK[previous]:
                ctx.side_effect(
                    f"{order.order_id} downgraded {previous} -> {params['new_priority']}, "
                    "it will be served after every other pending order of equal stock"
                )
            else:
                ctx.side_effect(
                    f"{order.order_id} promoted {previous} -> {params['new_priority']}, "
                    "it will jump ahead of lower-priority demand"
                )
            return (
                f"applied {action.action_type.value}: {order.order_id} "
                f"{previous} -> {params['new_priority']}",
                order.order_id,
            )

        # CANCEL_PURCHASE_ORDER
        po_id = params["po_id"]
        for po in ctx.state["purchase_orders"]:
            if po.po_id == po_id:
                if po.status != "pending":
                    ctx.violate(
                        f"purchase order {po_id} cannot be cancelled, status is "
                        f"{po.status!r}"
                    )
                else:
                    po.status = "cancelled"
                    ctx.side_effect(
                        f"{po_id} cancelled: {po.quantity} units of {po.product_id} "
                        f"will no longer arrive on {po.expected_arrival.isoformat()}"
                    )
                return (
                    f"applied {action.action_type.value}: cancelled {po_id}",
                    po_id,
                )
        ctx.violate(f"purchase order {po_id} does not exist")
        return (f"purchase order {po_id} not found", po_id)

    # -- step 5 --------------------------------------------------------------

    def _step_update(
        self, ctx: _Context, current_date: date, focus: str
    ) -> tuple[list[str], str]:
        notes: list[str] = []
        for order in sorted(
            ctx.state["customer_orders"], key=lambda o: o.order_id
        ):
            if order.order_id not in ctx.remaining:
                continue
            need = ctx.remaining[order.order_id]
            if need <= 0:
                continue
            reservation = ctx.reservations[order.order_id]
            if reservation.total >= need:
                for warehouse_id, qty in reservation.release():
                    row = ctx.inventory_row(order.product_id, warehouse_id)
                    if row is not None:
                        row.quantity_on_hand -= qty
                        row.quantity_reserved -= qty
                ctx.remaining[order.order_id] = 0
                order.status = "fulfilled"
                notes.append(f"{order.order_id} fulfilled")
                focus = focus or order.order_id
            elif order.due_date < current_date and order.status != "delayed":
                order.status = "delayed"
                notes.append(f"{order.order_id} delayed")
                focus = focus or order.order_id
        return notes, focus

    # -- step 6 --------------------------------------------------------------

    def _step_capacity(self, ctx: _Context) -> list[str]:
        notes: list[str] = []
        for warehouse in sorted(
            ctx.state["warehouses"], key=lambda w: w.warehouse_id
        ):
            held = sum(
                row.quantity_on_hand
                for row in ctx.state["inventory"]
                if row.warehouse_id == warehouse.warehouse_id
            )
            if not warehouse.capacity:
                continue
            pct = 100.0 * held / warehouse.capacity
            ctx.peak_capacity_pct[warehouse.warehouse_id] = max(
                pct, ctx.peak_capacity_pct.get(warehouse.warehouse_id, 0.0)
            )
            if held > warehouse.capacity:
                # Deliberately does not quote the live unit count: stock ships
                # during the run, so including it would emit a fresh message
                # every day and defeat deduplication. The worst level seen is in
                # the max_warehouse_capacity_pct KPI.
                ctx.violate(
                    f"warehouse {warehouse.warehouse_id} exceeded its capacity of "
                    f"{warehouse.capacity} units"
                )
                ctx.side_effect(
                    f"warehouse {warehouse.warehouse_id} is over capacity"
                )
                notes.append(f"capacity exceeded at {warehouse.warehouse_id}")
            elif pct > 90.0:
                ctx.side_effect(
                    f"warehouse {warehouse.warehouse_id} at {pct:.0f}% capacity"
                )
        return notes

    # -- step 7 --------------------------------------------------------------

    def _step_detect(self, ctx: _Context) -> None:
        if not ctx.rules.allow_negative_inventory:
            for row in ctx.state["inventory"]:
                if row.quantity_on_hand - row.quantity_reserved < 0:
                    ctx.violate(
                        f"negative available inventory for {row.product_id}@"
                        f"{row.warehouse_id}: on_hand {row.quantity_on_hand}, "
                        f"reserved {row.quantity_reserved}"
                    )

        for order in ctx.state["customer_orders"]:
            if (
                order.status == "delayed"
                and order.order_id not in ctx.initially_delayed
            ):
                if order.priority == "high":
                    # Spec Section 5.3: a high-priority order pushed to delayed
                    # is a hard violation, not a risk note.
                    ctx.violate(
                        f"high priority order {order.order_id} was pushed to delayed"
                    )
                else:
                    ctx.side_effect(
                        f"{order.priority} priority order {order.order_id} was "
                        "pushed to delayed"
                    )

    # -- KPIs and identity ---------------------------------------------------

    def _compute_kpis(
        self, ctx: _Context, action: ProposedAction
    ) -> dict[str, float]:
        value = 0.0
        for row in ctx.state["inventory"]:
            value += row.quantity_on_hand * ctx.price(row.product_id)

        orders = ctx.state["customer_orders"]
        fulfilled = sum(1 for o in orders if o.status == "fulfilled")
        delayed = sum(1 for o in orders if o.status == "delayed")
        outstanding = sum(
            1
            for o in orders
            if o.order_id in ctx.remaining and ctx.remaining[o.order_id] > 0
        )

        total_on_hand = sum(row.quantity_on_hand for row in ctx.state["inventory"])
        total_capacity = sum(w.capacity for w in ctx.state["warehouses"])
        pending_pos = sum(
            1 for po in ctx.state["purchase_orders"] if po.status == "pending"
        )
        received_pos = sum(
            1 for po in ctx.state["purchase_orders"] if po.status == "received"
        )

        return {
            # required minimums
            "stockout_count": float(len(ctx.stockouts)),
            "orders_delayed": float(delayed),
            "total_inventory_value": round(value, 2),
            "budget_used": round(ctx.budget_used, 2),
            # context for _is_material_risk and for the UI
            "action_value": round(_action_value(ctx, action), 2),
            "orders_fulfilled": float(fulfilled),
            "orders_outstanding": float(outstanding),
            "total_units_on_hand": float(total_on_hand),
            "capacity_used_pct": round(
                100.0 * total_on_hand / total_capacity if total_capacity else 0.0, 2
            ),
            "max_warehouse_capacity_pct": round(
                max(ctx.peak_capacity_pct.values(), default=0.0), 2
            ),
            "purchase_orders_pending": float(pending_pos),
            "purchase_orders_received": float(received_pos),
            "violated_rule_count": float(len(ctx.violated_rules)),
            "side_effect_count": float(len(ctx.side_effects)),
        }

    @staticmethod
    def _simulation_id(
        snapshot_id: str, action: ProposedAction, horizon_days: int, seed: int
    ) -> str:
        payload = json.dumps(
            {
                "snapshot_id": snapshot_id,
                "action_id": action.action_id,
                "action_type": action.action_type.value,
                "parameters": action.parameters,
                "proposed_by": action.proposed_by.value,
                "horizon_days": horizon_days,
                "seed": seed,
            },
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        return f"sim-{digest}"


def _action_value(ctx: _Context, action: ProposedAction) -> float:
    """Currency value of the action, used by ``_is_material_risk``."""
    params = action.parameters
    if action.action_type in (
        ActionType.TRANSFER_INVENTORY,
        ActionType.CREATE_PURCHASE_ORDER,
    ):
        product_id = params.get("product_id")
        if product_id is None:
            return 0.0
        try:
            return float(params.get("quantity", 0)) * ctx.price(product_id)
        except ValueError:
            return 0.0
    return 0.0
