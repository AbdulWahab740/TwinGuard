"""One-time generator for NEW scenarios in contracts/scenarios/. Existing files are fixed: do not
regenerate them. After adding one, run scripts/make_fixtures.py to fill in its `expected`."""
import json
from pathlib import Path

from twinguard_core import ActionType, get_dataset
from twinguard_core.eval.actions import make_action
from twinguard_core.eval.corruption import corrupt_dataset
from twinguard_core.eval.labeler import label_action

OUT = Path(__file__).parent.parent / "contracts" / "scenarios"
SEED = 42
SCENARIOS = [
    ("safe-transfer", "Clean data, small transfer", [], ActionType.TRANSFER_INVENTORY, False),
    ("hidden-po-order", "Hidden purchase order, new order", ["missing_po"],
     ActionType.CREATE_PURCHASE_ORDER, True),
    ("stale-transfer", "Stale stock data, transfer", ["stale_sync"],
     ActionType.TRANSFER_INVENTORY, False),
]

for sid, title, corruptions, kind, unsafe_target in SCENARIOS:
    true = get_dataset("demo_baseline")
    observed = corrupt_dataset(true, corruptions, SEED)
    action = make_action(true, SEED, unsafe_target=unsafe_target, action_type=kind)
    label = label_action(true, action, SEED)
    (OUT / f"{sid}.json").write_text(json.dumps({
        "id": sid, "title": title, "synthetic": True, "seed": SEED,
        "observed": observed.model_dump(mode="json"),
        "action": action.model_dump(mode="json"),
        "truth": {"label": label, "corruptions": corruptions,
                  "state": true.model_dump(mode="json")},
    }))
    print(sid, label)
