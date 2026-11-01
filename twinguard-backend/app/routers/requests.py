import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from ..agent_runner import make_provider, run_request
from ..audit import record
from ..auth import User, current_user, require_role
from ..deps import get_db
from ..schemas import RequestCreated, RequestIn, RequestOut

router = APIRouter(prefix="/api/requests")


def to_out(db, row: dict) -> RequestOut:
    steps = sorted(db.select("agent_steps", request_id=row["id"]), key=lambda s: s["idx"])
    return RequestOut(
        request_id=row["id"], status=row["status"], text=row["text"],
        created_by=row["created_by"], created_at=str(row["created_at"]),
        steps=[{"index": s["idx"], "tool": s["tool"], "summary": s["summary"]} for s in steps],
        proposed_action=row.get("action"), decision=row.get("decision"),
        report=row.get("report"), explanation=row.get("explanation"),
        clarification=row.get("clarification"), error=row.get("error"),
    )


@router.post("", response_model=RequestCreated,
             dependencies=[require_role("planner", "manager")])
def create(body: RequestIn, bg: BackgroundTasks, request: Request,
           user: User = Depends(current_user), db=Depends(get_db)):
    rid = "req-" + uuid.uuid4().hex[:12]
    db.insert("requests", [{
        "id": rid, "text": body.text, "created_by": user.username,
        "created_by_role": user.role, "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }])
    record(db, rid, "request_received", user.username, {"text": body.text})
    st = request.app.state
    factory = getattr(st, "provider_factory", make_provider)
    bg.add_task(run_request, rid, db, st.settings, lambda: factory(st.settings, body.text))
    return RequestCreated(request_id=rid)


@router.get("/{rid}", response_model=RequestOut, dependencies=[Depends(current_user)])
def get_one(rid: str, db=Depends(get_db)):
    rows = db.select("requests", id=rid)
    if not rows:
        raise HTTPException(404, "request not found")
    return to_out(db, rows[0])


@router.get("", response_model=list[RequestOut], dependencies=[Depends(current_user)])
def list_all(status: Optional[str] = None, outcome: Optional[str] = None,
             from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
             db=Depends(get_db)):
    eq = {k: v for k, v in (("status", status), ("decision", outcome)) if v}
    rows = db.select("requests", order_by="created_at", desc=True, limit=500, **eq)  # shortcut: newest 500, date filter in Python
    rows = [r for r in rows if (not from_ or str(r["created_at"]) >= from_)
            and (not to or str(r["created_at"]) <= to)]
    return [to_out(db, r) for r in sorted(rows, key=lambda r: str(r["created_at"]), reverse=True)]
