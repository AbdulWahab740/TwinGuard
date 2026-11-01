from fastapi import APIRouter, Depends, HTTPException

from .. import scenarios
from ..auth import current_user
from ..deps import get_settings
from ..schemas import ScenarioOut, ScenarioRunOut

router = APIRouter(prefix="/api/scenarios", dependencies=[Depends(current_user)])


@router.get("", response_model=list[ScenarioOut])
def list_all(settings=Depends(get_settings)):
    return [ScenarioOut(id=s["id"], title=s["title"], synthetic=s.get("synthetic", False))
            for s in scenarios.load_all(settings.scenarios_dir).values()]


@router.post("/{sid}/run", response_model=ScenarioRunOut)
def run(sid: str, settings=Depends(get_settings)):
    scn = scenarios.load_all(settings.scenarios_dir).get(sid)
    if scn is None:
        raise HTTPException(404, "scenario not found")
    return scenarios.run(scn)
