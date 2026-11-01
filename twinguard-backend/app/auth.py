from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

Role = Literal["planner", "manager", "viewer"]
_bearer = HTTPBearer(auto_error=False)


class User(BaseModel):
    username: str
    role: Role


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


def verify_password(p: str, h: str) -> bool:
    return bcrypt.checkpw(p.encode(), h.encode())


def make_token(user: User, secret: str, hours: int = 8) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=hours)
    return jwt.encode({"sub": user.username, "role": user.role, "exp": exp}, secret, "HS256")


def current_user(
    request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> User:
    if cred is None:
        raise HTTPException(401, "missing token")
    try:
        c = jwt.decode(cred.credentials, request.app.state.settings.jwt_secret, ["HS256"])
        return User(username=c["sub"], role=c["role"])
    except Exception:
        raise HTTPException(401, "invalid token")


def require_role(*roles: Role):
    def check(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "role not allowed")
        return user
    return Depends(check)
