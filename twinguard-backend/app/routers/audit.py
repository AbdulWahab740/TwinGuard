from typing import Optional

from fastapi import APIRouter, Depends

from ..auth import current_user
from ..deps import get_db
from ..schemas import AuditEventOut

router = APIRouter(prefix="/api/audit")


@router.get("", response_model=list[AuditEventOut], dependencies=[Depends(current_user)])
def events(request_id: Optional[str] = None, db=Depends(get_db)):
    rows = db.select("audit_events", **({"request_id": request_id} if request_id else {}))
    return [AuditEventOut(**{**r, "at": str(r["at"])}) for r in sorted(rows, key=lambda r: str(r["at"]))]
