from fastapi import APIRouter, Depends, HTTPException

from ..auth import User, current_user, require_role
from ..deps import get_db
from ..review import Conflict, NotFound, decide_review
from ..schemas import RequestOut, ReviewIn, ReviewTaskOut
from .requests import to_out

router = APIRouter(prefix="/api/reviews")


@router.get("", response_model=list[ReviewTaskOut], dependencies=[Depends(current_user)])
def queue(db=Depends(get_db)):
    rows = db.select("review_tasks", status="pending")
    return [ReviewTaskOut(request_id=r["request_id"], status=r["status"], action=r["action"],
                          reasons=r["reasons"], created_at=str(r["created_at"]))
            for r in sorted(rows, key=lambda r: str(r["created_at"]))]


def _decide(rid: str, body: ReviewIn, approve: bool, user: User, db) -> RequestOut:
    try:
        decide_review(db, rid, approve, body.comment, user)
    except NotFound:
        raise HTTPException(404, "request not found")
    except Conflict as e:
        raise HTTPException(409, str(e))
    return to_out(db, db.select("requests", id=rid)[0])


@router.post("/{rid}/approve", response_model=RequestOut)
def approve(rid: str, body: ReviewIn, user: User = require_role("manager"), db=Depends(get_db)):
    return _decide(rid, body, True, user, db)


@router.post("/{rid}/reject", response_model=RequestOut)
def reject(rid: str, body: ReviewIn, user: User = require_role("manager"), db=Depends(get_db)):
    return _decide(rid, body, False, user, db)
