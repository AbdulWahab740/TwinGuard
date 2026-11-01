import json
from pathlib import Path

from fastapi import APIRouter, Depends

from ..auth import current_user
from ..deps import get_settings

router = APIRouter(prefix="/api/evaluation", dependencies=[Depends(current_user)])


@router.get("/summary")
def summary(settings=Depends(get_settings)):
    files = sorted(Path(settings.eval_results_dir).glob("*.json"))
    return {"files": {p.name: json.loads(p.read_text()) for p in files}}
