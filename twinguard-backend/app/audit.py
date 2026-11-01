import uuid
from datetime import datetime, timezone


def record(db, request_id: str | None, kind: str, actor: str, payload: dict | None = None) -> None:
    db.insert("audit_events", [{
        "id": uuid.uuid4().hex, "request_id": request_id, "kind": kind, "actor": actor,
        "at": datetime.now(timezone.utc).isoformat(), "payload": payload or {},
    }])
