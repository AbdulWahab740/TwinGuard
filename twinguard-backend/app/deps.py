from fastapi import Request

from .db import Db


def get_db(request: Request) -> Db:
    return request.app.state.db


def get_settings(request: Request):
    return request.app.state.settings
