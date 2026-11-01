"""The ONLY module that writes business data (via DbDataStore.apply_approved_action)."""
from twinguard_core import ProposedAction

from .audit import record
from .db import StaleVersionError
from .db_store import DbDataStore


class Conflict(Exception):
    """The review was already decided, or there is nothing to review."""


class NotFound(Exception):
    pass


def apply_request(db, request_id: str, actor: str) -> str:
    """Apply a request's action iff the data version still matches the one it was decided on.

    Returns the new request status: applied | stale_decision | failed.
    """
    row = db.select("requests", id=request_id)[0]
    set_status = lambda s, **v: db.update("requests", {"status": s, **v}, id=request_id)
    try:
        DbDataStore(db).apply_approved_action(
            ProposedAction.model_validate(row["action"]),
            expected_version=row["snapshot_version"],
        )
    except StaleVersionError:
        set_status("stale_decision")
        record(db, request_id, "stale_decision", actor, {"decided_on": row["snapshot_version"]})
        return "stale_decision"
    except Exception as e:  # noqa: BLE001 - surface as failed; the review stays closed (no double apply)
        set_status("failed", error=f"{type(e).__name__}: {e}"[:300])
        record(db, request_id, "failed", actor, {"error": str(e)[:300]})
        return "failed"
    set_status("applied")
    record(db, request_id, "applied", actor, {})
    return "applied"


def decide_review(db, request_id: str, approve: bool, comment: str, manager) -> str:
    if not db.select("requests", id=request_id):
        raise NotFound(request_id)
    verb = "approved" if approve else "rejected"
    # Atomic claim: only one caller can flip pending -> approved/rejected.
    claimed = db.update("review_tasks", {"status": verb, "decided_by": manager.username,
                                         "comment": comment},
                        request_id=request_id, status="pending")
    if not claimed:
        raise Conflict("no pending review for this request")
    record(db, request_id, f"review_{verb}", manager.username, {"comment": comment})
    if not approve:
        db.update("requests", {"status": "rejected"}, id=request_id)
        return "rejected"
    db.update("requests", {"status": "approved"}, id=request_id)
    return apply_request(db, request_id, manager.username)


def auto_apply(db, request_id: str) -> str:
    """Config-gated path for APPROVE decisions (allow_auto_apply=true)."""
    return apply_request(db, request_id, "gate")
