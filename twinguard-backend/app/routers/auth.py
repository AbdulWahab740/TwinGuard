from fastapi import APIRouter, Depends, HTTPException

from ..auth import User, make_token, verify_password
from ..deps import get_db, get_settings
from ..schemas import LoginIn, LoginOut

router = APIRouter(prefix="/api/auth")


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, db=Depends(get_db), settings=Depends(get_settings)):
    rows = db.select("users", username=body.username)
    if not rows or not verify_password(body.password, rows[0]["password_hash"]):
        raise HTTPException(401, "invalid credentials")
    user = User(username=rows[0]["username"], role=rows[0]["role"])
    return LoginOut(access_token=make_token(user, settings.jwt_secret), role=user.role)
